"""Test logging configuration."""

import pytest
from loguru import logger

from config.fanslyconfig import FanslyConfig
from config.logging import (
    _LEVEL_VALUES,
    _trace_level_only,
    get_log_level,
    init_logging_config,
    set_debug_enabled,
)
from errors import InvalidTraceLogError


@pytest.fixture(autouse=True)
def setup_loggers():
    """Initialize loggers before each test."""
    # Clean up any existing handlers
    logger.remove()

    # Initialize logging config
    config = FanslyConfig(program_version="test")
    init_logging_config(config)
    # Ensure debug mode is disabled for clean test state
    set_debug_enabled(False)

    yield
    # Clean up handlers and reset global state
    logger.remove()
    set_debug_enabled(False)


def test_loggers_have_correct_bindings():
    """Test that loggers have correct extra bindings."""
    # Textio binding tests
    record = {"extra": {"logger": "textio"}}
    assert record["extra"].get("logger", None) == "textio"
    record = {"extra": {"logger": "json"}}
    assert record["extra"].get("logger", None) != "textio"

    # JSON binding tests
    record = {"extra": {"logger": "json"}}
    assert record["extra"].get("logger", None) == "json"
    record = {"extra": {"logger": "textio"}}
    assert record["extra"].get("logger", None) != "json"

    # Stash binding tests
    record = {"extra": {"logger": "stash"}}
    assert record["extra"].get("logger", None) == "stash"
    record = {"extra": {"textio": True}}
    assert record["extra"].get("logger", None) != "stash"

    # DB binding tests
    record = {"extra": {"logger": "db"}}
    assert record["extra"].get("logger", None) == "db"
    record = {"extra": {"textio": True}}
    assert record["extra"].get("logger", None) != "db"


def test_trace_logger_only_accepts_trace_level():
    """Test that trace_logger only accepts TRACE level messages."""
    # Create a mock record with TRACE level
    trace_record = {
        "level": type("Level", (), {"no": _LEVEL_VALUES["TRACE"], "name": "TRACE"})()
    }
    assert _trace_level_only(trace_record) is True

    # Create mock records with other levels
    for level_name in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
        record = {
            "level": type(
                "Level", (), {"no": _LEVEL_VALUES[level_name], "name": level_name}
            )()
        }
        with pytest.raises(InvalidTraceLogError) as exc_info:
            _trace_level_only(record)
        assert f"got {level_name}" in str(exc_info.value)


def test_get_log_level_defaults():
    """Test default log levels without config."""
    # Default level is INFO
    assert get_log_level("textio") == _LEVEL_VALUES["INFO"]
    assert get_log_level("json") == _LEVEL_VALUES["INFO"]
    assert get_log_level("stash_console") == _LEVEL_VALUES["INFO"]
    assert get_log_level("stash_file") == _LEVEL_VALUES["INFO"]
    assert get_log_level("sqlalchemy") == _LEVEL_VALUES["INFO"]

    # trace_logger is effectively disabled by default
    assert get_log_level("trace") == _LEVEL_VALUES["CRITICAL"]


def test_get_log_level_with_debug_enabled():
    """Test that debug mode sets all non-trace loggers to DEBUG."""
    set_debug_enabled(True)
    try:
        # All non-trace loggers should be DEBUG
        assert get_log_level("textio") == _LEVEL_VALUES["DEBUG"]
        assert get_log_level("json") == _LEVEL_VALUES["DEBUG"]
        assert get_log_level("stash_console") == _LEVEL_VALUES["DEBUG"]
        assert get_log_level("stash_file") == _LEVEL_VALUES["DEBUG"]
        assert get_log_level("sqlalchemy") == _LEVEL_VALUES["DEBUG"]

        # trace_logger is still effectively disabled
        assert get_log_level("trace") == _LEVEL_VALUES["CRITICAL"]
    finally:
        set_debug_enabled(False)


def test_get_log_level_with_trace_enabled(tmp_path):
    """Test that trace mode affects trace_logger and sqlalchemy (for db_logger.trace())."""
    config = FanslyConfig(program_version="test")
    config.trace = True
    init_logging_config(config)

    try:
        # trace_logger should accept TRACE level
        assert get_log_level("trace") == _LEVEL_VALUES["TRACE"]

        # sqlalchemy should also use TRACE level (for db_logger.trace())
        assert get_log_level("sqlalchemy") == _LEVEL_VALUES["TRACE"]

        # Other loggers should still be at default levels
        assert get_log_level("textio") == _LEVEL_VALUES["INFO"]
        assert get_log_level("json") == _LEVEL_VALUES["INFO"]
        assert get_log_level("stash_console") == _LEVEL_VALUES["INFO"]
        assert get_log_level("stash_file") == _LEVEL_VALUES["INFO"]
    finally:
        config.trace = False
        init_logging_config(config)


def test_get_log_level_with_debug_and_trace(tmp_path):
    """Test interaction between debug and trace modes."""
    config = FanslyConfig(program_version="test")
    config.trace = True
    init_logging_config(config)
    set_debug_enabled(True)

    try:
        # trace_logger should accept TRACE level
        assert get_log_level("trace") == _LEVEL_VALUES["TRACE"]

        # sqlalchemy should use TRACE level (trace check happens before debug check)
        assert get_log_level("sqlalchemy") == _LEVEL_VALUES["TRACE"]

        # Other loggers should be at DEBUG level
        assert get_log_level("textio") == _LEVEL_VALUES["DEBUG"]
        assert get_log_level("json") == _LEVEL_VALUES["DEBUG"]
        assert get_log_level("stash_console") == _LEVEL_VALUES["DEBUG"]
        assert get_log_level("stash_file") == _LEVEL_VALUES["DEBUG"]
    finally:
        config.trace = False
        init_logging_config(config)
        set_debug_enabled(False)


def test_get_log_level_with_config_levels(tmp_path):
    """Test that configured levels are respected."""
    config = FanslyConfig(program_version="test")
    config.log_levels = {
        "textio": "WARNING",
        "json": "ERROR",
        "stash_console": "INFO",
        "stash_file": "DEBUG",
        "sqlalchemy": "INFO",
    }
    init_logging_config(config)

    try:
        # Each logger should use its configured level
        assert get_log_level("textio") == _LEVEL_VALUES["WARNING"]
        assert get_log_level("json") == _LEVEL_VALUES["ERROR"]
        assert get_log_level("stash_console") == _LEVEL_VALUES["INFO"]
        assert get_log_level("stash_file") == _LEVEL_VALUES["DEBUG"]
        assert get_log_level("sqlalchemy") == _LEVEL_VALUES["INFO"]

        # trace_logger is still effectively disabled
        assert get_log_level("trace") == _LEVEL_VALUES["CRITICAL"]
    finally:
        # Reset to defaults
        config.log_levels = {
            "textio": "INFO",
            "json": "INFO",
            "stash_console": "INFO",
            "stash_file": "INFO",
            "sqlalchemy": "INFO",
        }
        init_logging_config(config)


def test_get_log_level_minimum_debug():
    """Test that non-trace loggers can't go below DEBUG level."""
    # Set up config with TRACE level for all loggers
    config = FanslyConfig(program_version="test")
    config.log_levels = {
        "textio": "TRACE",
        "json": "TRACE",
        "stash_console": "TRACE",
        "stash_file": "TRACE",
        "sqlalchemy": "TRACE",
    }
    init_logging_config(config)

    # Try to set a level below DEBUG - should be forced to DEBUG
    assert get_log_level("textio", "TRACE") == _LEVEL_VALUES["DEBUG"]
    assert get_log_level("json", "TRACE") == _LEVEL_VALUES["DEBUG"]
    assert get_log_level("stash_console", "TRACE") == _LEVEL_VALUES["DEBUG"]
    assert get_log_level("stash_file", "TRACE") == _LEVEL_VALUES["DEBUG"]
    assert get_log_level("sqlalchemy", "TRACE") == _LEVEL_VALUES["DEBUG"]

    # trace_logger can go to TRACE when enabled
    config.trace = True
    init_logging_config(config)

    try:
        assert get_log_level("trace") == _LEVEL_VALUES["TRACE"]
    finally:
        config.trace = False
        init_logging_config(config)
