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
import logging
import os
import sys
from pathlib import Path
from pprint import pformat

from loguru import logger

# Ensure proper UTF-8 encoding for logging on Windows
if sys.platform == "win32":
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")

# Global configuration
_config = None
_debug_enabled = False

# Log file names
DEFAULT_LOG_FILE = "fansly_downloader_ng.log"
DEFAULT_JSON_LOG_FILE = "fansly_downloader_ng_json.log"
DEFAULT_STASH_LOG_FILE = "stash.log"
DEFAULT_DB_LOG_FILE = "sqlalchemy.log"


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
            level = record.levelno

        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


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

# Custom level definitions with colors, mapped to standard level numbers
_CUSTOM_LEVELS = {
    "CONFIG": {
        "name": "CONFIG",
        "no": _LEVEL_VALUES["INFO"],  # 20 (INFO)
        "color": "<light-magenta>",
        "icon": "🔧",
    },
    "DEBUG": {
        "name": "DEBUG",
        "no": _LEVEL_VALUES["DEBUG"],  # 10 (DEBUG)
        "color": "<light-red>",
        "icon": "🔍",
    },
    "INFO": {
        "name": "INFO",
        "no": _LEVEL_VALUES["INFO"],  # 20 (INFO)
        "color": "<light-blue>",
        "icon": "ℹ️",
    },
    "ERROR": {
        "name": "ERROR",
        "no": _LEVEL_VALUES["ERROR"],  # 40 (ERROR)
        "color": "<red><bold>",
        "icon": "❌",
    },
    "WARNING": {
        "name": "WARNING",
        "no": _LEVEL_VALUES["WARNING"],  # 30 (WARNING)
        "color": "<yellow>",
        "icon": "⚠️",
    },
    "INFO_HIGHLIGHT": {
        "name": "-INFO-",
        "no": _LEVEL_VALUES["INFO"],  # 20 (INFO)
        "color": "<light-cyan><bold>",
        "icon": "✨",
    },
    "UPDATE": {
        "name": "UPDATE",
        "no": _LEVEL_VALUES["SUCCESS"],  # 25 (SUCCESS)
        "color": "<green>",
        "icon": "📦",
    },
}

# Remove default handler
logger.remove()

# Register custom levels with loguru
for level_name, level_data in _CUSTOM_LEVELS.items():
    try:
        logger.level(
            level_data["name"],
            no=level_data["no"],
            color=level_data["color"],
            icon=level_data["icon"],
        )
    except (TypeError, ValueError):
        # Failsafe for level/color registration
        pass


# Pre-configured loggers with extra fields
textio_logger = logger.bind(textio=True)
json_logger = logger.bind(json=True)
stash_logger = logger.bind(stash=True)
db_logger = logger.bind(db=True)


def _trace_level_only(record):
    """Filter to ensure trace_logger only receives TRACE level messages."""
    if record["level"].no != _LEVEL_VALUES["TRACE"]:
        from errors import InvalidTraceLogError

        raise InvalidTraceLogError(record["level"].name)
    return True


# For very detailed logging
trace_logger = logger.bind(trace=True).patch(_trace_level_only)

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
    global _handler_ids, _debug_enabled

    # Remove any existing handlers
    for handler_id, (handler, file_handler) in list(_handler_ids.items()):
        try:
            logger.remove(handler_id)
            if file_handler:
                try:
                    file_handler.close()
                except Exception:
                    pass  # Ignore errors during cleanup
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
    enqueue_args = {
        "enqueue": True,  # Use loguru's built-in queue management
    }

    # 1. TextIO Console Handler
    handler_id = logger.add(
        sys.stdout,
        format="<level>{level.icon} {level.name:>8}</level> | <white>{time:HH:mm:ss.SS}</white> <level>|</level><light-white>| {message}</light-white>",
        level=get_log_level("textio", "INFO"),
        filter=lambda record: record["extra"].get("textio", False),
        colorize=True,
        **enqueue_args,
    )
    _handler_ids[handler_id] = (None, None)  # No file handler for stdout

    # 2. TextIO File Handler
    textio_file = log_dir / DEFAULT_LOG_FILE
    textio_handler = SizeTimeRotatingHandler(
        filename=str(textio_file),
        max_bytes=100 * 1024 * 1024,  # 100MB
        backup_count=5,
        when="h",
        interval=1,
        utc=True,
        compression="gz",
        keep_uncompressed=2,
        encoding="utf-8",
    )
    handler_id = logger.add(
        textio_handler.write,
        format="[{level.name}] [{time:YYYY-MM-DD} | {time:HH:mm:ss.SS}]: {message}",
        level=get_log_level("textio", "INFO"),
        filter=lambda record: record["extra"].get("textio", False),
        backtrace=True,
        diagnose=True,
        **enqueue_args,
    )
    _handler_ids[handler_id] = (textio_handler, None)

    # 3. JSON File Handler
    json_file = log_dir / os.getenv("LOGURU_JSON_LOG_FILE", DEFAULT_JSON_LOG_FILE)
    json_handler = SizeTimeRotatingHandler(
        filename=str(json_file),
        max_bytes=100 * 1024 * 1024,  # 100MB
        backup_count=10,
        when="h",
        interval=1,
        utc=True,
        compression="gz",
        keep_uncompressed=2,
        encoding="utf-8",
    )
    handler_id = logger.add(
        json_handler.write,
        format="[{time:YYYY-MM-DD HH:mm:ss}] {level.name} {message}",
        level=get_log_level("json", "INFO"),
        filter=lambda record: record["extra"].get("json", False),
        backtrace=True,
        diagnose=True,
        **enqueue_args,
    )
    _handler_ids[handler_id] = (json_handler, None)

    # 4. Stash Console Handler
    handler_id = logger.add(
        sys.stdout,
        format="<level>{level.name}</level>: {message}",
        level=get_log_level("stash_console", "INFO"),
        colorize=True,
        filter=lambda record: record["extra"].get("stash", False),
        **enqueue_args,
    )
    _handler_ids[handler_id] = (None, None)  # No file handler for stdout

    # 5. Stash File Handler
    stash_file = log_dir / DEFAULT_STASH_LOG_FILE
    stash_handler = SizeTimeRotatingHandler(
        filename=str(stash_file),
        max_bytes=100 * 1024 * 1024,
        backup_count=10,
        when="h",
        interval=1,
        utc=True,
        compression="gz",
        keep_uncompressed=2,
    )
    handler_id = logger.add(
        stash_handler.write,
        format="[{time:YYYY-MM-DD HH:mm:ss}] {level.name} - {name} - {message}",
        level=get_log_level("stash_file", "INFO"),
        filter=lambda record: record["extra"].get("stash", False),
        **enqueue_args,
    )
    _handler_ids[handler_id] = (stash_handler, None)

    # 6. Database File Handler
    db_file = log_dir / DEFAULT_DB_LOG_FILE
    db_handler = SizeTimeRotatingHandler(
        filename=str(db_file),
        max_bytes=100 * 1024 * 1024,
        backup_count=20,
        when="h",
        interval=1,
        utc=True,
        compression="gz",
        keep_uncompressed=2,
    )
    handler_id = logger.add(
        db_handler.write,
        format="[{time:YYYY-MM-DD HH:mm:ss}] {level.name} - {message}",
        level=get_log_level("sqlalchemy", "INFO"),
        filter=lambda record: record["extra"].get("db", False),
        **enqueue_args,
    )
    _handler_ids[handler_id] = (db_handler, None)

    # 7. Trace File Handler (for very detailed logging)
    trace_file = log_dir / "trace.log"
    trace_handler = SizeTimeRotatingHandler(
        filename=str(trace_file),
        max_bytes=100 * 1024 * 1024,  # 100MB
        backup_count=5,
        when="h",
        interval=1,
        utc=True,
        compression="gz",
        keep_uncompressed=2,
    )
    handler_id = logger.add(
        trace_handler.write,
        format="[{time:YYYY-MM-DD HH:mm:ss.SSSSSS}] {level.name} - {message}",
        level=get_log_level("trace", "TRACE"),  # Default to TRACE level
        filter=lambda record: record["extra"].get("trace", False),
        **enqueue_args,
    )
    _handler_ids[handler_id] = (trace_handler, None)


def init_logging_config(config) -> None:
    """Initialize logging configuration."""
    global _config
    _config = config

    # Initial setup of handlers
    setup_handlers()  # Always do initial setup


def set_debug_enabled(enabled: bool) -> None:
    """Set the global debug flag."""
    global _debug_enabled
    _debug_enabled = enabled


def get_log_level(logger_name: str, default: str = "INFO") -> int:
    """Get log level for a logger.

    Args:
        logger_name: Name of the logger (e.g., "textio", "stash_console")
        default: Default level if config not set or logger not found

    Returns:
        Log level as integer (e.g., 10 for DEBUG, 20 for INFO)
        For trace_logger:
            - 5 (TRACE) if config.trace is True
            - 50 (CRITICAL) if config.trace is False (effectively disabled)
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


def update_logging_config(config, enabled: bool) -> None:
    """Update the logging configuration.

    Args:
        config: The FanslyConfig instance to use
        enabled: Whether debug mode should be enabled
    """
    global _config, _debug_enabled
    _config = config  # Update config reference
    _debug_enabled = enabled  # Update debug flag

    # Update handlers with new configuration
    setup_handlers()
