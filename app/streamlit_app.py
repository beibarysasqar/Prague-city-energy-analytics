"""Prague City & Energy Analytics — Streamlit dashboard on the dbt gold layer.

Run: `uv run streamlit run app/streamlit_app.py` (or `make app`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# `streamlit run` puts app/ (not the project root) on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import charts, data  # noqa: E402
from app.theme import palette_for  # noqa: E402

CACHE_TTL_S = 600

st.set_page_config(page_title="Prague City & Energy", page_icon="🌆", layout="wide")


# --------------------------------------------------------------------------- cached loaders

cached = st.cache_data(ttl=CACHE_TTL_S, show_spinner=False)
load_date_bounds = cached(data.load_date_bounds)
load_districts = cached(data.load_districts)
load_district_daily = cached(data.load_district_daily)
load_station_pollution = cached(data.load_station_pollution)
load_hourly_pollutant_wind = cached(data.load_hourly_pollutant_wind)
load_daily_price = cached(data.load_daily_price)
load_bike_daily = cached(data.load_bike_daily)
load_bike_counter_daily = cached(data.load_bike_counter_daily)
load_freshness = cached(data.load_freshness)


def show_table(df: pd.DataFrame, label: str = "Show data") -> None:
    with st.expander(label):
        st.dataframe(df, hide_index=True, width="stretch")


def fmt(value: float | None, pattern: str = "{:,.1f}") -> str:
    return "—" if value is None else pattern.format(value)


# --------------------------------------------------------------------------- page

if not data.warehouse_path().exists():
    st.error(
        f"Warehouse not found at `{data.warehouse_path()}` — run `make extract` and `make dbt`."
    )
    st.stop()

palette = palette_for(getattr(getattr(st.context, "theme", None), "type", None))
lo, hi = load_date_bounds()
districts_df = load_districts()
district_names = dict(
    zip(districts_df["district_slug"], districts_df["district_name"], strict=True)
)

st.title("Prague City & Energy Analytics")
st.caption(
    "How do electricity prices and weather relate to air pollution across Prague districts, "
    "and where is bicycle traffic growing? Data: Golemio, ENTSO-E, ČNB, Open-Meteo."
)

with st.sidebar:
    st.header("Filters")
    period = st.date_input(
        "Period",
        value=(lo, hi),
        min_value=lo,
        max_value=hi,
        format="DD.MM.YYYY",
    )
    start, end = period if isinstance(period, tuple) and len(period) == 2 else (lo, hi)
    selected = st.multiselect(
        "Districts",
        options=list(district_names),
        format_func=district_names.get,
        placeholder="All districts",
    )
    pollutant = st.selectbox(
        "Pollutant",
        options=list(data.POLLUTANTS),
        format_func=data.POLLUTANTS.get,
        help="PM2.5 is measured by only 4 stations, PM10 by all of them.",
    )
    st.caption("Filters apply to every chart on the page.")

pollutant_label = data.POLLUTANTS[pollutant]

district_daily = load_district_daily(start, end, selected)
pollution = data.daily_pollution(district_daily, pollutant)
price = load_daily_price(start, end)
bike_daily = load_bike_daily(start, end)
bike_daily_selected = (
    bike_daily[bike_daily["district_slug"].isin(selected)] if selected else bike_daily
)

# --------------------------------------------------------------------------- KPIs

kpis = data.compute_kpis(pollution, price, bike_daily_selected)
c1, c2, c3, c4 = st.columns(4)
c1.metric(f"Mean {pollutant_label}", fmt(kpis.pollutant_mean, "{:.1f} µg/m³"))
c2.metric(f"Worst day {pollutant_label}", fmt(kpis.pollutant_max_day, "{:.1f} µg/m³"))
c3.metric(
    "Mean day-ahead price",
    fmt(kpis.price_mean_eur, "{:.1f} EUR/MWh"),
    help=f"≈ {fmt(kpis.price_mean_czk, '{:,.0f}')} CZK/MWh at the ČNB rate; "
    f"{kpis.negative_price_days} days had negative hourly prices.",
)
c4.metric("Bicycles per day", fmt(kpis.bikes_per_day, "{:,.0f}"), help="Complete days only.")

# --------------------------------------------------------------------------- air quality & energy

st.header("Air quality and electricity prices")

stations = load_station_pollution(start, end, pollutant)
if selected:
    stations = stations[stations["district_slug"].isin(selected)]
map_col, text_col = st.columns([3, 2])
with map_col:
    st.subheader(f"Stations — mean {pollutant_label}")
    if stations.empty:
        st.info("No air quality stations in the selected districts.")
    else:
        st.plotly_chart(charts.station_map(stations, pollutant_label, palette), width="stretch")
with text_col:
    n_with = int(stations["mean_ugm3"].notna().sum())
    st.markdown(
        f"**{n_with} of {len(stations)}** stations measured {pollutant_label} in the period. "
        "Grey markers have no data for this pollutant (some stations stopped publishing on "
        "12 Aug 2026; PM2.5 is measured at four stations only)."
    )
    show_table(
        stations[["station_name", "district_slug", "mean_ugm3", "max_ugm3", "n_hours"]],
        "Station table",
    )

st.subheader(f"{pollutant_label} and day-ahead price over time")
if pollution.empty or price.empty:
    st.info(f"No {pollutant_label} data for this selection.")
else:
    st.plotly_chart(
        charts.pollution_and_price(pollution, price, pollutant_label, palette),
        width="stretch",
    )
    st.caption(
        "Two charts with their own scales on a shared time axis. Before 12 Aug 2026 the source "
        "publishes 3-hour running averages, afterwards 1-hour values."
    )
    show_table(pollution.merge(price, on="date_day", how="outer"))

st.subheader(f"{pollutant_label} vs wind speed, coloured by electricity price")
hourly = load_hourly_pollutant_wind(start, end, pollutant, selected)
if hourly.empty:
    st.info(f"No hourly {pollutant_label} data for this selection.")
else:
    st.plotly_chart(charts.pollutant_vs_wind(hourly, pollutant_label, palette), width="stretch")
    st.caption(
        f"{len(hourly):,} station-hours. Wind disperses pollution: values fall as wind rises."
    )
    show_table(hourly)

# --------------------------------------------------------------------------- correlations

st.header("What moves with pollution? Correlations by district")
correlations = data.district_correlations(district_daily, pollutant)
if correlations.empty:
    st.info(
        f"Not enough days with {pollutant_label} data "
        f"(at least {data.MIN_DAYS_FOR_CORRELATION} per district) for this selection."
    )
else:
    st.plotly_chart(charts.correlation_heatmap(correlations, palette), width="stretch")
    st.warning(
        "**Correlation ≠ causation.** Weather drives both pollution and electricity demand "
        "(e.g. calm, cold days raise heating, load and pollution at once), so a price–pollution "
        "correlation does not mean prices cause pollution. Pearson r of daily means; "
        f"cells need ≥ {data.MIN_DAYS_FOR_CORRELATION} days.",
        icon="⚠️",
    )
    show_table(correlations)

# --------------------------------------------------------------------------- bikes

st.header("Where is bicycle traffic growing?")
if bike_daily.empty:
    st.info("No complete bicycle counter days in the selected period.")
else:
    growth = data.bike_growth(bike_daily_selected)
    rolling = data.rolling_bike_traffic(bike_daily)
    highlighted = selected or growth["district_slug"].head(3).tolist()
    left, right = st.columns(2)
    with left:
        st.subheader(f"{data.ROLLING_WINDOW_DAYS}-day rolling mean by district")
        st.plotly_chart(
            charts.bike_rolling(rolling, highlighted, data.ROLLING_WINDOW_DAYS, palette),
            width="stretch",
        )
        st.caption(
            "Coloured: selected districts (or the three fastest-growing); grey: all others. "
            f"At most {charts.MAX_HIGHLIGHTED} are highlighted."
        )
    with right:
        st.subheader("Growth by district")
        if growth.empty:
            st.info(
                f"Growth needs two full {data.GROWTH_WINDOW_DAYS}-day windows — "
                "choose a longer period."
            )
        else:
            st.plotly_chart(
                charts.bike_growth_bar(growth, data.GROWTH_WINDOW_DAYS, palette),
                width="stretch",
            )
        st.caption(
            "Year-over-year growth becomes available after one year of data; until then growth "
            "compares the last and the first 28 complete days (season effects included). "
            "Large swings often come from a single counter (sensor repair or outage, roadworks) "
            "— check the counter table below before reading them as a trend."
        )
    show_table(growth, "Growth by district (table)")
    counter_growth = data.bike_growth(
        load_bike_counter_daily(start, end),
        group_cols=("counter_id", "counter_name", "district_name"),
    )
    if selected:
        counter_growth = counter_growth[
            counter_growth["district_name"].isin([district_names[s] for s in selected])
        ]
    show_table(counter_growth, "Growth by counter (spot single-counter effects)")

# --------------------------------------------------------------------------- footer

st.divider()
freshness = load_freshness()
latest = ", ".join(
    f"{row.dataset}: {row.latest_utc:%d %b %Y %H:%M} UTC" for row in freshness.itertuples()
)
st.caption(f"Data as of — {latest}. Period shown: {start:%d.%m.%Y} – {end:%d.%m.%Y}.")
