"""Open-Meteo Archive API: hourly weather at the Golemio air quality station coordinates.

All stations are requested in one call (comma-separated coordinates); the API answers with a
list in request order and no location id, so station ids are matched by position. Recent days
are model/forecast based and get revised, hence a re-load lookback on incremental runs.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from extract.common import (
    BRONZE_DIR,
    STATE_PATH,
    RateLimiter,
    build_session,
    day_range,
    get_json,
    get_logger,
    resolve_window,
    update_state,
    utc_today,
    write_bronze,
)
from extract.golemio_air_quality import STATIONS_SOURCE

BASE_URL = "https://archive-api.open-meteo.com/v1/archive"
SOURCE = "open_meteo_weather_hourly"
HOURLY_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
)
CHUNK_DAYS = 31
LOOKBACK_DAYS = 3
RATE_LIMIT_CALLS = 5
RATE_LIMIT_PERIOD_S = 1.0

logger = get_logger(__name__)


class MissingStationsError(RuntimeError):
    """No station snapshot in bronze to take coordinates from."""


@dataclass(frozen=True)
class Station:
    station_id: str
    latitude: float
    longitude: float


# --------------------------------------------------------------------------- pure helpers


def stations_from_snapshot(df: pd.DataFrame) -> list[Station]:
    """Stations with coordinates from a raw Golemio stations snapshot (GeoJSON [lon, lat])."""
    stations = []
    for row in df.itertuples(index=False):
        lon, lat = json.loads(row.geometry)["coordinates"]
        stations.append(Station(row.id, float(lat), float(lon)))
    return sorted(stations, key=lambda s: s.station_id)


def date_chunks(start: date, end: date, size: int = CHUNK_DAYS) -> list[tuple[date, date]]:
    """Split [start, end] into inclusive chunks of at most `size` days."""
    chunks = []
    while start <= end:
        chunk_end = min(start + timedelta(days=size - 1), end)
        chunks.append((start, chunk_end))
        start = chunk_end + timedelta(days=1)
    return chunks


def build_params(stations: Sequence[Station], start: date, end: date) -> dict[str, str]:
    return {
        "latitude": ",".join(str(s.latitude) for s in stations),
        "longitude": ",".join(str(s.longitude) for s in stations),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY_VARIABLES),
        "timezone": "UTC",
    }


def parse_response(
    payload: Sequence[Mapping[str, Any]] | Mapping[str, Any], stations: Sequence[Station]
) -> list[dict[str, Any]]:
    """One raw row per station x hour; values as returned (arrays unzipped, nothing typed)."""
    locations = [payload] if isinstance(payload, Mapping) else list(payload)
    if len(locations) != len(stations):
        raise ValueError(f"Expected {len(stations)} locations, got {len(locations)}")
    rows = []
    for station, location in zip(stations, locations, strict=True):
        hourly = location["hourly"]
        units = json.dumps(location.get("hourly_units", {}), ensure_ascii=False)
        for i, time in enumerate(hourly["time"]):
            rows.append(
                {
                    "station_id": station.station_id,
                    "requested_latitude": station.latitude,
                    "requested_longitude": station.longitude,
                    "latitude": location["latitude"],
                    "longitude": location["longitude"],
                    "elevation": location["elevation"],
                    "timezone": location["timezone"],
                    "time": time,
                    **{var: hourly[var][i] for var in HOURLY_VARIABLES},
                    "hourly_units": units,
                }
            )
    return rows


def to_frame(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Rows → DataFrame with weather variables always float64.

    Integer-valued variables (humidity, wind direction) would otherwise flip between int64 and
    float64 depending on whether a partition contains nulls, breaking a stable Parquet schema.
    """
    df = pd.DataFrame(list(rows))
    if df.empty:
        return df
    return df.astype({var: "float64" for var in HOURLY_VARIABLES})


def split_by_day(rows: Sequence[dict[str, Any]]) -> dict[date, list[dict[str, Any]]]:
    """Group rows by the UTC day of their `time` (YYYY-MM-DDTHH:MM)."""
    by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_day[date.fromisoformat(row["time"][:10])].append(row)
    return dict(by_day)


# --------------------------------------------------------------------------- I/O


def load_stations(bronze_dir: Path = BRONZE_DIR) -> list[Station]:
    """Coordinates from the latest Golemio stations snapshot in bronze."""
    snapshots = sorted((bronze_dir / STATIONS_SOURCE).glob("load_date=*/part.parquet"))
    if not snapshots:
        raise MissingStationsError(
            f"No {STATIONS_SOURCE} snapshot in bronze; run --source golemio_air_quality first"
        )
    return stations_from_snapshot(pd.read_parquet(snapshots[-1], columns=["id", "geometry"]))


def fetch_chunk(
    session: requests.Session,
    limiter: RateLimiter,
    stations: Sequence[Station],
    start: date,
    end: date,
) -> Any:
    limiter.wait()
    return get_json(session, BASE_URL, build_params(stations, start, end))


def run(
    start: date | None = None,
    end: date | None = None,
    *,
    bronze_dir: Path = BRONZE_DIR,
    state_path: Path = STATE_PATH,
    today: date | None = None,
) -> None:
    """Load hourly weather per UTC day for all stations (default: incremental + lookback)."""
    today = today or utc_today()
    stations = load_stations(bronze_dir)
    start, end = resolve_window(
        SOURCE, start, end, today=today, lookback_days=LOOKBACK_DAYS, state_path=state_path
    )
    session = build_session()
    limiter = RateLimiter(RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD_S)
    logger.info("Loading %s for %d stations from %s to %s", SOURCE, len(stations), start, end)

    for chunk_start, chunk_end in date_chunks(start, end):
        payload = fetch_chunk(session, limiter, stations, chunk_start, chunk_end)
        by_day = split_by_day(parse_response(payload, stations))
        for day in day_range(chunk_start, chunk_end):
            rows = to_frame(by_day.get(day, []))
            if write_bronze(rows, SOURCE, day, bronze_dir=bronze_dir) is not None:
                update_state(SOURCE, day, state_path)
