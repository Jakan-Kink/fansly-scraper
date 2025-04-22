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
import traceback
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

# Import textio logger for user-facing progress messages
from textio import print_error, print_info, print_warning
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
        skip_migrations: bool = False,  # Add migration control
    ) -> None:
        """Initialize database manager.

        Args:
            config: FanslyConfig instance
            creator_name: Optional creator name for separate databases
            skip_migrations: Skip running migrations during initialization
        """
        # Add cleanup coordination flags
        self._cleanup_done = threading.Event()
        self._final_sync_done = (
            threading.Event()
        )  # New flag specifically for final sync
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
        self._force_sync = False  # Add flag for forced sync during shutdown

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
            """Configure connection on checkout.

            Args:
                dbapi_connection: The raw SQLite connection
                connection_record: The connection record containing metadata
            """
            # Set thread-local storage for connection
            if not hasattr(connection_record, "_thread_id"):
                connection_record._thread_id = threading.get_ident()

            # Configure SQLite connection
            dbapi_connection.execute("PRAGMA busy_timeout=60000")
            dbapi_connection.execute("PRAGMA temp_store=MEMORY")

        def _on_checkin(dbapi_connection, connection_record):
            """Clean up connection on checkin.

            Args:
                dbapi_connection: The raw SQLite connection
                connection_record: The connection record containing metadata
            """
            if hasattr(connection_record, "_thread_id"):
                del connection_record._thread_id

        def _on_checkout(dbapi_connection, connection_record, connection_proxy):
            """Verify connection on checkout.

            Args:
                dbapi_connection: The raw SQLite connection
                connection_record: The connection record containing metadata
                connection_proxy: The connection proxy

            Raises:
                DisconnectionError: If the connection was created in a different thread
            """
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

        # Create and keep a persistent connection for migrations and validation
        connection = self._sync_engine.connect()
        connection = connection.execution_options(
            expire_on_commit=False,  # Don't expire objects after commit
            preserve_session=True,  # Keep session alive
            keep_transaction=True,  # Keep transaction open
            close_with_result=False,  # Don't close after execute
        )

        alembic_cfg = AlembicConfig("alembic.ini")
        if not skip_migrations:
            self._run_migrations_if_needed(alembic_cfg)
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
        else:
            db_logger.info("Skipping migrations as requested.")

        self._sqlalchemy_connection = connection

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

    def _sync_to_disk(self, is_final_sync: bool = False) -> None:
        """Sync in-memory database to disk.

        This method performs an atomic write of the in-memory database to disk:
        1. Creates a temporary file for the new database
        2. Backs up the shared connection to the temporary file
        3. Verifies the backup integrity
        4. Creates a backup of the existing file
        5. Atomically moves the temporary file into place
        6. Uses fsync to ensure durability

        The operation is protected by a sync lock to prevent concurrent syncs.

        Args:
            is_final_sync: Whether this is the final sync during program shutdown
        """
        # Skip if final sync already done
        if is_final_sync and self._final_sync_done.is_set():
            db_logger.info("Final sync already performed, skipping")
            return

        # For final sync during shutdown, use longer timeout to ensure completion
        lock_timeout = 30 if is_final_sync else 10  # Give more time during final sync

        # Try to acquire lock with timeout
        acquired_lock = False
        try:
            # Check stop conditions first
            if not is_final_sync and self._stop_sync.is_set():
                db_logger.info("Skipping sync - stop requested")
                return

            # Try to acquire the lock first
            acquired_lock = self._sync_lock.acquire(timeout=lock_timeout)
            if not acquired_lock:
                if is_final_sync:
                    # During shutdown, try to detect if background thread has the lock
                    if (
                        hasattr(self, "_sync_thread")
                        and self._sync_thread
                        and self._sync_thread.is_alive()
                    ):
                        db_logger.warning(
                            "Background sync thread appears to be active - interrupting"
                        )
                        # Force the thread to stop
                        self._stop_sync.set()
                        try:
                            if hasattr(self._sync_thread, "_tstate_lock"):
                                self._sync_thread._tstate_lock.release()
                            if hasattr(self._sync_thread, "_stop"):
                                self._sync_thread._stop()
                        except Exception:
                            pass

                        # Short wait for thread to exit
                        try:
                            self._sync_thread.join(timeout=1)
                        except Exception:
                            pass

                        # Try to acquire lock again
                        acquired_lock = self._sync_lock.acquire(timeout=1)
                        if not acquired_lock:
                            db_logger.warning(
                                "Could not acquire lock - forcing final sync"
                            )
                            self._force_sync = True
                    else:
                        # Thread exited but lock is still held - try to recover
                        try:
                            # First try a clean release
                            try:
                                self._sync_lock.release()
                                db_logger.info("Successfully released stuck lock")
                            except Exception:
                                # If clean release fails, force it
                                self._sync_lock._value = 1
                                db_logger.warning("Forcefully released stuck lock")

                            acquired_lock = self._sync_lock.acquire(timeout=1)
                            if not acquired_lock:
                                db_logger.error(
                                    "Still could not acquire lock after force release"
                                )
                                self._force_sync = True
                        except Exception as e:
                            db_logger.error(f"Error handling stuck lock: {e}")
                            self._force_sync = True
                else:
                    db_logger.error("Could not acquire sync lock, skipping sync")
                    return

            start_time = time.time()
            try:
                # Use our existing shared connection as the source
                if not self._shared_connection:
                    db_logger.error("No shared connection available for sync")
                    return

                # Exit early if stopped and not final
                if not is_final_sync and self._stop_sync.is_set():
                    db_logger.info("Skipping sync - stop requested after lock")
                    return

                # Ensure we're in a good state for backup
                if is_final_sync:
                    try:
                        # Force rollback any pending transactions
                        self._shared_connection.execute("ROLLBACK")
                        # Checkpoint WAL if it exists
                        self._shared_connection.execute(
                            "PRAGMA wal_checkpoint(TRUNCATE)"
                        )
                    except Exception as e:
                        db_logger.warning(f"Error preparing for final backup: {e}")

                # Create a temp file for atomic writes
                temp_dir = self.db_file.parent
                temp_path = None
                temp_file = None

                try:
                    temp_file = tempfile.NamedTemporaryFile(dir=temp_dir, delete=False)
                    temp_path = Path(temp_file.name)
                    temp_file.close()  # Close but don't delete

                    # Create destination connection with proper settings
                    dest_conn = None
                    try:
                        dest_conn = sqlite3.connect(
                            temp_path,
                            isolation_level=None,
                            detect_types=sqlite3.PARSE_DECLTYPES
                            | sqlite3.PARSE_COLNAMES,
                            timeout=60,  # Higher timeout for backup
                        )

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

                        if is_final_sync:
                            print_info(f"Saving database to file: {self.db_file}")
                        else:
                            db_logger.info(
                                f"Saving in-memory db to file: {self.db_file}"
                            )

                        # Initialize progress tracking variables
                        total_pages = None
                        remaining_pages = None
                        last_progress_time = time.time()
                        last_progress_percent = -1

                        def progress(status, remaining, total):
                            nonlocal total_pages, remaining_pages, last_progress_time, last_progress_percent

                            # Check stop flag during backup
                            if not is_final_sync and self._stop_sync.is_set():
                                raise InterruptedError(
                                    "Backup interrupted - stop requested"
                                )

                            total_pages = total
                            remaining_pages = remaining

                            # Only report progress if some pages exist
                            if total > 0:
                                percent = 100.0 * (total - remaining) / total
                                current_time = time.time()

                                # For final sync, show progress on console and in debug log
                                if is_final_sync:
                                    # Always log to debug for consistent logging
                                    db_logger.debug(
                                        f"Backup progress: {percent:.1f}% ({remaining} pages remaining)"
                                    )

                                    # Show on console if significant change or enough time has passed
                                    if (
                                        (current_time - last_progress_time > 0.5)
                                        or (
                                            int(percent) > int(last_progress_percent)
                                            and int(percent) % 5 == 0
                                        )
                                        or (
                                            percent >= 99 and last_progress_percent < 99
                                        )
                                    ):
                                        print_info(
                                            f"Database sync progress: {percent:.1f}% ({remaining} pages remaining)"
                                        )
                                        last_progress_time = current_time
                                        last_progress_percent = percent
                                # For normal sync, just log to debug
                                else:
                                    db_logger.debug(
                                        f"Backup progress: {percent:.1f}% ({remaining} pages remaining)"
                                    )

                        # Check stop flag before starting backup
                        if not is_final_sync and self._stop_sync.is_set():
                            db_logger.info("Skipping backup - stop requested")
                            return

                        # Perform backup with progress callback and larger page size for speed
                        page_size = 5000  # Process 5000 pages at a time
                        backup_in_progress = False
                        try:
                            backup_in_progress = True
                            self._shared_connection.backup(
                                dest_conn, pages=page_size, progress=progress
                            )
                            backup_in_progress = False
                        except InterruptedError:
                            db_logger.info("Backup interrupted by stop request")
                            return
                        except Exception as backup_e:
                            if (
                                backup_in_progress
                                and not is_final_sync
                                and self._stop_sync.is_set()
                            ):
                                db_logger.info("Backup interrupted during stop")
                                return
                            raise backup_e

                        # Verify backup only if not interrupted
                        if not (not is_final_sync and self._stop_sync.is_set()):
                            source_tables = self._shared_connection.execute(
                                "SELECT name FROM sqlite_master WHERE type='table'"
                            ).fetchall()
                            dest_tables = dest_conn.execute(
                                "SELECT name FROM sqlite_master WHERE type='table'"
                            ).fetchall()

                            if {t[0] for t in source_tables} != {
                                t[0] for t in dest_tables
                            }:
                                raise RuntimeError(
                                    "Table verification failed after backup"
                                )

                        # Ensure all changes are written
                        dest_conn.commit()
                        dest_conn.execute("PRAGMA wal_checkpoint(FULL)")

                        # Explicitly close destination connection
                        dest_conn.close()
                        dest_conn = None

                        # Exit if stopped and not final
                        if not is_final_sync and self._stop_sync.is_set():
                            db_logger.info("Skipping file move - stop requested")
                            return

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

                        # Handle WAL and SHM files
                        for ext in ["-wal", "-shm"]:
                            temp_wal = Path(str(temp_path) + ext)
                            if temp_wal.exists():
                                dest_wal = Path(str(self.db_file) + ext)
                                shutil.copy2(temp_wal, dest_wal)
                                with open(dest_wal, "rb+") as f:
                                    f.flush()
                                    os.fsync(f.fileno())

                        # Report final size and time
                        final_size = self.db_file.stat().st_size
                        total_time = time.time() - start_time

                        # For final sync, use textio's print_info for better visibility
                        if is_final_sync:
                            print_info(
                                f"Database save complete. Size: {final_size / (1024*1024):.1f}MB, Time: {total_time:.2f}s"
                            )
                        else:
                            db_logger.info(
                                f"Save complete. File size: {final_size / (1024*1024):.1f}MB, Time: {total_time:.2f}s"
                            )

                        # Reset counters
                        self._commit_count = 0
                        self._last_sync = time.time()

                    finally:
                        # Clean up temp connection
                        if dest_conn:
                            try:
                                dest_conn.close()
                            except Exception as close_e:
                                db_logger.error(
                                    f"Error closing destination connection: {close_e}"
                                )

                finally:
                    # Clean up temp files
                    try:
                        if temp_path and temp_path.exists():
                            temp_path.unlink()
                        if temp_path:
                            for ext in ["-wal", "-shm"]:
                                temp_wal = Path(str(temp_path) + ext)
                                if temp_wal.exists():
                                    temp_wal.unlink()
                    except Exception as cleanup_error:
                        db_logger.error(
                            f"Error cleaning up temp files: {cleanup_error}"
                        )

            except Exception as e:
                if is_final_sync:
                    print_error(f"Error syncing database to disk: {e}")
                else:
                    db_logger.error(f"Error syncing to disk: {e}")

            # Mark final sync as done if this was a final sync
            if is_final_sync:
                self._final_sync_done.set()

        finally:
            # Handle lock release
            if acquired_lock:
                try:
                    self._sync_lock.release()
                except Exception as release_error:
                    if is_final_sync:
                        print_warning(
                            f"Error releasing sync lock during shutdown: {release_error}"
                        )
                    else:
                        db_logger.error(f"Error releasing sync lock: {release_error}")
            elif hasattr(self, "_force_sync") and self._force_sync:
                delattr(self, "_force_sync")  # Clean up force flag

    def _sync_task(self) -> None:
        """Background task to sync database to disk.

        This task periodically checks if a sync is needed based on commit count or time elapsed.
        It performs the sync operation and handles any exceptions that might occur during the process.
        """
        try:
            while not self._stop_sync.is_set():
                try:
                    # First check stop signal with no wait to exit quickly
                    if self._stop_sync.is_set():
                        db_logger.info("Sync thread stopping (immediate exit)")
                        break

                    # Use shorter timeouts to check stop condition more frequently
                    current_time = time.time()
                    # Use try/finally to ensure lock is always released
                    acquired = False
                    try:
                        # Try to acquire lock with very short timeout
                        acquired = self._sync_lock.acquire(timeout=0.1)  # 100ms timeout
                        if not acquired:
                            # If stopping, exit immediately
                            if self._stop_sync.is_set():
                                db_logger.info("Sync thread stopping (lock busy)")
                                break
                            time.sleep(0.1)
                            continue

                        commit_count = self._commit_count
                        last_sync = self._last_sync
                        sync_commits = self._sync_commits
                        sync_interval = self._sync_interval

                    finally:
                        if acquired:
                            self._sync_lock.release()

                    # Quick exit check after lock release
                    if self._stop_sync.is_set():
                        db_logger.info("Sync thread stopping (post-lock check)")
                        break

                    # Determine if sync is needed
                    if (
                        commit_count >= sync_commits
                        or current_time - last_sync >= sync_interval
                    ):
                        # Final check before starting sync
                        if self._stop_sync.is_set() or self._cleanup_done.is_set():
                            db_logger.info("Sync thread stopping (pre-sync)")
                            break

                        try:
                            # Only perform sync if not stopping
                            if not self._stop_sync.is_set():
                                self._sync_to_disk()
                        except Exception as sync_e:
                            db_logger.error(f"Error in background sync: {sync_e}")
                            # Don't retry immediately on error
                            time.sleep(0.1)  # Shorter sleep after error

                        # Exit immediately if stopped
                        if self._stop_sync.is_set():
                            db_logger.info("Sync thread stopping (post-sync)")
                            break

                    # Sleep in very small intervals while checking stop flag
                    sleep_until = time.time() + 0.2  # 200ms total sleep
                    while time.time() < sleep_until:
                        if self._stop_sync.is_set():
                            db_logger.info("Sync thread stopping (during sleep)")
                            return
                        time.sleep(0.01)  # 10ms intervals

                except Exception as e:
                    db_logger.error(f"Error in sync task: {e}")
                    if self._stop_sync.is_set():
                        db_logger.info("Sync thread stopping (after error)")
                        break
                    db_logger.debug(
                        f"Sync task error details: {traceback.format_exc()}"
                    )
                    time.sleep(0.1)  # Brief sleep after error

        finally:
            db_logger.info("Sync thread exiting")
            # DON'T try final sync here - let the main thread handle it
            self._stop_sync.set()  # Ensure stop flag is set

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

        This context manager handles session creation, transaction management,
        and proper cleanup, including session reuse for the same thread.

        Returns:
            SQLAlchemy Session object with active transaction

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

        This context manager provides comprehensive session management:
        1. Creates AsyncSession instances with async_sessionmaker
        2. Handles transactions and savepoints correctly
        3. Manages complete session lifecycle with proper cleanup
        4. Implements multi-stage recovery from transaction errors
        5. Uses task-local storage for efficient session reuse

        Error recovery includes these progressive stages:
        1. Savepoint recovery - Tries to rollback to last good savepoint
        2. Transaction rollback - Attempts full transaction rollback
        3. Connection reset - Resets the underlying connection state
        4. Session recreation - Creates a fresh session if needed
        5. Engine disposal - Last resort full engine cleanup and recreation

        Example:
            async with db.async_session_scope() as session:
                result = await session.execute(text("SELECT * FROM table"))
                data = await session.scalars(select(Model))
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
                # Verify the session is still valid
                await session_wrapper["session"].execute(text("SELECT 1"))
                try:
                    yield session_wrapper["session"]
                except (OperationalError, PendingRollbackError) as e:
                    # Handle SQLAlchemy specific errors
                    db_logger.error(f"SQLAlchemy error in nested session: {e}")
                    # Check if this is a savepoint/transaction error
                    error_msg = str(e).lower()
                    if any(
                        err_msg in error_msg
                        for err_msg in ["savepoint", "transaction", "connection"]
                    ):
                        db_logger.warning(
                            "Detected transaction error in nested session, attempting recovery"
                        )
                        await self._handle_savepoint_error(
                            session_wrapper["session"],
                            session_wrapper,
                            task_id,
                            task_local,
                        )
                    # Ensure proper decrement of depth before re-raising
                    session_wrapper["depth"] -= 1
                    raise
                except Exception as e:
                    # Handle other exceptions in nested sessions
                    db_logger.error(f"Unexpected error in nested session: {e}")
                    # Rollback if in transaction
                    if session_wrapper["session"].in_transaction():
                        try:
                            await session_wrapper["session"].rollback()
                            db_logger.debug("Rolled back nested session transaction")
                        except Exception as rollback_e:
                            db_logger.error(
                                f"Error rolling back nested session: {rollback_e}"
                            )
                    # Ensure proper decrement of depth before re-raising
                    session_wrapper["depth"] -= 1
                    raise
                return
            except Exception as e:
                db_logger.error(f"Session validation failed: {e}")
                # If validation fails, remove the session and continue to create a new one
                try:
                    await session_wrapper["session"].close()
                except Exception as close_e:
                    db_logger.error(f"Error closing invalid session: {close_e}")
                del task_local[task_id]

        # Create a new session wrapper with a depth counter starting at 1
        session = self._async_session_factory()
        session_wrapper = {"session": session, "depth": 1}
        task_local[task_id] = session_wrapper
        get_db_logger().setup_session_logging(session)

        try:
            try:
                yield session

                # Only commit if there are changes and no active transaction
                # and this is the outermost session scope
                if (
                    session.in_transaction()
                    and session.is_active
                    and session_wrapper["depth"] == 1
                ):
                    try:
                        await session.commit()
                        self._commit_count += 1
                    except (OperationalError, PendingRollbackError) as e:
                        # Handle SQLAlchemy specific errors during commit
                        db_logger.error(f"Error during commit: {e}")
                        error_msg = str(e).lower()
                        if any(
                            err_msg in error_msg
                            for err_msg in ["savepoint", "transaction", "connection"]
                        ):
                            db_logger.warning(
                                "Detected transaction error during commit, attempting recovery"
                            )
                            await self._handle_savepoint_error(
                                session, session_wrapper, task_id, task_local
                            )
                        raise
                    except Exception as e:
                        db_logger.error(f"Unexpected error during commit: {e}")
                        if session.in_transaction():
                            await session.rollback()
                        raise

            except (OperationalError, PendingRollbackError) as e:
                # Handle SQLAlchemy specific errors
                db_logger.error(f"SQLAlchemy error in session: {e}")
                error_msg = str(e).lower()
                if any(
                    err_msg in error_msg
                    for err_msg in ["savepoint", "transaction", "connection"]
                ):
                    db_logger.warning("Detected transaction error, attempting recovery")
                    await self._handle_savepoint_error(
                        session, session_wrapper, task_id, task_local
                    )
                elif session.in_transaction():
                    try:
                        await session.rollback()
                    except Exception as rollback_e:
                        db_logger.error(f"Error during rollback: {rollback_e}")
                raise

            except Exception as e:
                # Handle other exceptions
                db_logger.error(f"Unexpected error in session: {e}")
                if session.in_transaction():
                    try:
                        await session.rollback()
                    except (OperationalError, PendingRollbackError) as rollback_e:
                        db_logger.error(
                            f"SQLAlchemy error during rollback: {rollback_e}"
                        )
                        # Check if this is a savepoint error
                        error_msg = str(rollback_e).lower()
                        if any(
                            err_msg in error_msg
                            for err_msg in ["savepoint", "transaction", "connection"]
                        ):
                            db_logger.warning(
                                "Detected transaction error during rollback, attempting recovery"
                            )
                            await self._handle_savepoint_error(
                                session, session_wrapper, task_id, task_local
                            )
                    except Exception as rollback_e:
                        db_logger.error(
                            f"Unexpected error during rollback: {rollback_e}"
                        )
                raise

        finally:
            # Guard against potential KeyError if session_wrapper was removed by error handler
            if task_id not in task_local or "depth" not in session_wrapper:
                db_logger.warning(
                    "Session wrapper missing or incomplete during cleanup"
                )
                # Try to close session if it exists and remove from tracking
                if session and hasattr(session, "close"):
                    try:
                        await session.close()
                    except Exception as e:
                        db_logger.error(f"Error closing orphaned session: {e}")
                # Clean up task tracking if needed
                if task_id in task_local:
                    del task_local[task_id]
                return

            # Decrease nesting depth; if outermost, remove from tracking and close it
            session_wrapper["depth"] -= 1
            if session_wrapper["depth"] <= 0:
                try:
                    # Check for active transaction before closing
                    if session.in_transaction():
                        try:
                            await session.rollback()
                            db_logger.debug(
                                "Rolled back active transaction during cleanup"
                            )
                        except Exception as rollback_e:
                            db_logger.error(
                                f"Error rolling back transaction during cleanup: {rollback_e}"
                            )

                    await session.close()
                    db_logger.debug("Session closed successfully")
                except (OperationalError, PendingRollbackError) as e:
                    db_logger.error(f"SQLAlchemy error during session cleanup: {e}")
                    # Even for errors, we want to remove from tracking
                except Exception as e:
                    db_logger.error(f"Unexpected error during session cleanup: {e}")
                finally:
                    if task_id in task_local:
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
        """Clean up all database connections.

        This method performs a complete cleanup of all database resources:
        1. Stops the background sync thread
        2. Performs a final sync to disk (if not already done)
        3. Cleans up connections and engines
        4. Handles any pending sessions

        This is designed to be called during application shutdown.
        The sync cleanup path (close_sync) will skip its sync if this has already run.
        """
        # Check if cleanup already done
        if self._cleanup_done.is_set():
            db_logger.info("Cleanup already performed, skipping")
            return

        try:
            if hasattr(self, "_stop_sync"):  # Check if init completed
                # Stop sync thread first
                db_logger.info("Stopping database sync thread...")
                self._stop_sync.set()
                if self._sync_thread is not None:
                    # Try joining with increased timeout
                    join_timeout = 10  # 10 seconds timeout
                    db_logger.info(
                        f"Waiting up to {join_timeout} seconds for sync thread to exit..."
                    )
                    self._sync_thread.join(timeout=join_timeout)

                    # Check if thread actually terminated
                    if self._sync_thread.is_alive():
                        db_logger.warning(
                            "Sync thread did not exit within timeout, thread may still be running"
                        )

                # Perform final sync if not already done by background thread
                if not self._final_sync_done.is_set():
                    db_logger.info("Performing final database sync...")
                    try:
                        print_info("Syncing database to disk...")
                        sync_start = time.time()
                        self._sync_to_disk(is_final_sync=True)
                        sync_time = time.time() - sync_start
                        db_logger.info(
                            f"Final sync completed in {sync_time:.2f} seconds"
                        )
                    except Exception as sync_e:
                        db_logger.error(f"Error during final sync: {sync_e}")
                else:
                    db_logger.info("Final sync already performed by background thread")

                # Handle any pending async sessions
                try:
                    if hasattr(self._thread_local, "async_session"):
                        async_session = self._thread_local.async_session
                        if async_session.is_active:
                            try:
                                await async_session.rollback()
                                db_logger.info("Rolled back pending async transaction")
                            except Exception as rollback_e:
                                db_logger.error(
                                    f"Error rolling back async transaction: {rollback_e}"
                                )
                            finally:
                                await async_session.close()
                                db_logger.info("Closed active async session")
                except Exception as session_e:
                    db_logger.error(f"Error handling async session: {session_e}")

                # Handle any pending sync sessions
                try:
                    if hasattr(self._thread_local, "session"):
                        session = self._thread_local.session
                        if session.is_active:
                            try:
                                session.rollback()
                                db_logger.info("Rolled back pending sync transaction")
                            except Exception as rollback_e:
                                db_logger.error(
                                    f"Error rolling back sync transaction: {rollback_e}"
                                )
                            finally:
                                session.close()
                                db_logger.info("Closed active sync session")
                except Exception as session_e:
                    db_logger.error(f"Error handling sync session: {session_e}")

                # Close shared connection before engines
                if (
                    hasattr(self, "_shared_connection")
                    and self._shared_connection is not None
                ):
                    try:
                        try:
                            self._shared_connection.execute("ROLLBACK")
                            db_logger.info("Executed ROLLBACK on shared connection")
                        except Exception as direct_rollback_e:
                            db_logger.error(
                                f"Error executing ROLLBACK: {direct_rollback_e}"
                            )

                        self._shared_connection.close()
                        db_logger.info("Closed shared connection successfully")
                    except Exception as e:
                        db_logger.error(f"Error closing shared connection: {e}")

                # Dispose engines
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

        finally:
            # Mark cleanup as done
            self._cleanup_done.set()

    def close_sync(self) -> None:
        """Synchronous cleanup for atexit handler.

        This method provides a synchronous version of cleanup that can be safely
        called from an atexit handler. It will:
        1. Skip final sync if async cleanup already did it
        2. Otherwise, stop background thread and perform final sync
        3. Clean up sync-safe resources
        4. Leave async cleanup to garbage collector
        """
        # Check if cleanup already done
        if self._cleanup_done.is_set():
            db_logger.info("Cleanup already performed (sync), skipping")
            return

        try:
            if hasattr(self, "_stop_sync"):  # Check if init completed
                # Stop sync thread
                db_logger.info("Stopping database sync thread (sync)...")
                self._stop_sync.set()
                if self._sync_thread is not None:
                    join_timeout = 10  # 10 seconds timeout
                    db_logger.info(
                        f"Waiting up to {join_timeout} seconds for sync thread to exit (sync)..."
                    )
                    self._sync_thread.join(timeout=join_timeout)

                    if self._sync_thread.is_alive():
                        db_logger.warning(
                            "Sync thread did not exit within timeout (sync)"
                        )
                        # Force interrupt the thread if supported
                        try:
                            if hasattr(self._sync_thread, "_tstate_lock"):
                                self._sync_thread._tstate_lock.release()
                            if hasattr(self._sync_thread, "_stop"):
                                self._sync_thread._stop()
                        except Exception as e:
                            db_logger.error(f"Failed to force stop sync thread: {e}")

                # Only do final sync if async cleanup hasn't already done it
                if not self._final_sync_done.is_set():
                    db_logger.info("Performing final database sync (sync)...")
                    try:
                        sync_start = time.time()
                        self._sync_to_disk(is_final_sync=True)
                        sync_time = time.time() - sync_start
                        db_logger.info(
                            f"Final sync completed in {sync_time:.2f} seconds (sync)"
                        )
                    except Exception as sync_e:
                        db_logger.error(f"Error during final sync (sync): {sync_e}")
                else:
                    db_logger.info("Final sync already performed by async cleanup")

                # Handle any pending sync sessions
                try:
                    if hasattr(self._thread_local, "session"):
                        session = self._thread_local.session
                        if session.is_active:
                            try:
                                session.rollback()
                                db_logger.info("Rolled back pending transaction (sync)")
                            except Exception as rollback_e:
                                db_logger.error(
                                    f"Error rolling back transaction (sync): {rollback_e}"
                                )
                            finally:
                                session.close()
                                db_logger.info("Closed active session (sync)")
                except Exception as session_e:
                    db_logger.error(f"Error handling session (sync): {session_e}")

                # Close shared connection
                if (
                    hasattr(self, "_shared_connection")
                    and self._shared_connection is not None
                ):
                    try:
                        try:
                            self._shared_connection.execute("ROLLBACK")
                            db_logger.info(
                                "Executed ROLLBACK on shared connection (sync)"
                            )
                        except Exception as direct_rollback_e:
                            db_logger.error(
                                f"Error executing ROLLBACK (sync): {direct_rollback_e}"
                            )

                        self._shared_connection.close()
                        db_logger.info("Closed shared connection (sync)")
                    except Exception as e:
                        db_logger.error(f"Error closing shared connection (sync): {e}")

                # Close sync engine only
                if hasattr(self, "_sync_engine") and self._sync_engine is not None:
                    try:
                        self._sync_engine.dispose()
                        db_logger.info("Disposed sync engine (sync)")
                    except Exception as e:
                        db_logger.error(f"Error disposing sync engine (sync): {e}")

                # Don't try to dispose async engine in sync context
                # It will be cleaned up by Python's GC

                # Check for leaked semaphores
                monitor_semaphores(threshold=20)

        finally:
            # Mark cleanup as done
            self._cleanup_done.set()

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
