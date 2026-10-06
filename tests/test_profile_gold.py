import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

# profiling/ is not a package and the script runs in its own env; load it by path. Its module-level
# imports are stdlib + duckdb only (ydata-profiling is imported lazily inside profile_table).
SCRIPT = Path(__file__).resolve().parent.parent / "profiling" / "profile_gold.py"
spec = importlib.util.spec_from_file_location("profile_gold", SCRIPT)
profile_gold = importlib.util.module_from_spec(spec)
sys.modules["profile_gold"] = profile_gold  # dataclasses resolve annotations via sys.modules
spec.loader.exec_module(profile_gold)


def test_sample_query_full_table_when_small() -> None:
    assert profile_gold.sample_query("gold", "dim_date", 1_095, 50_000) == (
        'select * from "gold"."dim_date"'
    )


def test_sample_query_reproducible_sample_when_large() -> None:
    query = profile_gold.sample_query("gold", "fact_air_quality_hourly", 93_912, 50_000)
    assert query.endswith("using sample reservoir(50000 rows) repeatable (42)")


def test_missing_percentages_only_columns_with_nulls_sorted() -> None:
    df = pd.DataFrame({"a": [1, None, None, 4], "b": [1, 2, 3, None], "c": [1, 2, 3, 4]})
    assert profile_gold.missing_percentages(df) == {"a": 50.0, "b": 25.0}


def test_to_profile_frame_makes_timestamps_naive_utc() -> None:
    df = pd.DataFrame({"ts": pd.to_datetime(["2026-10-03 00:00"]).tz_localize("Europe/Prague")})
    out = profile_gold.to_profile_frame(df)
    assert out["ts"].dt.tz is None
    assert out["ts"].iloc[0] == pd.Timestamp("2026-10-02 22:00")


def test_markdown_index_rows_and_links() -> None:
    summaries = [
        profile_gold.TableSummary("dim_date", 1_095, 1_095, 14, 0),
        profile_gold.TableSummary(
            "fact_air_quality_hourly", 93_912, 50_000, 11, 0, {"aq_hourly_index": 1.2}
        ),
    ]
    md = profile_gold.markdown_index(summaries, "2026-10-06 01:00 UTC")
    assert md.startswith("# Gold layer profiling")
    assert "| `dim_date` | 1,095 | 1,095 | 14 | 0 | — | [dim_date.html](dim_date.html) |" in md
    assert "50,000 (sample)" in md
    assert "`aq_hourly_index` 1.2 %" in md


@pytest.mark.parametrize("profiled, expected", [(10, False), (5, True)])
def test_table_summary_sampled_flag(profiled: int, expected: bool) -> None:
    assert profile_gold.TableSummary("t", 10, profiled, 1, 0).sampled is expected
