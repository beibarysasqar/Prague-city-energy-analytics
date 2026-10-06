"""CLI: `python -m extract.run [--source NAME|all] [--start DATE | --days N] [--end DATE]`.

Without a window, each source runs incrementally from `_state.json` (90 days on the first run).
`--days N` loads the last N days up to today (UTC) regardless of state, e.g. `--days 7` in CI.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import date, timedelta

from extract import (
    cnb_fx,
    entsoe,
    golemio_air_quality,
    golemio_bicycle,
    golemio_districts,
    open_meteo,
)
from extract.common import MissingSecretError, get_logger, load_env, setup_logging, utc_today

SourceRunner = Callable[[date | None, date | None], None]

SOURCES: dict[str, SourceRunner] = {
    "golemio_air_quality": golemio_air_quality.run,
    "golemio_bicycle": golemio_bicycle.run,
    "golemio_districts": golemio_districts.run,
    "entsoe": entsoe.run,
    "cnb_fx": cnb_fx.run,
    "open_meteo": open_meteo.run,
}

logger = get_logger("extract.run")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract sources into the bronze layer.")
    parser.add_argument("--source", choices=[*SOURCES, "all"], default="all")
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--start", type=date.fromisoformat, help="first day (UTC), inclusive")
    window.add_argument("--days", type=positive_int, help="last N days up to today (UTC)")
    parser.add_argument("--end", type=date.fromisoformat, help="last day (UTC), inclusive")
    return parser.parse_args(argv)


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def resolve_cli_window(
    args: argparse.Namespace, today: date | None = None
) -> tuple[date | None, date | None]:
    """(start, end) passed to the sources; (None, None) means incremental from state."""
    if args.days is None:
        return args.start, args.end
    end = args.end or today or utc_today()
    return end - timedelta(days=args.days - 1), end


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    load_env()
    setup_logging()
    start, end = resolve_cli_window(args)
    names = list(SOURCES) if args.source == "all" else [args.source]
    for name in names:
        logger.info("Running source %s", name)
        try:
            SOURCES[name](start, end)
        except (MissingSecretError, open_meteo.MissingStationsError) as exc:
            logger.error("%s", exc)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
