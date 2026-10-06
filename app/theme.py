"""Chart palette and Plotly layout, light and dark.

Values come from the dataviz reference palette (pre-validated for colour-vision deficiency in both
modes). The red diverging arm is not part of that palette; its steps were derived in OKLCH to match
the lightness of the blue steps they mirror (L 0.764 / 0.480), so both arms read as equally strong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", sans-serif'


@dataclass(frozen=True)
class Palette:
    mode: str
    surface: str
    text_primary: str
    text_secondary: str
    text_muted: str
    grid: str
    axis: str
    categorical: tuple[str, ...]
    neutral: str  # de-emphasised series (emphasis encoding)
    sequential: tuple[str, ...]  # light -> dark, one hue (blue)
    diverging: tuple[str, ...]  # negative pole -> grey midpoint -> positive pole

    def sequential_scale(self) -> list[list[float | str]]:
        n = len(self.sequential) - 1
        return [[i / n, c] for i, c in enumerate(self.sequential)]

    def diverging_scale(self) -> list[list[float | str]]:
        n = len(self.diverging) - 1
        return [[i / n, c] for i, c in enumerate(self.diverging)]


LIGHT = Palette(
    mode="light",
    surface="#fcfcfb",
    text_primary="#0b0b0b",
    text_secondary="#52514e",
    text_muted="#898781",
    grid="#e1e0d9",
    axis="#c3c2b7",
    categorical=(
        "#2a78d6",
        "#eb6834",
        "#1baf7a",
        "#eda100",
        "#e87ba4",
        "#008300",
        "#4a3aa7",
        "#e34948",
    ),
    neutral="#c3c2b7",
    sequential=("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"),
    diverging=("#1c5cab", "#86b6ef", "#f0efec", "#f1968e", "#a6272a"),
)

DARK = Palette(
    mode="dark",
    surface="#1a1a19",
    text_primary="#ffffff",
    text_secondary="#c3c2b7",
    text_muted="#898781",
    grid="#2c2c2a",
    axis="#383835",
    categorical=(
        "#3987e5",
        "#d95926",
        "#199e70",
        "#c98500",
        "#d55181",
        "#008300",
        "#9085e9",
        "#e66767",
    ),
    neutral="#52514e",
    # dark mode: the ramp runs from near-surface (low) to bright (high)
    sequential=("#104281", "#184f95", "#256abf", "#2a78d6", "#3987e5", "#6da7ec", "#b7d3f6"),
    diverging=("#3987e5", "#1c5cab", "#383835", "#a6272a", "#e14c4a"),
)


def palette_for(theme_type: str | None) -> Palette:
    return DARK if theme_type == "dark" else LIGHT


def base_layout(p: Palette, **overrides: Any) -> dict[str, Any]:
    """Recessive chrome: hairline grid, muted axes, system sans, unified hover."""
    axis = {
        "gridcolor": p.grid,
        "gridwidth": 1,
        "linecolor": p.axis,
        "zerolinecolor": p.axis,
        "tickfont": {"color": p.text_muted, "size": 12},
        "title": {"font": {"color": p.text_secondary, "size": 12}},
        "showline": True,
    }
    layout: dict[str, Any] = {
        "paper_bgcolor": p.surface,
        "plot_bgcolor": p.surface,
        "font": {"family": FONT_FAMILY, "color": p.text_primary, "size": 13},
        "colorway": list(p.categorical),
        "margin": {"l": 56, "r": 16, "t": 40, "b": 48},
        "xaxis": dict(axis),
        "yaxis": dict(axis),
        "legend": {"font": {"color": p.text_secondary}, "orientation": "h", "y": -0.2},
        "hoverlabel": {"font": {"family": FONT_FAMILY}},
    }
    for key in ("xaxis", "yaxis"):
        if key in overrides:  # merge, so a custom title keeps the shared title font
            custom = dict(overrides.pop(key))
            merged = {**layout[key], **custom}
            if "title" in custom:
                merged["title"] = {**layout[key]["title"], **custom["title"]}
            layout[key] = merged
    layout.update(overrides)
    return layout
