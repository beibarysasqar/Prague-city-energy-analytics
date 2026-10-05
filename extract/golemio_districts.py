"""Golemio city districts: reference list of Prague districts with boundary polygons (snapshot).

Air quality stations reference districts by `slug`; bicycle counters have no district, so they
are mapped to districts in silver with a point-in-polygon join on `geometry`.
"""

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
    get_json,
    get_logger,
    paginate,
    utc_today,
    write_bronze,
)
from extract.golemio import (
    API_ROOT,
    PAGE_LIMIT,
    build_golemio_session,
    build_limiter,
    parse_features,
)

BASE_URL = f"{API_ROOT}/citydistricts"
SOURCE = "golemio_city_districts"

logger = get_logger(__name__)


def parse_districts(features: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One raw row per district: properties as-is plus the polygon as a GeoJSON string."""
    return parse_features({"features": features})


def fetch_districts(
    session: requests.Session, limiter: RateLimiter, page_limit: int = PAGE_LIMIT
) -> list[dict[str, Any]]:
    """All district features; each page is a FeatureCollection."""

    def fetch_page(limit: int, offset: int) -> list[dict[str, Any]]:
        limiter.wait()
        payload = get_json(session, BASE_URL, {"limit": limit, "offset": offset})
        return payload.get("features", [])

    return [feature for page in paginate(fetch_page, limit=page_limit) for feature in page]


def run(
    start: date | None = None,
    end: date | None = None,
    *,
    bronze_dir: Path = BRONZE_DIR,
    state_path: Path = STATE_PATH,
    today: date | None = None,
) -> None:
    """Snapshot of all districts, partitioned by run date (UTC); the date window is not used."""
    if start or end:
        logger.info("%s is a full snapshot; --start/--end are ignored", SOURCE)
    rows = parse_districts(fetch_districts(build_golemio_session(), build_limiter()))
    write_bronze(rows, SOURCE, today or utc_today(), bronze_dir=bronze_dir)
