import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import responses
from responses import matchers

from extract import common
from extract.common import MissingSecretError, build_session, get_last_loaded
from extract.golemio import RateLimiter, day_bounds
from extract.golemio_bicycle import (
    BASE_URL,
    COUNTERS_SOURCE,
    DETECTIONS_SOURCE,
    fetch_detections_day,
    parse_counters,
    parse_detections,
    run,
)

FIXTURES = Path(__file__).parent / "fixtures" / "golemio"
DETECTIONS_URL = f"{BASE_URL}/detections"
KEY = ["locations_id", "id", "measured_from"]


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "_sleep", lambda seconds: None)


@pytest.fixture
def counters_json() -> dict[str, Any]:
    return load_fixture("bicyclecounters.json")


@pytest.fixture
def detections_json() -> list[dict[str, Any]]:
    return load_fixture("bicyclecounters_detections.json")


# --------------------------------------------------------------------------- parsing


def test_parse_counters_keeps_raw_fields(counters_json: dict[str, Any]) -> None:
    rows = parse_counters(counters_json)
    assert len(rows) == len(counters_json["features"]) == 3
    first = counters_json["features"][0]
    assert set(rows[0]) == {"id", "name", "route", "updated_at", "directions", "geometry"}
    assert rows[0]["id"] == first["properties"]["id"]
    assert json.loads(rows[0]["directions"]) == first["properties"]["directions"]
    assert json.loads(rows[0]["geometry"]) == first["geometry"]


def test_parse_detections_values_as_returned(detections_json: list[dict[str, Any]]) -> None:
    df = parse_detections(detections_json)
    assert len(df) == len(detections_json)
    assert list(df.columns) == list(detections_json[0])
    assert df["measured_from"].tolist() == [r["measured_from"] for r in detections_json]
    assert df["value"].dtype == "float64" and df["value_pedestrians"].dtype == "float64"
    assert df["value"].isna().sum() == sum(r["value"] is None for r in detections_json)


def test_direction_id_is_not_unique_but_natural_key_is(
    detections_json: list[dict[str, Any]],
) -> None:
    df = parse_detections(detections_json)
    assert df.duplicated(["id", "measured_from"]).any()  # same direction on two counters
    assert not df.duplicated(KEY).any()


def test_parse_detections_empty() -> None:
    assert parse_detections([]).empty


# --------------------------------------------------------------------------- fetching


@responses.activate
def test_fetch_detections_day_paginates(detections_json: list[dict[str, Any]]) -> None:
    date_from, date_to = day_bounds(date(2026, 10, 2))
    for offset, page in [(0, detections_json[:3]), (3, detections_json[3:])]:
        responses.get(
            DETECTIONS_URL,
            json=page,
            match=[
                matchers.header_matcher({"X-Access-Token": "test-key"}),
                matchers.query_param_matcher(
                    {"from": date_from, "to": date_to, "limit": "3", "offset": str(offset)}
                ),
            ],
        )
    session = build_session({"X-Access-Token": "test-key"})
    limiter = RateLimiter(max_calls=1000, period_s=1)
    records = fetch_detections_day(session, limiter, date(2026, 10, 2), page_limit=3)
    assert records == detections_json
    assert len(responses.calls) == 2


# --------------------------------------------------------------------------- run


@responses.activate
def test_run_writes_partitions_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    counters_json: dict[str, Any],
    detections_json: list[dict[str, Any]],
) -> None:
    monkeypatch.setenv("GOLEMIO_API_KEY", "test-key")
    responses.get(BASE_URL, json=counters_json)
    for iso_day, records in {"2026-10-02": detections_json, "2026-10-03": []}.items():
        date_from, date_to = day_bounds(date.fromisoformat(iso_day))
        responses.get(
            DETECTIONS_URL,
            json=records,
            match=[
                matchers.query_param_matcher({"from": date_from, "to": date_to}, strict_match=False)
            ],
        )
    state = tmp_path / "_state.json"
    kwargs = {"bronze_dir": tmp_path, "state_path": state, "today": date(2026, 10, 3)}
    run(date(2026, 10, 2), date(2026, 10, 3), **kwargs)
    run(date(2026, 10, 2), date(2026, 10, 3), **kwargs)

    files = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*.parquet"))
    assert files == [
        f"{COUNTERS_SOURCE}/load_date=2026-10-03/part.parquet",
        f"{DETECTIONS_SOURCE}/load_date=2026-10-02/part.parquet",
    ]
    detections = pd.read_parquet(tmp_path / DETECTIONS_SOURCE)
    assert len(detections) == len(detections_json)
    assert not detections.duplicated(KEY).any()
    assert len(pd.read_parquet(tmp_path / COUNTERS_SOURCE)) == 3
    assert get_last_loaded(DETECTIONS_SOURCE, state) == date(2026, 10, 2)


def test_run_requires_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GOLEMIO_API_KEY", raising=False)
    with pytest.raises(MissingSecretError, match="GOLEMIO_API_KEY"):
        run(bronze_dir=tmp_path, state_path=tmp_path / "_state.json")
