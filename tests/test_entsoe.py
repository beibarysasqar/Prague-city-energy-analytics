import logging
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import requests
import responses
from entsoe import EntsoePandasClient

from extract import common
from extract.common import MissingSecretError, get_last_loaded
from extract.entsoe import (
    API_URL,
    LOAD_SOURCE,
    PRICES_SOURCE,
    EntsoeRequestError,
    call_entsoe,
    delivery_day_bounds,
    run,
    to_bronze_rows,
)

FIXTURES = Path(__file__).parent / "fixtures" / "entsoe"
TOKEN = "SECRET-TOKEN-123"
DAY = date(2026, 10, 3)  # fixtures hold delivery day 2026-10-03 (Prague)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "_sleep", lambda seconds: None)


@pytest.fixture
def client() -> EntsoePandasClient:
    return EntsoePandasClient(api_key=TOKEN, retry_count=1, retry_delay=0)


def entsoe_api(request: requests.PreparedRequest) -> tuple[int, dict, str]:
    """Mock ENTSO-E: data on offset 0, 'no matching data' on further pages / other days."""
    params = request.params  # type: ignore[attr-defined]
    # entsoe-py pads price queries by ±1 day, so check overlap with the fixture's period.
    covers_fixture = params["periodStart"] <= "202610022200" < params["periodEnd"]
    if params.get("offset", "0") != "0" or not covers_fixture:
        return 200, {"Content-Type": "text/xml"}, fixture("no_data.xml")
    body = {"A44": "day_ahead_prices_cz.xml", "A65": "actual_load_cz.xml"}[params["documentType"]]
    return 200, {"Content-Type": "text/xml"}, fixture(body)


# --------------------------------------------------------------------------- pure helpers


def test_delivery_day_bounds_follow_prague_time() -> None:
    start, end = delivery_day_bounds(DAY)
    assert start.tz_convert("UTC") == pd.Timestamp("2026-10-02T22:00Z")
    assert end.tz_convert("UTC") == pd.Timestamp("2026-10-03T22:00Z")
    # DST end day (25 hours)
    start, end = delivery_day_bounds(date(2026, 10, 25))
    assert end - start == pd.Timedelta(hours=25)


def test_to_bronze_rows_keeps_half_open_window() -> None:
    start, end = delivery_day_bounds(DAY)
    index = pd.date_range(start, end, freq="15min")  # includes `end` like entsoe-py does
    rows = to_bronze_rows(pd.Series(range(len(index)), index=index), "MW", start, end)
    assert len(rows) == 96
    assert list(rows.columns) == ["ts_utc", "value", "unit", "area_code"]
    assert rows["ts_utc"].iloc[0] == pd.Timestamp("2026-10-02T22:00Z")
    assert rows["ts_utc"].iloc[-1] == pd.Timestamp("2026-10-03T21:45Z")
    assert str(rows["ts_utc"].dt.tz) == "UTC"


# --------------------------------------------------------------------------- parsing via entsoe-py


@responses.activate
def test_prices_from_fixture(client: EntsoePandasClient) -> None:
    responses.add_callback(responses.GET, API_URL, callback=entsoe_api)
    start, end = delivery_day_bounds(DAY)
    data = call_entsoe(client.query_day_ahead_prices, "CZ", start=start, end=end)
    rows = to_bronze_rows(data, "EUR/MWh", start, end)
    assert len(rows) == 96
    assert rows["ts_utc"].is_unique
    assert rows["value"].iloc[0] == pytest.approx(204.51)
    assert set(rows["unit"]) == {"EUR/MWh"}


@responses.activate
def test_load_from_fixture(client: EntsoePandasClient) -> None:
    responses.add_callback(responses.GET, API_URL, callback=entsoe_api)
    start, end = delivery_day_bounds(DAY)
    rows = to_bronze_rows(
        call_entsoe(client.query_load, "CZ", start=start, end=end), "MW", start, end
    )
    assert len(rows) == 96
    assert rows["value"].iloc[0] == pytest.approx(5348.66)


@responses.activate
def test_no_data_returns_none(client: EntsoePandasClient) -> None:
    responses.get(API_URL, body=fixture("no_data.xml"), content_type="text/xml")
    start, end = delivery_day_bounds(date(2026, 12, 2))
    assert call_entsoe(client.query_load, "CZ", start=start, end=end) is None


# --------------------------------------------------------------------------- retries & secrets


@responses.activate
def test_retries_server_errors(client: EntsoePandasClient) -> None:
    responses.get(API_URL, status=503)
    responses.get(API_URL, body=fixture("actual_load_cz.xml"), content_type="text/xml")
    start, end = delivery_day_bounds(DAY)
    assert len(call_entsoe(client.query_load, "CZ", start=start, end=end)) == 96
    assert len(responses.calls) == 2


@responses.activate
def test_client_error_is_not_retried_and_hides_token(
    client: EntsoePandasClient, caplog: pytest.LogCaptureFixture
) -> None:
    responses.get(API_URL, status=401, body="<html>Unauthorized</html>")
    start, end = delivery_day_bounds(DAY)
    with caplog.at_level(logging.DEBUG), pytest.raises(EntsoeRequestError) as exc_info:
        call_entsoe(client.query_load, "CZ", start=start, end=end)
    assert len(responses.calls) == 1
    assert "401" in str(exc_info.value)
    assert TOKEN not in str(exc_info.value)
    assert exc_info.value.__cause__ is None and exc_info.value.__context__ is None
    assert TOKEN in responses.calls[0].request.url  # sanity: the token really is in the URL
    assert TOKEN not in caplog.text


@responses.activate
def test_connection_errors_are_retried_and_hide_token(
    client: EntsoePandasClient, caplog: pytest.LogCaptureFixture
) -> None:
    responses.get(API_URL, body=requests.ConnectionError(f"failed /api?securityToken={TOKEN}"))
    start, end = delivery_day_bounds(DAY)
    with caplog.at_level(logging.DEBUG), pytest.raises(EntsoeRequestError) as exc_info:
        call_entsoe(client.query_load, "CZ", start=start, end=end, max_attempts=3)
    assert len(responses.calls) == 3
    assert TOKEN not in str(exc_info.value)
    assert exc_info.value.__context__ is None
    assert TOKEN not in caplog.text


# --------------------------------------------------------------------------- run


@responses.activate
def test_run_writes_both_sources_idempotently(tmp_path: Path, client: EntsoePandasClient) -> None:
    responses.add_callback(responses.GET, API_URL, callback=entsoe_api)
    bronze, state = tmp_path / "bronze", tmp_path / "_state.json"
    kwargs = {"bronze_dir": bronze, "state_path": state, "today": DAY, "client": client}

    run(DAY, date(2026, 10, 4), **kwargs)  # 2026-10-04 has no data in the mock
    run(DAY, date(2026, 10, 4), **kwargs)

    files = sorted(str(p.relative_to(bronze)) for p in bronze.rglob("*.parquet"))
    assert files == [
        f"{LOAD_SOURCE}/load_date=2026-10-03/part.parquet",
        f"{PRICES_SOURCE}/load_date=2026-10-03/part.parquet",
    ]
    for source in (PRICES_SOURCE, LOAD_SOURCE):
        df = pd.read_parquet(bronze / source)
        assert len(df) == 96 and df["ts_utc"].is_unique
        assert get_last_loaded(source, state) == DAY


def test_run_requires_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ENTSOE_API_KEY", raising=False)
    with pytest.raises(MissingSecretError, match="ENTSOE_API_KEY"):
        run(bronze_dir=tmp_path, state_path=tmp_path / "_state.json")
