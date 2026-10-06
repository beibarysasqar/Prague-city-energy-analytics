"""Shared helpers for extractors: env, logging, HTTP with retries, pagination, bronze, state."""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
BRONZE_DIR = DATA_DIR / "bronze"
STATE_PATH = BRONZE_DIR / "_state.json"

BRONZE_FILE_NAME = "part.parquet"
LOADED_AT_COLUMN = "_loaded_at"
LOG_FORMAT = "%(asctime)sZ %(levelname)s %(name)s: %(message)s"
MAX_RETRY_AFTER_S = 120.0

logger = logging.getLogger(__name__)

# Indirection so tests can replace sleeping without waiting.
_sleep: Callable[[float], None] = time.sleep


# --------------------------------------------------------------------------- env & logging


class MissingSecretError(RuntimeError):
    """Raised when a required environment variable is not set."""


def load_env() -> None:
    """Load variables from the project .env file without overriding the real environment."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def require_env(name: str) -> str:
    """Return a required env var; the error names the variable but never shows a value."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingSecretError(f"Environment variable {name} is not set (add it to .env)")
    return value


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logging once, with UTC timestamps."""
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    formatter = logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Return a module logger (call `setup_logging` once at the entry point)."""
    return logging.getLogger(name)


# --------------------------------------------------------------------------- HTTP


class RetryableHTTPError(requests.HTTPError):
    """HTTP 429 or 5xx response that should be retried."""

    def __init__(self, status: int, url: str, retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.retry_after = retry_after


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Parse a Retry-After header (delta seconds or HTTP-date) into seconds to wait."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return max((retry_at - now).total_seconds(), 0.0)


def build_session(headers: Mapping[str, str] | None = None) -> requests.Session:
    """Create a requests session with the given default headers."""
    session = requests.Session()
    session.headers.update({"Accept": "application/json", **(headers or {})})
    return session


_exponential_wait = wait_exponential(multiplier=1, min=1, max=30)


def _wait_strategy(retry_state: RetryCallState) -> float:
    """Wait for Retry-After when the server sent it, otherwise back off exponentially."""
    outcome = retry_state.outcome
    exc = outcome.exception() if outcome else None
    if isinstance(exc, RetryableHTTPError) and exc.retry_after is not None:
        return min(exc.retry_after, MAX_RETRY_AFTER_S)
    return _exponential_wait(retry_state)


def describe_error(exc: BaseException | None) -> str:
    """Log-safe description: never includes request URLs, which may carry secrets in the query."""
    if isinstance(exc, RetryableHTTPError):
        return f"HTTP {exc.status}"
    status = getattr(getattr(exc, "response", None), "status_code", None)
    name = type(exc).__name__
    return f"{name} (HTTP {status})" if status else name


def _log_before_sleep(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    wait_s = retry_state.next_action.sleep if retry_state.next_action else 0.0
    logger.warning(
        "Request failed (attempt %d): %s; retrying in %.1fs",
        retry_state.attempt_number,
        describe_error(exc),
        wait_s,
    )


RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    requests.ConnectionError,
    requests.Timeout,
    RetryableHTTPError,
)


def build_retrying(max_attempts: int = 5) -> Retrying:
    """Tenacity policy shared by all extractors: network errors, 429 and 5xx are retried."""
    return Retrying(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(max_attempts),
        wait=_wait_strategy,
        sleep=lambda seconds: _sleep(seconds),
        before_sleep=_log_before_sleep,
        reraise=True,
    )


def _request_json(
    session: requests.Session, url: str, params: Mapping[str, Any] | None, timeout: float
) -> Any:
    response = session.get(url, params=params, timeout=timeout)
    status = response.status_code
    if status == 429 or status >= 500:
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        raise RetryableHTTPError(status, url, retry_after)
    response.raise_for_status()  # other 4xx: fail fast, no retry
    return response.json()


def get_json(
    session: requests.Session,
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    timeout: float = 60,
    max_attempts: int = 5,
) -> Any:
    """GET a URL and return parsed JSON, retrying network errors, 429 and 5xx."""
    return build_retrying(max_attempts)(_request_json, session, url, params, timeout)


class RateLimiter:
    """Sliding-window throttle: at most `max_calls` calls per `period_s` seconds."""

    def __init__(
        self,
        max_calls: int,
        period_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.max_calls = max_calls
        self.period_s = period_s
        self._clock = clock
        self._sleep = sleep or (lambda seconds: _sleep(seconds))
        self._calls: deque[float] = deque()

    def wait(self) -> None:
        """Block until another call is allowed, then record it."""
        now = self._clock()
        while self._calls and now - self._calls[0] >= self.period_s:
            self._calls.popleft()
        if len(self._calls) >= self.max_calls:
            delay = self.period_s - (now - self._calls[0])
            if delay > 0:
                self._sleep(delay)
            self._calls.popleft()
            now = self._clock()
        self._calls.append(now)


def paginate(
    fetch_page: Callable[[int, int], Sequence[Any]],
    limit: int = 10_000,
    max_pages: int = 1_000,
) -> Iterator[Sequence[Any]]:
    """Yield pages from `fetch_page(limit, offset)` until a page is shorter than `limit`."""
    offset = 0
    for _ in range(max_pages):
        page = fetch_page(limit, offset)
        if page:
            yield page
        if len(page) < limit:
            return
        offset += limit
    raise RuntimeError(f"Pagination exceeded max_pages={max_pages}")


# --------------------------------------------------------------------------- dates


def day_range(start: date, end: date) -> list[date]:
    """Inclusive list of days from start to end."""
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def utc_today() -> date:
    return datetime.now(UTC).date()


# --------------------------------------------------------------------------- bronze


def partition_path(source: str, partition_date: date, bronze_dir: Path = BRONZE_DIR) -> Path:
    """Path of the bronze file for a source and partition date."""
    return bronze_dir / source / f"load_date={partition_date.isoformat()}" / BRONZE_FILE_NAME


def write_bronze(
    records: Sequence[Mapping[str, Any]] | pd.DataFrame,
    source: str,
    partition_date: date,
    *,
    bronze_dir: Path = BRONZE_DIR,
    loaded_at: datetime | None = None,
) -> Path | None:
    """Write records to the bronze partition, atomically replacing any previous content.

    Re-running the same source/date overwrites the partition, so loads never duplicate.
    Empty input leaves an existing partition untouched and returns None.
    """
    df = records.copy() if isinstance(records, pd.DataFrame) else pd.DataFrame(list(records))
    if df.empty:
        logger.warning("No records for %s %s; partition not written", source, partition_date)
        return None

    loaded_at = loaded_at or datetime.now(UTC)
    df[LOADED_AT_COLUMN] = pd.Timestamp(loaded_at).tz_convert("UTC")

    target = partition_path(source, partition_date, bronze_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, target)

    for stray in target.parent.iterdir():
        if stray != target:
            stray.unlink()

    logger.info("Wrote %d rows to %s", len(df), target.relative_to(bronze_dir))
    return target


# --------------------------------------------------------------------------- state


def read_state(path: Path = STATE_PATH) -> dict[str, Any]:
    """Read the extractor state file; missing file means empty state."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def get_last_loaded(source: str, path: Path = STATE_PATH) -> date | None:
    entry = read_state(path).get(source)
    if not entry or not entry.get("last_loaded_date"):
        return None
    return date.fromisoformat(entry["last_loaded_date"])


def update_state(source: str, last_loaded_date: date, path: Path = STATE_PATH) -> None:
    """Record the last loaded date for a source; never moves it backwards (backfills)."""
    state = read_state(path)
    current = get_last_loaded(source, path)
    if current is not None and last_loaded_date < current:
        return
    state[source] = {
        "last_loaded_date": last_loaded_date.isoformat(),
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def resolve_window(
    source: str,
    start: date | None = None,
    end: date | None = None,
    *,
    today: date | None = None,
    default_days: int = 90,
    lookback_days: int = 0,
    state_path: Path = STATE_PATH,
) -> tuple[date, date]:
    """Pick the date window to extract.

    Explicit start wins; otherwise resume from the last loaded date (re-loading it, as it may
    have been partial) minus `lookback_days` for sources that revise recent data, or go back
    `default_days` on the first run. End defaults to today (UTC).
    """
    today = today or utc_today()
    end = end or today
    if start is None:
        last = get_last_loaded(source, state_path)
        if last is None:
            start = today - timedelta(days=default_days)
        else:
            start = last - timedelta(days=lookback_days)
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    return start, end
