import logging

from extract.common import BRONZE_DIR, PROJECT_ROOT, get_logger


def test_paths_point_into_project() -> None:
    assert BRONZE_DIR.is_relative_to(PROJECT_ROOT)
    assert (PROJECT_ROOT / "pyproject.toml").exists()


def test_get_logger_returns_named_logger() -> None:
    logger = get_logger("extract.test")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "extract.test"
