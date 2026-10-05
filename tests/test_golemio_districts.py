import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import responses
from responses import matchers

from extract import common
from extract.common import MissingSecretError, RateLimiter, build_session
from extract.golemio_districts import BASE_URL, SOURCE, fetch_districts, parse_districts, run

FIXTURES = Path(__file__).parent / "fixtures" / "golemio"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "_sleep", lambda seconds: None)


@pytest.fixture
def districts_json() -> dict[str, Any]:
    return json.loads((FIXTURES / "citydistricts.json").read_text(encoding="utf-8"))


def test_parse_districts_keeps_raw_fields(districts_json: dict[str, Any]) -> None:
    features = districts_json["features"]
    rows = parse_districts(features)
    assert len(rows) == 2
    assert set(rows[0]) == {"id", "name", "slug", "updated_at", "geometry"}
    assert rows[0]["id"] == features[0]["properties"]["id"]  # numeric id kept as-is
    assert rows[0]["slug"] == "praha-1"
    geometry = json.loads(rows[0]["geometry"])
    assert geometry == features[0]["geometry"]
    assert geometry["type"] == "Polygon"


@responses.activate
def test_fetch_districts_paginates(districts_json: dict[str, Any]) -> None:
    features = districts_json["features"]
    for offset, page in [(0, features[:1]), (1, features[1:]), (2, [])]:
        responses.get(
            BASE_URL,
            json={"type": "FeatureCollection", "features": page},
            match=[
                matchers.header_matcher({"X-Access-Token": "test-key"}),
                matchers.query_param_matcher({"limit": "1", "offset": str(offset)}),
            ],
        )
    session = build_session({"X-Access-Token": "test-key"})
    limiter = RateLimiter(max_calls=1000, period_s=1)
    assert fetch_districts(session, limiter, page_limit=1) == features
    assert len(responses.calls) == 3


@responses.activate
def test_run_writes_snapshot_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, districts_json: dict[str, Any]
) -> None:
    monkeypatch.setenv("GOLEMIO_API_KEY", "test-key")
    responses.get(BASE_URL, json=districts_json)
    for _ in range(2):
        run(bronze_dir=tmp_path, state_path=tmp_path / "_state.json", today=date(2026, 10, 5))

    files = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*.parquet"))
    assert files == [f"{SOURCE}/load_date=2026-10-05/part.parquet"]
    df = pd.read_parquet(tmp_path / SOURCE)
    assert len(df) == 2 and df["slug"].is_unique
    assert not (tmp_path / "_state.json").exists()  # snapshot, no incremental state


def test_run_requires_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GOLEMIO_API_KEY", raising=False)
    with pytest.raises(MissingSecretError, match="GOLEMIO_API_KEY"):
        run(bronze_dir=tmp_path, state_path=tmp_path / "_state.json")
