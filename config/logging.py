"""Centralized logging configuration and control.

This module provides centralized logging configuration for all components:
1. textio_logger - For user-facing console output
2. json_logger - For structured JSON logging
3. stash_logger - For Stash-related operations
4. db_logger - For database operations

Each logger is pre-configured with appropriate handlers and levels.
Other modules should import and use these loggers rather than
creating their own handlers.
"""

import codecs
import contextlib
import logging
import os
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from errors import InvalidTraceLogError


if sys.platform == "win32":
    # Set console mode to handle UTF-8
    try:
        import ctypes

        # Enable VIRTUAL_TERMINAL_PROCESSING for ANSI support
        kernel32 = ctypes.WinDLL("kernel32")
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        # Enable VIRTUAL_TERMINAL_PROCESSING (0x0004)
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)

        # Set UTF-8 codepage
        kernel32.SetConsoleCP(65001)  # CP_UTF8
        kernel32.SetConsoleOutputCP(65001)  # CP_UTF8

        # Configure stdout/stderr
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # Fallback for older Python versions or if Windows API calls fail
        if hasattr(sys.stdout, "buffer"):
            sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")
            sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, errors="replace")

# Global configuration
_config = None
_debug_enabled = False

# Log file names
DEFAULT_LOG_FILE = "fansly_downloader_ng.log"
DEFAULT_JSON_LOG_FILE = "fansly_downloader_ng_json.log"
DEFAULT_STASH_LOG_FILE = "stash.log"
DEFAULT_DB_LOG_FILE = "sqlalchemy.log"
DEFAULT_WEBSOCKET_LOG_FILE = "websocket.log"


class InterceptHandler(logging.Handler):
    """Intercepts standard logging and redirects to loguru.

    This handler can be used to capture logs from libraries that use
    standard logging and redirect them to loguru. Example:

    ```python
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    ```
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = str(record.levelno)

        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back  # type: ignore[assignment]
            depth += 1

        exc_info = record.exc_info
        try:
            logger.opt(depth=depth, exception=exc_info).log(level, record.getMessage())
        finally:
            # Explicitly clear references to prevent ResourceWarning
            # from coverage.py's SQLite connection being held in call stack
            del frame
            if exc_info:
                del exc_info
            record.exc_info = None


class SQLAlchemyInterceptHandler(logging.Handler):
    """Specialized handler for SQLAlchemy logging with proper logger binding.

    Routes SQLAlchemy logs specifically to the database logger context,
    ensuring they appear in sqlalchemy.log and are properly tagged.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = str(record.levelno)

        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back  # type: ignore[assignment]
            depth += 1

        exc_info = record.exc_info
        try:
            # Use db_logger to ensure proper logger binding for SQLAlchemy
            db_logger.opt(depth=depth, exception=exc_info).log(
                level, record.getMessage()
            )
        finally:
            # Explicitly clear references to prevent ResourceWarning
            # from coverage.py's SQLite connection being held in call stack
            del frame
            if exc_info:
                del exc_info
            record.exc_info = None


# Standard level values (loguru's default scale)
_LEVEL_VALUES = {
    "TRACE": 5,  # Detailed information for diagnostics
    "DEBUG": 10,  # Debug information
    "INFO": 20,  # Normal information
    "SUCCESS": 25,  # Successful operation
    "WARNING": 30,  # Warning messages
    "ERROR": 40,  # Error messages
    "CRITICAL": 50,  # Critical errors
}

# Custom level definitions with colors, mapped to standard level numbers.
#
# Each level carries TWO color strings because loguru and Rich disagree on
# naming conventions:
#
#   * ``color`` is loguru markup with hyphens and angle brackets
#     (e.g. ``<light-magenta>``, ``<red><bold>``).
#   * ``rich_style`` is a Rich style string with underscores
#     (e.g. ``bright_magenta``, ``bold red``).
#
# Storing them as separate fields avoids runtime translation and lets the
# palette script (``scripts/color_palette.py``) show both forms side by
# side. Keep them semantically equivalent — the goal is for a log line
# to look identical under loguru's direct ANSI path and under the
# Rich-integrated path. See the script for the full palette reference.
_CUSTOM_LEVELS = {
    "CONFIG": {
        "name": "CONFIG",
        "no": _LEVEL_VALUES["INFO"],  # 20 (INFO)
        "color": "<magenta>",
        "rich_style": "magenta",
        "icon": "🔧",
    },
    "DEBUG": {
        "name": "DEBUG",
        "no": _LEVEL_VALUES["DEBUG"],  # 10 (DEBUG)
        "color": "<red>",
        "rich_style": "red",
        "icon": "🔍",
    },
    "INFO": {
        "name": "INFO",
        "no": _LEVEL_VALUES["INFO"],  # 20 (INFO)
        "color": "<blue>",
        "rich_style": "blue",
        "icon": "ℹ️",  # noqa: RUF001
    },
    "ERROR": {
        "name": "ERROR",
        "no": _LEVEL_VALUES["ERROR"],  # 40 (ERROR)
        "color": "<red><bold>",
        "rich_style": "bold red",
        "icon": "❌",
    },
    "WARNING": {
        "name": "WARNING",
        "no": _LEVEL_VALUES["WARNING"],  # 30 (WARNING)
        "color": "<yellow>",
        "rich_style": "yellow",
        "icon": "⚠️",
    },
    "INFO_HIGHLIGHT": {
        "name": "-INFO-",
        "no": _LEVEL_VALUES["INFO"],  # 20 (INFO)
        "color": "<cyan><bold>",
        "rich_style": "bold cyan",
        "icon": "✨",
    },
    "UPDATE": {
        "name": "UPDATE",
        "no": _LEVEL_VALUES["SUCCESS"],  # 25 (SUCCESS)
        "color": "<green>",
        "rich_style": "green",
        "icon": "📦",
    },
}

# Remove default handler
logger.remove()

# Register (or update) custom levels with loguru.
#
# Loguru refuses ``logger.level(name, no=X, ...)`` whenever the level
# already exists — even when X matches the built-in's existing ``no`` —
# with ``ValueError: Level 'DEBUG' already exists, you can't update its
# severity no``. We therefore split the call into two paths:
#
#   * New level names (CONFIG, -INFO-, UPDATE) → create with full params.
#   * Existing built-ins (DEBUG/INFO/WARNING/ERROR/SUCCESS/CRITICAL/TRACE)
#     → omit ``no`` so loguru treats it as an update of color+icon only.
#
# Before this split, a blanket ``contextlib.suppress(ValueError)`` hid
# the raise and every built-in level kept loguru's default color instead
# of the one declared in _CUSTOM_LEVELS, which is why DEBUG rendered
# blue in the console even though it was marked ``<light-red>`` here.
for level_data in _CUSTOM_LEVELS.values():
    name = level_data["name"]
    try:
        logger.level(name)  # arg-less call: exists? returns it; missing? ValueError
        level_exists = True
    except ValueError:
        level_exists = False

    if level_exists:
        # Update path — only color+icon may change; ``no`` is immutable.
        logger.level(
            name,
            color=level_data["color"],
            icon=level_data["icon"],
        )
    else:
        # Create path — first registration of this name.
        logger.level(
            name,
            no=level_data["no"],
            color=level_data["color"],
            icon=level_data["icon"],
        )


# Pre-configured loggers with extra fields
textio_logger = logger.bind(logger="textio")
json_logger = logger.bind(logger="json")
stash_logger = logger.bind(logger="stash")
db_logger = logger.bind(logger="db")
websocket_logger = logger.bind(logger="websocket")


def _auto_bind_logger(record: Any) -> Any:
    """Automatically bind unbound logger calls to appropriate context.

    Routes logs to appropriate logger context based on their source:
    - SQLAlchemy loggers -> "db"
    - Everything else -> "textio"

    This ensures proper routing of echo=True SQLAlchemy logs while
    maintaining textio binding for direct loguru imports.
    """
    if "logger" not in record["extra"]:
        logger_name = record.get("name", "")
        if (
            logger_name.startswith(("sqlalchemy.", "asyncpg"))
            or logger_name == "alembic.runtime.migration"
        ):
            record["extra"]["logger"] = "db"
        else:
            record["extra"]["logger"] = "textio"
    return record


# Patch global logger to auto-bind imports to appropriate context
logger.patch(_auto_bind_logger)


# Configure SQLAlchemy logging immediately at module import time
# This prevents any early console output before init_logging_config is called
def _early_sqlalchemy_suppression() -> None:
    """Suppress SQLAlchemy console output as early as possible."""
    sqlalchemy_loggers = [
        "sqlalchemy.engine",
        "sqlalchemy.engine.Engine",
        "sqlalchemy.pool",
        "sqlalchemy.orm",
        "sqlalchemy.dialects",
        "sqlalchemy.engine.stat",
        "asyncpg",
        "alembic.runtime.migration",
        "alembic",
        "alembic.env",
    ]

    # Disable lastResort handler immediately
    if hasattr(logging, "lastResort") and logging.lastResort:
        logging.lastResort = logging.NullHandler()

    for logger_name in sqlalchemy_loggers:
        sql_logger = logging.getLogger(logger_name)
        sql_logger.handlers.clear()
        sql_logger.propagate = False
        # Set to CRITICAL to suppress all output initially
        sql_logger.setLevel(logging.CRITICAL)
        sql_logger.addHandler(logging.NullHandler())


_early_sqlalchemy_suppression()


def _trace_level_only(record: Any) -> bool:
    """Filter to ensure trace_logger only receives TRACE level messages."""
    if record["level"].no != _LEVEL_VALUES["TRACE"]:
        raise InvalidTraceLogError(record["level"].name)
    return True


# For very detailed logging
trace_logger = logger.bind(logger="trace").patch(_trace_level_only)

# Handler IDs for cleanup
_handler_ids = {}  # {id: (handler, file_handler)}


def setup_handlers() -> None:
    """Set up all logging handlers.

    This function configures all loggers with appropriate handlers:
    1. textio_logger - Console output with colors and formatting
    2. json_logger - JSON-formatted logs with rotation
    3. stash_logger - Stash-specific logs
    4. db_logger - Database operation logs
    """
    # Remove any existing handlers
    for handler_id, (_handler, file_handler) in list(_handler_ids.items()):
        try:
            logger.remove(handler_id)
            if file_handler:
                with contextlib.suppress(Exception):
                    file_handler.close()
        except ValueError:
            pass  # Handler already removed

    # Clear all handlers
    _handler_ids.clear()

    # Create logs directory
    log_dir = Path.cwd() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Import handler here to avoid circular imports
    from textio.logging import SizeTimeRotatingHandler

    # Common enqueue settings for all handlers
    enqueue_args = (
        {"enqueue": False} if os.getenv("TESTING") == "1" else {"enqueue": True}
    )

    # 1. TextIO Console Handler with SQL filtering
    def textio_filter(record: Any) -> bool:
        """Filter for textio console handler - exclude SQL logs."""
        extra = record.get("extra", {})
        logger_type = extra.get("logger")

        # Only show textio logs, exclude db logs from console
        if logger_type == "textio":
            return True
        if logger_type == "db":
            return False

        # For unbound logs, check if they're SQLAlchemy related
        logger_name = record.get("name", "")
        if (
            logger_name.startswith(("sqlalchemy.", "asyncpg"))
            or logger_name == "alembic.runtime.migration"
        ):
            return False

        return logger_type == "textio"

    # Use RichHandler via shared console so log output coordinates with
    # the ProgressManager's Live display (prevents striped/duplicated bars).
    # Falls back to sys.stdout if Rich integration is unavailable.
    format_record: Any
    console_sink: Any
    use_colorize: bool
    try:
        from helpers.rich_progress import create_rich_handler

        # Rich's Theme/Style parser wants underscore-separated names
        # (e.g. "bright_magenta"), not loguru's hyphen form. _CUSTOM_LEVELS
        # carries both variants — pick the Rich one here so Theme() never
        # falls over on a stray hyphen.
        level_styles = {
            str(data["name"]): str(data["rich_style"])
            for _name, data in _CUSTOM_LEVELS.items()
            if isinstance(data.get("rich_style"), str) and isinstance(data["name"], str)
        }
        console_sink = create_rich_handler(level_styles=level_styles)

        def format_record(record: Any) -> str:
            level_name = record["level"].name
            level_data = next(
                (
                    data
                    for _n, data in _CUSTOM_LEVELS.items()
                    if data["name"] == level_name
                ),
                None,
            )
            icon = level_data["icon"] if level_data else "●"
            safe_msg = str(record["message"]).replace("{", "{{").replace("}", "}}")
            return f"{icon} {safe_msg}"

        # RichHandler handles its own coloring — disable loguru's ANSI injection
        use_colorize = False
    except Exception as exc:
        # Fallback to raw stdout if Rich integration fails for any reason.
        # We must surface *why* — raw stdout bypasses the ProgressManager's
        # Live display and log lines collide with progress bars mid-frame,
        # producing the striped/duplicated-bar symptom in terminals. A
        # silent fallback hides this until someone reads the output.
        import traceback as _tb

        print(
            f"[logging] RichHandler setup failed — falling back to plain stdout "
            f"(bars will jitter). Cause: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        _tb.print_exc(file=sys.stderr)
        console_sink = sys.stdout
        format_record = "<level>{level.icon} {level.name:>8}</level> | <white>{time:HH:mm:ss.SS}</white> <level>|</level><light-white>| {message}</light-white>"
        use_colorize = True

    handler_id = logger.add(  # type: ignore[call-overload]
        sink=console_sink,
        format=format_record,
        level=get_log_level("textio", "INFO"),
        filter=textio_filter,
        colorize=use_colorize,
        **enqueue_args,
    )
    _handler_ids[handler_id] = (None, None)

    # 2. TextIO File Handler
    textio_file = log_dir / DEFAULT_LOG_FILE
    textio_handler = SizeTimeRotatingHandler(
        filename=str(textio_file),
        maxBytes=100 * 1024 * 1024,  # 100MB
        backupCount=5,
        when="h",
        interval=1,
        utc=True,
        compression="gz",
        keep_uncompressed=2,
        encoding="utf-8",
    )
    handler_id = logger.add(
        textio_handler.write,
        format="[{time:YYYY-MM-DD HH:mm:ss.SSS}] [{level.name:<8}] {name}:{function}:{line} - {message}",
        level=get_log_level("textio", "INFO"),
        filter=lambda record: record.get("extra", {}).get("logger") == "textio",
        backtrace=True,
        diagnose=True,
        **enqueue_args,
    )
    _handler_ids[handler_id] = (textio_handler, None)

    # 3. JSON File Handler
    json_file = log_dir / os.getenv("LOGURU_JSON_LOG_FILE", DEFAULT_JSON_LOG_FILE)
    json_handler = SizeTimeRotatingHandler(
        filename=str(json_file),
        maxBytes=100 * 1024 * 1024,  # 100MB
        backupCount=10,
        when="h",
        interval=1,
        utc=True,
        compression="gz",
        keep_uncompressed=2,
        encoding="utf-8",
    )
    handler_id = logger.add(
        json_handler.write,
        format="{level.icon}   {level.name:>8} | {time:HH:mm:ss.SS} || {message}",
        level=get_log_level("json", "INFO"),
        filter=lambda record: record.get("extra", {}).get("logger") == "json",
        backtrace=True,
        diagnose=True,
        **enqueue_args,
    )
    _handler_ids[handler_id] = (json_handler, None)

    # 4. Stash Console Handler — same shared console sink as textio
    handler_id = logger.add(  # type: ignore[call-overload]
        sink=console_sink,
        format=format_record,
        level=get_log_level("stash_console", "INFO"),
        colorize=use_colorize,
        filter=lambda record: record.get("extra", {}).get("logger") == "stash",
        **enqueue_args,
    )
    _handler_ids[handler_id] = (None, None)

    # 5. Stash File Handler
    stash_file = log_dir / DEFAULT_STASH_LOG_FILE
    stash_handler = SizeTimeRotatingHandler(
        filename=str(stash_file),
        maxBytes=100 * 1024 * 1024,
        backupCount=10,
        when="h",
        interval=1,
        utc=True,
        compression="gz",
        keep_uncompressed=2,
    )
    handler_id = logger.add(
        stash_handler.write,
        format="{level.icon}   {level.name:>8} | {time:HH:mm:ss.SS} || {message}",
        level=get_log_level("stash_file", "INFO"),
        filter=lambda record: record.get("extra", {}).get("logger") == "stash",
        **enqueue_args,
    )
    _handler_ids[handler_id] = (stash_handler, None)

    # 6. Database File Handler
    db_file = log_dir / DEFAULT_DB_LOG_FILE
    db_handler = SizeTimeRotatingHandler(
        filename=str(db_file),
        maxBytes=100 * 1024 * 1024,
        backupCount=20,
        when="h",
        interval=1,
        utc=True,
        compression="gz",
        keep_uncompressed=2,
    )
    # Add a special tag for debugging
    db_handler.handler.db_logger_name = "database_logger"
    handler_id = logger.add(
        db_handler.write,
        format="{level.icon}   {level.name:>8} | {time:HH:mm:ss.SS} || {message}",
        level=get_log_level("sqlalchemy", "INFO"),
        filter=lambda record: record.get("extra", {}).get("logger") == "db",
        **enqueue_args,
    )
    _handler_ids[handler_id] = (db_handler, None)

    # 7. Trace File Handler (for very detailed logging)
    trace_file = log_dir / "trace.log"
    trace_handler = SizeTimeRotatingHandler(
        filename=str(trace_file),
        maxBytes=100 * 1024 * 1024,  # 100MB
        backupCount=5,
        when="h",
        interval=1,
        utc=True,
        compression="gz",
        keep_uncompressed=2,
    )
    handler_id = logger.add(
        trace_handler.write,
        format="{level.icon}   {level.name:>8} | {time:HH:mm:ss.SSS} | {name}:{function}:{line} - {message}",
        level=get_log_level("trace", "TRACE"),  # Default to TRACE level
        filter=lambda record: record.get("extra", {}).get("logger", None) == "trace",
        **enqueue_args,
    )
    _handler_ids[handler_id] = (trace_handler, None)

    # 8. WebSocket File Handler — dedicated sink for api/websocket.py and
    # daemon WS event dispatch. Keeps frame-level traffic out of the main
    # log so operators can inspect WS activity without wading through
    # download/metadata noise.
    websocket_file = log_dir / DEFAULT_WEBSOCKET_LOG_FILE
    websocket_handler = SizeTimeRotatingHandler(
        filename=str(websocket_file),
        maxBytes=100 * 1024 * 1024,
        backupCount=10,
        when="h",
        interval=1,
        utc=True,
        compression="gz",
        keep_uncompressed=2,
    )
    handler_id = logger.add(
        websocket_handler.write,
        format="{level.icon}   {level.name:>8} | {time:HH:mm:ss.SS} || {name}:{function}:{line} - {message}",
        level=get_log_level("websocket", "INFO"),
        filter=lambda record: record.get("extra", {}).get("logger") == "websocket",
        **enqueue_args,
    )
    _handler_ids[handler_id] = (websocket_handler, None)


def init_logging_config(config: Any) -> None:
    """Initialize logging configuration."""
    global _config, _debug_enabled
    _config = config

    # Set debug mode based on config settings (important for IPython sessions)
    if config:
        # Check debug setting (trace is separate and only affects trace_logger)
        debug_enabled = config.debug
        _debug_enabled = debug_enabled

    # IMPORTANT: Set up handlers FIRST so db_logger has somewhere to write
    setup_handlers()

    # THEN configure SQLAlchemy logging to route to those handlers
    _configure_sqlalchemy_logging()


def set_debug_enabled(enabled: bool) -> None:
    """Set the global debug flag."""
    global _debug_enabled
    _debug_enabled = enabled
    update_logging_config(_config, enabled)


def get_log_level(logger_name: str, default: str = "INFO") -> int:
    """Get log level for a logger.

    Args:
        logger_name: Name of the logger (e.g., "textio", "stash_console", "sqlalchemy")
        default: Default level if config not set or logger not found

    Returns:
        Log level as integer (e.g., 10 for DEBUG, 20 for INFO)
        For trace_logger:
            - 5 (TRACE) if config.trace is True
            - 50 (CRITICAL) if config.trace is False (effectively disabled)
        For sqlalchemy logger:
            - 5 (TRACE) if config.trace is True (enables db_logger.trace())
            - Level from config or default otherwise
        For other loggers:
            - 10 (DEBUG) if debug mode is enabled
            - Level from config or default, but never below DEBUG
    """
    # Special handling for trace_logger
    if logger_name == "trace":
        # Only allow TRACE level when trace=True, otherwise effectively disable
        return (
            _LEVEL_VALUES["TRACE"]
            if (_config and _config.trace)
            else _LEVEL_VALUES["CRITICAL"]
        )

    # Special handling for sqlalchemy logger - allow TRACE level when trace is enabled
    if logger_name == "sqlalchemy" and _config and _config.trace:
        return _LEVEL_VALUES["TRACE"]
    # For sqlalchemy when trace is disabled, fall through to normal handling

    # Special handling for websocket logger - allow TRACE level when trace is enabled.
    # When config.trace is True, per-frame receive + ping/pong logs surface.
    # When False, falls through to the user's log_levels["websocket"] setting.
    if logger_name == "websocket" and _config and _config.trace:
        return _LEVEL_VALUES["TRACE"]

    # Force DEBUG level if debug mode is enabled (for non-trace loggers)
    if _debug_enabled:
        return _LEVEL_VALUES["DEBUG"]

    # Get level name from config or use default
    if _config is None:
        level_name = default
    else:
        level_name = _config.log_levels.get(logger_name, default)

    # Convert level name to integer and ensure minimum DEBUG level
    level = _LEVEL_VALUES[level_name.upper()]
    return max(level, _LEVEL_VALUES["DEBUG"])


def update_logging_config(config: Any, enabled: bool) -> None:
    """Update the logging configuration.

    Args:
        config: The FanslyConfig instance to use
        enabled: Whether debug mode should be enabled
    """
    from config.fanslyconfig import FanslyConfig

    if not isinstance(config, FanslyConfig):
        raise TypeError("config must be an instance of FanslyConfig")
    global _config, _debug_enabled
    _config = config
    _debug_enabled = enabled

    # Configure asyncio debug logging
    if enabled:
        asyncio_logger = logging.getLogger("asyncio")
        asyncio_logger.setLevel(logging.DEBUG)
        # Add handler to redirect to loguru
        asyncio_logger.addHandler(InterceptHandler())
    else:
        # Disable asyncio debug logging
        asyncio_logger = logging.getLogger("asyncio")
        asyncio_logger.setLevel(logging.WARNING)
        # Remove any existing handlers
        for handler in asyncio_logger.handlers[:]:
            asyncio_logger.removeHandler(handler)

    # Update handlers with new configuration (includes SQLAlchemy configuration)
    setup_handlers()
    _configure_sqlalchemy_logging()


def _configure_sqlalchemy_logging() -> None:
    """Configure SQLAlchemy module logging through loguru.

    Routes SQLAlchemy's built-in loggers through SQLAlchemyInterceptHandler
    to ensure proper db logger binding and file routing.
    """
    sqlalchemy_loggers = [
        "sqlalchemy.engine",  # SQL statements
        "sqlalchemy.engine.Engine",  # Engine-specific SQL statements (the one appearing in console)
        "sqlalchemy.pool",  # Connection pool
        "sqlalchemy.orm",  # ORM operations
        "sqlalchemy.dialects",  # Dialect-specific
        "sqlalchemy.engine.stat",  # Statistics and performance
        "asyncpg",  # AsyncPG database operations
        "alembic.runtime.migration",  # Alembic migrations
        "alembic",  # General alembic logging
        "alembic.env",  # Alembic environment
    ]

    # Disable the root logger's lastResort handler to prevent console fallback
    if hasattr(logging, "lastResort") and logging.lastResort:
        logging.lastResort = logging.NullHandler()

    for logger_name in sqlalchemy_loggers:
        sql_logger = logging.getLogger(logger_name)

        # Clear ALL existing handlers aggressively
        sql_logger.handlers.clear()

        # Disable propagation to prevent console spam from root logger
        sql_logger.propagate = False

        # Use existing get_log_level function (respects trace mode)
        level = get_log_level("sqlalchemy", "INFO")
        sql_logger.setLevel(level)

        # Clear any existing handlers (including null handlers from early suppression)
        sql_logger.handlers.clear()

        # Add ONLY our SQLAlchemyInterceptHandler to route to db_logger
        if level < logging.ERROR:
            sql_logger.addHandler(SQLAlchemyInterceptHandler())

        # Add a null handler as the only other handler to prevent console fallback
        sql_logger.addHandler(logging.NullHandler())

        # Debug: Log the configuration
        db_logger.debug(
            f"Configured SQLAlchemy logger '{logger_name}': level={level}, "
            f"handlers={[type(h).__name__ for h in sql_logger.handlers]}, "
            f"propagate={sql_logger.propagate}"
        )
