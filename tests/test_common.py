import json
import logging
from datetime import UTC, date, datetime

import pandas as pd
import pytest
import requests
import responses

from extract import common
from extract.common import (
    MissingSecretError,
    RateLimiter,
    RetryableHTTPError,
    build_session,
    day_range,
    get_json,
    get_last_loaded,
    paginate,
    parse_retry_after,
    partition_path,
    read_state,
    require_env,
    resolve_window,
    setup_logging,
    update_state,
    write_bronze,
)

URL = "https://api.example.com/v2/items"


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record sleep durations instead of actually sleeping."""
    recorded: list[float] = []
    monkeypatch.setattr(common, "_sleep", recorded.append)
    return recorded


# --------------------------------------------------------------------------- env & logging


def test_require_env_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_KEY", "secret-value")
    assert require_env("SOME_KEY") == "secret-value"


def test_require_env_missing_names_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_KEY", "  ")
    with pytest.raises(MissingSecretError, match="SOME_KEY"):
        require_env("SOME_KEY")


def test_setup_logging_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    setup_logging()
    setup_logging()
    assert len(root.handlers) == 1


# --------------------------------------------------------------------------- HTTP


@responses.activate
def test_get_json_returns_payload(sleeps: list[float]) -> None:
    responses.get(URL, json={"ok": True})
    assert get_json(build_session(), URL, {"limit": 1}) == {"ok": True}
    assert responses.calls[0].request.url == f"{URL}?limit=1"
    assert sleeps == []


@responses.activate
def test_get_json_retries_5xx_then_succeeds(sleeps: list[float]) -> None:
    responses.get(URL, status=500)
    responses.get(URL, status=503)
    responses.get(URL, json=[1, 2])
    assert get_json(build_session(), URL) == [1, 2]
    assert len(responses.calls) == 3
    assert len(sleeps) == 2


@responses.activate
def test_get_json_honours_retry_after_on_429(sleeps: list[float]) -> None:
    responses.get(URL, status=429, headers={"Retry-After": "7"})
    responses.get(URL, json={})
    get_json(build_session(), URL)
    assert sleeps == [7.0]


@responses.activate
def test_get_json_retries_connection_error(sleeps: list[float]) -> None:
    responses.get(URL, body=requests.ConnectionError("boom"))
    responses.get(URL, json={"ok": 1})
    assert get_json(build_session(), URL) == {"ok": 1}
    assert len(responses.calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
@responses.activate
def test_get_json_does_not_retry_client_errors(status: int, sleeps: list[float]) -> None:
    responses.get(URL, status=status)
    with pytest.raises(requests.HTTPError) as exc_info:
        get_json(build_session(), URL)
    assert not isinstance(exc_info.value, RetryableHTTPError)
    assert len(responses.calls) == 1
    assert sleeps == []


@responses.activate
def test_get_json_gives_up_after_max_attempts(sleeps: list[float]) -> None:
    responses.get(URL, status=502)
    with pytest.raises(RetryableHTTPError):
        get_json(build_session(), URL, max_attempts=3)
    assert len(responses.calls) == 3
    assert len(sleeps) == 2


def test_parse_retry_after_variants() -> None:
    now = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after("Mon, 05 Oct 2026 12:00:30 GMT", now) == 30.0
    assert parse_retry_after("Mon, 05 Oct 2026 11:00:00 GMT", now) == 0.0
    assert parse_retry_after("abc") is None
    assert parse_retry_after(None) is None


def test_rate_limiter_sleeps_when_window_is_full() -> None:
    clock = [0.0]
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds

    limiter = RateLimiter(max_calls=2, period_s=8, clock=lambda: clock[0], sleep=fake_sleep)
    limiter.wait()
    clock[0] = 1.0
    limiter.wait()
    clock[0] = 2.0
    limiter.wait()  # third call within 8 s of the first → wait until t=8
    assert slept == [6.0]


# --------------------------------------------------------------------------- pagination


def _pager(pages: list[list[int]]) -> tuple[list[tuple[int, int]], object]:
    calls: list[tuple[int, int]] = []

    def fetch(limit: int, offset: int) -> list[int]:
        calls.append((limit, offset))
        return pages[len(calls) - 1]

    return calls, fetch


def test_paginate_stops_on_short_page() -> None:
    calls, fetch = _pager([[1, 2, 3], [4, 5, 6], [7]])
    assert [x for page in paginate(fetch, limit=3) for x in page] == [1, 2, 3, 4, 5, 6, 7]
    assert calls == [(3, 0), (3, 3), (3, 6)]


def test_paginate_stops_on_empty_page() -> None:
    calls, fetch = _pager([[1, 2, 3], [4, 5, 6], []])
    assert list(paginate(fetch, limit=3)) == [[1, 2, 3], [4, 5, 6]]
    assert len(calls) == 3


def test_paginate_guards_against_endless_loop() -> None:
    with pytest.raises(RuntimeError, match="max_pages"):
        list(paginate(lambda limit, offset: [0] * limit, limit=2, max_pages=3))


def test_day_range_is_inclusive() -> None:
    assert day_range(date(2026, 10, 1), date(2026, 10, 3)) == [
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 3),
    ]
    with pytest.raises(ValueError):
        day_range(date(2026, 10, 3), date(2026, 10, 1))


# --------------------------------------------------------------------------- bronze


def test_write_bronze_creates_partition_with_loaded_at(tmp_path) -> None:
    loaded_at = datetime(2026, 10, 5, 8, 30, tzinfo=UTC)
    path = write_bronze(
        [{"id": "A", "value": 1.5}],
        "golemio_air_quality_history",
        date(2026, 10, 1),
        bronze_dir=tmp_path,
        loaded_at=loaded_at,
    )
    assert path == tmp_path / "golemio_air_quality_history/load_date=2026-10-01/part.parquet"
    df = pd.read_parquet(path)
    assert list(df.columns) == ["id", "value", "_loaded_at"]
    assert df["_loaded_at"].iloc[0] == pd.Timestamp(loaded_at)
    assert str(df["_loaded_at"].dt.tz) == "UTC"


def test_write_bronze_rerun_overwrites_not_duplicates(tmp_path) -> None:
    day = date(2026, 10, 1)
    write_bronze([{"id": "A"}, {"id": "B"}], "src", day, bronze_dir=tmp_path)
    path = write_bronze([{"id": "C"}], "src", day, bronze_dir=tmp_path)
    files = sorted(p.name for p in path.parent.iterdir())
    assert files == ["part.parquet"]
    assert pd.read_parquet(path)["id"].tolist() == ["C"]


def test_write_bronze_removes_stray_files(tmp_path) -> None:
    target = partition_path("src", date(2026, 10, 1), tmp_path)
    target.parent.mkdir(parents=True)
    (target.parent / "part.parquet.tmp").write_bytes(b"partial")
    write_bronze([{"id": "A"}], "src", date(2026, 10, 1), bronze_dir=tmp_path)
    assert [p.name for p in target.parent.iterdir()] == ["part.parquet"]


def test_write_bronze_empty_keeps_existing_partition(tmp_path) -> None:
    day = date(2026, 10, 1)
    path = write_bronze([{"id": "A"}], "src", day, bronze_dir=tmp_path)
    assert write_bronze([], "src", day, bronze_dir=tmp_path) is None
    assert pd.read_parquet(path)["id"].tolist() == ["A"]


# --------------------------------------------------------------------------- state


def test_read_state_missing_file_is_empty(tmp_path) -> None:
    assert read_state(tmp_path / "_state.json") == {}
    assert get_last_loaded("src", tmp_path / "_state.json") is None


def test_update_state_round_trip_and_never_moves_backwards(tmp_path) -> None:
    state_path = tmp_path / "_state.json"
    update_state("src", date(2026, 10, 3), state_path)
    update_state("other", date(2026, 9, 1), state_path)
    update_state("src", date(2026, 9, 20), state_path)  # backfill must not reset state
    assert get_last_loaded("src", state_path) == date(2026, 10, 3)
    assert get_last_loaded("other", state_path) == date(2026, 9, 1)
    raw = json.loads(state_path.read_text())
    assert raw["src"]["last_loaded_date"] == "2026-10-03"
    assert not (tmp_path / "_state.json.tmp").exists()


def test_resolve_window_explicit_dates_win(tmp_path) -> None:
    state_path = tmp_path / "_state.json"
    update_state("src", date(2026, 10, 3), state_path)
    window = resolve_window(
        "src", date(2026, 9, 1), date(2026, 9, 2), today=date(2026, 10, 5), state_path=state_path
    )
    assert window == (date(2026, 9, 1), date(2026, 9, 2))


def test_resolve_window_resumes_from_state(tmp_path) -> None:
    state_path = tmp_path / "_state.json"
    update_state("src", date(2026, 10, 3), state_path)
    window = resolve_window("src", today=date(2026, 10, 5), state_path=state_path)
    assert window == (date(2026, 10, 3), date(2026, 10, 5))


def test_resolve_window_first_run_goes_back_90_days(tmp_path) -> None:
    window = resolve_window("src", today=date(2026, 10, 5), state_path=tmp_path / "_state.json")
    assert window == (date(2026, 7, 7), date(2026, 10, 5))


def test_resolve_window_rejects_inverted_range(tmp_path) -> None:
    with pytest.raises(ValueError):
        resolve_window(
            "src", date(2026, 10, 5), date(2026, 10, 1), state_path=tmp_path / "_state.json"
        )
