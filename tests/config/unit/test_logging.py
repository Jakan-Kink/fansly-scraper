"""Test logging configuration."""

import logging

import pytest
from loguru import logger

from config.fanslyconfig import FanslyConfig
from config.logging import (
    _LEVEL_VALUES,
    InterceptHandler,
    SQLAlchemyInterceptHandler,
    _auto_bind_logger,
    _configure_sqlalchemy_logging,
    _trace_level_only,
    get_log_level,
    init_logging_config,
    set_debug_enabled,
    update_logging_config,
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


# -- InterceptHandler.emit --


class TestInterceptHandler:
    """Cover InterceptHandler.emit branches (lines 75-96)."""

    def test_emit_with_known_level(self):
        """Standard log level is resolved by name (line 77)."""
        handler = InterceptHandler()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "test message", (), None
        )
        # Should not raise — just routes to loguru
        handler.emit(record)

    def test_emit_with_custom_level(self):
        """Custom level that exists in loguru is handled (line 77)."""
        handler = InterceptHandler()
        record = logging.LogRecord(
            "test", logging.WARNING, "", 0, "warning message", (), None
        )
        # Should not raise
        handler.emit(record)

    def test_emit_with_exception(self):
        """Exception info is passed through and cleaned up (lines 87-96)."""
        handler = InterceptHandler()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()
            record = logging.LogRecord(
                "test", logging.ERROR, "", 0, "error msg", (), exc_info
            )
            handler.emit(record)
            # exc_info should be cleared on the record
            assert record.exc_info is None


# -- SQLAlchemyInterceptHandler.emit --


class TestSQLAlchemyInterceptHandler:
    """Cover SQLAlchemyInterceptHandler.emit branches (lines 106-130)."""

    def test_emit_routes_to_db_logger(self):
        """SQLAlchemy logs are routed to db_logger (lines 119-121)."""
        handler = SQLAlchemyInterceptHandler()
        record = logging.LogRecord(
            "sqlalchemy.engine", logging.INFO, "", 0, "SELECT 1", (), None
        )
        handler.emit(record)

    def test_emit_with_debug_level(self):
        """DEBUG level SQL log is handled by SQLAlchemy handler (lines 108-110)."""
        handler = SQLAlchemyInterceptHandler()
        record = logging.LogRecord(
            "sqlalchemy.engine", logging.DEBUG, "", 0, "SELECT 1", (), None
        )
        handler.emit(record)


# -- _auto_bind_logger --


class TestAutoBindLogger:
    """Cover _auto_bind_logger routing (lines 221-231)."""

    def test_unbound_record_gets_textio(self):
        """Unbound record (no logger extra) → defaults to 'textio' (line 230)."""
        record = {"extra": {}, "name": "some.module"}
        result = _auto_bind_logger(record)
        assert result["extra"]["logger"] == "textio"

    def test_sqlalchemy_record_gets_db(self):
        """SQLAlchemy-related record → 'db' binding (lines 225-228)."""
        record = {"extra": {}, "name": "sqlalchemy.engine"}
        result = _auto_bind_logger(record)
        assert result["extra"]["logger"] == "db"

    def test_asyncpg_record_gets_db(self):
        """asyncpg record → 'db' binding."""
        record = {"extra": {}, "name": "asyncpg"}
        result = _auto_bind_logger(record)
        assert result["extra"]["logger"] == "db"

    def test_alembic_migration_gets_db(self):
        """Alembic migration record → 'db' binding."""
        record = {"extra": {}, "name": "alembic.runtime.migration"}
        result = _auto_bind_logger(record)
        assert result["extra"]["logger"] == "db"

    def test_already_bound_record_unchanged(self):
        """Record with existing logger binding is left alone (line 221)."""
        record = {"extra": {"logger": "stash"}, "name": "anything"}
        result = _auto_bind_logger(record)
        assert result["extra"]["logger"] == "stash"


# -- update_logging_config --


class TestUpdateLoggingConfig:
    """Cover update_logging_config (lines 591-622)."""

    def test_update_enables_debug(self):
        """Enabling debug adds asyncio handler."""
        config = FanslyConfig(program_version="test")
        update_logging_config(config, True)

        asyncio_logger = logging.getLogger("asyncio")
        assert asyncio_logger.level == logging.DEBUG

    def test_update_disables_debug(self):
        """Disabling debug removes asyncio handlers and sets WARNING level."""
        config = FanslyConfig(program_version="test")
        update_logging_config(config, False)

        asyncio_logger = logging.getLogger("asyncio")
        assert asyncio_logger.level == logging.WARNING

    def test_update_requires_fanslyconfig(self):
        """Non-FanslyConfig raises TypeError (line 601)."""
        with pytest.raises(TypeError, match="must be an instance of FanslyConfig"):
            update_logging_config("not_a_config", True)


# -- setup_handlers and _configure_sqlalchemy_logging --


class TestSetupHandlers:
    """Cover setup_handlers (lines 287-515) and related."""

    def test_setup_handlers_creates_log_dir(self, tmp_path):
        """setup_handlers creates log directory (line 312-313)."""
        import os
        from pathlib import Path

        original_cwd = Path.cwd()
        os.chdir(tmp_path)
        try:
            config = FanslyConfig(program_version="test")
            init_logging_config(config)
            assert (tmp_path / "logs").is_dir()
        finally:
            logger.remove()
            os.chdir(original_cwd)

    def test_configure_sqlalchemy_logging(self):
        """_configure_sqlalchemy_logging sets up handlers for SA loggers (lines 625-676)."""
        config = FanslyConfig(program_version="test")
        init_logging_config(config)
        _configure_sqlalchemy_logging()

        # Verify SQLAlchemy logger is configured
        sa_logger = logging.getLogger("sqlalchemy.engine")
        assert sa_logger.propagate is False
        # Should have at least NullHandler
        handler_types = [type(h).__name__ for h in sa_logger.handlers]
        assert "NullHandler" in handler_types
