"""CLI: `python -m extract.run [--source NAME|all] [--start YYYY-MM-DD] [--end YYYY-MM-DD]`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import date

from extract import (
    cnb_fx,
    entsoe,
    golemio_air_quality,
    golemio_bicycle,
    golemio_districts,
    open_meteo,
)
from extract.common import MissingSecretError, get_logger, load_env, setup_logging

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
    parser.add_argument("--start", type=date.fromisoformat, help="first day (UTC), inclusive")
    parser.add_argument("--end", type=date.fromisoformat, help="last day (UTC), inclusive")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    load_env()
    setup_logging()
    names = list(SOURCES) if args.source == "all" else [args.source]
    for name in names:
        logger.info("Running source %s", name)
        try:
            SOURCES[name](args.start, args.end)
        except (MissingSecretError, open_meteo.MissingStationsError) as exc:
            logger.error("%s", exc)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
