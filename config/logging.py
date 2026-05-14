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
import traceback
import warnings as _warnings_module
from pathlib import Path
from typing import Any

import regex as _regex
from loguru import logger

from errors import InvalidTraceLogError


def _collapse_grapheme_clusters(text: str) -> str:
    """Reduce every grapheme cluster to its base codepoint for console display.

    tmux and many SSH terminals render compound-cluster modifiers (skin tones,
    ZWJ, variation selectors) as independent wide glyphs, while Rich 15 correctly
    measures each cluster as a single base-width unit. The mismatch causes log
    lines to overflow. Replacing each cluster with its first codepoint gives a
    string every terminal stack agrees on — no hardcoded modifier list needed.
    """
    return _regex.sub(r"\X", lambda m: m.group()[0], text)


# Frames inside ``logging.__file__`` and ``warnings.__file__`` should be
# skipped when computing loguru's stack depth — otherwise the recorded
# call site lands inside the stdlib instead of user code. Used by
# ``InterceptHandler.emit`` and ``SQLAlchemyInterceptHandler.emit``.
_SKIP_FRAME_FILES = frozenset((logging.__file__, _warnings_module.__file__))


if sys.platform == "win32":  # pragma: no cover
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

# Global configuration. ``_debug_enabled`` / ``_trace_enabled`` are runtime
# overrides driven by ``-v`` / ``-vv`` (or programmatic toggling via
# ``set_debug_enabled`` / ``set_trace_enabled`` for IPython sessions). When
# set, they short-circuit ``get_log_level`` to apply a uniform floor across
# all handlers — TRACE wins over DEBUG, DEBUG wins over per-handler config.
_config = None
_debug_enabled = False
_trace_enabled = False

# Log file names
DEFAULT_LOG_FILE = "fansly_downloader_ng.log"
DEFAULT_JSON_LOG_FILE = "fansly_downloader_ng_json.log"
DEFAULT_STASH_LOG_FILE = "stash.log"
DEFAULT_DB_LOG_FILE = "sqlalchemy.log"
DEFAULT_WEBSOCKET_LOG_FILE = "websocket.log"


class InterceptHandler(logging.Handler):
    """Intercepts standard logging and routes records to bound loguru sinks.

    Routes by ``record.name`` (and ``record.pathname`` for captured warnings):
      * ``sqlalchemy.*`` / ``asyncpg`` / ``alembic.runtime.migration`` → ``db_logger``
      * ``stash_graphql_client.*`` → ``stash_logger``
      * ``py.warnings`` records → ``stash_logger`` if the originating file is
        under ``stash_graphql_client/`` (e.g., ``StashUnmappedFieldWarning``);
        otherwise ``textio_logger``
      * everything else → ``textio_logger``

    The bound-target dispatch is essential — file sinks in
    ``setup_handlers()`` filter on ``record.extra.logger == "<name>"``, so
    routing through the unbound global ``logger`` would silently drop
    records that don't match any sink filter.

    Example:
        logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            # Unknown levelname → pass the integer through; loguru's .log()
            # accepts ints directly (a string fallback would re-raise here).
            level = record.levelno

        target = self._select_target(record)

        frame, depth = sys._getframe(6), 6
        while (
            frame and frame.f_code.co_filename in _SKIP_FRAME_FILES
        ):  # pragma: no cover
            frame = frame.f_back  # type: ignore[assignment]
            depth += 1

        exc_info = record.exc_info
        try:
            target.opt(depth=depth, exception=exc_info).log(level, record.getMessage())
        finally:
            # Release refs so coverage.py's SQLite connection doesn't linger
            # on the call stack and trigger a ResourceWarning.
            del frame
            if exc_info:
                del exc_info
            record.exc_info = None

    @staticmethod
    def _select_target(record: logging.LogRecord) -> Any:
        """Pick the bound loguru logger for ``record`` based on origin."""
        name = record.name
        if (
            name.startswith(("sqlalchemy.", "asyncpg"))
            or name == "alembic.runtime.migration"
        ):
            return db_logger
        if name.startswith("stash_graphql_client"):
            return stash_logger
        if name == "py.warnings":
            # Captured warnings: ``record.pathname`` is always ``warnings.py``
            # (where ``_showwarning`` lives), so we can't route by it. The
            # originating module is embedded in the formatted warning string
            # — ``warnings.formatwarning(...)`` produces
            # ``"<filename>:<lineno>: <Category>: <message>\n  <source line>"``,
            # which the stdlib captureWarnings hook passes as the log message.
            try:
                message = record.getMessage()
            except Exception:  # pragma: no cover — defensive: getMessage formats args
                message = ""
            if "stash_graphql_client" in message:
                return stash_logger
            return textio_logger
        return textio_logger


class SQLAlchemyInterceptHandler(logging.Handler):
    """Specialized handler for SQLAlchemy logging with proper logger binding.

    Routes SQLAlchemy logs specifically to the database logger context,
    ensuring they appear in sqlalchemy.log and are properly tagged.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            # Same int-passthrough rationale as InterceptHandler.emit.
            level = record.levelno

        frame, depth = sys._getframe(6), 6
        while (
            frame and frame.f_code.co_filename in _SKIP_FRAME_FILES
        ):  # pragma: no cover
            frame = frame.f_back  # type: ignore[assignment]
            depth += 1

        exc_info = record.exc_info
        try:
            db_logger.opt(depth=depth, exception=exc_info).log(
                level, record.getMessage()
            )
        finally:
            # Same ResourceWarning mitigation as InterceptHandler.emit above.
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

# Custom level definitions. Each level carries TWO color strings
# because loguru and Rich disagree on naming:
#   * ``color``      — loguru markup, hyphens + angle brackets
#   * ``rich_style`` — Rich style, underscores + space-compound
# Separate fields avoid runtime translation. Preview any edit with
# ``poetry run python scripts/color_palette.py`` before shipping —
# invalid rich_style renders [INVALID: ...] in the sample column.
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
        # U+1F4A1 LIGHT BULB — supplementary-plane, unambiguously
        # width=2. The canonical INFORMATION SOURCE glyph is
        # BMP + VS16, which misaligns over SSH/tmux.
        "icon": "💡",
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
        # U+1F6A8 POLICE CAR LIGHT — same VS16 rationale as INFO.
        "icon": "🚨",
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

# Register (or update) custom levels. Loguru refuses
# ``logger.level(name, no=X, ...)`` on any pre-existing level — even
# when X matches the current no — with ``ValueError: Level 'DEBUG'
# already exists, you can't update its severity no``. Split into
# create vs. update: for built-ins (DEBUG/INFO/WARNING/ERROR/SUCCESS/
# CRITICAL/TRACE) omit ``no=`` so only color+icon change.
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


# Run at import time so SQLAlchemy can't log to console before
# init_logging_config installs the real handlers.
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
        sql_logger.setLevel(logging.CRITICAL)
        sql_logger.addHandler(logging.NullHandler())


_early_sqlalchemy_suppression()


def _trace_level_only(record: Any) -> bool:
    """Filter to ensure trace_logger only receives TRACE level messages."""
    if record["level"].no != _LEVEL_VALUES["TRACE"]:
        raise InvalidTraceLogError(record["level"].name)
    return True


trace_logger = logger.bind(logger="trace").patch(_trace_level_only)

_handler_ids: dict[int, tuple[Any, Any]] = {}  # {id: (handler, file_handler)}


def _resolve(entry: Any, global_section: Any, attr: str, default_attr: str) -> Any:
    """Resolve a per-handler field, falling through to a global default.

    Returns the entry's value if non-``None``, otherwise the matching
    ``default_*`` on ``global_section``. Keeps the per-handler config
    free of repetition: operators write ``backup_count: 20`` only on
    handlers that need to override the global default.
    """
    value = getattr(entry, attr, None)
    if value is not None:
        return value
    return getattr(global_section, default_attr)


def setup_handlers() -> None:
    """Set up all logging handlers.

    Reads per-handler config from ``_config.logging`` (LoggingSection):
    each entry's ``enabled`` / ``level`` / ``format`` plus — for file
    entries — ``filename`` / ``max_size`` / ``rotation_when`` /
    ``rotation_interval`` / ``utc`` / ``backup_count`` / ``compression``
    / ``keep_uncompressed``. Any per-handler ``None`` falls through to
    the matching ``logging.global_.default_*``. When ``_config`` or
    ``_config.logging`` is absent (early boot, tests), behavior matches
    the pre-config-driven defaults.
    """
    for handler_id, (_handler, file_handler) in list(_handler_ids.items()):
        try:
            logger.remove(handler_id)
            if file_handler:  # pragma: no cover — _handler_ids' second tuple slot is always None today
                with contextlib.suppress(Exception):
                    file_handler.close()
        except ValueError:
            pass  # Handler already removed
    _handler_ids.clear()

    # Pull the schema's LoggingSection if available; tests + early boot
    # run with _config=None and get a default-constructed section so all
    # the per-handler / global_ fields are present.
    logging_section = getattr(_config, "logging", None)
    if logging_section is None:
        from config.schema import LoggingSection  # noqa: PLC0415, I001  # circular: config.schema → config.logging

        logging_section = LoggingSection()

    # ``-vv`` (or ``set_trace_enabled(True)``) opens the trace file sink at
    # TRACE regardless of the schema's per-entry ``trace.enabled`` default.
    # The ``get_log_level`` override below then also forces every other
    # handler to TRACE for the duration of the run.
    #
    # Operate on a model_copy(deep=True) — direct mutation of the live
    # ``_config.logging`` instance would leak ``global_.trace = True`` into
    # the schema and the next ``_save_config`` would persist a per-run
    # CLI flag into YAML. The runtime trace floor lives in
    # ``_trace_enabled``; the schema stays clean.
    if _trace_enabled:
        logging_section = logging_section.model_copy(deep=True)
        logging_section.trace.enabled = True
        logging_section.global_.trace = True

    g = logging_section.global_

    # ``directory`` defaults to <cwd>/logs when unset
    log_dir = Path(g.directory).expanduser() if g.directory else Path.cwd() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Import inside the function to avoid circular imports.
    from textio.logging import SizeTimeRotatingHandler  # noqa: PLC0415, I001  # circular: textio.logging → config.logging

    # Loguru's multi-process queue is off in tests (enqueue=True pickles
    # the sink, which breaks with RichHandler + module-scoped fixtures).
    enqueue_args = (
        {"enqueue": False} if os.getenv("TESTING") == "1" else {"enqueue": True}
    )

    # Named sinks that own their own handlers — unbound records must NOT be
    # double-routed into those handlers.  Any logger_type NOT in this set
    # (including None for bare ``logger.*`` calls) falls through to textio.
    _owned_sinks = {"db", "stash", "websocket", "trace", "json"}

    # 1. TextIO Console Handler with SQL filtering
    def textio_filter(record: Any) -> bool:
        """Filter for textio console handler - exclude SQL logs."""
        extra = record.get("extra", {})
        logger_type = extra.get("logger")

        # Explicitly owned sinks stay out of the textio console.
        if logger_type in _owned_sinks:
            return False

        # Unbound logs: suppress SQLAlchemy/asyncpg/alembic noise.
        # pragma: no cover — defensive net; InterceptHandler.emit routes
        # SA/asyncpg/alembic records to db_logger BEFORE they hit this
        # filter, so by the time they arrive logger_type is already "db"
        # and the early check above returns False. This guard catches
        # records that bypass InterceptHandler entirely.
        logger_name = record.get("name", "")
        return not (  # pragma: no cover
            logger_name.startswith(("sqlalchemy.", "asyncpg"))
            or logger_name == "alembic.runtime.migration"
        )

    # Console handler routes through Rich's shared console to coordinate
    # with ProgressManager's Live display. Fallback is loud (see except).
    format_record: Any
    console_sink: Any
    use_colorize: bool
    try:
        from helpers.rich_progress import create_rich_handler  # noqa: PLC0415, I001  # circular: helpers.rich_progress → config.logging

        # Pick rich_style (not color) — Rich's parser wants underscores.
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
            # Escape `{`/`}` (Python format specs) AND `<` (loguru color tags).
            # loguru re-parses the returned format string to strip tags even
            # with colorize=False; an embedded traceback frame name like
            # `<module>` would otherwise crash Colorizer.prepare_stripped_format.
            # Collapse compound grapheme clusters to their base codepoint so
            # tmux/SSH terminals (which render modifiers as full-width glyphs)
            # and Rich agree on the physical line width.
            safe_msg = (
                _collapse_grapheme_clusters(str(record["message"]))
                .replace("{", "{{")
                .replace("}", "}}")
                .replace("<", r"\<")
            )
            return f"{icon} {safe_msg}"

        # RichHandler handles its own coloring — disable loguru's ANSI injection
        use_colorize = False
    except Exception as exc:
        # Loud fallback: raw stdout bypasses Live coordination, so the
        # symptom (striped bars) is visible before the cause. Silent
        # fallback hid this for months before we caught it.
        print(
            f"[logging] RichHandler setup failed — falling back to plain stdout "
            f"(bars will jitter). Cause: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        traceback.print_exc(file=sys.stderr)
        console_sink = sys.stdout
        format_record = "<level>{level.icon} {level.name:>8}</level> | <white>{time:HH:mm:ss.SS}</white> <level>|</level><light-white>| {message}</light-white>"
        use_colorize = True

    def _add_file_handler(
        entry: Any,
        *,
        filter: Any,
        default_format: str,
        level_logger_name: str,
        default_level: str = "INFO",
        encoding: str | None = None,
        tag_db: bool = False,
    ) -> Any:
        """Build a SizeTimeRotatingHandler from a FileLoggerEntry and add it.

        Skips the add entirely when ``entry.enabled`` is False. Returns
        the wrapper handler (or None when disabled) so callers can stash
        it in ``_handler_ids``.
        """
        if not entry.enabled:
            return None
        file_path = log_dir / entry.filename
        kwargs: dict[str, Any] = {
            "filename": str(file_path),
            "maxBytes": _resolve(entry, g, "max_size", "default_max_size"),
            "backupCount": _resolve(entry, g, "backup_count", "default_backup_count"),
            "when": _resolve(entry, g, "rotation_when", "default_rotation_when"),
            "interval": _resolve(
                entry, g, "rotation_interval", "default_rotation_interval"
            ),
            "utc": _resolve(entry, g, "utc", "default_utc"),
            "compression": _resolve(entry, g, "compression", "default_compression"),
            "keep_uncompressed": _resolve(
                entry, g, "keep_uncompressed", "default_keep_uncompressed"
            ),
        }
        if encoding is not None:
            kwargs["encoding"] = encoding
        wrapper = SizeTimeRotatingHandler(**kwargs)
        if tag_db:
            wrapper.handler.db_logger_name = "database_logger"  # debug tag
        handler_id = logger.add(
            wrapper.write,
            format=default_format,
            level=get_log_level(level_logger_name, default_level),
            filter=filter,
            backtrace=True,
            diagnose=True,
            **enqueue_args,
        )
        _handler_ids[handler_id] = (wrapper, None)
        return wrapper

    def _add_console_handler(
        entry: Any, *, filter: Any, level_logger_name: str
    ) -> None:
        """Add a Rich-console sink driven by a ConsoleLoggerEntry."""
        if not entry.enabled:
            return
        handler_id = logger.add(  # type: ignore[call-overload]
            sink=console_sink,
            format=format_record,
            level=get_log_level(level_logger_name, "INFO"),
            filter=filter,
            colorize=use_colorize,
            **enqueue_args,
        )
        _handler_ids[handler_id] = (None, None)

    # 1. Rich console (textio)
    _add_console_handler(
        logging_section.rich_handler,
        filter=textio_filter,
        level_logger_name="textio",
    )

    # 2. Main log file
    _add_file_handler(
        logging_section.main_log,
        filter=lambda record: record.get("extra", {}).get("logger") not in _owned_sinks,
        default_format="[{time:YYYY-MM-DD HH:mm:ss.SSS}] [{level.name:<8}] "
        "{name}:{function}:{line} - {message}",
        level_logger_name="textio",
        encoding="utf-8",
    )

    # 3. JSON file. LOGURU_JSON_LOG_FILE env override remains supported
    # (operator escape hatch ahead of YAML edits); when set, it wins over
    # the schema's filename for this run only.
    json_entry = logging_section.json_
    json_filename_env = os.getenv("LOGURU_JSON_LOG_FILE")
    if json_filename_env:
        json_entry = json_entry.model_copy(update={"filename": json_filename_env})
    _add_file_handler(
        json_entry,
        filter=lambda record: record.get("extra", {}).get("logger") == "json",
        default_format="{level.icon}   {level.name:>8} | {time:HH:mm:ss.SS} || {message}",
        level_logger_name="json",
        encoding="utf-8",
    )

    # 4. Stash console
    _add_console_handler(
        logging_section.stash_console,
        filter=lambda record: record.get("extra", {}).get("logger") == "stash",
        level_logger_name="stash_console",
    )

    # 5. Stash file
    _add_file_handler(
        logging_section.stash_file,
        filter=lambda record: record.get("extra", {}).get("logger") == "stash",
        default_format="{level.icon}   {level.name:>8} | {time:HH:mm:ss.SS} || {message}",
        level_logger_name="stash_file",
    )

    # 6. Database file
    _add_file_handler(
        logging_section.db,
        filter=lambda record: record.get("extra", {}).get("logger") == "db",
        default_format="{level.icon}   {level.name:>8} | {time:HH:mm:ss.SS} || {message}",
        level_logger_name="sqlalchemy",
        tag_db=True,
    )

    # 7. Trace file (file-only, default-disabled)
    _add_file_handler(
        logging_section.trace,
        filter=lambda record: record.get("extra", {}).get("logger", None) == "trace",
        default_format="{level.icon}   {level.name:>8} | {time:HH:mm:ss.SSS} | "
        "{name}:{function}:{line} - {message}",
        level_logger_name="trace",
        default_level="TRACE",
    )

    # 8. WebSocket file — frame-level traffic kept out of the main log
    _add_file_handler(
        logging_section.websocket,
        filter=lambda record: record.get("extra", {}).get("logger") == "websocket",
        default_format="{level.icon}   {level.name:>8} | {time:HH:mm:ss.SS} || "
        "{name}:{function}:{line} - {message}",
        level_logger_name="websocket",
    )


def init_logging_config(config: Any) -> None:
    """Initialize logging configuration.

    Mirrors the live ``config.debug`` / ``config.trace`` runtime attributes
    into the module-level overrides so ``get_log_level`` sees the right
    floor. Tests rely on this — they mutate ``config.trace`` then call
    ``init_logging_config(config)`` to re-prime the handlers.
    """
    global _config, _debug_enabled, _trace_enabled
    _config = config

    if config:
        _debug_enabled = bool(getattr(config, "debug", False))
        _trace_enabled = bool(getattr(config, "trace", False))

    # IMPORTANT: Set up handlers FIRST so db_logger has somewhere to write
    setup_handlers()

    # THEN configure SQLAlchemy logging to route to those handlers
    _configure_sqlalchemy_logging()

    # Capture stdlib warnings (warnings.warn) into loguru so SGC's
    # StashUnmappedFieldWarning and similar reach the rich console + log
    # files. Without captureWarnings(True), warnings.warn() goes to stderr
    # and bypasses every loguru sink.
    _configure_warnings_capture()


def set_debug_enabled(enabled: bool) -> None:
    """Toggle the runtime DEBUG-floor override (driven by ``-v``).

    Re-runs handler setup so per-handler levels pick up the change. Safe
    to call from IPython sessions to flip verbosity mid-run.
    """
    global _debug_enabled
    _debug_enabled = enabled
    update_logging_config(_config, enabled)


def set_trace_enabled(enabled: bool) -> None:
    """Toggle the runtime TRACE-floor override (driven by ``-vv``).

    When set, all handlers (including the otherwise-dormant trace.log
    sink) are forced to TRACE level. Mirrors ``set_debug_enabled`` for
    the higher verbosity tier.
    """
    global _trace_enabled
    _trace_enabled = enabled
    # Re-run setup so the trace-handler enable bridge + level overrides
    # propagate. Pass _debug_enabled through unchanged — set_trace_enabled
    # is orthogonal to debug; -vv mode keeps debug on too.
    update_logging_config(_config, _debug_enabled)


def get_log_level(logger_name: str, default: str = "INFO") -> int:
    """Get log level for a logger.

    Precedence (highest first):
        1. ``-vv`` / ``_trace_enabled`` — every handler floors at TRACE,
           including ``trace_logger`` and the otherwise-default-INFO peers.
        2. ``-v`` / ``_debug_enabled`` — every non-trace handler floors at
           DEBUG; ``trace_logger`` stays disabled (CRITICAL).
        3. Per-handler schema config (``config.log_levels[name]``), with
           a DEBUG ceiling so users can't accidentally TRACE-spam non-trace
           handlers via YAML.

    Args:
        logger_name: handler identity — "textio", "stash_console",
            "stash_file", "sqlalchemy", "trace", "websocket", "json".
        default: Fallback when neither overrides nor config provide one.

    Returns:
        Log level as integer (e.g., 5 for TRACE, 10 for DEBUG, 20 for INFO).
    """
    if logger_name == "trace":
        # Trace handler is dormant unless trace mode is on (either CLI
        # ``-vv`` or YAML ``logging.global.trace=true``). CRITICAL is the
        # canonical "effectively off" level for a loguru sink that should
        # otherwise stay registered.
        schema_trace = bool(
            _config
            and getattr(getattr(_config, "logging", None), "global_", None)
            and _config.logging.global_.trace
        )
        return (
            _LEVEL_VALUES["TRACE"]
            if (_trace_enabled or schema_trace)
            else _LEVEL_VALUES["CRITICAL"]
        )

    if _trace_enabled:
        # -vv override — every handler goes to TRACE. Console handlers'
        # schema-level "no TRACE in YAML" rule doesn't apply here because
        # this is a runtime opt-in, not a persisted misconfiguration.
        return _LEVEL_VALUES["TRACE"]

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
    """Refresh handlers + asyncio plumbing after a verbosity toggle.

    Args:
        config: The FanslyConfig instance to use.
        enabled: Whether debug-floor mode is active. Stored as
            ``_debug_enabled``; asyncio is also flipped to DEBUG when set,
            WARNING when cleared.
    """
    from config.fanslyconfig import FanslyConfig  # noqa: PLC0415, I001  # circular: config.fanslyconfig → config.logging

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
    _configure_warnings_capture()


def _configure_warnings_capture() -> None:
    """Wire ``warnings.warn`` capture so SGC + other library warnings reach loguru.

    Without ``captureWarnings(True)``, ``warnings.warn(...)`` writes raw
    text to ``sys.stderr`` — bypassing the rich console handler and every
    log-file sink. With it, warnings are routed through the ``py.warnings``
    stdlib logger; we attach an ``InterceptHandler`` so the handler's
    routing logic picks the appropriate bound sink (e.g.,
    ``StashUnmappedFieldWarning`` from ``stash_graphql_client/`` → ``stash_logger``).
    """
    logging.captureWarnings(True)
    py_warnings_logger = logging.getLogger("py.warnings")
    # Clear any existing handlers (re-runs idempotent on repeated init).
    py_warnings_logger.handlers.clear()
    py_warnings_logger.addHandler(InterceptHandler())
    py_warnings_logger.propagate = False
    py_warnings_logger.setLevel(logging.WARNING)


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
        sql_logger.handlers.clear()
        # Propagation off prevents root-logger console spam.
        sql_logger.propagate = False
        level = get_log_level("sqlalchemy", "INFO")
        sql_logger.setLevel(level)
        # Second clear drops any null handlers from _early_sqlalchemy_suppression.
        sql_logger.handlers.clear()
        if level < logging.ERROR:
            sql_logger.addHandler(SQLAlchemyInterceptHandler())
        # NullHandler prevents console fallback if our intercept is removed.
        sql_logger.addHandler(logging.NullHandler())
        db_logger.debug(
            f"Configured SQLAlchemy logger '{logger_name}': level={level}, "
            f"handlers={[type(h).__name__ for h in sql_logger.handlers]}, "
            f"propagate={sql_logger.propagate}"
        )
