"""Golemio bicycle counters: counter reference list (snapshot) and 5-minute detections.

A direction `id` is not unique on its own (the same direction can be measured by two counters,
e.g. road and cycle path), so the natural key of a detection is
(`locations_id`, `id`, `measured_from`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
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
    fetch_day,
    parse_features,
    to_raw_row,
)

BASE_URL = f"{API_ROOT}/bicyclecounters"
COUNTERS_SOURCE = "golemio_bicycle_counters"
DETECTIONS_SOURCE = "golemio_bicycle_detections"
# Counters upload with a delay, so incremental runs re-load the last days.
LOOKBACK_DAYS = 3
# Nullable counts: keep float64 so the Parquet schema does not flip between int and float.
COUNT_COLUMNS = ("value", "value_pedestrians")

logger = get_logger(__name__)


# --------------------------------------------------------------------------- parsing (pure)


def parse_counters(geojson: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One raw row per counter: properties (directions as JSON) plus the geometry."""
    return parse_features(geojson)


def parse_detections(records: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Raw detection rows, values as returned; count columns forced to float64."""
    df = pd.DataFrame([to_raw_row(record) for record in records])
    if df.empty:
        return df
    return df.astype({col: "float64" for col in COUNT_COLUMNS if col in df.columns})


# --------------------------------------------------------------------------- fetching


def fetch_counters(session: requests.Session, limiter: RateLimiter) -> dict[str, Any]:
    limiter.wait()
    return get_json(session, BASE_URL, {"limit": PAGE_LIMIT})


def fetch_detections_day(
    session: requests.Session, limiter: RateLimiter, day: date, page_limit: int = PAGE_LIMIT
) -> list[dict[str, Any]]:
    """All 5-minute detections with `measured_from` on one UTC day (~20k rows, 2 pages)."""
    return fetch_day(session, limiter, f"{BASE_URL}/detections", day, page_limit)


# --------------------------------------------------------------------------- entry point


def run(
    start: date | None = None,
    end: date | None = None,
    *,
    bronze_dir: Path = BRONZE_DIR,
    state_path: Path = STATE_PATH,
    today: date | None = None,
) -> None:
    """Load the counter snapshot and detections per UTC day (default: incremental + lookback)."""
    session = build_golemio_session()
    limiter = build_limiter()
    today = today or utc_today()

    counters = parse_counters(fetch_counters(session, limiter))
    write_bronze(counters, COUNTERS_SOURCE, today, bronze_dir=bronze_dir)

    start, end = resolve_window(
        DETECTIONS_SOURCE,
        start,
        end,
        today=today,
        lookback_days=LOOKBACK_DAYS,
        state_path=state_path,
    )
    logger.info("Loading %s from %s to %s", DETECTIONS_SOURCE, start, end)
    for day in day_range(start, end):
        rows = parse_detections(fetch_detections_day(session, limiter, day))
        if write_bronze(rows, DETECTIONS_SOURCE, day, bronze_dir=bronze_dir) is not None:
            update_state(DETECTIONS_SOURCE, day, state_path)
