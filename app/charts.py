"""Plotly figure builders. Each takes prepared DataFrames + a Palette and returns a Figure.

Rules followed (dataviz guide): one y-scale per plot (no dual axis), colour follows the entity,
one hue for magnitude, blue <-> red with a grey midpoint for signed values, thin marks, hover on
every chart.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from app.theme import Palette, base_layout

PRAGUE_CENTER = {"lat": 50.075, "lon": 14.44}
MAX_HIGHLIGHTED = 4


def _padded_range(values: pd.Series, pad: float = 0.18) -> list[float]:
    """Axis range around 0 with room for outside bar labels."""
    lo, hi = min(float(values.min()), 0.0), max(float(values.max()), 0.0)
    span = (hi - lo) or 1.0
    return [lo - pad * span if lo < 0 else 0.0, hi + pad * span if hi > 0 else 0.0]


def _map_style(p: Palette) -> str:
    return "carto-darkmatter" if p.mode == "dark" else "carto-positron"


def station_map(stations: pd.DataFrame, pollutant_label: str, p: Palette) -> go.Figure:
    """Stations coloured by mean concentration; stations without data in grey."""
    with_data = stations.dropna(subset=["mean_ugm3"])
    without = stations[stations["mean_ugm3"].isna()]
    fig = go.Figure()
    if not without.empty:
        fig.add_trace(
            go.Scattermap(
                lat=without["latitude"],
                lon=without["longitude"],
                mode="markers",
                marker={"size": 11, "color": p.neutral},
                name="No data in period",
                text=without["station_name"],
                hovertemplate="<b>%{text}</b><br>no " + pollutant_label + " data<extra></extra>",
            )
        )
    if not with_data.empty:
        fig.add_trace(
            go.Scattermap(
                lat=with_data["latitude"],
                lon=with_data["longitude"],
                mode="markers",
                marker={
                    "size": 16,
                    "color": with_data["mean_ugm3"],
                    "colorscale": p.sequential_scale(),
                    "colorbar": {
                        "title": {"text": "µg/m³", "font": {"color": p.text_secondary}},
                        "tickfont": {"color": p.text_muted},
                        "thickness": 12,
                    },
                },
                name=f"Mean {pollutant_label}",
                customdata=with_data[["district_slug", "max_ugm3", "n_hours"]],
                text=with_data["station_name"],
                hovertemplate=(
                    "<b>%{text}</b><br>%{customdata[0]}<br>mean %{marker.color:.1f} µg/m³"
                    "<br>max %{customdata[1]:.1f} µg/m³ · %{customdata[2]} h<extra></extra>"
                ),
            )
        )
    fig.update_layout(
        **base_layout(
            p,
            margin={"l": 0, "r": 0, "t": 0, "b": 0},
            height=420,
            map={"style": _map_style(p), "center": PRAGUE_CENTER, "zoom": 10},
            showlegend=not without.empty,
            legend={"orientation": "h", "y": 0, "x": 0, "bgcolor": p.surface},
        )
    )
    return fig


def pollution_and_price(
    pollution: pd.DataFrame, price: pd.DataFrame, pollutant_label: str, p: Palette
) -> go.Figure:
    """Two stacked plots on a shared time axis — one scale each, never a dual axis."""
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=(
            f"{pollutant_label}, daily mean across districts (µg/m³)",
            "Day-ahead electricity price, daily mean (EUR/MWh)",
        ),
    )
    fig.add_trace(
        go.Scatter(
            x=pollution["date_day"],
            y=pollution["value_ugm3"],
            mode="lines",
            line={"color": p.categorical[0], "width": 2},
            name=pollutant_label,
            customdata=pollution[["n_districts"]],
            hovertemplate=(
                "%{x|%a %d %b}<br>%{y:.1f} µg/m³ · %{customdata[0]} districts<extra></extra>"
            ),
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=price["date_day"],
            y=price["price_eur_mwh"],
            mode="lines",
            line={"color": p.categorical[1], "width": 2},
            name="Price",
            customdata=price[["min_price_eur_mwh", "max_price_eur_mwh", "price_czk_mwh"]],
            hovertemplate=(
                "%{x|%a %d %b}<br>%{y:.1f} EUR/MWh (%{customdata[2]:,.0f} CZK/MWh)"
                "<br>hourly range %{customdata[0]:.1f} – %{customdata[1]:.1f}<extra></extra>"
            ),
        ),
        row=2,
        col=1,
    )
    layout = base_layout(p, height=520, showlegend=False, hovermode="x unified")
    fig.update_layout(**{k: v for k, v in layout.items() if k not in ("xaxis", "yaxis")})
    fig.update_xaxes(**layout["xaxis"])
    fig.update_yaxes(**layout["yaxis"])
    fig.update_annotations(font={"color": p.text_secondary, "size": 13})
    return fig


def pollutant_vs_wind(hourly: pd.DataFrame, pollutant_label: str, p: Palette) -> go.Figure:
    """Hourly station values: wind speed vs concentration, colour = electricity price."""
    fig = go.Figure(
        go.Scattergl(
            x=hourly["wind_speed_ms"],
            y=hourly["value_ugm3"],
            mode="markers",
            marker={
                "size": 7,
                "opacity": 0.55,
                "color": hourly["price_eur_mwh"],
                "colorscale": p.sequential_scale(),
                # 5th-95th percentile: rare price spikes would otherwise squeeze all points
                # into one shade; the colour bar ends are then "≤ / ≥" values
                "cmin": float(hourly["price_eur_mwh"].quantile(0.05)),
                "cmax": float(hourly["price_eur_mwh"].quantile(0.95)),
                "colorbar": {
                    "title": {"text": "EUR/MWh", "font": {"color": p.text_secondary}},
                    "tickfont": {"color": p.text_muted},
                    "thickness": 12,
                },
                "line": {"width": 0},
            },
            customdata=hourly[["station_name", "hour_start_local"]],
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>%{customdata[1]|%d %b %H:00}"
                "<br>wind %{x:.1f} m/s · " + pollutant_label + " %{y:.1f} µg/m³"
                "<br>price %{marker.color:.1f} EUR/MWh<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        **base_layout(
            p,
            height=440,
            xaxis={"title": {"text": "Wind speed at 10 m (m/s)"}},
            yaxis={"title": {"text": f"{pollutant_label} (µg/m³)"}},
        )
    )
    return fig


def correlation_heatmap(correlations: pd.DataFrame, p: Palette) -> go.Figure:
    """District x driver Pearson r on a diverging blue-grey-red scale fixed to [-1, 1]."""
    pivot = correlations.pivot(index="district_name", columns="driver", values="r")
    days = correlations.pivot(index="district_name", columns="driver", values="n_days")
    pivot = pivot.sort_index(ascending=False)
    days = days.reindex(index=pivot.index, columns=pivot.columns)
    fig = go.Figure(
        go.Heatmap(
            z=pivot.values,
            x=list(pivot.columns),
            y=list(pivot.index),
            zmin=-1,
            zmax=1,
            zmid=0,
            colorscale=p.diverging_scale(),
            xgap=2,
            ygap=2,
            text=pivot.map(lambda v: "" if pd.isna(v) else f"{v:+.2f}").values,
            texttemplate="%{text}",
            textfont={"size": 12},
            customdata=days.values,
            hovertemplate="%{y} · %{x}<br>r = %{z:+.2f} (%{customdata} days)<extra></extra>",
            colorbar={
                "title": {"text": "r", "font": {"color": p.text_secondary}},
                "tickfont": {"color": p.text_muted},
                "thickness": 12,
            },
        )
    )
    fig.update_layout(
        **base_layout(
            p,
            height=max(260, 40 * len(pivot.index) + 120),
            xaxis={"side": "top", "showgrid": False, "showline": False},
            yaxis={"showgrid": False, "showline": False},
            margin={"l": 140, "r": 16, "t": 48, "b": 16},
        )
    )
    return fig


def bike_rolling(
    rolling: pd.DataFrame, highlighted: list[str], window: int, p: Palette
) -> go.Figure:
    """7-day rolling mean per district; highlighted districts in colour, the rest grey."""
    highlighted = highlighted[:MAX_HIGHLIGHTED]
    fig = go.Figure()
    data = rolling.dropna(subset=["bikes_rolling"])
    for slug, group in data.groupby("district_slug"):
        if slug in highlighted:
            continue
        fig.add_trace(
            go.Scatter(
                x=group["date_day"],
                y=group["bikes_rolling"],
                mode="lines",
                line={"color": p.neutral, "width": 1},
                name=group["district_name"].iloc[0],
                showlegend=False,
                hovertemplate="%{fullData.name}<br>%{x|%d %b}: %{y:,.0f} bikes/day<extra></extra>",
            )
        )
    for i, slug in enumerate(highlighted):
        group = data[data["district_slug"] == slug]
        if group.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=group["date_day"],
                y=group["bikes_rolling"],
                mode="lines",
                line={"color": p.categorical[i], "width": 2},
                name=group["district_name"].iloc[0],
                hovertemplate="%{fullData.name}<br>%{x|%d %b}: %{y:,.0f} bikes/day<extra></extra>",
            )
        )
    fig.update_layout(
        **base_layout(
            p,
            height=420,
            yaxis={
                **base_layout(p)["yaxis"],
                "title": {"text": f"Bicycles per day ({window}-day rolling mean)"},
            },
        )
    )
    return fig


def bike_growth_bar(growth: pd.DataFrame, window: int, p: Palette) -> go.Figure:
    """Growth % per district: growth on the blue pole, decline on the red pole."""
    data = growth.sort_values("growth_pct")
    growth_color, decline_color = p.diverging[0], p.diverging[-1]
    colors = [growth_color if v >= 0 else decline_color for v in data["growth_pct"]]
    fig = go.Figure(
        go.Bar(
            x=data["growth_pct"],
            y=data["district_name"],
            orientation="h",
            marker={"color": colors, "line": {"width": 0}},
            text=[f"{v:+.0f} %" for v in data["growth_pct"]],
            textposition="outside",
            cliponaxis=False,
            textfont={"color": p.text_secondary, "size": 12},
            customdata=data[["first_window_mean", "last_window_mean"]],
            hovertemplate=(
                "%{y}<br>%{x:+.1f} %<br>first " + str(window) + " days: %{customdata[0]:,.0f}/day"
                "<br>last " + str(window) + " days: %{customdata[1]:,.0f}/day<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        **base_layout(
            p,
            height=max(260, 28 * len(data) + 100),
            bargap=0.35,
            xaxis={
                **base_layout(p)["xaxis"],
                "title": {"text": f"Change: last {window} vs first {window} complete days (%)"},
                "zeroline": True,
                "range": _padded_range(data["growth_pct"]),
            },
            yaxis={"showgrid": False},
            margin={"l": 140, "r": 48, "t": 24, "b": 56},
        )
    )
    return fig
