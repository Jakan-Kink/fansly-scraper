"""Database logging configuration.

This module provides:
1. SQLAlchemy logging setup
2. Database operation logging
3. Performance monitoring
4. Error tracking

Note: All logger configuration is now centralized in config/logging.py.
This module only provides database monitoring and statistics.
"""

import time
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from config import db_logger


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
            db_logger.debug(f"Transaction started: {transaction}")

        @event.listens_for(session, "after_transaction_end")
        def after_transaction_end(session: Session, transaction: Any) -> None:
            db_logger.debug(f"Transaction ended: {transaction}")

        @event.listens_for(session, "after_rollback")
        def after_rollback(session: Session) -> None:
            db_logger.error("Transaction rolled back")

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
