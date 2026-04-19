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


_EXCLUDED_PATH_PARTS = frozenset(
    {
        ".venv",
        "venv",
        "venv_test",
        "virtualenv",
        "site-packages",
        ".tox",
        "__pypackages__",
        "node_modules",
        ".eggs",
    }
)

_SKIP_FUNCS = frozenset(
    {
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
)


def get_caller_info() -> str:
    """Return the first stack frame that is application code.

    Application code = inside the repo root AND not inside a vendored
    directory (``.venv``, ``venv_test``, ``site-packages`` etc.). stdlib
    lives outside the repo so ``relative_to`` raises ValueError for
    those frames; vendored code lives inside the repo so we additionally
    exclude frames whose relative path includes a known virtual-env or
    package-dir segment. Without both checks, asyncio's event-loop
    dispatcher (events.py:94) gets attributed to every async query.
    """
    repo_root = Path.cwd().resolve()
    for frame in inspect.stack()[1:]:
        if frame.function in _SKIP_FUNCS:
            continue
        try:
            relative_path = Path(frame.filename).resolve().relative_to(repo_root)
        except ValueError:
            continue  # stdlib, system packages, anywhere outside the repo
        if _EXCLUDED_PATH_PARTS.intersection(relative_path.parts):
            continue  # vendored code inside the repo (.venv, site-packages)
        return f"{relative_path}:{frame.lineno} in {frame.function}"
    return "<unknown caller>"


class DatabaseLogger:
    """asyncpg query monitoring.

    Features:
    1. Query counting and timing via add_query_logger
    2. Slow query detection (>100ms)
    3. Error tracking (queries that raised exceptions)
    """

    def __init__(self) -> None:
        self._stats: dict[str, Any] = {
            "queries": 0,
            "errors": 0,
            "slow_queries": 0,
            "total_time": 0.0,
        }

    def setup_connection_logging(self, conn: asyncpg.Connection) -> None:
        """Register a query logger on an asyncpg connection.

        Called from ``PostgresEntityStore._init_pg_connection`` for every
        new connection created by the pool.

        Note: we intentionally do NOT register a Postgres log listener
        via ``add_log_listener``. asyncpg's ``_on_release`` emits an
        ``InterfaceWarning`` every first release of a pooled connection
        that has an active log listener (connection.py:1779), because
        log listeners are expected to be per-acquire state. We have no
        stored procedures that emit ``RAISE NOTICE``, so the DEBUG-level
        Postgres server message capture was low-value relative to the
        warning noise. ``add_query_logger`` is the supported persistent
        hook and does not trigger the warning.
        """
        conn.add_query_logger(self.query_logger_callback)

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
