import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import requests
import responses
from responses import matchers

from extract import common
from extract.common import get_last_loaded, resolve_window, update_state, write_bronze
from extract.golemio_air_quality import STATIONS_SOURCE
from extract.open_meteo import (
    BASE_URL,
    HOURLY_VARIABLES,
    SOURCE,
    MissingStationsError,
    Station,
    build_params,
    date_chunks,
    load_stations,
    parse_response,
    run,
    split_by_day,
    to_frame,
)

FIXTURES = Path(__file__).parent / "fixtures" / "open_meteo"
# Same coordinates (and order) as the fixture request.
STATIONS = [Station("ABREA", 50.084385, 14.380116), Station("ACHOA", 50.03017, 14.51745)]


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "_sleep", lambda seconds: None)


def write_station_snapshot(bronze: Path) -> None:
    snapshot = [
        {"id": s.station_id, "geometry": json.dumps({"coordinates": [s.longitude, s.latitude]})}
        for s in reversed(STATIONS)
    ]
    write_bronze(snapshot, STATIONS_SOURCE, date(2026, 10, 4), bronze_dir=bronze)


# --------------------------------------------------------------------------- pure helpers


def test_parse_response_one_row_per_station_hour() -> None:
    payload = load_fixture("archive_2_stations_2_days.json")
    rows = parse_response(payload, STATIONS)
    assert len(rows) == 2 * 48
    first = rows[0]
    assert first["station_id"] == "ABREA"
    assert first["time"] == "2026-10-02T00:00"
    assert (first["requested_latitude"], first["requested_longitude"]) == (50.084385, 14.380116)
    assert first["latitude"] == payload[0]["latitude"]  # grid cell, not the requested point
    assert first["temperature_2m"] == payload[0]["hourly"]["temperature_2m"][0]
    assert json.loads(first["hourly_units"])["wind_speed_10m"] == "km/h"
    assert rows[48]["station_id"] == "ACHOA"
    assert set(HOURLY_VARIABLES) <= set(first)


def test_parse_response_accepts_single_location_object() -> None:
    payload = load_fixture("archive_2_stations_2_days.json")[0]
    assert len(parse_response(payload, STATIONS[:1])) == 48


def test_parse_response_rejects_location_count_mismatch() -> None:
    with pytest.raises(ValueError, match="Expected 1 locations, got 2"):
        parse_response(load_fixture("archive_2_stations_2_days.json"), STATIONS[:1])


def test_split_by_day_uses_utc_date() -> None:
    by_day = split_by_day(parse_response(load_fixture("archive_2_stations_2_days.json"), STATIONS))
    assert sorted(by_day) == [date(2026, 10, 2), date(2026, 10, 3)]
    assert all(len(rows) == 2 * 24 for rows in by_day.values())


def test_to_frame_keeps_float_schema_with_and_without_nulls() -> None:
    rows = parse_response(load_fixture("archive_2_stations_2_days.json"), STATIONS)
    with_nulls = [dict(rows[0], relative_humidity_2m=None, wind_direction_10m=None), rows[1]]
    for frame in (to_frame(rows), to_frame(with_nulls)):
        assert all(frame[var].dtype == "float64" for var in HOURLY_VARIABLES)
    assert to_frame([]).empty


def test_date_chunks() -> None:
    assert date_chunks(date(2026, 7, 1), date(2026, 8, 5), size=31) == [
        (date(2026, 7, 1), date(2026, 7, 31)),
        (date(2026, 8, 1), date(2026, 8, 5)),
    ]
    assert date_chunks(date(2026, 10, 2), date(2026, 10, 2)) == [
        (date(2026, 10, 2), date(2026, 10, 2))
    ]


def test_build_params_keeps_station_order() -> None:
    params = build_params(STATIONS, date(2026, 10, 2), date(2026, 10, 3))
    assert params["latitude"] == "50.084385,50.03017"
    assert params["longitude"] == "14.380116,14.51745"
    assert params["timezone"] == "UTC"
    assert params["hourly"] == ",".join(HOURLY_VARIABLES)


def test_resolve_window_lookback(tmp_path: Path) -> None:
    state = tmp_path / "_state.json"
    update_state("src", date(2026, 10, 4), state)
    window = resolve_window("src", today=date(2026, 10, 5), lookback_days=3, state_path=state)
    assert window == (date(2026, 10, 1), date(2026, 10, 5))


# --------------------------------------------------------------------------- stations


def test_load_stations_from_latest_snapshot(tmp_path: Path) -> None:
    write_station_snapshot(tmp_path)
    assert load_stations(tmp_path) == STATIONS  # sorted by id


def test_load_stations_without_snapshot(tmp_path: Path) -> None:
    with pytest.raises(MissingStationsError, match="golemio_air_quality"):
        load_stations(tmp_path)


# --------------------------------------------------------------------------- run


@responses.activate
def test_run_writes_day_partitions_idempotently(tmp_path: Path) -> None:
    write_station_snapshot(tmp_path)
    responses.get(
        BASE_URL,
        json=load_fixture("archive_2_stations_2_days.json"),
        match=[
            matchers.query_param_matcher(
                build_params(STATIONS, date(2026, 10, 2), date(2026, 10, 3))
            )
        ],
    )
    state = tmp_path / "_state.json"
    for _ in range(2):
        run(date(2026, 10, 2), date(2026, 10, 3), bronze_dir=tmp_path, state_path=state)

    files = sorted(str(p.relative_to(tmp_path)) for p in (tmp_path / SOURCE).rglob("*.parquet"))
    assert files == [
        f"{SOURCE}/load_date=2026-10-02/part.parquet",
        f"{SOURCE}/load_date=2026-10-03/part.parquet",
    ]
    df = pd.read_parquet(tmp_path / SOURCE)
    assert len(df) == 96
    assert not df.duplicated(["station_id", "time"]).any()
    assert get_last_loaded(SOURCE, state) == date(2026, 10, 3)


@responses.activate
def test_bad_request_is_not_retried(tmp_path: Path) -> None:
    write_station_snapshot(tmp_path)
    responses.get(BASE_URL, status=400, json=load_fixture("error_400.json"))
    with pytest.raises(requests.HTTPError):
        run(
            date(2026, 10, 2),
            date(2026, 10, 3),
            bronze_dir=tmp_path,
            state_path=tmp_path / "_state.json",
        )
    assert len(responses.calls) == 1
