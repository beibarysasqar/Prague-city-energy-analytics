from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from app import data


def bike_frame(district: str, values: list[float], n_counters: int = 1) -> pd.DataFrame:
    start = date(2026, 7, 7)
    return pd.DataFrame(
        {
            "district_slug": district,
            "district_name": district.title(),
            "date_day": [start + timedelta(days=i) for i in range(len(values))],
            "bikes": values,
            "n_counters": n_counters,
        }
    )


def test_warehouse_path_default_and_relative_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DUCKDB_PATH", raising=False)
    assert data.warehouse_path() == data.PROJECT_ROOT / "data" / "warehouse.duckdb"
    monkeypatch.setenv("DUCKDB_PATH", "../data/other.duckdb")  # relative to dbt/, like the profile
    assert data.warehouse_path() == (data.PROJECT_ROOT / "data" / "other.duckdb").resolve()
    monkeypatch.setenv("DUCKDB_PATH", "/tmp/x.duckdb")
    assert data.warehouse_path() == Path("/tmp/x.duckdb")


def test_bike_growth_compares_first_and_last_window() -> None:
    df = pd.concat(
        [
            bike_frame("praha-1", [100.0] * 28 + [150.0] * 28, n_counters=3),
            bike_frame("praha-2", [200.0] * 28 + [100.0] * 28),
            bike_frame("praha-3", [100.0] * 40),  # not two full windows -> left out
        ]
    )
    growth = data.bike_growth(df, window=28)
    assert growth["district_slug"].tolist() == ["praha-1", "praha-2"]  # sorted by growth
    p1 = growth.iloc[0]
    assert (p1["first_window_mean"], p1["last_window_mean"], p1["growth_pct"]) == (100, 150, 50)
    assert p1["n_counters"] == 3
    assert growth.iloc[1]["growth_pct"] == -50


def test_bike_growth_custom_grouping_without_counters() -> None:
    df = bike_frame("x", [10.0] * 4 + [20.0] * 4).rename(columns={"district_slug": "counter_id"})
    df = df.drop(columns=["n_counters"])
    growth = data.bike_growth(df, window=4, group_cols=("counter_id",))
    assert list(growth.columns) == [
        "counter_id",
        "first_window_mean",
        "last_window_mean",
        "growth_pct",
    ]
    assert growth["growth_pct"].iloc[0] == 100


def test_bike_growth_empty_input_has_columns() -> None:
    growth = data.bike_growth(bike_frame("x", [1.0, 2.0]), window=28)
    assert growth.empty and "growth_pct" in growth.columns


def test_rolling_bike_traffic_needs_full_window() -> None:
    rolled = data.rolling_bike_traffic(bike_frame("x", [7.0, 7.0, 7.0, 14.0]), window=3)
    assert rolled["bikes_rolling"].isna().tolist() == [True, True, False, False]
    assert rolled["bikes_rolling"].iloc[-1] == pytest.approx(28 / 3)


def mart_frame() -> pd.DataFrame:
    days = pd.date_range("2026-08-01", periods=20, freq="D")
    wind = list(range(20))
    return pd.DataFrame(
        {
            "district_slug": "praha-1",
            "district_name": "Praha 1",
            "date_day": days,
            "avg_pm10_ugm3": [40.0 - w for w in wind],  # perfectly anti-correlated with wind
            "avg_no2_ugm3": [None] * 20,
            "avg_pm2_5_ugm3": [None] * 20,
            "avg_price_eur_mwh": [100.0 + 2 * w for w in wind],
            "avg_load_mw": [6000.0] * 20,  # constant -> skipped
            "avg_wind_speed_ms": [float(w) for w in wind],
            "avg_temperature_c": [15.0 + (w % 3) for w in wind],
            "precipitation_mm": [0.0] * 20,
        }
    )


def test_district_correlations() -> None:
    corr = data.district_correlations(mart_frame(), "PM10", min_days=14)
    by_driver = dict(zip(corr["driver"], corr["r"], strict=True))
    assert by_driver["Wind speed"] == -1.0
    assert by_driver["Day-ahead price"] == -1.0
    assert "Electricity load" not in by_driver  # constant series has no correlation
    assert set(corr["n_days"]) == {20}


def test_district_correlations_requires_min_days_and_data() -> None:
    assert data.district_correlations(mart_frame(), "PM10", min_days=30).empty
    assert data.district_correlations(mart_frame(), "NO2").empty


def test_daily_pollution_mean_of_districts() -> None:
    df = pd.DataFrame(
        {
            "date_day": ["d1", "d1", "d2"],
            "district_slug": ["a", "b", "a"],
            "avg_pm10_ugm3": [10.0, 30.0, None],
        }
    )
    out = data.daily_pollution(df, "PM10")
    assert out.to_dict("records") == [{"date_day": "d1", "value_ugm3": 20.0, "n_districts": 2}]


def test_compute_kpis() -> None:
    pollution = pd.DataFrame({"value_ugm3": [10.0, 30.0]})
    price = pd.DataFrame(
        {
            "price_eur_mwh": [100.0, 120.0],
            "price_czk_mwh": [2440.0, 2928.0],
            "min_price_eur_mwh": [-5.0, 20.0],
        }
    )
    bikes = pd.concat([bike_frame("a", [100.0, 200.0]), bike_frame("b", [50.0, 50.0])])
    kpis = data.compute_kpis(pollution, price, bikes)
    assert (kpis.pollutant_mean, kpis.pollutant_max_day) == (20.0, 30.0)
    assert (kpis.price_mean_eur, kpis.negative_price_days) == (110.0, 1)
    assert kpis.bikes_per_day == 200.0  # (150 + 250) / 2 days


def test_compute_kpis_empty() -> None:
    empty = pd.DataFrame()
    kpis = data.compute_kpis(empty, empty, empty)
    assert kpis.pollutant_mean is None and kpis.price_mean_eur is None
    assert kpis.bikes_per_day is None and kpis.negative_price_days == 0
