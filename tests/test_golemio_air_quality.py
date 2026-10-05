import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import responses
from responses import matchers

from extract import common
from extract.common import MissingSecretError, RateLimiter, build_session, get_last_loaded
from extract.golemio import day_bounds
from extract.golemio_air_quality import (
    BASE_URL,
    HISTORY_SOURCE,
    STATIONS_SOURCE,
    fetch_history_day,
    parse_history,
    parse_stations,
    run,
)

FIXTURES = Path(__file__).parent / "fixtures" / "golemio"
HISTORY_URL = f"{BASE_URL}/history"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def stations_json() -> dict[str, Any]:
    return load_fixture("airqualitystations.json")


@pytest.fixture
def history_json() -> list[dict[str, Any]]:
    return load_fixture("airqualitystations_history.json")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "_sleep", lambda seconds: None)


def unlimited() -> RateLimiter:
    return RateLimiter(max_calls=1000, period_s=1)


# --------------------------------------------------------------------------- parsing


def test_parse_stations_keeps_raw_fields(stations_json: dict[str, Any]) -> None:
    rows = parse_stations(stations_json)
    assert len(rows) == len(stations_json["features"]) == 3
    assert set(rows[0]) == {"id", "name", "district", "updated_at", "measurement", "geometry"}

    first = stations_json["features"][0]
    assert rows[0]["id"] == first["properties"]["id"]
    assert rows[0]["name"] == "Praha 6-Břevnov"
    assert rows[0]["updated_at"] == first["properties"]["updated_at"]
    assert json.loads(rows[0]["geometry"]) == first["geometry"]
    assert json.loads(rows[0]["measurement"]) == first["properties"]["measurement"]


def test_parse_stations_does_not_type_values(stations_json: dict[str, Any]) -> None:
    measurement = json.loads(parse_stations(stations_json)[0]["measurement"])
    assert measurement["AQ_hourly_index"] == "1B"
    assert measurement["components"][0]["averaged_time"]["averaged_hours"] == "1"


def test_parse_history_one_row_per_record(history_json: list[dict[str, Any]]) -> None:
    rows = parse_history(history_json)
    assert len(rows) == 3
    for row, record in zip(rows, history_json, strict=True):
        assert set(row) == {"id", "updated_at", "measurement"}
        assert row["id"] == record["id"]
        assert row["updated_at"] == record["updated_at"]
        assert json.loads(row["measurement"]) == record["measurement"]


def test_parse_stations_handles_empty_collection() -> None:
    assert parse_stations({"type": "FeatureCollection", "features": []}) == []


def test_day_bounds_cover_whole_utc_day() -> None:
    assert day_bounds(date(2026, 10, 3)) == (
        "2026-10-03T00:00:00.000Z",
        "2026-10-03T23:59:59.999Z",
    )


# --------------------------------------------------------------------------- fetching


@responses.activate
def test_fetch_history_day_sends_params_and_paginates(history_json: list[dict[str, Any]]) -> None:
    date_from, date_to = day_bounds(date(2026, 10, 3))
    for offset, page in [(0, history_json[:2]), (2, history_json[2:])]:
        responses.get(
            HISTORY_URL,
            json=page,
            match=[
                matchers.header_matcher({"X-Access-Token": "test-key"}),
                matchers.query_param_matcher(
                    {"from": date_from, "to": date_to, "limit": "2", "offset": str(offset)}
                ),
            ],
        )
    session = build_session({"X-Access-Token": "test-key"})
    records = fetch_history_day(session, unlimited(), date(2026, 10, 3), page_limit=2)
    assert records == history_json
    assert len(responses.calls) == 2


# --------------------------------------------------------------------------- run


def _mock_api(stations_json: dict[str, Any], history_by_day: dict[str, list]) -> None:
    responses.get(BASE_URL, json=stations_json)
    for iso_day, records in history_by_day.items():
        date_from, date_to = day_bounds(date.fromisoformat(iso_day))
        responses.get(
            HISTORY_URL,
            json=records,
            match=[
                matchers.query_param_matcher({"from": date_from, "to": date_to}, strict_match=False)
            ],
        )


@responses.activate
def test_run_writes_partitions_and_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stations_json: dict[str, Any],
    history_json: list[dict[str, Any]],
) -> None:
    monkeypatch.setenv("GOLEMIO_API_KEY", "test-key")
    _mock_api(
        stations_json,
        {"2026-10-03": history_json, "2026-10-04": history_json[:1], "2026-10-05": []},
    )
    bronze, state = tmp_path / "bronze", tmp_path / "_state.json"
    kwargs = {"bronze_dir": bronze, "state_path": state, "today": date(2026, 10, 5)}

    run(date(2026, 10, 3), date(2026, 10, 5), **kwargs)
    run(date(2026, 10, 3), date(2026, 10, 5), **kwargs)  # re-run must not duplicate

    files = sorted(str(p.relative_to(bronze)) for p in bronze.rglob("*") if p.is_file())
    assert files == [
        f"{HISTORY_SOURCE}/load_date=2026-10-03/part.parquet",
        f"{HISTORY_SOURCE}/load_date=2026-10-04/part.parquet",
        f"{STATIONS_SOURCE}/load_date=2026-10-05/part.parquet",
    ]
    history = pd.read_parquet(bronze / HISTORY_SOURCE)
    assert len(history) == 4
    assert {"id", "updated_at", "measurement", "_loaded_at"} <= set(history.columns)
    assert len(pd.read_parquet(bronze / STATIONS_SOURCE)) == 3
    # 2026-10-05 returned no rows, so state stays on the last day with data
    assert get_last_loaded(HISTORY_SOURCE, state) == date(2026, 10, 4)


def test_run_requires_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GOLEMIO_API_KEY", raising=False)
    with pytest.raises(MissingSecretError, match="GOLEMIO_API_KEY"):
        run(bronze_dir=tmp_path, state_path=tmp_path / "_state.json")
