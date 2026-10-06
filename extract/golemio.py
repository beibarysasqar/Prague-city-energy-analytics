"""Shared helpers for Golemio API resources (auth, rate limit, pagination, raw rows)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from typing import Any

import requests

from extract.common import RateLimiter, build_session, get_json, paginate, require_env

API_ROOT = "https://api.golemio.cz/v2"
API_KEY_ENV = "GOLEMIO_API_KEY"
PAGE_LIMIT = 10_000
# Golemio documents 20 requests per 8 s per key, but bursts of 20 still get HTTP 429
# (Retry-After: 8), so stay below it.
RATE_LIMIT_CALLS = 15
RATE_LIMIT_PERIOD_S = 8.0


def build_golemio_session() -> requests.Session:
    return build_session({"X-Access-Token": require_env(API_KEY_ENV)})


def build_limiter() -> RateLimiter:
    return RateLimiter(RATE_LIMIT_CALLS, RATE_LIMIT_PERIOD_S)


def to_raw_row(record: Mapping[str, Any]) -> dict[str, Any]:
    """Keep top-level scalars as-is; serialise nested objects to JSON strings (no typing)."""
    return {
        key: json.dumps(value, ensure_ascii=False) if isinstance(value, dict | list) else value
        for key, value in record.items()
    }


def parse_features(geojson: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One raw row per GeoJSON feature: its properties plus the geometry."""
    return [
        to_raw_row({**feature["properties"], "geometry": feature["geometry"]})
        for feature in geojson.get("features", [])
    ]


def day_bounds(day: date) -> tuple[str, str]:
    """UTC `from`/`to` query values covering one whole day."""
    iso = day.isoformat()
    return f"{iso}T00:00:00.000Z", f"{iso}T23:59:59.999Z"


def fetch_all_pages(
    session: requests.Session,
    limiter: RateLimiter,
    url: str,
    params: Mapping[str, Any] | None = None,
    page_limit: int = PAGE_LIMIT,
) -> list[dict[str, Any]]:
    """All records of a list endpoint, following limit/offset pagination."""

    def fetch_page(limit: int, offset: int) -> list[dict[str, Any]]:
        limiter.wait()
        return get_json(session, url, {**(params or {}), "limit": limit, "offset": offset})

    return [record for page in paginate(fetch_page, limit=page_limit) for record in page]


def fetch_day(
    session: requests.Session,
    limiter: RateLimiter,
    url: str,
    day: date,
    page_limit: int = PAGE_LIMIT,
) -> list[dict[str, Any]]:
    """All records of a time-filtered endpoint for one UTC day."""
    date_from, date_to = day_bounds(day)
    return fetch_all_pages(session, limiter, url, {"from": date_from, "to": date_to}, page_limit)
