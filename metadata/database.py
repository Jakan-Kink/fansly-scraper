"""Database management for PostgreSQL.

This module provides PostgreSQL database management with schema-based isolation
for per-creator databases. It replaces the previous SQLite implementation with
a much simpler architecture that leverages PostgreSQL's native features.

Key features:
- PostgreSQL schema-based isolation (replaces separate SQLite files)
- Connection pooling with psycopg3 and asyncpg
- Alembic migrations with schema support
- Session management for sync and async operations
- Proper cleanup and resource management

The reduction from ~1800 lines (SQLite) to ~400 lines (PostgreSQL) comes from:
- No in-memory database with write-through caching
- No background sync thread
- No complex file I/O and atomic writes
- No SQLite-specific optimizations
- PostgreSQL handles concurrency natively
"""

from __future__ import annotations

import asyncio
import atexit
import os
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager, suppress
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from config import db_logger

from .logging_config import DatabaseLogger


if TYPE_CHECKING:
    from config import FanslyConfig

# Set up database logging
logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)


def get_db_logger() -> DatabaseLogger:
    """Get the global database logger, initializing it if needed."""
    if not hasattr(get_db_logger, "instance"):
        get_db_logger.instance = DatabaseLogger()
    return get_db_logger.instance


def require_database_config[RT](func: Callable[..., RT]) -> Callable[..., RT]:
    """Decorator to ensure database configuration is present."""
    is_async = asyncio.iscoroutinefunction(func)

    def get_config(*args: Any, **kwargs: Any) -> Any:
        """Helper to extract config from args/kwargs."""
        config = kwargs.get("config")
        if config is None:
            for arg in args:
                if hasattr(arg, "_database"):
                    config = arg
                    break
        return config

    @wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> RT:
        """Async wrapper that checks for database config."""
        config = get_config(*args, **kwargs)
        if (config is None) or (not hasattr(config, "_database")):
            raise ValueError("Database configuration not found")
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> RT:
        """Sync wrapper that checks for database config."""
        config = get_config(*args, **kwargs)
        if (config is None) or (not hasattr(config, "_database")):
            raise ValueError("Database configuration not found")
        return func(*args, **kwargs)

    return async_wrapper if is_async else sync_wrapper


class Database:
    """PostgreSQL database management.

    This class provides a streamlined approach to database management:
    - Uses PostgreSQL 'public' schema for global metadata
    - Leverages PostgreSQL's native connection pooling
    - Supports both sync and async operations
    - Provides proper cleanup and resource management

    Class Attributes:
        _sync_session_factory: Class-level session factory for sync operations
        _async_session_factory: Class-level session factory for async operations
    """

    # Class-level session factories
    _sync_session_factory = None
    _async_session_factory = None

    def __init__(
        self,
        config: FanslyConfig,
        *,
        creator_name: str | None = None,
        skip_migrations: bool = False,
    ) -> None:
        """Initialize PostgreSQL database manager.

        Args:
            config: FanslyConfig instance
            creator_name: Optional (unused, kept for compatibility)
            skip_migrations: Skip running migrations during initialization
        """
        self.config = config
        self.creator_name = creator_name

        # Always use public schema (single global database)
        self.schema_name = "public"

        # Cleanup tracking
        import threading

        self._cleanup_done = threading.Event()
        self._cleanup_lock = threading.Lock()

        # Build connection URLs
        self.db_url = self._build_connection_url()
        self.async_db_url = self.db_url.replace(
            "postgresql://", "postgresql+asyncpg://"
        )

        db_logger.info("Initializing PostgreSQL database")

        # Create engines with connection pooling
        self._sync_engine = create_engine(
            self.db_url,
            pool_size=config.pg_pool_size,
            max_overflow=config.pg_max_overflow,
            pool_timeout=config.pg_pool_timeout,
            pool_pre_ping=True,  # Check connection health before using
            pool_recycle=3600,  # Recycle connections after 1 hour
            echo=False,  # Disable SQL echoing (we use our own logging)
        )

        self._async_engine = create_async_engine(
            self.async_db_url,
            pool_size=config.pg_pool_size,
            max_overflow=config.pg_max_overflow,
            pool_timeout=config.pg_pool_timeout,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=False,
        )

        # Set up logging for engines
        db_logger_monitor = get_db_logger()
        db_logger_monitor.log_level = config.log_levels.get("sqlalchemy", "INFO")
        db_logger_monitor.setup_engine_logging(self._sync_engine)
        db_logger_monitor.setup_engine_logging(self._async_engine)

        # Set up event listeners for SQL logging (bypasses Python's logging system)
        self._setup_sql_logging()

        # Also reconfigure SQLAlchemy logging for any other loggers
        from config.logging import _configure_sqlalchemy_logging

        _configure_sqlalchemy_logging()

        # Run migrations (public schema always exists, no need to create)
        if not skip_migrations:
            self._run_migrations()

        # Create session factories
        Database._sync_session_factory = sessionmaker(
            bind=self._sync_engine,
            expire_on_commit=False,
        )

        Database._async_session_factory = async_sessionmaker(
            bind=self._async_engine,
            expire_on_commit=False,
            sync_session_class=Database._sync_session_factory,
            class_=AsyncSession,
        )

        # Use class-level factories for this instance
        self._sync_session_factory = Database._sync_session_factory
        self._async_session_factory = Database._async_session_factory

        # Register cleanup
        atexit.register(self.close_sync)

        db_logger.info("PostgreSQL database initialized successfully")

    def _build_connection_url(self) -> str:
        """Build PostgreSQL connection URL from config.

        Returns:
            PostgreSQL connection URL string
        """
        config = self.config

        # Get password from environment variable or config
        # Allow empty string for local development (trust authentication)
        password = os.getenv("FANSLY_PG_PASSWORD")
        if password is None:
            password = config.pg_password if config.pg_password is not None else ""

        # URL-encode password to handle special characters (empty string is OK)
        password_encoded = quote_plus(password)

        # Build base connection URL
        url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"

        return url

    def _run_migrations(self) -> None:
        """Run Alembic migrations for PostgreSQL.

        Applies any pending migrations to bring the database schema up to date.
        For databases migrated from SQLite, the migration script should have already
        set up the alembic_version table with the appropriate revision.
        """
        try:
            db_logger.info("Checking database migrations...")

            alembic_cfg = AlembicConfig("alembic.ini")
            # Don't set sqlalchemy.url - we provide connection directly
            # This avoids ConfigParser interpolation issues with % in passwords

            with self._sync_engine.begin() as conn:
                alembic_cfg.attributes["connection"] = conn

                # Run migrations to bring database up to latest version
                db_logger.info("Running database migrations...")
                alembic_upgrade(alembic_cfg, "head")

            db_logger.info("Migrations completed successfully")
        except Exception as e:
            db_logger.error(f"Error running migrations: {e}")
            raise

    def _setup_sql_logging(self) -> None:
        """Set up event listeners for SQL logging.

        Uses the sync_engine from the async engine to attach listeners
        that log SQL statements directly to db_logger, bypassing Python's
        standard logging system.
        """
        if not self._async_engine:
            return

        # Get the underlying sync engine from async engine
        sync_engine = self._async_engine.sync_engine

        @event.listens_for(sync_engine, "before_cursor_execute")
        def before_cursor_execute(
            _conn: Any,
            _cursor: Any,
            statement: str,
            parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            # Log the SQL statement through db_logger
            db_logger.info(statement)
            if parameters:
                db_logger.debug(f"Parameters: {parameters}")

        @event.listens_for(sync_engine, "handle_error")
        def handle_error(context: Any) -> None:
            error = context.original_exception
            db_logger.error(f"Database error: {error}")

    @contextmanager
    def session_scope(self) -> Generator[Session]:
        """Get a sync session with proper resource management.

        This context manager handles session creation, transaction management,
        and proper cleanup.

        Returns:
            SQLAlchemy Session object with active transaction

        Example:
            with db.session_scope() as session:
                result = session.execute(text("SELECT * FROM table"))
        """
        session = self._sync_session_factory()

        # Set up logging for session
        get_db_logger().setup_session_logging(session)

        try:
            yield session
            if session.is_active:
                session.commit()
        except Exception as e:
            db_logger.error(f"Error in sync session: {e}")
            if session.is_active:
                try:
                    session.rollback()
                except PendingRollbackError:
                    db_logger.warning(
                        "PendingRollbackError during rollback, transaction already rolled back"
                    )
                except Exception as rollback_e:
                    db_logger.error(f"Error during session rollback: {rollback_e}")
            raise
        finally:
            try:
                session.close()
            except PendingRollbackError:
                db_logger.warning(
                    "PendingRollbackError during close, transaction already rolled back"
                )
            except Exception as close_e:
                db_logger.error(f"Error during session close: {close_e}")

    @asynccontextmanager
    async def async_session_scope(self) -> AsyncGenerator[AsyncSession]:
        """Get an async session with proper resource management.

        This context manager provides comprehensive session management:
        1. Creates AsyncSession instances with async_sessionmaker
        2. Handles transactions and savepoints correctly
        3. Manages complete session lifecycle with proper cleanup
        4. Implements error recovery from transaction errors

        Example:
            async with db.async_session_scope() as session:
                result = await session.execute(text("SELECT * FROM table"))
                data = await session.scalars(select(Model))
        """
        session = self._async_session_factory()
        get_db_logger().setup_session_logging(session)

        try:
            yield session

            # Commit if in transaction and active
            if session.in_transaction() and session.is_active:
                try:
                    await session.commit()
                except (OperationalError, PendingRollbackError) as e:
                    db_logger.error(f"Error during commit: {e}")
                    if session.in_transaction():
                        await session.rollback()
                    raise
                except Exception as e:
                    db_logger.error(f"Unexpected error during commit: {e}")
                    if session.in_transaction():
                        await session.rollback()
                    raise

        except (OperationalError, PendingRollbackError) as e:
            db_logger.error(f"SQLAlchemy error in session: {e}")
            if session.in_transaction():
                try:
                    await session.rollback()
                except Exception as rollback_e:
                    db_logger.error(f"Error during rollback: {rollback_e}")
            raise

        except Exception as e:
            db_logger.error(f"Unexpected error in session: {e}")
            if session.in_transaction():
                try:
                    await session.rollback()
                except Exception as rollback_e:
                    db_logger.error(f"Error during rollback: {rollback_e}")
            raise

        finally:
            try:
                # Check for active transaction before closing
                if session.in_transaction():
                    try:
                        await session.rollback()
                        db_logger.debug("Rolled back active transaction during cleanup")
                    except Exception as rollback_e:
                        db_logger.error(
                            f"Error rolling back transaction during cleanup: {rollback_e}"
                        )

                await session.close()
                db_logger.debug("Session closed successfully")
            except Exception as e:
                db_logger.error(f"Error during session cleanup: {e}")

    async def cleanup(self) -> None:
        """Clean up all database connections.

        This method performs a complete cleanup of all database resources:
        1. Disposes async engine
        2. Disposes sync engine

        This is designed to be called during application shutdown.
        Thread-safe and idempotent - can be called multiple times safely.
        """
        # Check if cleanup already done
        if self._cleanup_done.is_set():
            db_logger.info("Database cleanup already completed, skipping")
            return

        # Acquire lock to prevent concurrent cleanup
        with self._cleanup_lock:
            # Double-check after acquiring lock
            if self._cleanup_done.is_set():
                db_logger.info(
                    "Database cleanup already completed (after lock), skipping"
                )
                return

            db_logger.info("Starting database cleanup...")

            try:
                # Dispose async engine (closes all connections in the pool)
                if hasattr(self, "_async_engine") and self._async_engine is not None:
                    try:
                        # dispose() will close all connections in the pool
                        # This is the correct way to clean up in PostgreSQL
                        await self._async_engine.dispose()
                        db_logger.info(
                            "Disposed async engine successfully (all async connections closed)"
                        )
                    except Exception as e:
                        db_logger.error(f"Error disposing async engine: {e}")

                # Dispose sync engine (closes all connections in the pool)
                if hasattr(self, "_sync_engine") and self._sync_engine is not None:
                    try:
                        # dispose() will close all connections in the pool
                        self._sync_engine.dispose()
                        db_logger.info(
                            "Disposed sync engine successfully (all sync connections closed)"
                        )
                    except Exception as e:
                        db_logger.error(f"Error disposing sync engine: {e}")

            except Exception as e:
                db_logger.error(f"Error during cleanup: {e}")
            finally:
                # Mark cleanup as done
                self._cleanup_done.set()
                db_logger.info("Database cleanup complete")

    def close_sync(self) -> None:
        """Synchronous cleanup for atexit handler.

        This method provides a synchronous version of cleanup that can be safely
        called from an atexit handler. It disposes sync-safe resources.
        Thread-safe and idempotent - can be called multiple times safely.
        """
        # Check if cleanup already done
        if self._cleanup_done.is_set():
            db_logger.info("Database cleanup already completed (sync), skipping")
            return

        # Acquire lock to prevent concurrent cleanup
        with self._cleanup_lock:
            # Double-check after acquiring lock
            if self._cleanup_done.is_set():
                db_logger.info(
                    "Database cleanup already completed (sync, after lock), skipping"
                )
                return

            db_logger.info("Starting sync database cleanup...")

            try:
                # Dispose sync engine (closes all connections in the pool)
                if hasattr(self, "_sync_engine") and self._sync_engine is not None:
                    try:
                        # dispose() will close all connections in the pool
                        self._sync_engine.dispose()
                        db_logger.info(
                            "Disposed sync engine (sync - all sync connections closed)"
                        )
                    except Exception as e:
                        db_logger.error(f"Error disposing sync engine (sync): {e}")

                # Don't try to dispose async engine in sync context
                # Async engine will be cleaned up by Python's GC or by async cleanup()

            except Exception as e:
                db_logger.error(f"Error during sync cleanup: {e}")
            finally:
                # Mark cleanup as done
                self._cleanup_done.set()
                db_logger.info("Sync database cleanup complete")

    def __del__(self) -> None:
        """Ensure cleanup on deletion."""
        with suppress(Exception):
            self.close_sync()
