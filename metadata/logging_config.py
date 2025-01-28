"""Database logging configuration.

This module provides:
1. SQLAlchemy logging setup
2. Database operation logging
3. Performance monitoring
4. Error tracking
"""

import logging
import time
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from textio import print_error, print_info


class DatabaseLogger:
    """Configure and manage database logging.

    Features:
    1. SQLAlchemy query logging
    2. Performance monitoring
    3. Error tracking
    4. Operation statistics
    """

    def __init__(self, log_path: Path | None = None) -> None:
        """Initialize database logger.

        Args:
            log_path: Optional path for log file
        """
        self.log_path = log_path
        self._setup_logging()
        self._stats = {
            "queries": 0,
            "errors": 0,
            "slow_queries": 0,
            "total_time": 0.0,
        }

    def _setup_logging(self) -> None:
        """Set up logging configuration."""
        # SQLAlchemy loggers
        loggers = [
            "sqlalchemy.engine",
            "sqlalchemy.pool",
            "sqlalchemy.dialects",
            "sqlalchemy.orm",
        ]

        # Configure each logger
        for logger_name in loggers:
            logger = logging.getLogger(logger_name)
            logger.setLevel(logging.INFO)

            # Add file handler if path provided
            if self.log_path:
                handler = logging.FileHandler(self.log_path)
                handler.setLevel(logging.INFO)
                formatter = logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
                )
                handler.setFormatter(formatter)
                logger.addHandler(handler)

    def setup_engine_logging(self, engine: Engine) -> None:
        """Set up logging for SQLAlchemy engine.

        Args:
            engine: SQLAlchemy Engine instance
        """

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
                print_info(f"Slow query ({total:.2f}s): {statement[:100]}...")

        @event.listens_for(engine, "handle_error")
        def handle_error(context: Any) -> None:
            self._stats["errors"] += 1
            error = context.original_exception
            print_error(f"Database error: {error}")

    def setup_session_logging(self, session: Session) -> None:
        """Set up logging for SQLAlchemy session.

        Args:
            session: SQLAlchemy Session instance
        """

        @event.listens_for(session, "after_transaction_create")
        def after_transaction_create(session: Session, transaction: Any) -> None:
            print_info(f"Transaction started: {transaction}")

        @event.listens_for(session, "after_transaction_end")
        def after_transaction_end(session: Session, transaction: Any) -> None:
            print_info(f"Transaction ended: {transaction}")

        @event.listens_for(session, "after_rollback")
        def after_rollback(session: Session) -> None:
            print_error("Transaction rolled back")

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
