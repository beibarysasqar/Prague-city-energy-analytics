"""Data access for the dashboard: read-only queries on the gold layer + pure transforms.

Loaders return pandas DataFrames and take plain parameters so Streamlit can cache them; the
transforms below them have no Streamlit/DuckDB dependency and are unit-tested.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Pollutants offered in the filter: the three health-relevant ones the mart aggregates per district.
POLLUTANTS: dict[str, str] = {"PM10": "PM10", "NO2": "NO₂", "PM2_5": "PM2.5"}
MART_POLLUTANT_COLUMN = {"PM10": "avg_pm10_ugm3", "NO2": "avg_no2_ugm3", "PM2_5": "avg_pm2_5_ugm3"}
DRIVERS: dict[str, str] = {
    "avg_price_eur_mwh": "Day-ahead price",
    "avg_load_mw": "Electricity load",
    "avg_wind_speed_ms": "Wind speed",
    "avg_temperature_c": "Temperature",
    "precipitation_mm": "Precipitation",
}
GROWTH_WINDOW_DAYS = 28
ROLLING_WINDOW_DAYS = 7
MIN_DAYS_FOR_CORRELATION = 14


def warehouse_path() -> Path:
    """DuckDB file; DUCKDB_PATH follows the dbt profile semantics (relative to dbt/)."""
    configured = os.environ.get("DUCKDB_PATH", "").strip()
    if not configured:
        return PROJECT_ROOT / "data" / "warehouse.duckdb"
    path = Path(configured)
    return path if path.is_absolute() else (PROJECT_ROOT / "dbt" / path).resolve()


def query(sql: str, params: list[Any] | None = None) -> pd.DataFrame:
    """Run a read-only query (short-lived connection, so dbt can write in between)."""
    with duckdb.connect(str(warehouse_path()), read_only=True) as con:
        con.execute("set timezone = 'UTC'")
        return con.execute(sql, params or []).df()


# --------------------------------------------------------------------------- loaders


def load_date_bounds() -> tuple[date, date]:
    row = query(
        "select min(date_day) as lo, max(date_day) as hi from gold.mart_air_vs_energy_daily"
    )
    return row["lo"].iloc[0].date(), row["hi"].iloc[0].date()


def load_districts() -> pd.DataFrame:
    """Districts that have air quality or bicycle data."""
    return query(
        """
        select distinct d.district_slug, d.district_name
        from gold.dim_district as d
        where d.district_key in (
            select district_key from gold.mart_air_vs_energy_daily
            union
            select district_key from gold.fact_bike_traffic_daily
        )
        order by d.district_name
        """
    )


def load_district_daily(start: date, end: date, districts: list[str]) -> pd.DataFrame:
    """Mart rows (district x day) in the period, optionally limited to some districts."""
    return query(
        """
        select *
        from gold.mart_air_vs_energy_daily
        where date_day between ? and ?
          and (len(?::varchar[]) = 0 or list_contains(?::varchar[], district_slug))
        order by date_day, district_name
        """,
        [start, end, districts, districts],
    )


def load_station_pollution(start: date, end: date, pollutant: str) -> pd.DataFrame:
    """Current station attributes + mean pollutant level in the period (null = no data)."""
    return query(
        """
        with stats as (
            select
                station_id,
                avg(value_ugm3) as mean_ugm3,
                max(value_ugm3) as max_ugm3,
                count(*) as n_hours
            from gold.fact_air_quality_hourly
            where pollutant = ?
              and cast(hour_start_local as date) between ? and ?
            group by station_id
        )
        select
            s.station_id, s.station_name, s.district_slug, s.latitude, s.longitude,
            stats.mean_ugm3, stats.max_ugm3, coalesce(stats.n_hours, 0) as n_hours
        from gold.dim_station as s
        left join stats on s.station_id = stats.station_id
        where s.is_current and s.latitude is not null  -- inferred stations have no location
        order by s.station_name
        """,
        [pollutant, start, end],
    )


def load_hourly_pollutant_wind(
    start: date, end: date, pollutant: str, districts: list[str]
) -> pd.DataFrame:
    """Station-hour pollutant value with the wind at the station and the hour's price."""
    return query(
        """
        select
            aq.station_id,
            s.station_name,
            aq.hour_start_local,
            aq.value_ugm3,
            w.wind_speed_ms,
            e.price_eur_mwh
        from gold.fact_air_quality_hourly as aq
        inner join gold.dim_station as s on aq.station_key = s.station_key
        inner join gold.fact_weather_hourly as w
            on aq.station_id = w.station_id and aq.hour_start_ts_utc = w.hour_start_ts_utc
        inner join gold.fact_energy_price_hourly as e on aq.hour_start_ts_utc = e.hour_start_ts_utc
        where aq.pollutant = ?
          and cast(aq.hour_start_local as date) between ? and ?
          and (len(?::varchar[]) = 0 or list_contains(?::varchar[], s.district_slug))
        """,
        [pollutant, start, end, districts, districts],
    )


def load_daily_price(start: date, end: date) -> pd.DataFrame:
    return query(
        """
        select
            cast(hour_start_local as date) as date_day,
            avg(price_eur_mwh) as price_eur_mwh,
            avg(price_czk_mwh) as price_czk_mwh,
            min(price_eur_mwh) as min_price_eur_mwh,
            max(price_eur_mwh) as max_price_eur_mwh,
            avg(load_mw) as load_mw
        from gold.fact_energy_price_hourly
        where cast(hour_start_local as date) between ? and ?
        group by 1
        order by 1
        """,
        [start, end],
    )


def load_bike_daily(start: date, end: date) -> pd.DataFrame:
    """Bicycles per district and day, complete days only (partial days would fake a drop)."""
    return query(
        """
        select
            d.district_slug,
            d.district_name,
            f.local_date as date_day,
            sum(f.bike_count) as bikes,
            count(distinct f.counter_id) as n_counters
        from gold.fact_bike_traffic_daily as f
        inner join gold.dim_district as d on f.district_key = d.district_key
        where f.local_date between ? and ?
        group by 1, 2, 3
        having bool_and(f.is_complete_day) and sum(f.bike_count) is not null
        order by 1, 3
        """,
        [start, end],
    )


def load_bike_counter_daily(start: date, end: date) -> pd.DataFrame:
    """Bicycles per counter and day (complete days), to spot single-counter effects."""
    return query(
        """
        select
            c.counter_id,
            c.counter_name,
            d.district_name,
            f.local_date as date_day,
            sum(f.bike_count) as bikes
        from gold.fact_bike_traffic_daily as f
        inner join gold.dim_bike_counter as c on f.counter_key = c.counter_key
        inner join gold.dim_district as d on c.district_key = d.district_key
        where f.local_date between ? and ?
        group by 1, 2, 3, 4
        having bool_and(f.is_complete_day) and sum(f.bike_count) is not null
        order by 1, 4
        """,
        [start, end],
    )


def load_freshness() -> pd.DataFrame:
    return query(
        """
        select 'Air quality' as dataset, max(published_ts_utc) as latest_utc
        from gold.fact_air_quality_hourly
        union all
        select 'Electricity price', max(hour_start_ts_utc) from gold.fact_energy_price_hourly
        union all
        select 'Weather', max(hour_start_ts_utc) from gold.fact_weather_hourly
        union all
        select 'Bicycle counters', max(cast(local_date as timestamp))
        from gold.fact_bike_traffic_daily
        """
    )


# --------------------------------------------------------------------------- pure transforms


def daily_pollution(district_daily: pd.DataFrame, pollutant: str) -> pd.DataFrame:
    """Mean of the district means per day (each district with data counts once)."""
    column = MART_POLLUTANT_COLUMN[pollutant]
    df = district_daily.dropna(subset=[column])
    return (
        df.groupby("date_day", as_index=False)
        .agg(value_ugm3=(column, "mean"), n_districts=("district_slug", "nunique"))
        .sort_values("date_day")
    )


def district_correlations(
    district_daily: pd.DataFrame, pollutant: str, min_days: int = MIN_DAYS_FOR_CORRELATION
) -> pd.DataFrame:
    """Pearson r between the pollutant and each driver per district (long format)."""
    column = MART_POLLUTANT_COLUMN[pollutant]
    rows = []
    for (slug, name), group in district_daily.groupby(["district_slug", "district_name"]):
        for driver, label in DRIVERS.items():
            pair = group[[column, driver]].dropna()
            if len(pair) < min_days or pair[column].nunique() < 2 or pair[driver].nunique() < 2:
                continue
            rows.append(
                {
                    "district_slug": slug,
                    "district_name": name,
                    "driver": label,
                    "r": round(float(pair[column].corr(pair[driver])), 2),
                    "n_days": len(pair),
                }
            )
    return pd.DataFrame(rows, columns=["district_slug", "district_name", "driver", "r", "n_days"])


def rolling_bike_traffic(
    bike_daily: pd.DataFrame, window: int = ROLLING_WINDOW_DAYS
) -> pd.DataFrame:
    """Add a trailing rolling mean of daily bicycles per district (needs a full window)."""
    df = bike_daily.sort_values(["district_slug", "date_day"]).copy()
    df["bikes_rolling"] = df.groupby("district_slug")["bikes"].transform(
        lambda s: s.rolling(window, min_periods=window).mean()
    )
    return df


def bike_growth(
    bike_daily: pd.DataFrame,
    window: int = GROWTH_WINDOW_DAYS,
    group_cols: tuple[str, ...] = ("district_slug", "district_name"),
) -> pd.DataFrame:
    """Mean daily bicycles in the last `window` days vs the first `window` days per group.

    Groups without two full, non-overlapping windows are left out. `n_counters` (if present)
    is reported as the maximum per group.
    """
    rows = []
    for key, group in bike_daily.sort_values("date_day").groupby(list(group_cols)):
        if len(group) < 2 * window:
            continue
        first = group["bikes"].head(window).mean()
        last = group["bikes"].tail(window).mean()
        if first <= 0:
            continue
        row = dict(zip(group_cols, key, strict=True))
        if "n_counters" in group:
            row["n_counters"] = int(group["n_counters"].max())
        row.update(
            first_window_mean=round(first, 1),
            last_window_mean=round(last, 1),
            growth_pct=round((last / first - 1) * 100, 1),
        )
        rows.append(row)
    columns = [*group_cols]
    if "n_counters" in bike_daily:
        columns.append("n_counters")
    columns += ["first_window_mean", "last_window_mean", "growth_pct"]
    return pd.DataFrame(rows, columns=columns).sort_values("growth_pct", ascending=False)


@dataclass(frozen=True)
class Kpis:
    pollutant_mean: float | None
    pollutant_max_day: float | None
    price_mean_eur: float | None
    price_mean_czk: float | None
    negative_price_days: int
    bikes_per_day: float | None


def compute_kpis(pollution: pd.DataFrame, price: pd.DataFrame, bike_daily: pd.DataFrame) -> Kpis:
    def mean_or_none(series: pd.Series) -> float | None:
        return None if series.dropna().empty else float(series.mean())

    def max_or_none(series: pd.Series) -> float | None:
        return None if series.dropna().empty else float(series.max())

    daily_bikes = (
        bike_daily.groupby("date_day")["bikes"].sum() if not bike_daily.empty else pd.Series()
    )
    return Kpis(
        pollutant_mean=mean_or_none(pollution["value_ugm3"]) if not pollution.empty else None,
        pollutant_max_day=max_or_none(pollution["value_ugm3"]) if not pollution.empty else None,
        price_mean_eur=mean_or_none(price["price_eur_mwh"]) if not price.empty else None,
        price_mean_czk=mean_or_none(price["price_czk_mwh"]) if not price.empty else None,
        negative_price_days=int((price["min_price_eur_mwh"] < 0).sum()) if not price.empty else 0,
        bikes_per_day=mean_or_none(daily_bikes),
    )
