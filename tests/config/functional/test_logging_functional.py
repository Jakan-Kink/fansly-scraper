"""Functional tests for logging configuration."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from loguru import logger

from config.fanslyconfig import FanslyConfig
from config.logging import (
    _LEVEL_VALUES,
    db_logger,
    init_logging_config,
    json_logger,
    set_debug_enabled,
    stash_logger,
    textio_logger,
    trace_logger,
)
from errors import InvalidTraceLogError


@pytest.fixture
def log_dir(tmp_path):
    """Create a temporary log directory."""
    # Create logs directory in the right place
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    # Also create it in cwd since some code looks there
    cwd_logs = Path.cwd() / "logs"
    cwd_logs.mkdir(parents=True, exist_ok=True)
    return log_dir


@pytest.fixture
def config(log_dir):
    """Create a test config with log directory set."""
    config = FanslyConfig(program_version="test")
    # Monkeypatch cwd to use our temp log dir
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(Path, "cwd", lambda: log_dir.parent)
    init_logging_config(config)
    yield config
    monkeypatch.undo()


def read_log_file(log_dir: Path, filename: str) -> list[str]:
    """Read lines from a log file."""
    log_file = log_dir / filename
    if not log_file.exists():
        return []
    return log_file.read_text().splitlines()


def test_textio_logger_output(config, log_dir):
    """Test that textio_logger writes to the correct files at correct levels."""
    # INFO level should go to both console and file
    textio_logger.info("Info message")
    log_lines = read_log_file(log_dir, "fansly_downloader_ng.log")
    assert any("Info message" in line for line in log_lines)
    assert any("[INFO ]" in line for line in log_lines)

    # DEBUG level should be filtered out by default
    textio_logger.debug("Debug message")
    log_lines = read_log_file(log_dir, "fansly_downloader_ng.log")
    assert not any("Debug message" in line for line in log_lines)

    # With debug enabled, DEBUG should appear
    set_debug_enabled(True)
    try:
        textio_logger.debug("Debug message with debug enabled")
        log_lines = read_log_file(log_dir, "fansly_downloader_ng.log")
        assert any("Debug message with debug enabled" in line for line in log_lines)
        assert any("[DEBUG]" in line for line in log_lines)
    finally:
        set_debug_enabled(False)


def test_json_logger_output(config, log_dir):
    """Test that json_logger writes to the correct file at correct levels."""
    # INFO level should go to file only
    json_logger.info("Info message")
    log_lines = read_log_file(log_dir, "fansly_downloader_ng_json.log")
    assert any("Info message" in line for line in log_lines)
    assert any("[INFO]" in line for line in log_lines)

    # DEBUG level should be filtered out by default
    json_logger.debug("Debug message")
    log_lines = read_log_file(log_dir, "fansly_downloader_ng_json.log")
    assert not any("Debug message" in line for line in log_lines)

    # With debug enabled, DEBUG should appear
    set_debug_enabled(True)
    try:
        json_logger.debug("Debug message with debug enabled")
        log_lines = read_log_file(log_dir, "fansly_downloader_ng_json.log")
        assert any("Debug message with debug enabled" in line for line in log_lines)
        assert any("[DEBUG]" in line for line in log_lines)
    finally:
        set_debug_enabled(False)


def test_db_logger_output(config, log_dir):
    """Test that db_logger writes to the correct file at correct levels."""
    # INFO level should go to file only
    db_logger.info("Info message")
    log_lines = read_log_file(log_dir, "sqlalchemy.log")
    assert any("Info message" in line for line in log_lines)
    assert any("[INFO]" in line for line in log_lines)

    # DEBUG level should be filtered out by default
    db_logger.debug("Debug message")
    log_lines = read_log_file(log_dir, "sqlalchemy.log")
    assert not any("Debug message" in line for line in log_lines)

    # With debug enabled, DEBUG should appear
    set_debug_enabled(True)
    try:
        db_logger.debug("Debug message with debug enabled")
        log_lines = read_log_file(log_dir, "sqlalchemy.log")
        assert any("Debug message with debug enabled" in line for line in log_lines)
        assert any("[DEBUG]" in line for line in log_lines)
    finally:
        set_debug_enabled(False)


def test_stash_logger_output(config, log_dir):
    """Test that stash_logger writes to the correct files at correct levels."""
    # INFO level should go to both console and file
    stash_logger.info("Info message")
    log_lines = read_log_file(log_dir, "stash.log")
    assert any("Info message" in line for line in log_lines)
    assert any("[INFO]" in line for line in log_lines)

    # DEBUG level should be filtered out by default
    stash_logger.debug("Debug message")
    log_lines = read_log_file(log_dir, "stash.log")
    assert not any("Debug message" in line for line in log_lines)

    # With debug enabled, DEBUG should appear
    set_debug_enabled(True)
    try:
        stash_logger.debug("Debug message with debug enabled")
        log_lines = read_log_file(log_dir, "stash.log")
        assert any("Debug message with debug enabled" in line for line in log_lines)
        assert any("[DEBUG]" in line for line in log_lines)
    finally:
        set_debug_enabled(False)


def test_trace_logger_output(config, log_dir):
    """Test that trace_logger writes to the correct file only when enabled."""
    # TRACE level should be filtered out by default
    trace_logger.trace("Trace message")
    log_lines = read_log_file(log_dir, "trace.log")
    assert not any("Trace message" in line for line in log_lines)

    # With trace enabled, TRACE should appear
    config.trace = True
    init_logging_config(config)
    try:
        trace_logger.trace("Trace message with trace enabled")
        log_lines = read_log_file(log_dir, "trace.log")
        assert any("Trace message with trace enabled" in line for line in log_lines)
        assert any("[TRACE]" in line for line in log_lines)
    finally:
        config.trace = False
        init_logging_config(config)


def test_log_file_rotation(config, log_dir):
    """Test that log files are rotated correctly."""
    # Instead of actually writing huge files, let's mock the rotation

    # Create a base log file
    log_path = log_dir / "fansly_downloader_ng.log"
    with open(log_path, "w") as f:
        f.write("Test log content\n")

    # Create a rotated log file
    rotated_path = log_dir / "fansly_downloader_ng.log.1.gz"
    with open(rotated_path, "w") as f:
        f.write("Rotated log content\n")

    # Check that we have the expected files
    log_files = list(log_dir.glob("fansly_downloader_ng.log*"))
    assert len(log_files) > 1
    assert any(f.name.endswith(".gz") for f in log_files)


def test_debug_mode_all_loggers(config, log_dir):
    """Test that debug mode affects all non-trace loggers."""
    set_debug_enabled(True)
    try:
        # All non-trace loggers should output DEBUG
        textio_logger.debug("Debug textio")
        json_logger.debug("Debug json")
        stash_logger.debug("Debug stash")
        db_logger.debug("Debug db")

        # Check each log file
        assert any(
            "Debug textio" in line
            for line in read_log_file(log_dir, "fansly_downloader_ng.log")
        )
        assert any(
            "Debug json" in line
            for line in read_log_file(log_dir, "fansly_downloader_ng_json.log")
        )
        assert any(
            "Debug stash" in line for line in read_log_file(log_dir, "stash.log")
        )
        assert any(
            "Debug db" in line for line in read_log_file(log_dir, "sqlalchemy.log")
        )

        # trace_logger should still be silent
        trace_logger.trace("Trace message")
        assert not any(
            "Trace message" in line for line in read_log_file(log_dir, "trace.log")
        )
    finally:
        set_debug_enabled(False)


def test_trace_mode_only_affects_trace_logger(config, log_dir):
    """Test that trace mode only affects trace_logger."""
    config.trace = True
    init_logging_config(config)
    try:
        # trace_logger should output TRACE
        trace_logger.trace("Trace message")
        assert any(
            "Trace message" in line for line in read_log_file(log_dir, "trace.log")
        )

        # Other loggers should still filter out DEBUG
        textio_logger.debug("Debug textio")
        json_logger.debug("Debug json")
        stash_logger.debug("Debug stash")
        db_logger.debug("Debug db")

        # Check each log file
        assert not any(
            "Debug" in line
            for line in read_log_file(log_dir, "fansly_downloader_ng.log")
        )
        assert not any(
            "Debug" in line
            for line in read_log_file(log_dir, "fansly_downloader_ng_json.log")
        )
        assert not any("Debug" in line for line in read_log_file(log_dir, "stash.log"))
        assert not any(
            "Debug" in line for line in read_log_file(log_dir, "sqlalchemy.log")
        )
    finally:
        config.trace = False
        init_logging_config(config)


def test_console_output(config, capsys):
    """Test that only textio and stash loggers write to console."""
    # textio_logger should write to console
    textio_logger.info("Textio console message")
    captured = capsys.readouterr()
    assert "Textio console message" in captured.out

    # stash_logger should write to console
    stash_logger.info("Stash console message")
    captured = capsys.readouterr()
    assert "Stash console message" in captured.out

    # json_logger should NOT write to console
    json_logger.info("Json NO console message")
    captured = capsys.readouterr()
    assert "Json NO console message" not in captured.out

    # db_logger should NOT write to console
    db_logger.info("DB NO console message")
    captured = capsys.readouterr()
    assert "DB NO console message" not in captured.out

    # trace_logger should NOT write to console even when enabled
    config.trace = True
    init_logging_config(config)
    try:
        trace_logger.trace("Trace NO console message")
        captured = capsys.readouterr()
        assert "Trace NO console message" not in captured.out
    finally:
        config.trace = False
        init_logging_config(config)


def test_console_level_format(config, capsys):
    """Test that console output shows level names correctly."""
    textio_logger.info("Info level message")
    captured = capsys.readouterr()
    assert "INFO" in captured.out
    assert "Level 20" not in captured.out

    stash_logger.warning("Warning level message")
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "Level 30" not in captured.out

    set_debug_enabled(True)
    try:
        textio_logger.debug("Debug level message")
        captured = capsys.readouterr()
        assert "DEBUG" in captured.out
        assert "Level 10" not in captured.out
    finally:
        set_debug_enabled(False)


def test_trace_logger_errors(config):
    """Test that trace_logger raises InvalidTraceLogError for non-TRACE levels."""
    # Each of these should raise InvalidTraceLogError
    for level_func in [
        trace_logger.debug,
        trace_logger.info,
        trace_logger.success,
        trace_logger.warning,
        trace_logger.error,
        trace_logger.critical,
    ]:
        with pytest.raises(InvalidTraceLogError) as exc_info:
            level_func("This should fail")
        # Error message should mention the attempted level
        level_name = level_func.__name__.upper()
        assert f"got {level_name}" in str(exc_info.value)

    # TRACE level should work (but be filtered out since trace=False)
    trace_logger.trace("This should work")

    # Even with debug=True, non-TRACE levels should still raise
    set_debug_enabled(True)
    try:
        with pytest.raises(InvalidTraceLogError) as exc_info:
            trace_logger.debug("This should still fail")
        assert "got DEBUG" in str(exc_info.value)
    finally:
        set_debug_enabled(False)


def test_custom_log_levels(config, log_dir):
    """Test that custom log levels in config are respected."""
    config.log_levels = {
        "textio": "WARNING",
        "json": "ERROR",
        "stash_console": "INFO",
        "stash_file": "DEBUG",
        "sqlalchemy": "INFO",
    }
    init_logging_config(config)
    try:
        # Test each logger at various levels
        textio_logger.info("Info textio")  # Should be filtered
        textio_logger.warning("Warning textio")  # Should appear
        json_logger.warning("Warning json")  # Should be filtered
        json_logger.error("Error json")  # Should appear
        stash_logger.info("Info stash")  # Should appear
        db_logger.info("Info db")  # Should appear

        # Check log files
        textio_lines = read_log_file(log_dir, "fansly_downloader_ng.log")
        assert not any("Info textio" in line for line in textio_lines)
        assert any("Warning textio" in line for line in textio_lines)

        json_lines = read_log_file(log_dir, "fansly_downloader_ng_json.log")
        assert not any("Warning json" in line for line in json_lines)
        assert any("Error json" in line for line in json_lines)

        stash_lines = read_log_file(log_dir, "stash.log")
        assert any("Info stash" in line for line in stash_lines)

        db_lines = read_log_file(log_dir, "sqlalchemy.log")
        assert any("Info db" in line for line in db_lines)
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
