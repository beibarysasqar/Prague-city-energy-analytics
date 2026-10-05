"""Golemio air quality: station reference list (snapshot) and hourly measurement history."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import requests

from extract.common import (
    BRONZE_DIR,
    STATE_PATH,
    RateLimiter,
    day_range,
    get_json,
    get_logger,
    resolve_window,
    update_state,
    utc_today,
    write_bronze,
)
from extract.golemio import (
    API_ROOT,
    PAGE_LIMIT,
    build_golemio_session,
    build_limiter,
    day_bounds,  # noqa: F401  (re-exported for callers/tests)
    fetch_day,
    parse_features,
    to_raw_row,
)

BASE_URL = f"{API_ROOT}/airqualitystations"
STATIONS_SOURCE = "golemio_air_quality_stations"
HISTORY_SOURCE = "golemio_air_quality_history"

logger = get_logger(__name__)


# --------------------------------------------------------------------------- parsing (pure)


def parse_stations(geojson: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One raw row per station feature: its properties plus the geometry."""
    return parse_features(geojson)


def parse_history(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One raw row per history record (station x publication time)."""
    return [to_raw_row(record) for record in records]


# --------------------------------------------------------------------------- fetching


def fetch_stations(session: requests.Session, limiter: RateLimiter) -> dict[str, Any]:
    limiter.wait()
    return get_json(session, BASE_URL, {"limit": PAGE_LIMIT})


def fetch_history_day(
    session: requests.Session, limiter: RateLimiter, day: date, page_limit: int = PAGE_LIMIT
) -> list[dict[str, Any]]:
    """All history records published on one UTC day, following limit/offset pagination."""
    return fetch_day(session, limiter, f"{BASE_URL}/history", day, page_limit)


# --------------------------------------------------------------------------- entry point


def run(
    start: date | None = None,
    end: date | None = None,
    *,
    bronze_dir: Path = BRONZE_DIR,
    state_path: Path = STATE_PATH,
    today: date | None = None,
) -> None:
    """Load the station snapshot and history for [start, end] (default: incremental)."""
    session = build_golemio_session()
    limiter = build_limiter()
    today = today or utc_today()

    stations = parse_stations(fetch_stations(session, limiter))
    write_bronze(stations, STATIONS_SOURCE, today, bronze_dir=bronze_dir)

    start, end = resolve_window(HISTORY_SOURCE, start, end, today=today, state_path=state_path)
    logger.info("Loading %s from %s to %s", HISTORY_SOURCE, start, end)
    for day in day_range(start, end):
        rows = parse_history(fetch_history_day(session, limiter, day))
        if write_bronze(rows, HISTORY_SOURCE, day, bronze_dir=bronze_dir) is not None:
            update_state(HISTORY_SOURCE, day, state_path)
