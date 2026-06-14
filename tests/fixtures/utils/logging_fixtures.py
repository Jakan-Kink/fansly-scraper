"""Logging-related test fixtures."""

import logging
import os
import tempfile
from pathlib import Path

import pytest
from loguru import logger as loguru_logger

from config.logging import init_logging_config


@pytest.fixture
def log_setup():
    """Provide an isolated stdlib logger plus a temp dir for log-file tests.

    Yields ``(temp_dir, log_filename, logger)`` and tears down the logger's
    handlers and the temp directory afterward.
    """
    temp_dir = Path(tempfile.mkdtemp())
    log_filename = str(temp_dir / "test.log")
    test_logger = logging.getLogger("test_logger")
    test_logger.setLevel(logging.INFO)

    yield str(temp_dir), log_filename, test_logger

    # Cleanup — copy the handler list so removal doesn't mutate during iteration
    for handler in test_logger.handlers.copy():
        test_logger.removeHandler(handler)
        handler.close()

    try:
        for file_path in temp_dir.iterdir():
            file_path.unlink()
        temp_dir.rmdir()
    except OSError as e:
        loguru_logger.warning(f"Cleanup issue: {e}")


@pytest.fixture
def log_dir(tmp_path):
    """Chdir into tmp_path and yield its ``logs/`` subdirectory for log-file output."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    old_cwd = Path.cwd()
    os.chdir(str(tmp_path))
    yield log_dir
    os.chdir(str(old_cwd))


@pytest.fixture
def logging_config(log_dir, uuid_test_db_factory, monkeypatch):
    """A config with UUID database + log_dir cwd, with logging initialized.

    Named distinctly from the database ``config`` fixture so opt-in is explicit.
    """
    config = uuid_test_db_factory
    # TESTING flag via monkeypatch so it's reverted at teardown — avoids
    # cross-test pollution under pytest-xdist.
    monkeypatch.setenv("TESTING", "1")
    init_logging_config(config)
    return config


__all__ = [
    "log_dir",
    "log_setup",
    "logging_config",
]
