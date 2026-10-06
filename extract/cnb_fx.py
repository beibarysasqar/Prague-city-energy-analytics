"""ČNB public FX API: official daily exchange rates (CZK per `amount` units of a currency).

ARAD (the ČNB time-series API) has no daily official EUR/CZK fixing, so the public, key-less
API is the primary source. For weekends/holidays and for future dates the API returns the last
published rates; `validFor` tells which business day they belong to.
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
    build_session,
    day_range,
    get_json,
    get_logger,
    resolve_window,
    update_state,
    utc_today,
    write_bronze,
)

BASE_URL = "https://api.cnb.cz/cnbapi/exrates/daily"
SOURCE = "cnb_fx_daily"
LANG = "EN"
# No documented rate limit; stay polite.
RATE_LIMIT_CALLS = 5
RATE_LIMIT_PERIOD_S = 1.0

logger = get_logger(__name__)


def parse_rates(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One raw row per currency, fields exactly as returned."""
    return [dict(rate) for rate in payload.get("rates", [])]


def fetch_day(session: requests.Session, limiter: RateLimiter, day: date) -> dict[str, Any]:
    limiter.wait()
    return get_json(session, BASE_URL, {"date": day.isoformat(), "lang": LANG})


def run(
    start: date | None = None,
    end: date | None = None,
    *,
    bronze_dir: Path = BRONZE_DIR,
    state_path: Path = STATE_PATH,
    today: date | None = None,
) -> None:
    """Load the daily rate table for each calendar day in [start, end] (default: incremental)."""
    today = today or utc_today()
    if end is not None and end > today:
        # Future dates silently return the latest rates, which would pollute the partitions.
        logger.warning("End %s is in the future; clamping to %s", end, today)
        end = today
    start, end = resolve_window(SOURCE, start, end, today=today, state_path=state_path)

    session = build_session()
    limiter = RateLimiter(RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD_S)
    logger.info("Loading %s from %s to %s", SOURCE, start, end)
    for day in day_range(start, end):
        rows = parse_rates(fetch_day(session, limiter, day))
        if write_bronze(rows, SOURCE, day, bronze_dir=bronze_dir) is not None:
            update_state(SOURCE, day, state_path)
