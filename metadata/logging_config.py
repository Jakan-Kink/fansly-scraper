"""Database logging configuration for asyncpg.

This module provides:
1. Query performance monitoring via asyncpg's add_query_logger
2. Postgres server log capture via asyncpg's add_log_listener
3. Error tracking and slow query detection
4. Operation statistics

All logging is handled by the centralized db_logger from config/.
This module provides the callbacks and statistics counters.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Any

from config import db_logger


if TYPE_CHECKING:
    import asyncpg


def get_caller_info() -> str:
    """Get relevant caller information from the stack.

    Skips internal asyncpg/asyncio calls and common wrapper functions.
    Returns a string with the most relevant caller info.
    """
    repo_root = Path.cwd()
    st = inspect.stack()
    skip_funcs = {
        "async_wrapper",
        "sync_wrapper",
        "_run_sync",
        "__call__",
        "__aenter__",
        "__enter__",
        "__aexit__",
        "__exit__",
        "close",
        "query_logger_callback",
        "_execute",
        "_do_execute",
        "execute",
        "fetch",
        "fetchrow",
        "fetchval",
        "executemany",
    }
    for frame in st[1:]:
        filename = frame.filename
        lower_fname = filename.lower()
        if (
            "site-packages" in lower_fname
            or "virtualenv" in lower_fname
            or "venv" in lower_fname
        ):
            continue
        if frame.function in skip_funcs:
            continue
        filepath = Path(filename)
        try:
            relative_path = str(filepath.relative_to(repo_root))
        except ValueError:
            relative_path = filepath.name
        return f"{relative_path}:{frame.lineno} in {frame.function}"
    # Fallback: deepest frames in the stack
    deepest = st[-5:]
    frames_info = []
    for frame in deepest:
        filepath = Path(frame.filename)
        try:
            relative_path = str(filepath.relative_to(repo_root))
        except Exception:
            relative_path = filepath.name
        frames_info.append(f"{relative_path}:{frame.lineno} in {frame.function}")
    return "\n".join(frames_info)


class DatabaseLogger:
    """asyncpg query and log monitoring.

    Features:
    1. Query counting and timing via add_query_logger
    2. Slow query detection (>100ms)
    3. Error tracking (queries that raised exceptions)
    4. Postgres server log capture via add_log_listener
    """

    def __init__(self) -> None:
        self._stats: dict[str, Any] = {
            "queries": 0,
            "errors": 0,
            "slow_queries": 0,
            "total_time": 0.0,
        }

    def setup_connection_logging(self, conn: asyncpg.Connection) -> None:
        """Register query and log listeners on an asyncpg connection.

        Called from ``PostgresEntityStore._init_pg_connection`` for every
        new connection created by the pool.
        """
        conn.add_query_logger(self.query_logger_callback)
        conn.add_log_listener(self.log_listener_callback)

    def query_logger_callback(self, record: Any) -> None:
        """asyncpg query logger callback.

        ``record`` is a ``LoggedQuery`` with: query, args, timeout,
        elapsed, exception, conn_addr, conn_params.
        """
        self._stats["queries"] += 1
        self._stats["total_time"] += record.elapsed

        if record.exception is not None:
            self._stats["errors"] += 1
            # .opt(exception=...) lets loguru format the full traceback from
            # the exception's __traceback__ instead of bare str(exception).
            # Include a query prefix so the log entry identifies which
            # statement failed (useful when many concurrent queries run).
            db_logger.opt(exception=record.exception).error(
                f"Database error on query: {record.query[:200]}"
            )

        if record.elapsed > 0.1:
            self._stats["slow_queries"] += 1
            caller = get_caller_info()
            db_logger.warning(
                f"Slow query ({record.elapsed:.2f}s): "
                f"{record.query[:100]}... caller={caller}"
            )

    @staticmethod
    def log_listener_callback(
        conn: asyncpg.Connection,  # noqa: ARG004
        message: Any,
    ) -> None:
        """asyncpg log listener for Postgres server messages.

        Receives async ``NoticeResponse`` messages (WARNING, NOTICE,
        DEBUG, INFO, LOG).
        """
        db_logger.debug(f"Postgres: [{message.severity}] {message.message}")

    def get_stats(self) -> dict[str, Any]:
        """Return a copy of current statistics."""
        return self._stats.copy()

    def reset_stats(self) -> None:
        """Reset statistics counters."""
        self._stats = {
            "queries": 0,
            "errors": 0,
            "slow_queries": 0,
            "total_time": 0.0,
        }

    def cleanup(self) -> None:
        """Reset statistics (logging cleanup handled by config/logging.py)."""
        self.reset_stats()


def get_db_logger() -> DatabaseLogger:
    """Get the global DatabaseLogger singleton, initializing if needed."""
    if not hasattr(get_db_logger, "instance"):
        get_db_logger.instance = DatabaseLogger()
    return get_db_logger.instance
