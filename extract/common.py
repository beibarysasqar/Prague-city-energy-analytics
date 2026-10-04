"""Shared helpers for extractors (HTTP, state, Parquet writing are added in phase 2)."""

import logging
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
BRONZE_DIR = DATA_DIR / "bronze"

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def load_env() -> None:
    """Load variables from the project .env file without overriding the real environment."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Return a module logger, configuring root logging once."""
    if not logging.getLogger().handlers:
        logging.basicConfig(level=level, format=LOG_FORMAT)
    return logging.getLogger(name)
