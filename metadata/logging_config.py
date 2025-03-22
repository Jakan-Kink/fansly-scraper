"""Database logging configuration.

This module provides:
1. SQLAlchemy logging setup
2. Database operation logging
3. Performance monitoring
4. Error tracking

Note: All logger configuration is now centralized in config/logging.py.
This module only provides database monitoring and statistics.
"""

import inspect
import os
import time
import traceback
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, SessionTransaction

from config import db_logger


def get_transaction_nesting_level(transaction: Any) -> int:
    level = 0
    current = transaction
    while hasattr(current, "parent") and current.parent is not None:
        level += 1
        current = current.parent
    return level


def get_caller_info() -> str:
    """Get relevant caller information from the stack.

    Skips internal SQLAlchemy calls and common wrapper functions.
    Returns a string with the most relevant caller info.
    """
    repo_root = os.getcwd()  # assume repo root is the current working directory
    st = inspect.stack()
    # List of functions we want to skip
    skip_funcs = {
        "async_wrapper",
        "sync_wrapper",
        "_run_sync",
        "__call__",
        "_execute_context",
        "greenlet_spawn",
        "do_orm_execute",
        "session_transaction",
        "_transaction",
        "begin_nested",
        "begin",
        "_begin",
        "__aenter__",
        "__enter__",
        "__aexit__",
        "__exit__",
        "close",
        "commit",
        "rollback",
        "prepare",
        "get_transaction_info",
        "after_transaction_create",
        "after_transaction_end",
        "after_rollback",
        "after_begin",
    }
    for frame in st[1:]:
        # Skip frames from external libraries:
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
        # Compute a relative path if possible.
        if repo_root in filename:
            relative_path = os.path.relpath(filename, repo_root)
        else:
            # Otherwise, return only the basename.
            relative_path = os.path.basename(filename)
        return f"{relative_path}:{frame.lineno} in {frame.function}"
    # Fallback: list the deepest three frames in the stack.
    deepest = st[-5:]
    frames_info = []
    for frame in deepest:
        filename = frame.filename
        try:
            relative_path = os.path.relpath(filename, repo_root)
        except Exception:
            relative_path = os.path.basename(filename)
        frames_info.append(f"{relative_path}:{frame.lineno} in {frame.function}")
    return "\n".join(frames_info)


def get_parent_chain(transaction: object) -> str:
    """
    Walk the transaction's parent chain and return a comma-separated string of parent IDs.
    If there are no parents, returns 'None'.
    """
    chain = []
    current = transaction
    # Look for an attribute named "parent" that may hold the parent transaction.
    while hasattr(current, "parent") and current.parent is not None:
        # Append the parent's ID (formatted as hexadecimal) to the chain
        chain.append(hex(id(current.parent)))
        current = current.parent
    return ", ".join(chain) if chain else "None"


class DatabaseLogger:
    """Configure and manage database logging.

    Features:
    1. SQLAlchemy query logging
    2. Performance monitoring
    3. Error tracking
    4. Operation statistics

    Note: All logging is handled by the centralized db_logger.
    This class only provides monitoring and statistics.
    """

    def __init__(self) -> None:
        """Initialize database logger."""
        self._stats = {
            "queries": 0,
            "errors": 0,
            "slow_queries": 0,
            "total_time": 0.0,
        }

    def setup_engine_logging(self, engine: Engine | Any) -> None:
        """Set up logging for SQLAlchemy engine.

        Args:
            engine: SQLAlchemy Engine instance (sync or async)
        """
        # For async engines, use the underlying sync engine
        if hasattr(engine, "sync_engine"):
            engine = engine.sync_engine

        @event.listens_for(engine, "before_cursor_execute")
        def before_cursor_execute(
            conn: Any,
            cursor: Any,
            statement: str,
            parameters: tuple[Any, ...],
            context: Any,
            executemany: bool,
        ) -> None:
            conn.info.setdefault("query_start_time", []).append(time.time())
            self._stats["queries"] += 1

        @event.listens_for(engine, "after_cursor_execute")
        def after_cursor_execute(
            conn: Any,
            cursor: Any,
            statement: str,
            parameters: tuple[Any, ...],
            context: Any,
            executemany: bool,
        ) -> None:
            total = time.time() - conn.info["query_start_time"].pop()
            self._stats["total_time"] += total

            # Log slow queries (>100ms)
            if total > 0.1:
                self._stats["slow_queries"] += 1
                db_logger.warning(f"Slow query ({total:.2f}s): {statement[:100]}...")

        @event.listens_for(engine, "handle_error")
        def handle_error(context: Any) -> None:
            self._stats["errors"] += 1
            error = context.original_exception
            db_logger.error(f"Database error: {error}")

    def setup_session_logging(self, session: Session | Any) -> None:
        """Set up logging for SQLAlchemy session.

        Args:
            session: SQLAlchemy Session instance (sync or async)
        """
        # For async sessions, use the underlying sync session
        if hasattr(session, "sync_session"):
            session = session.sync_session

        @event.listens_for(session, "after_transaction_create")
        def after_transaction_create(session: Session, transaction: Any) -> None:
            level = get_transaction_nesting_level(transaction)
            caller = get_caller_info()
            parent_chain = get_parent_chain(transaction)
            db_logger.debug(
                f"Transaction started: id={hex(id(transaction))}, level={level}, parent_chain=[{parent_chain}], "
                f"caller={caller}, _current_fn={transaction._current_fn if hasattr(transaction, '_current_fn') else 'N/A'}"
            )

        @event.listens_for(session, "after_transaction_end")
        def after_transaction_end(session: Session, transaction: Any) -> None:
            # Get transaction info
            is_active = transaction.is_active
            level = get_transaction_nesting_level(transaction)
            parent_chain = get_parent_chain(transaction)
            caller = get_caller_info()
            db_logger.debug(
                f"Transaction ended: id={hex(id(transaction))}, level={level}, parent_chain=[{parent_chain}], "
                f"active={is_active}, caller={caller}"
            )

        @event.listens_for(session, "after_rollback")
        def after_rollback(session: Session) -> None:
            # Get current transaction info if available
            transaction = session.get_transaction()
            if transaction:
                level = get_transaction_nesting_level(transaction)
                caller = get_caller_info()

                db_logger.error(
                    f"Transaction rolled back: id={hex(id(transaction))}, level={level}, "
                    f"caller={caller}, _current_fn={transaction._current_fn if hasattr(transaction, '_current_fn') else 'N/A'}"
                )
            else:
                db_logger.error("Transaction rolled back (no active transaction)")

        # Add listener for savepoint operations
        @event.listens_for(session, "after_begin")
        def after_begin(
            session: Session, transaction: SessionTransaction | Any, connection: Any
        ) -> None:
            if hasattr(transaction, "_current_fn") and transaction._current_fn:
                caller = get_caller_info()
                db_logger.debug(
                    f"Savepoint created: {transaction._current_fn} "
                    f"(transaction={hex(id(transaction))}, caller={caller})"
                )

    def get_stats(self) -> dict[str, Any]:
        """Get current statistics.

        Returns:
            Dictionary of statistics
        """
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
        """Clean up any resources.

        Note: Logging cleanup is now handled by config/logging.py.
        This method only resets statistics.
        """
        self.reset_stats()
