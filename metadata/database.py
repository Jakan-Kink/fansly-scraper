"""Database management with simplified resource handling."""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from functools import wraps
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DisconnectionError, OperationalError, PendingRollbackError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.elements import BindParameter

from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config as AlembicConfig
from config import db_logger
from utils.semaphore_monitor import monitor_semaphores

from .logging_config import DatabaseLogger

if TYPE_CHECKING:
    from config import FanslyConfig

# Set up database logging
logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)

# Global database logger
_db_logger: DatabaseLogger | None = None


def get_db_logger() -> DatabaseLogger:
    """Get the global database logger, initializing it if needed."""
    global _db_logger
    if _db_logger is None:
        _db_logger = DatabaseLogger()
    return _db_logger


RT = TypeVar("RT")  # Return type for decorator


def require_database_config(func: Callable[..., RT]) -> Callable[..., RT]:
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
        if config is None or not hasattr(config, "_database"):
            raise ValueError("Database configuration not found")
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> RT:
        """Sync wrapper that checks for database config."""
        config = get_config(*args, **kwargs)
        if config is None or not hasattr(config, "_database"):
            raise ValueError("Database configuration not found")
        return func(*args, **kwargs)

    return async_wrapper if is_async else sync_wrapper


class Database:
    """Database management with in-memory optimization.

    This class provides a streamlined approach to database management:
    - Uses in-memory SQLite with write-through caching
    - Leverages SQLite's built-in locking
    - Supports both sync and async operations
    - Handles network path optimization
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
    ) -> None:
        """Initialize database manager.

        Args:
            config: FanslyConfig instance
            creator_name: Optional creator name for separate databases
        """
        # Initialize all instance variables first to prevent cleanup errors
        self.config = config
        self.creator_name = creator_name
        self._sync_thread = None
        self._stop_sync = threading.Event()
        self._commit_count = 0
        self._last_sync = time.time()
        self._sync_lock = threading.Lock()
        self._thread_local = threading.local()
        self._prepared_statements = {}
        self._sync_engine = None
        self._async_engine = None
        self._sync_session_factory = None
        self._async_session_factory = None
        self._shared_connection = None  # Keeps in-memory database alive
        self._sqlalchemy_connection = None  # Keeps SQLAlchemy connection alive
        self._sync_interval = config.db_sync_seconds or 60
        self._sync_commits = config.db_sync_commits or 1000

        # Determine database path
        if creator_name and config.separate_metadata:
            # Use creator-specific database
            safe_name = "".join(c if c.isalnum() else "_" for c in creator_name)
            self.db_file = (
                config.metadata_db_file.parent / f"{safe_name}_metadata.sqlite3"
            )
        else:
            # Use global database
            self.db_file = config.metadata_db_file

        # Create shared memory URI
        if self.config.separate_metadata and self.creator_name:
            safe_name = "".join(c if c.isalnum() else "_" for c in self.creator_name)
            self.shared_uri = f"file:creator_{safe_name}?mode=memory&cache=shared"
        else:
            self.shared_uri = "file:global_db?mode=memory&cache=shared"
        # Create SQLAlchemy URI with proper shared memory syntax
        self.sqlalchemy_uri = f"sqlite:///{self.shared_uri}?uri=true"

        # Create initial in-memory database
        self._shared_connection = sqlite3.connect(
            self.shared_uri,
            uri=True,
            isolation_level=None,
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            timeout=60,  # 60 second connection timeout
        )
        # Configure SQLite for in-memory operation
        self._configure_memory_settings(self._shared_connection)

        # Prepare commonly used SQL statements
        self._prepare_statements()

        # Load existing database if it exists
        if self.db_file.exists():
            source_conn = sqlite3.connect(
                self.db_file,
                detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            )
            source_conn.backup(self._shared_connection)
            source_conn.close()

        # Keep the shared connection alive
        self._shared_connection = self._shared_connection

        # Create SQLAlchemy engine with optimized settings
        engine_config = {
            "future": True,  # Use SQLAlchemy 2.0 style
            "echo": False,  # Disable SQL echoing (we use our own logging)
            "poolclass": None,  # Disable pooling for in-memory DB with shared cache
            "connect_args": {
                "uri": True,  # Required for shared memory URIs
                "timeout": 60,  # Connection timeout in seconds
                "check_same_thread": False,  # Allow multi-threading
                "isolation_level": "SERIALIZABLE",  # SQLite's default transaction mode
                "cached_statements": 1000,  # Cache more prepared statements
            },
            "creator": lambda: self._shared_connection,  # Use our existing shared connection
            "execution_options": {
                "expire_on_commit": False,  # Don't expire objects after commit
                "preserve_session": True,  # Keep session alive
            },
        }

        # Create sync engine with optimized settings
        self._sync_engine = create_engine(
            self.sqlalchemy_uri,
            **engine_config,
        )

        # Set up event listeners for connection management
        def _on_connect(dbapi_connection, connection_record):
            """Configure connection on checkout."""
            # Set thread-local storage for connection
            if not hasattr(connection_record, "_thread_id"):
                connection_record._thread_id = threading.get_ident()

            # Configure SQLite connection
            dbapi_connection.execute("PRAGMA busy_timeout=60000")
            dbapi_connection.execute("PRAGMA temp_store=MEMORY")

        def _on_checkin(dbapi_connection, connection_record):
            """Clean up connection on checkin."""
            if hasattr(connection_record, "_thread_id"):
                del connection_record._thread_id

        def _on_checkout(dbapi_connection, connection_record, connection_proxy):
            """Verify connection on checkout."""
            if (
                hasattr(connection_record, "_thread_id")
                and connection_record._thread_id != threading.get_ident()
            ):
                # Connection was created in a different thread
                connection_proxy._pool.dispose()
                raise DisconnectionError(
                    "Connection was created in thread %s but checked out from thread %s"
                    % (connection_record._thread_id, threading.get_ident())
                )

        # Set up event listeners for connection management
        event.listen(self._sync_engine, "connect", _on_connect)
        event.listen(self._sync_engine, "checkin", _on_checkin)
        event.listen(self._sync_engine, "checkout", _on_checkout)

        # Set up logging for sync engine
        db_logger_monitor = get_db_logger()
        db_logger_monitor.setup_engine_logging(self._sync_engine)

        # Run migrations if needed
        alembic_cfg = AlembicConfig("alembic.ini")
        self._run_migrations_if_needed(alembic_cfg)

        # Create and keep a persistent connection for migrations and validation
        connection = self._sync_engine.connect()
        connection = connection.execution_options(
            expire_on_commit=False,  # Don't expire objects after commit
            preserve_session=True,  # Keep session alive
            keep_transaction=True,  # Keep transaction open
            close_with_result=False,  # Don't close after execute
        )

        # Check if alembic_version table exists
        result = connection.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
            )
        )
        alembic_version_exists = result.scalar() is not None

        if not alembic_version_exists:
            db_logger.info("No alembic_version table found. Initializing migrations...")
            alembic_cfg.attributes["connection"] = connection
            alembic_upgrade(alembic_cfg, "head")  # Run all migrations
            db_logger.info("Migrations applied successfully.")
        else:
            db_logger.info(
                "Database is already initialized. Running migrations to latest version..."
            )
            alembic_cfg.attributes["connection"] = connection
            alembic_upgrade(alembic_cfg, "head")
            db_logger.info("Migrations applied successfully.")

        # Keep the connection for later use
        self._sqlalchemy_connection = connection

        # Get logger and update its level from config
        db_logger_monitor = get_db_logger()
        db_logger_monitor.log_level = config.log_levels.get("sqlalchemy", "INFO")

        # Set up logging for sync engine
        db_logger_monitor.setup_engine_logging(self._sync_engine)

        # Create async engine with same settings
        async_config = engine_config.copy()
        async_config["connect_args"] = engine_config["connect_args"].copy()

        # Add additional connection settings for better reliability
        # Only add pool settings if we're not using StaticPool
        if "poolclass" not in async_config or async_config["poolclass"] is not None:
            async_config["pool_pre_ping"] = True  # Check connection health before using
            async_config["pool_recycle"] = 3600  # Recycle connections after 1 hour

            # Add more aggressive connection settings for better reliability
            async_config["max_overflow"] = 10  # Allow more overflow connections
            async_config["pool_size"] = 5  # Maintain a pool of connections

            # Add connect args for better reliability
            async_config["connect_args"][
                "timeout"
            ] = 120  # Increase timeout for busy operations

        # Add execution options for better transaction handling
        if "execution_options" not in async_config:
            async_config["execution_options"] = {}

        async_config["execution_options"].update(
            {
                "isolation_level": "SERIALIZABLE",  # Use serializable isolation for safety
                "autocommit": False,  # Explicit transaction control
            }
        )

        # Ensure connect_args has all the settings we need
        if "connect_args" not in async_config:
            async_config["connect_args"] = {}

        # Add SQLite-specific settings to connect_args
        async_config["connect_args"].update(
            {
                "timeout": 120,  # Wait up to 120 seconds for busy DB
                "check_same_thread": False,  # Allow cross-thread usage
            }
        )

        # Create the async engine with our config
        self._async_engine = create_async_engine(
            self.sqlalchemy_uri.replace("sqlite://", "sqlite+aiosqlite://"),
            **async_config,
        )
        # Set up logging for async engine
        db_logger_monitor.setup_engine_logging(self._async_engine)

        # Create session factories
        # Simple sync session factory
        Database._sync_session_factory = sessionmaker(
            bind=self._sync_engine,
            expire_on_commit=False,  # Don't expire objects after commit
        )

        # Create async session factory with sync session for lazy loading
        Database._async_session_factory = async_sessionmaker(
            bind=self._async_engine,
            expire_on_commit=False,  # Don't expire objects after commit
            sync_session_class=Database._sync_session_factory,  # Use sync session for lazy loading
            class_=AsyncSession,  # Ensure we get async sessions
        )

        # Use class-level factories for this instance
        self._sync_session_factory = Database._sync_session_factory
        self._async_session_factory = Database._async_session_factory

        # Thread-local storage for session reuse
        self._thread_local = threading.local()

        # Sync management
        self._sync_interval = config.db_sync_seconds or 60
        self._sync_commits = config.db_sync_commits or 1000
        self._commit_count = 0
        self._last_sync = time.time()
        self._sync_lock = threading.Lock()
        self._stop_sync = threading.Event()

        # Start sync thread
        self._sync_thread = Thread(
            target=self._sync_task,
            daemon=True,
            name=f"DBSync-{self.db_file.stem}",
        )
        self._sync_thread.start()

        # Register cleanup
        atexit.register(self.close_sync)

    def _sync_to_disk(self) -> None:
        """Sync in-memory database to disk."""
        with self._sync_lock:
            try:
                # Use our existing shared connection as the source
                if not self._shared_connection:
                    db_logger.error("No shared connection available for sync")
                    return

                # Create a temp file for atomic writes
                temp_dir = self.db_file.parent
                with tempfile.NamedTemporaryFile(
                    dir=temp_dir, delete=False
                ) as temp_file:
                    temp_path = Path(temp_file.name)

                try:
                    # Create destination connection with proper settings
                    dest_conn = sqlite3.connect(
                        temp_path,
                        isolation_level=None,
                        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
                    )

                    try:
                        # Configure destination for backup
                        dest_conn.execute(
                            "PRAGMA journal_mode=DELETE"
                        )  # Simpler journal for backup
                        dest_conn.execute(
                            "PRAGMA synchronous=FULL"
                        )  # Full sync for safety
                        dest_conn.execute(
                            "PRAGMA foreign_keys=OFF"
                        )  # Disable FKs for backup

                        db_logger.info(f"Saving in-memory db to file: {self.db_file}")
                        # Backup with progress reporting
                        total_pages = None
                        remaining_pages = None

                        def progress(status, remaining, total):
                            nonlocal total_pages, remaining_pages
                            total_pages = total
                            remaining_pages = remaining
                            if total > 0:
                                percent = 100.0 * (total - remaining) / total
                                from config import db_logger

                                db_logger.debug(
                                    f"Backup progress: {percent:.1f}% ({remaining} pages remaining)"
                                )

                        # Perform backup with progress callback
                        self._shared_connection.backup(
                            dest_conn, pages=1000, progress=progress
                        )

                        # Verify backup
                        source_tables = self._shared_connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                        dest_tables = dest_conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()

                        if {t[0] for t in source_tables} != {t[0] for t in dest_tables}:
                            raise RuntimeError("Table verification failed after backup")

                        # Ensure all changes are written
                        dest_conn.commit()
                        dest_conn.execute("PRAGMA wal_checkpoint(FULL)")

                    finally:
                        dest_conn.close()

                    # Move temp file into place
                    if self.db_file.exists():
                        # Create backup of existing file
                        backup_path = self.db_file.with_suffix(".sqlite3.bak")
                        shutil.copy2(self.db_file, backup_path)

                        # Copy WAL and SHM files if they exist
                        for ext in ["-wal", "-shm"]:
                            old_wal = Path(str(self.db_file) + ext)
                            if old_wal.exists():
                                backup_wal = Path(str(backup_path) + ext)
                                shutil.copy2(old_wal, backup_wal)

                    # Move new file into place with fsync
                    shutil.move(temp_path, self.db_file)
                    with open(self.db_file, "rb+") as f:
                        f.flush()
                        os.fsync(f.fileno())

                    # Copy WAL and SHM files if they exist
                    for ext in ["-wal", "-shm"]:
                        temp_wal = Path(str(temp_path) + ext)
                        if temp_wal.exists():
                            dest_wal = Path(str(self.db_file) + ext)
                            shutil.copy2(temp_wal, dest_wal)
                            with open(dest_wal, "rb+") as f:
                                f.flush()
                                os.fsync(f.fileno())

                    # Report final size
                    final_size = self.db_file.stat().st_size
                    db_logger.info(
                        f"Save complete. File size: {final_size / (1024*1024):.1f}MB"
                    )

                    # Reset counters
                    self._commit_count = 0
                    self._last_sync = time.time()

                finally:
                    # Clean up temp files
                    try:
                        if temp_path.exists():
                            temp_path.unlink()
                        for ext in ["-wal", "-shm"]:
                            temp_wal = Path(str(temp_path) + ext)
                            if temp_wal.exists():
                                temp_wal.unlink()
                    except Exception as cleanup_error:
                        db_logger.error(
                            f"Error cleaning up temp files: {cleanup_error}"
                        )

            except Exception as e:
                db_logger.error(f"Error syncing to disk: {e}")
                # Original file and its backup should still be intact

    def _sync_task(self) -> None:
        """Background task to sync database to disk."""
        while not self._stop_sync.is_set():
            try:
                # Check if sync needed
                current_time = time.time()
                if (
                    self._commit_count >= self._sync_commits
                    or current_time - self._last_sync >= self._sync_interval
                ):
                    self._sync_to_disk()

                # Sleep briefly
                time.sleep(1)

            except Exception as e:
                db_logger.error(f"Error in sync task: {e}")

    def _get_thread_session(self) -> Session | None:
        """Get existing session for current thread if healthy."""
        if hasattr(self._thread_local, "session"):
            session: Session = self._thread_local.session
            try:
                # Check if session is healthy
                with session.begin():
                    session.execute(text("SELECT 1"))
                return session
            except Exception:
                delattr(self._thread_local, "session")
        return None

    @contextmanager
    def session_scope(self) -> Generator[Session]:
        """Get a sync session with proper resource management.

        Returns:
            SQLAlchemy Session object

        Example:
            with db.session_scope() as session:
                result = session.execute(text("SELECT * FROM table"))
        """
        # Try to reuse existing session
        session = self._get_thread_session()
        if session is not None:
            yield session
            return

        # Create new session
        session = Database._sync_session_factory()
        self._thread_local.session = session
        # Set up logging for session
        get_db_logger().setup_session_logging(session)

        try:
            yield session
            if session.is_active:
                session.commit()
                self._commit_count += 1
        except Exception as e:
            db_logger.error(f"Error in sync session: {e}")
            if session.is_active:
                try:
                    session.rollback()
                except PendingRollbackError:
                    db_logger.warning(
                        "PendingRollbackError during session rollback, transaction already rolled back"
                    )
                except Exception as rollback_e:
                    db_logger.error(f"Error during session rollback: {rollback_e}")
            raise
        finally:
            try:
                session.close()
            except PendingRollbackError:
                db_logger.warning(
                    "PendingRollbackError during session close, transaction already rolled back"
                )
            except Exception as close_e:
                db_logger.error(f"Error during session close: {close_e}")

            # Always remove the session from thread local
            if hasattr(self._thread_local, "session"):
                delattr(self._thread_local, "session")

    @asynccontextmanager
    async def async_session_scope(self) -> AsyncGenerator[AsyncSession]:
        """Get an async session with proper resource management.

        This context manager:
        1. Reuses sessions within the same task
        2. Handles transactions and savepoints properly
        3. Manages session lifecycle
        4. Provides proper cleanup
        5. Implements robust recovery from transaction errors

        Example:
            async with db.async_session_scope() as session:
                result = await session.execute(text("SELECT * FROM table"))
        """
        task_id = id(asyncio.current_task())

        # Initialize thread-local storage if needed
        if not hasattr(self._thread_local, "async_sessions"):
            self._thread_local.async_sessions = {}
        task_local = self._thread_local.async_sessions

        # Check if a session is already in use for this task
        session_wrapper = task_local.get(task_id)
        if session_wrapper is not None:
            # Increase nesting depth and yield the existing session
            session_wrapper["depth"] += 1
            try:
                # Verify the session is still valid with a simple query

                db_logger.debug("Validating existing session")
                result = await session_wrapper["session"].execute(text("SELECT 1"))
                if result.scalar() != 1:
                    db_logger.warning(
                        "Session validation query returned unexpected result"
                    )
                    raise ValueError("Session validation failed: unexpected result")
                db_logger.debug("Existing session is valid")
            except (OperationalError, PendingRollbackError) as e:
                # Handle SQLAlchemy specific errors during validation

                db_logger.error(f"SQLAlchemy error during session validation: {e}")

                # Check if this is a savepoint or connection error
                error_msg = str(e).lower()
                if any(
                    err_msg in error_msg
                    for err_msg in ["savepoint", "transaction", "connection"]
                ):
                    db_logger.warning(
                        "Detected transaction error during validation, attempting recovery"
                    )
                    try:
                        # Try to recover the session
                        await self._handle_savepoint_error(
                            session_wrapper["session"],
                            session_wrapper,
                            task_id,
                            task_local,
                        )
                        # If recovery succeeded, session_wrapper will have a new session
                        db_logger.info("Session recovery successful during validation")
                    except Exception as recovery_e:
                        db_logger.error(
                            f"Session recovery failed during validation: {recovery_e}"
                        )
                        # If recovery failed, create a new session
                        del task_local[task_id]
                        session_wrapper = None
                else:
                    # For other errors, create a new session
                    db_logger.warning(
                        "Non-transaction error during validation, creating new session"
                    )
                    try:
                        await session_wrapper["session"].close()
                    except Exception:
                        pass  # Ignore errors during close of invalid session

                    del task_local[task_id]
                    session_wrapper = None
            except Exception as e:
                # Handle other errors during validation

                db_logger.error(f"Existing session validation failed: {e}")
                # If the session is invalid, close and remove it
                try:
                    await session_wrapper["session"].close()
                except Exception as close_e:
                    db_logger.debug(f"Error closing invalid session: {close_e}")

                del task_local[task_id]
                session_wrapper = None

        if session_wrapper is None:
            # Create a new session wrapper with a depth counter starting at 1
            session = self._async_session_factory()
            session_wrapper = {"session": session, "depth": 1}
            task_local[task_id] = session_wrapper
            get_db_logger().setup_session_logging(session)

        session: AsyncSession = session_wrapper["session"]

        try:
            yield session

            # Only commit if there are changes and no active transaction
            # and this is the outermost session scope
            if (
                session.in_transaction()
                and session.is_active
                and session_wrapper["depth"] == 1
            ):
                await session.commit()
                self._commit_count += 1

        except (OperationalError, PendingRollbackError) as e:
            # Handle SQLAlchemy specific errors

            db_logger.error(f"SQLAlchemy error in async session: {e}")

            # Check for savepoint errors specifically
            error_msg = str(e).lower()
            is_savepoint_error = any(
                err_msg in error_msg
                for err_msg in [
                    "no such savepoint",
                    "invalid savepoint",
                    "can't reconnect until invalid savepoint",
                    "savepoint",  # Catch any savepoint-related errors
                    "transaction",  # Catch transaction-related errors
                ]
            )

            # Also check for connection errors
            is_connection_error = any(
                err_msg in error_msg
                for err_msg in [
                    "database is locked",
                    "unable to open database file",
                    "disk i/o error",
                    "database or disk is full",
                    "database disk image is malformed",
                    "connection",  # Catch any connection-related errors
                ]
            )

            if is_savepoint_error or is_connection_error:
                # Use our aggressive recovery mechanism
                db_logger.warning(
                    f"Detected {'savepoint' if is_savepoint_error else 'connection'} error, initiating recovery"
                )
                await self._handle_savepoint_error(
                    session, session_wrapper, task_id, task_local
                )
            elif session.in_transaction():
                # For other operational errors, try standard rollback
                try:
                    db_logger.info(
                        "Attempting standard rollback for non-savepoint error"
                    )
                    await session.rollback()
                    db_logger.info("Standard rollback successful")
                except Exception as rollback_e:
                    db_logger.error(f"Error during standard rollback: {rollback_e}")
                    # If rollback fails, try recovery
                    db_logger.warning("Standard rollback failed, initiating recovery")
                    await self._handle_savepoint_error(
                        session, session_wrapper, task_id, task_local
                    )

            # Re-raise the original exception with context
            db_logger.error(
                f"Re-raising original exception after recovery attempt: {e}"
            )
            raise

        except Exception as e:
            # Handle general exceptions

            db_logger.error(f"General error in async session: {e}")

            if session.in_transaction():
                try:
                    # Standard rollback for general exceptions
                    db_logger.info("Attempting standard rollback for general exception")
                    await session.rollback()
                    db_logger.info("Standard rollback successful for general exception")
                except (OperationalError, PendingRollbackError) as rollback_e:
                    # Handle SQLAlchemy specific errors during rollback
                    db_logger.error(f"SQLAlchemy error during rollback: {rollback_e}")

                    # Check if this is a savepoint error
                    error_msg = str(rollback_e).lower()
                    if any(
                        err_msg in error_msg
                        for err_msg in ["savepoint", "transaction", "connection"]
                    ):
                        db_logger.warning(
                            "Detected savepoint/transaction error during rollback, initiating recovery"
                        )
                        await self._handle_savepoint_error(
                            session, session_wrapper, task_id, task_local
                        )
                except Exception as rollback_e:
                    # Handle other errors during rollback
                    db_logger.error(
                        f"Error during rollback for general exception: {rollback_e}"
                    )
                    # If rollback fails, try recovery
                    db_logger.warning(
                        "Standard rollback failed for general exception, initiating recovery"
                    )
                    await self._handle_savepoint_error(
                        session, session_wrapper, task_id, task_local
                    )

            # Re-raise the original exception with context
            db_logger.error(
                f"Re-raising original exception after recovery attempt: {e}"
            )
            raise

        finally:
            # Decrease nesting depth; if outermost, remove from tracking and close it
            if session_wrapper is not None:  # Check if session_wrapper still exists
                session_wrapper["depth"] -= 1
                if session_wrapper["depth"] <= 0:
                    # Only close the session if it's the outermost scope
                    try:
                        # Check if session is still valid before closing
                        if session.is_active:
                            db_logger.debug("Closing session in finally block")
                            await session.close()
                            db_logger.debug("Session closed successfully")
                        else:
                            db_logger.debug(
                                "Session already closed or invalid, skipping close"
                            )
                    except (OperationalError, PendingRollbackError) as e:
                        # Handle SQLAlchemy specific errors during close
                        db_logger.error(
                            f"SQLAlchemy error closing session in finally block: {e}"
                        )
                        # No need to recover here as we're already cleaning up
                    except Exception as e:
                        # Handle other errors during close
                        db_logger.error(f"Error closing session in finally block: {e}")
                    finally:
                        # Always remove from tracking even if close fails
                        if task_id in task_local:
                            db_logger.debug(f"Removing task {task_id} from tracking")
                            del task_local[task_id]

    async def _handle_savepoint_error(
        self,
        session: AsyncSession,
        session_wrapper: dict,
        task_id: int,
        task_local: dict,
    ) -> None:
        """Handle savepoint errors with aggressive recovery.

        This method implements a multi-stage recovery process for SQLite savepoint errors:
        1. Try to reset the connection state with ROLLBACK
        2. Try to reset the connection with raw DBAPI rollback
        3. Try to invalidate the connection and test new connections
        4. Try to force close the problematic session
        5. Try to dispose the engine with close=True and recreate session factory
        6. Create a new session and verify it works

        Args:
            session: The problematic session
            session_wrapper: The session wrapper containing the session and depth
            task_id: The task ID for tracking
            task_local: The task-local storage for sessions
        """
        # Use the imported db_logger directly
        db_logger.warning("Starting savepoint error recovery process")

        # Stage 1: Try to reset the connection state with ROLLBACK
        db_logger.debug("Stage 1: Attempting ROLLBACK")
        try:
            # Try a simple rollback first
            await session.rollback()
            # Test if the session is now usable
            await session.execute(text("SELECT 1"))
            db_logger.info("Stage 1 recovery successful: ROLLBACK fixed the session")
            return
        except Exception as e:
            db_logger.warning(f"Stage 1 recovery failed: {e}")

        # Stage 2: Try to reset the connection with raw DBAPI rollback
        db_logger.debug("Stage 2: Attempting raw DBAPI rollback")
        try:
            # Get the raw connection and try a direct rollback
            raw_connection = await session.connection()
            dbapi_connection = raw_connection.connection.connection
            if hasattr(dbapi_connection, "rollback"):
                dbapi_connection.rollback()
                # Test if the session is now usable
                await session.execute(text("SELECT 1"))
                db_logger.info(
                    "Stage 2 recovery successful: Raw DBAPI rollback fixed the session"
                )
                return
        except Exception as e:
            db_logger.warning(f"Stage 2 recovery failed: {e}")

        # Stage 3: Try to invalidate the connection and test new connections
        db_logger.debug("Stage 3: Attempting connection invalidation")
        try:
            # Get the connection and invalidate it
            connection = await session.connection()
            connection.invalidate()
            # Test if a new connection works
            async with self._async_engine.connect() as test_conn:
                await test_conn.execute(text("SELECT 1"))
            db_logger.info(
                "Stage 3 recovery successful: Connection invalidation worked"
            )
        except Exception as e:
            db_logger.warning(f"Stage 3 recovery failed: {e}")

        # Stage 4: Try to force close the problematic session
        db_logger.debug("Stage 4: Forcing session close")
        try:
            # Force close the session
            await session.close()
            db_logger.info("Stage 4: Session closed")
        except Exception as e:
            db_logger.warning(f"Stage 4: Error closing session: {e}")

        # Stage 5: Try to dispose the engine and recreate session factory
        db_logger.debug("Stage 5: Disposing engine and recreating session factory")
        try:
            # Dispose the engine with close=True to close all connections
            await self._async_engine.dispose(close=True)

            # Recreate the engine with the same settings
            async_config = {
                "echo": self._async_engine.echo,
                "pool_pre_ping": True,
                "pool_recycle": 3600,
                "pool_size": 5,
                "max_overflow": 10,
                "connect_args": {
                    "timeout": 120,
                    "check_same_thread": False,
                },
                "execution_options": {
                    "isolation_level": "SERIALIZABLE",
                    "autocommit": False,
                },
            }

            # Create a new engine
            self._async_engine = create_async_engine(
                self.sqlalchemy_uri.replace("sqlite://", "sqlite+aiosqlite://"),
                **async_config,
            )

            # Create a new session factory
            Database._async_session_factory = async_sessionmaker(
                bind=self._async_engine,
                expire_on_commit=False,
                class_=AsyncSession,
            )

            db_logger.info("Stage 5: Engine disposed and recreated")
        except Exception as e:
            db_logger.error(f"Stage 5: Error disposing engine: {e}")

        # Stage 6: Create a new session and verify it works
        db_logger.debug("Stage 6: Creating new session")
        try:
            # Create a new session
            new_session = self._async_session_factory()

            # Test if the new session works
            await new_session.execute(text("SELECT 1"))

            # Update the session wrapper with the new session
            session_wrapper["session"] = new_session
            task_local[task_id] = session_wrapper

            db_logger.info("Stage 6: New session created and verified")
        except Exception as e:
            db_logger.error(f"Stage 6: Error creating new session: {e}")
            # If we can't create a new session, remove the task from tracking
            if task_id in task_local:
                del task_local[task_id]
            # Re-raise to signal that recovery failed
            raise RuntimeError(f"Failed to recover from savepoint error: {e}") from e

    async def cleanup(self) -> None:
        """Clean up all database connections."""
        if hasattr(self, "_stop_sync"):  # Check if init completed
            # Stop sync thread
            self._stop_sync.set()
            if self._sync_thread is not None:
                self._sync_thread.join(timeout=5)

            # Final sync
            self._sync_to_disk()

            # Handle any pending async sessions before closing
            try:
                # Check if there's an active async session and handle pending transactions
                if hasattr(self._thread_local, "async_session"):
                    async_session = self._thread_local.async_session
                    if async_session.is_active:
                        try:
                            # Try to rollback any pending transactions
                            await async_session.rollback()
                            db_logger.info(
                                "Rolled back pending async transaction during cleanup"
                            )
                        except Exception as rollback_e:
                            db_logger.error(
                                f"Error rolling back async transaction during cleanup: {rollback_e}"
                            )
                        finally:
                            # Close the session
                            await async_session.close()
                            db_logger.info("Closed active async session during cleanup")
            except Exception as session_e:
                db_logger.error(
                    f"Error handling active async session during cleanup: {session_e}"
                )

            # Handle any pending sync sessions before closing
            try:
                # Check if there's an active session and handle pending transactions
                if hasattr(self._thread_local, "session"):
                    session = self._thread_local.session
                    if session.is_active:
                        try:
                            # Try to rollback any pending transactions
                            session.rollback()
                            db_logger.info(
                                "Rolled back pending transaction during cleanup"
                            )
                        except Exception as rollback_e:
                            db_logger.error(
                                f"Error rolling back transaction during cleanup: {rollback_e}"
                            )
                        finally:
                            # Close the session
                            session.close()
                            db_logger.info("Closed active session during cleanup")
            except Exception as session_e:
                db_logger.error(
                    f"Error handling active session during cleanup: {session_e}"
                )

            # Close shared connection first to properly close in-memory database
            if (
                hasattr(self, "_shared_connection")
                and self._shared_connection is not None
            ):
                try:
                    # Execute a rollback directly on the connection to clear any pending transactions
                    try:
                        self._shared_connection.execute("ROLLBACK")
                        db_logger.info(
                            "Executed ROLLBACK on shared connection before closing"
                        )
                    except Exception as direct_rollback_e:
                        db_logger.error(
                            f"Error executing direct ROLLBACK: {direct_rollback_e}"
                        )

                    # Now close the connection
                    self._shared_connection.close()
                    db_logger.info("Closed shared connection successfully")
                except Exception as e:
                    db_logger.error(f"Error closing shared connection: {e}")

            # Then close engines
            if hasattr(self, "_sync_engine") and self._sync_engine is not None:
                try:
                    self._sync_engine.dispose()
                    db_logger.info("Disposed sync engine successfully")
                except Exception as e:
                    db_logger.error(f"Error disposing sync engine: {e}")

            if hasattr(self, "_async_engine") and self._async_engine is not None:
                try:
                    await self._async_engine.dispose()
                    db_logger.info("Disposed async engine successfully")
                except Exception as e:
                    db_logger.error(f"Error disposing async engine: {e}")

    def close_sync(self) -> None:
        """Synchronous cleanup for atexit handler."""
        if hasattr(self, "_stop_sync"):  # Check if init completed
            # Stop sync thread
            self._stop_sync.set()
            if self._sync_thread is not None:
                self._sync_thread.join(timeout=5)

            # Handle any pending sessions before closing
            try:
                # Check if there's an active session and handle pending transactions
                if hasattr(self._thread_local, "session"):
                    session = self._thread_local.session
                    if session.is_active:
                        try:
                            # Try to rollback any pending transactions
                            session.rollback()
                            db_logger.info(
                                "Rolled back pending transaction during cleanup"
                            )
                        except Exception as rollback_e:
                            db_logger.error(
                                f"Error rolling back transaction during cleanup: {rollback_e}"
                            )
                        finally:
                            # Close the session
                            session.close()
                            db_logger.info("Closed active session during cleanup")
            except Exception as session_e:
                db_logger.error(
                    f"Error handling active session during cleanup: {session_e}"
                )

            # Close shared connection first to properly close in-memory database
            if (
                hasattr(self, "_shared_connection")
                and self._shared_connection is not None
            ):
                try:
                    # Execute a rollback directly on the connection to clear any pending transactions
                    try:
                        self._shared_connection.execute("ROLLBACK")
                        db_logger.info(
                            "Executed ROLLBACK on shared connection before closing"
                        )
                    except Exception as direct_rollback_e:
                        db_logger.error(
                            f"Error executing direct ROLLBACK: {direct_rollback_e}"
                        )

                    # Now close the connection
                    self._shared_connection.close()
                    db_logger.info("Closed shared connection successfully")
                except Exception as e:
                    db_logger.error(f"Error closing shared connection: {e}")

            # Then close engines
            if hasattr(self, "_sync_engine") and self._sync_engine is not None:
                try:
                    self._sync_engine.dispose()
                    db_logger.info("Disposed sync engine successfully")
                except Exception as e:
                    db_logger.error(f"Error disposing sync engine: {e}")

            # Check for leaked semaphores
            monitor_semaphores(threshold=20)  # Warn if too many semaphores

            # Don't try to dispose async engine in sync context
            # It will be cleaned up by Python's GC

    def __del__(self) -> None:
        """Ensure cleanup on deletion."""
        try:
            self.close_sync()
        except Exception:
            # Ignore errors during shutdown
            pass

    @contextmanager
    def batch_sync_settings(
        self,
        commit_threshold: int | None = None,
        interval: int | None = None,
    ) -> Generator[None]:
        """Temporarily adjust sync settings for batch operations.

        Args:
            commit_threshold: Number of commits between syncs
            interval: Seconds between syncs

        Example:
            with db.batch_sync_settings(commit_threshold=1000, interval=60):
                # Perform batch operations
                ...
        """
        old_commits = self._sync_commits
        old_interval = self._sync_interval

        if commit_threshold is not None:
            self._sync_commits = commit_threshold
        if interval is not None:
            self._sync_interval = interval

        try:
            yield
        finally:
            self._sync_commits = old_commits
            self._sync_interval = old_interval

    # region Database Methods
    def _run_migrations_if_needed(self, alembic_cfg: AlembicConfig) -> None:
        """Run database migrations if needed.

        Args:
            alembic_cfg: Alembic configuration for migrations
        """
        # Set the correct shared memory URI in Alembic config
        alembic_cfg.set_main_option("sqlalchemy.url", self.sqlalchemy_uri)
        db_logger.info(
            f"Running migrations on shared memory database: {self.sqlalchemy_uri}"
        )

        # Create persistent connection for migrations
        connection = self._sync_engine.connect()
        connection = connection.execution_options(
            isolation_level="SERIALIZABLE",
            expire_on_commit=False,
            autocommit=False,
            preserve_session=True,
            keep_transaction=True,
            close_with_result=False,
        )

        try:
            # Start a transaction to keep the connection open
            with connection.begin():
                # Check if alembic_version table exists
                result = connection.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
                    )
                )
                alembic_version_exists = result.scalar() is not None

                if not alembic_version_exists:
                    db_logger.info(
                        "No alembic_version table found. Initializing migrations..."
                    )
                    alembic_cfg.attributes["connection"] = connection
                    alembic_upgrade(alembic_cfg, "head")  # Run all migrations
                    db_logger.info("Migrations applied successfully.")
                else:
                    db_logger.info(
                        "Database is already initialized. Running migrations to latest version..."
                    )
                    alembic_cfg.attributes["connection"] = connection
                    alembic_upgrade(alembic_cfg, "head")
                    db_logger.info("Migrations applied successfully.")

            # Create a new connection for validation
            validation_connection = self._sync_engine.connect().execution_options(
                isolation_level="SERIALIZABLE",
                expire_on_commit=False,
                autocommit=False,
                preserve_session=True,
                keep_transaction=True,
                close_with_result=False,
            )
            # Get raw SQLite connection and validate prepared statements
            raw_conn = validation_connection.connection
            self._validate_prepared_statements(raw_conn)

            # Keep the validation connection for later use
            self._sqlalchemy_connection = validation_connection
        except Exception as e:
            db_logger.error(f"Error running migrations: {e}")
            connection.close()
            raise

    def _validate_prepared_statements(self, conn: sqlite3.Connection) -> None:
        """Validate prepared statements using SQLAlchemy.

        This runs EXPLAIN QUERY PLAN on each statement to:
        1. Verify the SQL is valid
        2. Cache the query plan
        3. Catch any table/schema issues
        """
        # Create a temporary SQLAlchemy engine for validation
        from sqlalchemy import create_engine

        engine = create_engine(
            "sqlite://",
            creator=lambda: conn,
        )

        # Validate each statement
        with engine.connect() as connection:
            for name, stmt in self._prepared_statements.items():
                try:
                    bind_params = [
                        p for p in stmt.get_children() if isinstance(p, BindParameter)
                    ]
                    # Create dummy parameters
                    params = {param.key: "1" for param in bind_params}
                    # Execute EXPLAIN QUERY PLAN
                    connection.execute(
                        text(f"EXPLAIN QUERY PLAN {stmt.text}"),
                        params,
                    )
                except Exception as e:
                    db_logger.error(f"Error validating statement '{name}': {e}")
                    db_logger.error(f"SQL: {stmt.text}")
                    raise

    def _prepare_statements(self) -> None:
        """Prepare commonly used SQL statements.

        This method creates SQLAlchemy text() objects with named parameters
        for better performance and safety.
        """
        from sqlalchemy.sql import bindparam

        # Define statements with their SQL
        self._prepared_statements = {
            "find_hashtag": text(
                "SELECT id, value FROM hashtags WHERE lower(value) = lower(:value)"
            ).bindparams(bindparam("value")),
            "find_hashtags_batch": text(
                "SELECT id, value FROM hashtags WHERE lower(value) IN ("
                "    SELECT lower(value) FROM json_each(:values)"
                ")"
            ).bindparams(bindparam("values")),
            "find_post_mentions": text(
                "SELECT * FROM post_mentions "
                "WHERE postId = :post_id "
                "AND (:account_id IS NULL OR accountId = :account_id) "
                "AND (:handle IS NULL OR handle = :handle)"
            ).bindparams(
                bindparam("post_id"),
                bindparam("account_id"),
                bindparam("handle"),
            ),
            "find_media_by_hash": text(
                "SELECT * FROM media WHERE content_hash = :content_hash"
            ).bindparams(bindparam("content_hash")),
            "find_wall_posts": text(
                "SELECT p.* "
                "FROM posts p "
                "JOIN wall_posts wp ON p.id = wp.postId "
                "WHERE wp.wallId = :wall_id "
                "ORDER BY p.createdAt DESC "
                "LIMIT :limit OFFSET :offset"
            ).bindparams(
                bindparam("wall_id"),
                bindparam("limit"),
                bindparam("offset"),
            ),
            "find_post_attachments": text(
                "SELECT a.* "
                "FROM attachments a "
                "WHERE a.postId = :post_id "
                "ORDER BY a.pos"
            ).bindparams(bindparam("post_id")),
        }

    def _configure_memory_settings(self, conn: sqlite3.Connection) -> None:
        """Configure SQLite connection for optimal memory performance.

        Args:
            conn: SQLite connection to configure
        """
        # Memory-specific optimizations
        conn.execute("PRAGMA journal_mode=MEMORY")  # In-memory journal
        conn.execute("PRAGMA synchronous=OFF")  # No disk syncs needed
        conn.execute("PRAGMA cache_size=-200000")  # 200MB cache
        conn.execute("PRAGMA temp_store=MEMORY")  # Store temp tables in memory
        conn.execute("PRAGMA page_size=4096")  # Optimal page size
        conn.execute("PRAGMA mmap_size=1073741824")  # 1GB memory mapping
        conn.execute("PRAGMA threads=4")  # Use multiple threads

        # Query optimization
        conn.execute(
            "PRAGMA automatic_index=TRUE"
        )  # Allow SQLite to create temp indexes
        conn.execute("PRAGMA optimize")  # Run internal optimizations

        # General optimizations
        conn.execute("PRAGMA locking_mode=NORMAL")  # Better concurrency
        conn.execute("PRAGMA busy_timeout=30000")  # 30 second timeout

    def get_shared_connection(self) -> sqlite3.Connection | None:
        """Get the shared connection to the in-memory database.

        Returns the single shared connection that is kept alive
        for the lifetime of the database.
        """
        return self._shared_connection

    def find_hashtag(self, value: str) -> tuple | None:
        """Find hashtag by value (case-insensitive)."""
        with self.session_scope() as session:
            result = session.execute(
                self._prepared_statements["find_hashtag"],
                {"value": value},
            )
            return result.fetchone()

    def find_hashtags_batch(self, values: list[str]) -> list[tuple]:
        """Find multiple hashtags by value (case-insensitive)."""
        with self.session_scope() as session:
            result = session.execute(
                self._prepared_statements["find_hashtags_batch"],
                {"values": json.dumps(values)},
            )
            return result.fetchall()

    def find_post_mentions(
        self,
        post_id: int,
        account_id: int | None = None,
        handle: str | None = None,
    ) -> list[tuple]:
        """Find mentions for a post."""
        with self.session_scope() as session:
            result = session.execute(
                self._prepared_statements["find_post_mentions"],
                {
                    "post_id": post_id,
                    "account_id": account_id,
                    "handle": handle,
                },
            )
            return result.fetchall()

    def find_media_by_hash(self, content_hash: str) -> tuple | None:
        """Find media by content hash."""
        with self.session_scope() as session:
            result = session.execute(
                self._prepared_statements["find_media_by_hash"],
                {"content_hash": content_hash},
            )
            return result.fetchone()

    def find_wall_posts(
        self,
        wall_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple]:
        """Find posts in a wall with pagination."""
        with self.session_scope() as session:
            result = session.execute(
                self._prepared_statements["find_wall_posts"],
                {
                    "wall_id": wall_id,
                    "limit": limit,
                    "offset": offset,
                },
            )
            return result.fetchall()

    def find_post_attachments(self, post_id: int) -> list[tuple]:
        """Find attachments for a post ordered by position."""
        with self.session_scope() as session:
            result = session.execute(
                self._prepared_statements["find_post_attachments"],
                {"post_id": post_id},
            )
            return result.fetchall()

    # endregion
