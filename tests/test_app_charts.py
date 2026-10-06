import pandas as pd
import pytest

from app import charts
from app.theme import DARK, LIGHT, Palette, base_layout, palette_for


@pytest.fixture(params=[LIGHT, DARK], ids=["light", "dark"])
def palette(request: pytest.FixtureRequest) -> Palette:
    return request.param


def test_palette_for_theme() -> None:
    assert palette_for("dark") is DARK
    assert palette_for("light") is LIGHT and palette_for(None) is LIGHT


def test_base_layout_merges_axis_title_font(palette: Palette) -> None:
    layout = base_layout(palette, xaxis={"title": {"text": "x"}})
    assert layout["xaxis"]["title"] == {
        "text": "x",
        "font": {"color": palette.text_secondary, "size": 12},
    }
    assert layout["xaxis"]["gridcolor"] == palette.grid


def test_pollution_and_price_is_two_plots_not_dual_axis(palette: Palette) -> None:
    days = pd.date_range("2026-09-01", periods=3)
    pollution = pd.DataFrame({"date_day": days, "value_ugm3": [10, 20, 15], "n_districts": 3})
    price = pd.DataFrame(
        {
            "date_day": days,
            "price_eur_mwh": [100, -5, 120],
            "price_czk_mwh": [2440, -122, 2928],
            "min_price_eur_mwh": [80, -20, 90],
            "max_price_eur_mwh": [130, 10, 150],
        }
    )
    fig = charts.pollution_and_price(pollution, price, "PM10", palette)
    layout = fig.to_plotly_json()["layout"]
    assert {k for k in layout if k.startswith("yaxis")} == {"yaxis", "yaxis2"}
    assert all("overlaying" not in layout[k] for k in ("yaxis", "yaxis2"))  # no dual axis
    assert [t.yaxis for t in fig.data] == ["y", "y2"]
    assert fig.data[0].line.color == palette.categorical[0]


def test_correlation_heatmap_is_symmetric_diverging(palette: Palette) -> None:
    corr = pd.DataFrame(
        {
            "district_slug": ["a", "a"],
            "district_name": ["A", "A"],
            "driver": ["Wind speed", "Temperature"],
            "r": [-0.5, 0.3],
            "n_days": [30, 30],
        }
    )
    heat = charts.correlation_heatmap(corr, palette).data[0]
    assert (heat.zmin, heat.zmax, heat.zmid) == (-1, 1, 0)
    assert heat.colorscale[len(heat.colorscale) // 2][1] == palette.diverging[2]  # grey midpoint


def test_bike_rolling_emphasis(palette: Palette) -> None:
    rows = [
        {"district_slug": slug, "district_name": slug, "date_day": d, "bikes_rolling": 1.0}
        for slug in ["a", "b", "c"]
        for d in pd.date_range("2026-09-01", periods=2)
    ]
    fig = charts.bike_rolling(pd.DataFrame(rows), ["b"], 7, palette)
    colors = {t.name: t.line.color for t in fig.data}
    assert colors["b"] == palette.categorical[0]
    assert colors["a"] == colors["c"] == palette.neutral
    assert [t.showlegend for t in fig.data if t.name != "b"] == [False, False]


def test_bike_growth_bar_colours_by_sign(palette: Palette) -> None:
    growth = pd.DataFrame(
        {
            "district_name": ["Up", "Down"],
            "first_window_mean": [100, 100],
            "last_window_mean": [150, 50],
            "growth_pct": [50.0, -50.0],
        }
    )
    bar = charts.bike_growth_bar(growth, 28, palette).data[0]
    by_name = dict(zip(bar.y, bar.marker.color, strict=True))
    assert by_name["Up"] == palette.diverging[0] and by_name["Down"] == palette.diverging[-1]
    assert bar.cliponaxis is False


def test_padded_range_keeps_zero_and_room_for_labels() -> None:
    # range 0..50 (zero always included), padded by 18 % of the span
    assert charts._padded_range(pd.Series([10.0, 50.0])) == [0.0, pytest.approx(59.0)]
    lo, hi = charts._padded_range(pd.Series([-20.0, 10.0]))
    assert lo < -20 and hi > 10


def test_station_map_greys_stations_without_data(palette: Palette) -> None:
    stations = pd.DataFrame(
        {
            "station_id": ["A", "B"],
            "station_name": ["A", "B"],
            "district_slug": ["praha-1", "praha-2"],
            "latitude": [50.08, 50.07],
            "longitude": [14.42, 14.43],
            "mean_ugm3": [20.0, None],
            "max_ugm3": [40.0, None],
            "n_hours": [100, 0],
        }
    )
    fig = charts.station_map(stations, "PM10", palette)
    assert [t.name for t in fig.data] == ["No data in period", "Mean PM10"]
    assert fig.data[0].marker.color == palette.neutral
