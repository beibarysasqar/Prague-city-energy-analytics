"""ENTSO-E Transparency Platform: day-ahead prices and actual total load for the CZ bidding zone.

The API key travels in the query string (`securityToken`), so exceptions raised by requests /
entsoe-py contain it. Every call goes through `call_entsoe`, which retries transient errors and
re-raises failures as `EntsoeRequestError` without the original message or traceback chain.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import XMLParsedAsHTMLWarning
from entsoe import EntsoePandasClient
from entsoe.exceptions import NoMatchingDataError

from extract.common import (
    BRONZE_DIR,
    STATE_PATH,
    RetryableHTTPError,
    build_retrying,
    day_range,
    describe_error,
    get_logger,
    parse_retry_after,
    require_env,
    resolve_window,
    update_state,
    utc_today,
    write_bronze,
)

API_URL = "https://web-api.tp.entsoe.eu/api"
API_KEY_ENV = "ENTSOE_API_KEY"
AREA = "CZ"
AREA_CODE = "10YCZ-CEPS-----N"
MARKET_TZ = "Europe/Prague"
PRICES_SOURCE = "entsoe_day_ahead_prices"
LOAD_SOURCE = "entsoe_actual_load"
REQUEST_TIMEOUT_S = 60

logger = get_logger(__name__)

# entsoe-py logs request params (incl. the token) at DEBUG level: never let that through.
logging.getLogger("entsoe").setLevel(logging.WARNING)
# entsoe-py parses XML with bs4's HTML parser; harmless but noisy.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


class EntsoeRequestError(RuntimeError):
    """ENTSO-E request failed; the message is safe to log (no URL, no token)."""


# --------------------------------------------------------------------------- pure helpers


def delivery_day_bounds(day: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[start, end) of a market delivery day in Prague time (23/25 h on DST switch days)."""
    start = pd.Timestamp(day.isoformat(), tz=MARKET_TZ)
    return start, pd.Timestamp((day + timedelta(days=1)).isoformat(), tz=MARKET_TZ)


def to_bronze_rows(
    data: pd.Series | pd.DataFrame, unit: str, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    """entsoe-py output → raw rows (UTC timestamp, value), limited to [start, end)."""
    series = data.iloc[:, 0] if isinstance(data, pd.DataFrame) else data
    series = series[(series.index >= start) & (series.index < end)]
    return pd.DataFrame(
        {
            "ts_utc": series.index.tz_convert("UTC"),
            "value": series.to_numpy(dtype="float64"),
            "unit": unit,
            "area_code": AREA_CODE,
        }
    )


# --------------------------------------------------------------------------- safe API calls


def _attempt(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """One call; 429/5xx are turned into RetryableHTTPError (no URL in it) for tenacity."""
    try:
        return fn(*args, **kwargs)
    except requests.HTTPError as exc:
        response = exc.response
        status = response.status_code if response is not None else None
        if status is None or (status != 429 and status < 500):
            raise
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
    raise RetryableHTTPError(status, API_URL, retry_after)


def call_entsoe(fn: Callable[..., Any], *args: Any, max_attempts: int = 5, **kwargs: Any) -> Any:
    """Call an entsoe-py query with retries; None when ENTSO-E has no data for the period."""
    try:
        return build_retrying(max_attempts)(_attempt, fn, *args, **kwargs)
    except NoMatchingDataError:
        return None
    except requests.RequestException as exc:
        failure = describe_error(exc)
    # Raised outside the except block so the original exception (with the URL) is not chained.
    raise EntsoeRequestError(f"ENTSO-E request failed: {failure}")


def build_client() -> EntsoePandasClient:
    # Retries are handled by `call_entsoe`; disable entsoe-py's own retry and its 10 s sleep.
    return EntsoePandasClient(
        api_key=require_env(API_KEY_ENV),
        retry_count=1,
        retry_delay=0,
        timeout=REQUEST_TIMEOUT_S,
    )


# --------------------------------------------------------------------------- entry point


def _load_dataset(
    client: EntsoePandasClient,
    source: str,
    query: str,
    unit: str,
    window: tuple[date, date],
    bronze_dir: Path,
    state_path: Path,
) -> None:
    logger.info("Loading %s from %s to %s", source, *window)
    for day in day_range(*window):
        start, end = delivery_day_bounds(day)
        data = call_entsoe(getattr(client, query), AREA, start=start, end=end)
        if data is None:
            logger.warning("No ENTSO-E data for %s on %s", source, day)
            continue
        rows = to_bronze_rows(data, unit, start, end)
        if write_bronze(rows, source, day, bronze_dir=bronze_dir) is not None:
            update_state(source, day, state_path)


def run(
    start: date | None = None,
    end: date | None = None,
    *,
    bronze_dir: Path = BRONZE_DIR,
    state_path: Path = STATE_PATH,
    today: date | None = None,
    client: EntsoePandasClient | None = None,
) -> None:
    """Load day-ahead prices and actual load per Prague delivery day (default: incremental)."""
    client = client or build_client()
    today = today or utc_today()
    datasets = [
        # Day-ahead prices for tomorrow are published around 13:00 CET.
        (PRICES_SOURCE, "query_day_ahead_prices", "EUR/MWh", today + timedelta(days=1)),
        (LOAD_SOURCE, "query_load", "MW", today),
    ]
    for source, query, unit, default_end in datasets:
        window = resolve_window(
            source, start, end or default_end, today=today, state_path=state_path
        )
        _load_dataset(client, source, query, unit, window, bronze_dir, state_path)
