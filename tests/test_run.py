from datetime import date

import pytest

from extract.run import SOURCES, parse_args, resolve_cli_window

TODAY = date(2026, 10, 6)


def test_no_window_means_incremental() -> None:
    assert resolve_cli_window(parse_args([]), TODAY) == (None, None)


def test_explicit_start_and_end() -> None:
    args = parse_args(["--start", "2026-10-01", "--end", "2026-10-03"])
    assert resolve_cli_window(args, TODAY) == (date(2026, 10, 1), date(2026, 10, 3))


def test_days_window_ends_today() -> None:
    assert resolve_cli_window(parse_args(["--days", "7"]), TODAY) == (
        date(2026, 9, 30),
        TODAY,
    )


def test_days_window_with_explicit_end() -> None:
    args = parse_args(["--days", "1", "--end", "2026-10-02"])
    assert resolve_cli_window(args, TODAY) == (date(2026, 10, 2), date(2026, 10, 2))


@pytest.mark.parametrize(
    "argv", [["--days", "0"], ["--days", "x"], ["--days", "3", "--start", "2026-10-01"]]
)
def test_invalid_windows_are_rejected(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        parse_args(argv)


def test_source_choices_include_all_extractors() -> None:
    assert parse_args(["--source", "open_meteo"]).source == "open_meteo"
    assert set(SOURCES) == {
        "golemio_air_quality",
        "golemio_bicycle",
        "golemio_districts",
        "entsoe",
        "cnb_fx",
        "open_meteo",
    }
