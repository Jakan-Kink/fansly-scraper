"""Test logging configuration."""

import logging
import os
import sys
from pathlib import Path

import pytest
from loguru import logger

import config.logging as cfg_logging
import helpers.rich_progress
from config.fanslyconfig import FanslyConfig
from config.logging import (
    _LEVEL_VALUES,
    InterceptHandler,
    SQLAlchemyInterceptHandler,
    _configure_sqlalchemy_logging,
    _configure_warnings_capture,
    _early_sqlalchemy_suppression,
    _trace_level_only,
    db_logger,
    get_log_level,
    init_logging_config,
    set_debug_enabled,
    stash_logger,
    textio_logger,
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
            exc_info = sys.exc_info()
            record = logging.LogRecord(
                "test", logging.ERROR, "", 0, "error msg", (), exc_info
            )
            handler.emit(record)
            # exc_info should be cleared on the record
            assert record.exc_info is None

    def test_emit_with_unregistered_level_falls_back_to_levelno(self):
        """Unknown level name → ValueError → str(levelno) fallback."""
        handler = InterceptHandler()
        # levelname auto-resolves to "Level 9999" — not in loguru's registry
        record = logging.LogRecord("test", 9999, "", 0, "weird", (), None)
        handler.emit(record)  # should not raise


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

    def test_emit_with_unregistered_level_falls_back_to_levelno(self):
        """Unknown level name in SA path → ValueError → str(levelno) fallback."""
        handler = SQLAlchemyInterceptHandler()
        record = logging.LogRecord("sqlalchemy.engine", 9999, "", 0, "weird", (), None)
        handler.emit(record)  # should not raise

    def test_emit_with_exception_clears_exc_info(self):
        """SA handler clears record.exc_info after emit (matches InterceptHandler)."""
        handler = SQLAlchemyInterceptHandler()
        try:
            raise ValueError("sa error")
        except ValueError:
            exc_info = sys.exc_info()
            record = logging.LogRecord(
                "sqlalchemy.engine", logging.ERROR, "", 0, "boom", (), exc_info
            )
            handler.emit(record)
            assert record.exc_info is None


# -- InterceptHandler._select_target (replaces _auto_bind_logger) --


def _make_log_record(
    name: str,
    *,
    pathname: str = "",
    msg: str = "test",
    args: tuple = (),
) -> logging.LogRecord:
    """Build a minimal LogRecord for routing tests."""
    return logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname=pathname,
        lineno=0,
        msg=msg,
        args=args,
        exc_info=None,
    )


class TestInterceptHandlerRouting:
    """Cover InterceptHandler._select_target — replaces the dead _auto_bind_logger.

    The old _auto_bind_logger was supposed to mutate record["extra"]["logger"]
    via logger.patch, but that was a no-op (loguru's .patch returns a NEW
    logger; the global was never mutated). Routing now happens inside
    InterceptHandler.emit which dispatches to the correct bound loguru target.
    """

    def test_sqlalchemy_record_routes_to_db_logger(self):
        record = _make_log_record("sqlalchemy.engine")
        assert InterceptHandler._select_target(record) is db_logger

    def test_asyncpg_record_routes_to_db_logger(self):
        record = _make_log_record("asyncpg")
        assert InterceptHandler._select_target(record) is db_logger

    def test_alembic_migration_routes_to_db_logger(self):
        record = _make_log_record("alembic.runtime.migration")
        assert InterceptHandler._select_target(record) is db_logger

    def test_stash_graphql_client_routes_to_stash_logger(self):
        record = _make_log_record("stash_graphql_client.types.scene")
        assert InterceptHandler._select_target(record) is stash_logger

    def test_warning_from_sgc_module_routes_to_stash_logger(self):
        """py.warnings record from a stash_graphql_client/* module → stash sink.

        captureWarnings sends the formatted warning string AS the log
        message — `record.pathname` is always `warnings.py` (where
        `_showwarning` lives), so routing keys off the message content
        (which begins with the warning's source filename:lineno).
        """
        record = _make_log_record(
            "py.warnings",
            msg="%s",
            args=("stash_graphql_client/types/scene.py:42: UserWarning: from sgc\n",),
        )
        assert InterceptHandler._select_target(record) is stash_logger

    def test_warning_from_other_module_routes_to_textio_logger(self):
        record = _make_log_record(
            "py.warnings",
            msg="%s",
            args=("some/other/module.py:1: UserWarning: from other module\n",),
        )
        assert InterceptHandler._select_target(record) is textio_logger

    def test_default_routes_to_textio_logger(self):
        record = _make_log_record("some.unrelated.module")
        assert InterceptHandler._select_target(record) is textio_logger


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

    def test_setup_handlers_falls_back_when_rich_handler_fails(
        self, tmp_path, monkeypatch
    ):
        """RichHandler init failure → loud fallback to plain stdout."""

        def boom(**kwargs):
            raise RuntimeError("rich init failed")

        monkeypatch.setattr(helpers.rich_progress, "create_rich_handler", boom)

        original_cwd = Path.cwd()
        os.chdir(tmp_path)
        try:
            config = FanslyConfig(program_version="test")
            init_logging_config(config)  # exercises the except branch
            assert (tmp_path / "logs").is_dir()
        finally:
            logger.remove()
            os.chdir(original_cwd)

    def test_init_logging_config_with_none_config(self, tmp_path):
        """init_logging_config(None) skips the debug-set branch."""
        original_cwd = Path.cwd()
        original_config = cfg_logging._config
        os.chdir(tmp_path)
        try:
            init_logging_config(None)  # should not raise
            assert (tmp_path / "logs").is_dir()
        finally:
            cfg_logging._config = original_config  # autouse teardown wants FanslyConfig
            logger.remove()
            os.chdir(original_cwd)

    def test_get_log_level_returns_default_when_config_none(self):
        """get_log_level uses default arg when _config is None."""
        original = cfg_logging._config
        cfg_logging._config = None
        try:
            level = get_log_level("textio", "INFO")
            # max(INFO, DEBUG) = INFO
            assert level == _LEVEL_VALUES["INFO"]
        finally:
            cfg_logging._config = original

    def test_early_suppression_skips_when_last_resort_is_none(self):
        """When logging.lastResort is None, suppression skips reassignment."""
        original = logging.lastResort
        logging.lastResort = None
        try:
            _early_sqlalchemy_suppression()  # should not raise; takes false branch
        finally:
            logging.lastResort = original

    def test_configure_sqlalchemy_logging_skips_when_last_resort_none(self, tmp_path):
        """_configure_sqlalchemy_logging takes false branch when lastResort is None."""
        original_cwd = Path.cwd()
        os.chdir(tmp_path)
        original_lr = logging.lastResort
        logging.lastResort = None
        try:
            config = FanslyConfig(program_version="test")
            init_logging_config(config)
            _configure_sqlalchemy_logging()
        finally:
            logging.lastResort = original_lr
            logger.remove()
            os.chdir(original_cwd)

    def test_configure_sqlalchemy_logging_with_error_level_skips_intercept(
        self, tmp_path
    ):
        """When sqlalchemy log level >= ERROR, the InterceptHandler is not added."""
        original_cwd = Path.cwd()
        os.chdir(tmp_path)
        try:
            config = FanslyConfig(program_version="test")
            config.log_levels = {"sqlalchemy": "ERROR"}
            init_logging_config(config)
            _configure_sqlalchemy_logging()

            sa_logger = logging.getLogger("sqlalchemy.engine")
            intercept_present = any(
                isinstance(h, SQLAlchemyInterceptHandler) for h in sa_logger.handlers
            )
            assert not intercept_present
        finally:
            logger.remove()
            os.chdir(original_cwd)

    def test_configure_warnings_capture_wires_py_warnings_handler(self):
        """_configure_warnings_capture wires captureWarnings + py.warnings handler.

        Without this, warnings.warn() writes raw text to sys.stderr instead
        of reaching loguru sinks — SGC's StashUnmappedFieldWarning would
        bypass both the rich console and every log file.
        """
        # Save state for restoration
        py_warnings_logger = logging.getLogger("py.warnings")
        original_handlers = py_warnings_logger.handlers[:]
        original_propagate = py_warnings_logger.propagate
        original_level = py_warnings_logger.level

        try:
            _configure_warnings_capture()

            # captureWarnings should be enabled
            assert logging._warnings_showwarning is not None  # type: ignore[attr-defined]

            # py.warnings logger should have exactly one InterceptHandler
            handlers = [
                h
                for h in py_warnings_logger.handlers
                if isinstance(h, InterceptHandler)
            ]
            assert len(handlers) == 1, (
                f"Expected exactly 1 InterceptHandler, got {len(handlers)}"
            )
            assert py_warnings_logger.propagate is False
            assert py_warnings_logger.level == logging.WARNING
        finally:
            py_warnings_logger.handlers[:] = original_handlers
            py_warnings_logger.propagate = original_propagate
            py_warnings_logger.setLevel(original_level)
            logging.captureWarnings(False)

    def test_format_record_escapes_loguru_tags_in_message(self, tmp_path, capsys):
        """Messages containing `<module>` (and similar) must not crash colorizer.

        Regression: a real ImportError traceback contains frame names like
        `<module>`, `<listcomp>`, `<genexpr>`. loguru re-parses callable
        formatter output to strip color tags even when colorize=False, so
        any unescaped `<` in the embedded message previously raised
        `ValueError: Tag "<module>" does not correspond to any known color
        directive` from Colorizer.prepare_stripped_format.

        Caveat: loguru's emit() catches exceptions and routes them through
        its error interceptor (which prints the `--- Logging error ...`
        envelope to stderr) rather than re-raising. So the assertion here
        must be on stderr, not on whether an exception propagated.
        """
        original_cwd = Path.cwd()
        os.chdir(tmp_path)
        try:
            config = FanslyConfig(program_version="test")
            init_logging_config(config)

            # Real-world traceback fragments that previously broke the sink.
            for payload in (
                'File ".../foo.py", line 1, in <module>',
                "list comprehension at <listcomp>",
                "generator expression at <genexpr>",
                "tag-soup with <unknown> and </closing>",
            ):
                textio_logger.error(payload)

            # The bug's user-visible symptom is loguru printing its error
            # envelope to stderr — that's what the original transcript
            # showed. Assert it doesn't appear.
            captured = capsys.readouterr()
            assert "Logging error in Loguru Handler" not in captured.err, (
                "loguru emitted its error envelope — colorizer likely "
                "tripped on an unescaped `<` in the formatted message."
            )
            assert "does not correspond to any known color directive" not in (
                captured.err
            ), "Colorizer parse failure leaked into stderr."
        finally:
            logger.remove()
            os.chdir(original_cwd)
