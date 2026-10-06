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
from extract.cnb_fx import BASE_URL, SOURCE, parse_rates, run
from extract.common import get_last_loaded

FIXTURES = Path(__file__).parent / "fixtures" / "cnb"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "_sleep", lambda seconds: None)


def mock_day(day: str, fixture: str) -> None:
    responses.get(
        BASE_URL,
        json=load_fixture(fixture),
        match=[matchers.query_param_matcher({"date": day, "lang": "EN"})],
    )


def test_parse_rates_keeps_raw_fields() -> None:
    payload = load_fixture("daily_2026-10-02.json")
    rows = parse_rates(payload)
    assert len(rows) == len(payload["rates"]) == 30
    eur = next(r for r in rows if r["currencyCode"] == "EUR")
    assert eur == {
        "validFor": "2026-10-02",
        "order": 190,
        "country": "EMU",
        "currency": "euro",
        "amount": 1,
        "currencyCode": "EUR",
        "rate": 24.465,
    }
    huf = next(r for r in rows if r["currencyCode"] == "HUF")
    assert huf["amount"] == 100  # rate is per 100 units; normalised in silver


def test_parse_rates_empty_payload() -> None:
    assert parse_rates({}) == []


@responses.activate
def test_run_loads_weekday_and_weekend_idempotently(tmp_path: Path) -> None:
    mock_day("2026-10-02", "daily_2026-10-02.json")
    mock_day("2026-10-03", "daily_2026-10-03_saturday.json")
    bronze, state = tmp_path / "bronze", tmp_path / "_state.json"
    kwargs = {"bronze_dir": bronze, "state_path": state, "today": date(2026, 10, 5)}

    run(date(2026, 10, 2), date(2026, 10, 3), **kwargs)
    run(date(2026, 10, 2), date(2026, 10, 3), **kwargs)

    files = sorted(str(p.relative_to(bronze)) for p in bronze.rglob("*.parquet"))
    assert files == [
        f"{SOURCE}/load_date=2026-10-02/part.parquet",
        f"{SOURCE}/load_date=2026-10-03/part.parquet",
    ]
    df = pd.read_parquet(bronze / SOURCE)
    assert len(df) == 60
    saturday = df[df["load_date"].astype(str) == "2026-10-03"]
    assert set(saturday["validFor"]) == {"2026-10-02"}  # weekend → last business day
    assert get_last_loaded(SOURCE, state) == date(2026, 10, 3)


@responses.activate
def test_run_clamps_future_end_to_today(tmp_path: Path) -> None:
    mock_day("2026-10-03", "daily_2026-10-03_saturday.json")
    run(
        date(2026, 10, 3),
        date(2026, 10, 10),
        bronze_dir=tmp_path,
        state_path=tmp_path / "_state.json",
        today=date(2026, 10, 3),
    )
    assert len(responses.calls) == 1


@responses.activate
def test_validation_error_is_not_retried(tmp_path: Path) -> None:
    responses.get(BASE_URL, status=400, json=load_fixture("validation_error.json"))
    with pytest.raises(requests.HTTPError):
        run(
            date(2026, 10, 2),
            date(2026, 10, 2),
            bronze_dir=tmp_path,
            state_path=tmp_path / "_state.json",
            today=date(2026, 10, 5),
        )
    assert len(responses.calls) == 1
    assert get_last_loaded(SOURCE, tmp_path / "_state.json") is None
