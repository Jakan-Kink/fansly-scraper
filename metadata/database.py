"""Database management module with write-through caching.

This module provides database configuration, connection management, and migration
handling for SQLite databases. It supports both synchronous and asynchronous
operations, with proper connection pooling, event handling, and memory-optimized
caching for databases under 1GB.

The module includes:
- Database configuration and initialization
- Migration management through Alembic
- Session management for database operations
- Logging configuration for SQLAlchemy
- Memory-optimized SQLite with write-through caching
- Support for both global and per-creator databases
"""

from __future__ import annotations

import asyncio
import atexit
import functools
import hashlib
import logging
import random
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
from pathlib import Path
from threading import local
from typing import TYPE_CHECKING, TypeVar

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config as AlembicConfig
from pathio import get_creator_database_path as _get_creator_database_path
from textio import (
    SizeAndTimeRotatingFileHandler,
    print_error,
    print_info,
    print_warning,
)

if TYPE_CHECKING:
    from config import FanslyConfig


RT = TypeVar("RT")

MAX_RETRIES = 5  # Increased from 3
BASE_RETRY_DELAY = 0.1  # seconds, start with a shorter delay
MAX_RETRY_DELAY = 5.0  # seconds, but cap the max delay


def _calculate_retry_delay(attempt: int) -> float:
    """Calculate delay for retry with exponential backoff and jitter.

    Args:
        attempt: Current retry attempt number

    Returns:
        Delay in seconds
    """
    return min(
        BASE_RETRY_DELAY * (2**attempt) * (1 + random.random()),
        MAX_RETRY_DELAY,
    )


def _is_locked_error(error: Exception) -> bool:
    """Check if error is a database locked error.

    Args:
        error: Exception to check

    Returns:
        True if error is a database locked error
    """
    return isinstance(error, DatabaseError) and "database is locked" in str(error)


async def _retry_async(func: Callable[..., RT], args: tuple, kwargs: dict) -> RT:
    """Retry an async function with exponential backoff.

    Args:
        func: Async function to retry
        args: Positional arguments for the function
        kwargs: Keyword arguments for the function

    Returns:
        Result from the function

    Raises:
        Exception: Last error encountered if all retries fail
    """
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if not _is_locked_error(e):
                raise
            last_error = e
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(_calculate_retry_delay(attempt))
    raise last_error


def _retry_sync(func: Callable[..., RT], args: tuple, kwargs: dict) -> RT:
    """Retry a sync function with exponential backoff.

    Args:
        func: Sync function to retry
        args: Positional arguments for the function
        kwargs: Keyword arguments for the function

    Returns:
        Result from the function

    Raises:
        Exception: Last error encountered if all retries fail
    """
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if not _is_locked_error(e):
                raise
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(_calculate_retry_delay(attempt))
    raise last_error


def retry_on_locked_db(func: Callable[..., RT]) -> Callable[..., RT]:
    """Decorator to retry operations when database is locked.

    Uses exponential backoff with jitter for retries.
    For async functions, uses asyncio.sleep instead of time.sleep.

    Args:
        func: Function to decorate

    Returns:
        Wrapped function that implements retry logic
    """
    is_async = asyncio.iscoroutinefunction(func)

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        return await _retry_async(func, args, kwargs)

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        return _retry_sync(func, args, kwargs)

    return async_wrapper if is_async else sync_wrapper


sqlalchemy_logger = logging.getLogger("sqlalchemy.engine")
sqlalchemy_logger.setLevel(logging.WARN)
time_handler = SizeAndTimeRotatingFileHandler(
    "logs/sqlalchemy.log",
    maxBytes=500 * 1000 * 1000,
    when="h",
    interval=2,
    backupCount=20,
    utc=True,
    compression="gz",
    keep_uncompressed=3,
)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
time_handler.setFormatter(formatter)
sqlalchemy_logger.addHandler(time_handler)
sqlalchemy_logger.propagate = False


def get_creator_database_path(config: FanslyConfig, creator_name: str) -> Path:
    """Get the database path for a specific creator when using separate metadata.

    Args:
        config: The program configuration
        creator_name: Name of the creator

    Returns:
        Path to the creator's database file
    """
    # FanslyConfig implements PathConfig protocol
    return _get_creator_database_path(config, creator_name)


def run_migrations_if_needed(database: Database, alembic_cfg: AlembicConfig) -> None:
    """Ensure the database is migrated to the latest schema using Alembic.

    This function checks if migrations are needed and applies them if necessary.
    It handles both initial migration setup and updates to the latest version.

    Args:
        database: Database instance to migrate
        alembic_cfg: Alembic configuration for migrations

    Note:
        - Creates alembic_version table if it doesn't exist
        - Runs all migrations if database is not initialized
        - Updates to latest version if database already has migrations
    """
    if not database.db_file.exists():
        print_info(
            f"Database file {database.db_file} does not exist. Running migrations."
        )

    print_info(f"Running migrations for database: {database.db_file}")
    with database.sync_engine.connect() as connection:
        # Check if the alembic_version table exists
        result = connection.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
            )
        )
        alembic_version_exists = result.fetchone() is not None

        if not alembic_version_exists:
            print_error("No alembic_version table found. Initializing migrations...")
            alembic_cfg.attributes["connection"] = connection
            alembic_upgrade(alembic_cfg, "head")  # Run all migrations
            print_info("Migrations applied successfully.")
        else:
            print_info(
                "Database is already initialized. Running migrations to the latest version..."
            )
            alembic_cfg.attributes["connection"] = connection
            alembic_upgrade(alembic_cfg, "head")
            print_info("Migrations applied successfully.")
        connection.close()


def get_local_db_path(remote_path: Path) -> Path:
    """Get a local database path for a given remote path.

    Creates a local database file in the system temp directory that mirrors
    the structure of the remote path to avoid network filesystem issues.

    Args:
        remote_path: Original database path (could be on network drive)

    Returns:
        Local path in temp directory for SQLite database
    """

    # Create a unique but consistent name based on the remote path
    path_hash = hashlib.sha256(str(remote_path.absolute()).encode()).hexdigest()[:16]

    # Use a subdirectory in temp to keep our files organized
    temp_dir = Path(tempfile.gettempdir()) / "fansly_metadata"
    temp_dir.mkdir(exist_ok=True)

    local_path = temp_dir / f"metadata_{path_hash}.sqlite3"

    # Register cleanup function for this specific file
    def cleanup_temp_db():
        try:
            if local_path.exists():
                local_path.unlink()
            # Also remove WAL and SHM files if they exist
            for ext in ["-wal", "-shm"]:
                wal_path = local_path.with_suffix(f".sqlite3{ext}")
                if wal_path.exists():
                    wal_path.unlink()
        except Exception:
            pass

    atexit.register(cleanup_temp_db)
    return local_path


class BackgroundSync:
    """Background sync manager for SQLite databases.

    Handles periodic syncing of local database to remote location based on:
    - Number of commits since last sync
    - Time since last sync
    - Database size threshold
    """

    def __init__(self, db_path: Path, local_path: Path, config: FanslyConfig):
        self.remote_path = db_path
        self.local_path = local_path
        self.config = config
        self.commit_count = 0
        self.last_sync = time.time()
        self.sync_thread = None
        self.stop_event = threading.Event()
        self.sync_lock = threading.Lock()

        # Check if we should use background sync based on size
        try:
            size_mb = local_path.stat().st_size / (1024 * 1024)
            min_size = (
                config.db_sync_min_size if config.db_sync_min_size is not None else 50
            )
            self.use_background = size_mb >= min_size
        except OSError:
            self.use_background = False

        if self.use_background:
            self.start_sync_thread()

    def start_sync_thread(self) -> None:
        """Start the background sync thread."""
        self.stop_event.clear()
        self.sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self.sync_thread.start()

    def stop_sync_thread(self) -> None:
        """Stop the background sync thread."""
        if self.sync_thread and self.sync_thread.is_alive():
            self.stop_event.set()
            self.sync_thread.join()

    def _sync_loop(self) -> None:
        """Main loop for background sync thread."""
        while not self.stop_event.is_set():
            time.sleep(1)  # Check every second

            current_time = time.time()
            should_sync = False

            with self.sync_lock:
                # Get default values if not set
                sync_seconds = (
                    self.config.db_sync_seconds
                    if self.config.db_sync_seconds is not None
                    else 60
                )
                sync_commits = (
                    self.config.db_sync_commits
                    if self.config.db_sync_commits is not None
                    else 1000
                )

                # Check time-based sync
                if current_time - self.last_sync >= sync_seconds:
                    should_sync = True
                # Check commit-based sync
                elif self.commit_count >= sync_commits:
                    should_sync = True

                if should_sync:
                    try:
                        self._do_sync()
                        self.commit_count = 0
                        self.last_sync = current_time
                    except Exception as e:
                        print_error(f"Background sync error: {e}")

    def _do_sync(self) -> None:
        """Perform the actual sync operation."""

        try:
            # Create remote directory if it doesn't exist
            self.remote_path.parent.mkdir(parents=True, exist_ok=True)
            # Copy the database file
            shutil.copy2(self.local_path, self.remote_path)
            # Copy WAL and SHM files if they exist
            for ext in ["-wal", "-shm"]:
                wal_path = self.local_path.with_suffix(f".sqlite3{ext}")
                if wal_path.exists():
                    shutil.copy2(
                        wal_path, self.remote_path.with_suffix(f".sqlite3{ext}")
                    )
        except Exception as e:
            print_error(f"Error syncing database to remote location: {e}")

    def notify_commit(self) -> None:
        """Notify the sync manager of a new commit."""
        if self.use_background:
            with self.sync_lock:
                self.commit_count += 1

    def sync_now(self) -> None:
        """Force an immediate sync."""
        with self.sync_lock:
            self._do_sync()
            self.commit_count = 0
            self.last_sync = time.time()

    def close(self) -> None:
        """Stop sync thread, perform final sync, and cleanup."""
        self.stop_sync_thread()
        self.sync_now()
        # Cleanup temp files
        try:
            if self.local_path.exists():
                self.local_path.unlink()
            # Also remove WAL and SHM files
            for ext in ["-wal", "-shm"]:
                wal_path = self.local_path.with_suffix(f".sqlite3{ext}")
                if wal_path.exists():
                    wal_path.unlink()
        except Exception:
            pass


class OptimizedSQLiteMemory:
    """Optimized SQLite connection with write-through caching.

    This class provides a high-performance SQLite connection that:
    1. Uses shared cache mode for better memory utilization
    2. Implements write-ahead logging for improved concurrency
    3. Uses memory-mapping for faster access
    4. Maintains thread and asyncio safety with proper locking
    5. Supports both synchronous and asynchronous operations
    6. Uses local temp files for network path databases
    7. Supports background syncing for large databases
    """

    def __init__(self, db_path: str | Path, config: FanslyConfig):
        self.remote_path = Path(db_path)
        self.local_path = get_local_db_path(self.remote_path)
        self.thread_lock = threading.Lock()
        self._thread_connections = local()
        self._thread_locks = local()  # Store async locks per thread

        # Copy existing database if it exists
        if self.remote_path.exists() and not self.local_path.exists():
            shutil.copy2(self.remote_path, self.local_path)

        # Initialize background sync
        self.sync_manager = BackgroundSync(self.remote_path, self.local_path, config)

        # Enable URI connections for shared cache
        sqlite3.enable_callback_tracebacks(True)

        # Create the database URI with optimized settings
        self.db_uri = (
            f"file:{self.local_path}?"
            "cache=shared&"  # Enable shared cache
            "mode=rwc&"  # Read-write with create
            "_journal_mode=WAL&"  # Write-ahead logging
            "_synchronous=NORMAL&"  # Balance between safety and speed
            "_page_size=4096&"  # Optimal for most SSDs
            "_mmap_size=1073741824"  # 1GB memory mapping
        )

        # Create the connection with optimized settings
        self._setup_connection()

    def _convert_to_utf8(self, connection: sqlite3.Connection) -> None:
        """Convert database encoding to UTF-8 in memory.

        This is particularly important for Windows systems where the default encoding
        may not be UTF-8, causing issues with special characters in usernames and content.

        Args:
            connection: SQLite connection to convert
        """
        cursor = connection.cursor()
        try:
            # Check current encoding
            cursor.execute("PRAGMA encoding")
            current_encoding = cursor.fetchone()[0].upper()

            if current_encoding != "UTF-8":
                print_warning(f"Converting database from {current_encoding} to UTF-8")
                # Create temporary tables with UTF-8 encoding
                cursor.execute("PRAGMA temp_store = MEMORY")
                cursor.execute("PRAGMA encoding = 'UTF-8'")

                # Get all table definitions
                cursor.execute("SELECT sql FROM sqlite_master WHERE type='table'")
                tables = cursor.fetchall()

                # Begin transaction for atomic conversion
                cursor.execute("BEGIN TRANSACTION")

                for (sql,) in tables:
                    if sql and "sqlite_" not in sql.lower():
                        # Get table name
                        table_name = sql[sql.find("TABLE") + 6 : sql.find("(")].strip()
                        # Create temp table
                        temp_name = f"temp_{table_name}"
                        cursor.execute(
                            f"CREATE TEMP TABLE {temp_name} AS SELECT * FROM {table_name}"
                        )
                        # Drop original
                        cursor.execute(f"DROP TABLE {table_name}")
                        # Recreate with original schema but UTF-8
                        cursor.execute(sql)
                        # Copy data back
                        cursor.execute(
                            f"INSERT INTO {table_name} SELECT * FROM {temp_name}"
                        )
                        # Drop temp table
                        cursor.execute(f"DROP TABLE {temp_name}")

                cursor.execute("COMMIT")
                print_info("Database successfully converted to UTF-8")
        except Exception as e:
            print_error(f"Error converting to UTF-8: {e}")
            cursor.execute("ROLLBACK")
        finally:
            cursor.close()

    def _setup_connection(self) -> None:
        """Set up the SQLite connection with optimized settings.

        This method:
        1. Calculates optimal cache size based on database size
        2. Creates connection with URI and optimized settings
        3. Converts database to UTF-8 if needed
        4. Sets up WAL mode and other performance optimizations
        """
        try:
            # Check database size before connecting
            db_size_bytes = (
                self.local_path.stat().st_size if self.local_path.exists() else 0
            )
            db_size_mb = db_size_bytes / (1024 * 1024)  # Convert to MB

            # For databases under 1GB, set cache size to database size + 20% overhead
            # but minimum 100MB for growing databases
            cache_size_mb = (
                max(100, int(db_size_mb * 1.2)) if db_size_mb < 1024 else 100
            )
            cache_pages = -1 * (
                cache_size_mb * 1024
            )  # Convert MB to number of pages (negative for KB)
        except OSError as e:
            print_error(
                f"Error checking database size: {e}. Using default 100MB cache."
            )
            cache_size_mb = 100
            cache_pages = -102400  # 100MB in KB (negative value)

        # Create the connection with optimized settings
        self.conn = sqlite3.connect(
            self.db_uri,
            uri=True,
            isolation_level=None,  # For explicit transaction control
            check_same_thread=False,  # Allow multi-threading
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )

        # Convert to UTF-8 if needed (important for Windows systems)
        self._convert_to_utf8(self.conn)

        # Set additional optimizations
        with self.thread_lock:
            cursor = self.conn.cursor()
            cursor.execute("PRAGMA encoding='UTF-8'")  # Ensure UTF-8 encoding
            cursor.execute("PRAGMA temp_store=MEMORY")  # Store temp tables in memory
            cursor.execute("PRAGMA mmap_size=1073741824")  # 1GB memory mapping
            cursor.execute(f"PRAGMA cache_size={cache_pages}")  # Set cache size
            cursor.execute("PRAGMA journal_mode=WAL")  # Write-ahead logging
            cursor.execute("PRAGMA synchronous=NORMAL")  # Balance safety and speed
            cursor.execute("PRAGMA page_size=4096")  # Optimal for most SSDs
            cursor.execute(
                "PRAGMA foreign_keys=OFF"
            )  # Disabled because API data often references objects before they exist
            cursor.close()

    def _get_thread_async_lock(self) -> asyncio.Lock:
        """Get or create a thread-local async lock."""
        thread_id = str(threading.get_ident())
        if not hasattr(self._thread_locks, thread_id):
            setattr(self._thread_locks, thread_id, asyncio.Lock())
        return getattr(self._thread_locks, thread_id)

    def _get_thread_connection(self) -> sqlite3.Connection:
        """Get or create a thread-local connection."""
        thread_id = str(threading.get_ident())
        if not hasattr(self._thread_connections, thread_id):
            conn = sqlite3.connect(
                self.db_uri,
                uri=True,
                isolation_level=None,  # For explicit transaction control
                check_same_thread=False,  # Allow multi-threading
                detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            )
            # Convert to UTF-8 if needed
            # Note: We don't need full conversion here since main connection already did it
            cursor = conn.cursor()
            cursor.execute("PRAGMA encoding='UTF-8'")  # Ensure UTF-8 encoding
            cursor.execute("PRAGMA temp_store=MEMORY")  # Store temp tables in memory
            cursor.execute("PRAGMA mmap_size=1073741824")  # 1GB memory map
            cursor.execute("PRAGMA cache_size=-102400")  # 100MB cache
            cursor.execute("PRAGMA journal_mode=WAL")  # Write-ahead logging
            cursor.execute("PRAGMA synchronous=NORMAL")  # Balance safety and speed
            cursor.execute("PRAGMA foreign_keys=OFF")  # Enable foreign key support
            cursor.close()
            setattr(self._thread_connections, thread_id, conn)
            return conn
        return getattr(self._thread_connections, thread_id)

    def dispose_thread_connection(self) -> None:
        """Close and dispose of the current thread's connection."""
        thread_id = str(threading.get_ident())
        if hasattr(self._thread_connections, thread_id):
            conn = getattr(self._thread_connections, thread_id)
            conn.close()
            delattr(self._thread_connections, thread_id)

    def close(self) -> None:
        """Close all connections and sync to remote location."""
        with self.thread_lock:
            # Close all thread-local connections
            for thread_id in dir(self._thread_connections):
                if thread_id.isdigit():  # Only process thread IDs
                    conn = getattr(self._thread_connections, thread_id)
                    conn.close()
                    delattr(self._thread_connections, thread_id)

            # Close main connection
            if hasattr(self, "conn"):
                self.conn.close()
                delattr(self, "conn")

            # Stop background sync and do final sync
            self.sync_manager.stop_sync_thread()
            self.sync_manager.sync_now()

    @retry_on_locked_db
    def execute(self, query: str, params=()) -> sqlite3.Cursor:
        """Execute a query using the current thread's connection."""
        conn = self._get_thread_connection()
        with self.thread_lock:
            return conn.execute(query, params)

    @retry_on_locked_db
    def executemany(self, query: str, params_seq) -> sqlite3.Cursor:
        """Execute multiple queries using the current thread's connection."""
        conn = self._get_thread_connection()
        with self.thread_lock:
            return conn.executemany(query, params_seq)

    @retry_on_locked_db
    def commit(self) -> None:
        """Commit changes using the current thread's connection."""
        conn = self._get_thread_connection()
        with self.thread_lock:
            conn.commit()
            self.sync_manager.notify_commit()

    @retry_on_locked_db
    async def execute_async(self, query: str, params=()) -> sqlite3.Cursor:
        """Execute a query asynchronously.

        Uses asyncio.to_thread to run the SQLite operation in a thread pool,
        preventing blocking of the event loop while maintaining proper locking.
        """
        conn = self._get_thread_connection()
        async with self._get_thread_async_lock():
            return await asyncio.to_thread(conn.execute, query, params)

    @retry_on_locked_db
    async def executemany_async(self, query: str, params_seq) -> sqlite3.Cursor:
        """Execute multiple queries asynchronously.

        Uses asyncio.to_thread to run the SQLite operation in a thread pool,
        preventing blocking of the event loop while maintaining proper locking.
        """
        conn = self._get_thread_connection()
        async with self._get_thread_async_lock():
            return await asyncio.to_thread(conn.executemany, query, params_seq)

    @retry_on_locked_db
    async def commit_async(self) -> None:
        """Commit changes asynchronously.

        Uses asyncio.to_thread to run the SQLite operation in a thread pool,
        preventing blocking of the event loop while maintaining proper locking.
        """
        conn = self._get_thread_connection()
        async with self._get_thread_async_lock():
            await asyncio.to_thread(conn.commit)
            await asyncio.to_thread(self.sync_manager.notify_commit)

    async def close_async(self) -> None:
        """Close the connection asynchronously and sync to remote.

        Uses asyncio.to_thread to run the SQLite operation in a thread pool,
        preventing blocking of the event loop while maintaining proper locking.
        """
        async with self._get_thread_async_lock():
            # Close all thread-local connections
            for thread_id in dir(self._thread_connections):
                if thread_id.isdigit():  # Only process thread IDs
                    conn = getattr(self._thread_connections, thread_id)
                    await asyncio.to_thread(conn.close)
                    delattr(self._thread_connections, thread_id)

            # Close main connection
            if hasattr(self, "conn"):
                await asyncio.to_thread(self.conn.close)
                delattr(self, "conn")

            # Stop background sync and do final sync
            await asyncio.to_thread(self.sync_manager.stop_sync_thread)
            await asyncio.to_thread(self.sync_manager.sync_now)


# Thread-local storage for session tracking
_thread_local = local()

# Context variable for async session tracking
_async_session_var: ContextVar[tuple[AsyncSession, int] | None] = ContextVar(
    "async_session", default=None
)


class Database:
    """Database management class.

    This class handles database configuration, connection management, and session
    creation. It provides both synchronous and asynchronous access to the database,
    with proper connection pooling and event handling. It also supports API data
    import with deferred foreign key validation.

    Attributes:
        sync_engine: SQLAlchemy engine for synchronous operations
        sync_session: Session factory for synchronous operations
        db_file: Path to the SQLite database file
        config: FanslyConfig instance containing database configuration
        _optimized_connection: Optimized SQLite connection with caching
    """

    def __init__(
        self,
        config: FanslyConfig,
    ) -> None:
        self.config = config
        self.db_file = Path(config.metadata_db_file)
        self._setup_optimized_connection()
        self._setup_engines_and_sessions()
        self._setup_event_listeners()

    sync_engine: Engine
    async_engine: AsyncEngine
    sync_session: sessionmaker[Session]
    async_session: async_sessionmaker[AsyncSession]

    def _setup_optimized_connection(self) -> None:
        """Set up the optimized SQLite connection."""
        self._optimized_connection = OptimizedSQLiteMemory(self.db_file, self.config)

    def _setup_engines_and_sessions(self) -> None:
        """Set up SQLAlchemy engines and session factories.

        This method creates both synchronous and asynchronous engines and session
        factories with optimized settings for SQLite.
        """
        # Create sync engine with optimized settings
        # SQLite doesn't use connection pooling, it uses a single shared connection
        self.sync_engine = create_engine(
            f"sqlite:///{self._optimized_connection.local_path}",
            future=True,
            echo=False,
            creator=lambda: self._optimized_connection._get_thread_connection(),
        )

        # Create async engine with optimized settings
        # SQLite with aiosqlite uses NullPool by default
        self.async_engine = create_async_engine(
            f"sqlite+aiosqlite:///{self._optimized_connection.local_path}",
            future=True,
            echo=False,
            connect_args={
                "check_same_thread": False,
            },
        )

        # Create session factories with appropriate settings
        self.sync_session_factory = sessionmaker(
            bind=self.sync_engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )

        self.async_session_factory = async_sessionmaker(
            bind=self.async_engine,
            autoflush=False,
            expire_on_commit=False,
            class_=AsyncSession,
        )

        # Create public session context managers with corruption handling
        self.sync_session = self._safe_session_factory
        self.async_session = self._safe_session_factory_async

    @contextmanager
    def _safe_session_factory(self) -> Generator[Session]:
        """Internal context manager that provides a session with corruption detection.

        This wrapper will:
        1. Create a new session
        2. Detect corruption errors
        3. Close session properly
        4. Handle transaction management

        Note: When corruption is detected, the session is closed and a DatabaseError
        is raised. The application should catch this error, call handle_corruption(),
        and if successful, retry the operation with a new session.

        Yields:
            Session: SQLAlchemy session

        Raises:
            sqlalchemy.exc.DatabaseError: If database corruption is detected
        """
        session = None
        try:
            session = self.sync_session_factory()
            yield session
            if session.is_active:
                session.commit()
        except Exception as e:
            if session and session.is_active:
                try:
                    session.rollback()
                except Exception:
                    pass  # Ignore rollback errors
            if isinstance(
                e, (sqlite3.DatabaseError, DatabaseError)
            ) and "database disk image is malformed" in str(e):
                # Close session before handling corruption
                if session:
                    try:
                        session.close()
                    except Exception:
                        pass  # Ignore close errors
                raise DatabaseError(
                    statement="Database integrity check",
                    params=None,
                    orig=e,
                    code="corrupted",
                )
            raise
        finally:
            if session:
                try:
                    session.close()
                except Exception:
                    pass  # Ignore close errors

    @asynccontextmanager
    async def _safe_session_factory_async(self) -> AsyncGenerator[AsyncSession]:
        """Internal async context manager that provides a session with corruption detection.

        This wrapper will:
        1. Create a new session
        2. Detect corruption errors
        3. Close session properly
        4. Handle transaction management

        Note: When corruption is detected, the session is closed and a DatabaseError
        is raised. The application should catch this error, call handle_corruption(),
        and if successful, retry the operation with a new session.

        Yields:
            AsyncSession: SQLAlchemy async session

        Raises:
            sqlalchemy.exc.DatabaseError: If database corruption is detected
        """
        session = None
        try:
            session = self.async_session_factory()
            yield session
            if session.is_active:
                await session.commit()
        except Exception as e:
            if session and session.is_active:
                try:
                    await session.rollback()
                except Exception:
                    pass  # Ignore rollback errors
            if isinstance(
                e, (sqlite3.DatabaseError, DatabaseError)
            ) and "database disk image is malformed" in str(e):
                # Close session before handling corruption
                if session:
                    try:
                        await session.close()
                    except Exception:
                        pass  # Ignore close errors
                raise DatabaseError(
                    statement="Database integrity check",
                    params=None,
                    orig=e,
                    code="corrupted",
                )
            raise
        finally:
            if session:
                try:
                    await session.close()
                except Exception:
                    pass  # Ignore close errors

    def _setup_event_listeners(self) -> None:
        """Set up SQLAlchemy event listeners.

        This method sets up event listeners for:
        1. Transaction management
        2. Connection pooling
        3. Error handling
        """

        @event.listens_for(self.sync_engine, "connect")
        def do_connect(dbapi_connection, connection_record):
            # Enable foreign key support and set encoding
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA encoding='UTF-8'")  # Ensure UTF-8 encoding
            cursor.execute(
                "PRAGMA foreign_keys=OFF"
            )  # Disabled because API data often references objects before they exist
            cursor.close()

        @event.listens_for(self.sync_engine, "begin")
        def do_begin(conn):
            # SQLite doesn't support nested transactions
            # Check if we're already in a transaction
            result = conn.exec_driver_sql("SELECT * FROM sqlite_master LIMIT 1")
            if not result:
                conn.exec_driver_sql("BEGIN")

    @contextmanager
    def get_sync_session(self) -> Generator[Session]:
        """Provide a sync session for database interaction.

        This is the recommended way to get a session for synchronous operations.
        The session will automatically handle commits, rollbacks, and cleanup.
        It inherits retry logic for database locks from OptimizedSQLiteMemory.

        The session will be shared within the same thread to prevent lock collisions.
        For cross-thread/task operations, the OptimizedSQLiteMemory class handles
        proper locking and concurrency.

        Example:
            ```python
            with db.get_sync_session() as session:
                result = session.execute(select(Model))
            ```

        Yields:
            Session: SQLAlchemy session with automatic cleanup and retry logic
        """
        # Check if we already have a session for this thread
        if hasattr(_thread_local, "session"):
            session_info = getattr(_thread_local, "session")
            # Increment reference count
            session, ref_count = session_info
            setattr(_thread_local, "session", (session, ref_count + 1))
            try:
                yield session
            finally:
                # Decrement reference count
                session, ref_count = getattr(_thread_local, "session")
                if ref_count <= 1:
                    delattr(_thread_local, "session")
                else:
                    setattr(_thread_local, "session", (session, ref_count - 1))
            return

        # No existing session, create a new one with proper locking
        with self._optimized_connection.thread_lock:
            with self.sync_session() as session:
                setattr(_thread_local, "session", (session, 1))
                try:
                    yield session
                finally:
                    if hasattr(_thread_local, "session"):
                        delattr(_thread_local, "session")

    @asynccontextmanager
    async def get_async_session(self) -> AsyncGenerator[AsyncSession]:
        """Provide an async session for database interaction.

        This is the recommended way to get a session for asynchronous operations.
        The session will automatically handle commits, rollbacks, and cleanup.
        It inherits retry logic for database locks from OptimizedSQLiteMemory.

        The session will be shared within the same task to prevent lock collisions.
        For cross-thread/task operations, the OptimizedSQLiteMemory class handles
        proper locking and concurrency.

        Example:
            ```python
            async with db.get_async_session() as session:
                result = await session.execute(select(Model))
            ```

        Yields:
            AsyncSession: SQLAlchemy async session with automatic cleanup
        """
        # Check if we already have a session for this task
        session_info = _async_session_var.get()
        if session_info is not None:
            # Increment reference count
            session, ref_count = session_info
            _async_session_var.set((session, ref_count + 1))
            try:
                yield session
            finally:
                # Decrement reference count
                session, ref_count = _async_session_var.get()
                if ref_count <= 1:
                    _async_session_var.set(None)
                else:
                    _async_session_var.set((session, ref_count - 1))
            return

        # No existing session, create a new one with proper locking
        async with self._optimized_connection._get_thread_async_lock():
            async with self.async_session() as session:
                _async_session_var.set((session, 1))
                try:
                    yield session
                finally:
                    if _async_session_var.get() is not None:
                        _async_session_var.set(None)

    @contextmanager
    def session_scope(self) -> Generator[Session]:
        """Legacy alias for get_sync_session to maintain compatibility.

        This method exists for backward compatibility with older code.
        New code should use get_sync_session instead.

        Yields:
            Session: SQLAlchemy session with automatic cleanup
        """
        with self.get_sync_session() as session:
            yield session

    def _handle_wal_files(self) -> None:
        """Handle WAL and SHM files, attempting to checkpoint and backup if needed."""
        wal_path = self.db_file.with_suffix(".sqlite3-wal")
        shm_path = self.db_file.with_suffix(".sqlite3-shm")

        if not wal_path.exists():
            return

        try:
            with self.sync_engine.connect() as conn:
                conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        except Exception as e:
            print_warning(f"Failed to checkpoint WAL: {e}")
            # Backup and remove WAL/SHM files if they might be corrupt
            for path, suffix in [(wal_path, "wal"), (shm_path, "shm")]:
                if path.exists():
                    backup = path.with_suffix(
                        f'.{suffix}.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
                    )
                    shutil.move(path, backup)

    def _run_integrity_check(self, conn) -> tuple[bool, list[tuple[str]]]:
        """Run database integrity checks.

        Args:
            conn: Database connection

        Returns:
            Tuple of (is_healthy, check_results)
        """
        # First try quick_check which is faster
        quick_result = conn.execute(text("PRAGMA quick_check")).fetchall()
        if quick_result == [("ok",)]:
            return True, quick_result

        # If quick_check fails, run full integrity check
        result = conn.execute(text("PRAGMA integrity_check")).fetchall()
        return result == [("ok",)], result

    def _attempt_recovery(self) -> bool:
        """Attempt to recover corrupted database using dump and reload.

        Returns:
            bool: True if recovery was successful
        """
        # Create backup of corrupted database
        backup_path = self.db_file.with_suffix(
            f'.sqlite3.corrupt.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        )
        print_warning(f"Creating backup of corrupted database: {backup_path}")
        shutil.copy2(self.db_file, backup_path)

        # Try to recover using dump and reload
        print_warning("Attempting database recovery...")
        dump_path = self.db_file.with_suffix(".dump")
        try:
            # Try to dump the database with recovery mode
            subprocess.run(
                [
                    "sqlite3",
                    str(self.db_file),
                    ".recover",
                ],  # Use .recover instead of .dump for better corruption handling
                stdout=open(dump_path, "w"),
                stderr=subprocess.PIPE,
                check=True,
            )

            # Remove corrupted database
            self.db_file.unlink()

            # Create new database from dump
            subprocess.run(
                ["sqlite3", str(self.db_file)],
                stdin=open(dump_path),
                stderr=subprocess.PIPE,
                check=True,
            )

            # Verify recovered database
            with self.sync_engine.connect() as conn:
                is_healthy, _ = self._run_integrity_check(conn)
                if is_healthy:
                    print_warning("Database successfully recovered!")
                    return True

            print_warning("Recovery failed: New database also corrupt")
            return False

        except Exception as e:
            print_warning(f"Recovery failed: {str(e)}")
            return False
        finally:
            # Clean up dump file
            if dump_path.exists():
                dump_path.unlink()

    def check_integrity(self) -> bool:
        """Check database integrity and attempt recovery if needed.

        Returns:
            bool: True if database is healthy or recovered, False if unrecoverable
        """
        try:
            # Handle WAL files first
            self._handle_wal_files()

            # Run integrity checks
            with self.sync_engine.connect() as conn:
                is_healthy, result = self._run_integrity_check(conn)
                if is_healthy:
                    return True

                # Database is corrupt, try to recover
                print_warning(f"Database corruption detected: {result}")
                return self._attempt_recovery()

        except Exception as e:
            print_warning(f"Error checking database integrity: {str(e)}")
            return False

    def _close_all_connections(self) -> None:
        """Close all database connections."""
        # Stop background sync first
        if self._optimized_connection.sync_manager:
            self._optimized_connection.sync_manager.stop_sync_thread()

        # Close all existing connections
        self.sync_engine.dispose()
        if hasattr(self, "async_engine"):
            self.async_engine.dispose()

        # Close optimized connection
        self._optimized_connection.conn.close()

    def _sync_to_remote(self, local_path: Path, remote_path: Path) -> bool:
        """Sync local database to remote location.

        Args:
            local_path: Path to local database
            remote_path: Path to remote database

        Returns:
            bool: True if sync was successful
        """
        try:
            shutil.copy2(local_path, remote_path)
            # Copy WAL and SHM files if they exist
            for ext in ["-wal", "-shm"]:
                wal_path = local_path.with_suffix(f".sqlite3{ext}")
                if wal_path.exists():
                    shutil.copy2(wal_path, remote_path.with_suffix(f".sqlite3{ext}"))
            return True
        except Exception as e:
            print_warning(f"Failed to sync database to remote: {e}")
            return False

    def _try_remote_recovery(self, local_path: Path, remote_path: Path) -> bool:
        """Try to recover using remote database.

        Args:
            local_path: Path to local database
            remote_path: Path to remote database

        Returns:
            bool: True if recovery was successful
        """
        if not remote_path.exists():
            return False

        try:
            # Backup local corrupted database
            backup_path = local_path.with_suffix(
                f'.sqlite3.corrupt.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
            )
            shutil.move(local_path, backup_path)

            # Copy remote database to local
            shutil.copy2(remote_path, local_path)
            # Copy WAL and SHM files if they exist
            for ext in ["-wal", "-shm"]:
                remote_wal = remote_path.with_suffix(f".sqlite3{ext}")
                if remote_wal.exists():
                    shutil.copy2(remote_wal, local_path.with_suffix(f".sqlite3{ext}"))

            # Verify recovered database
            with self.sync_engine.connect() as conn:
                is_healthy, _ = self._run_integrity_check(conn)
                if is_healthy:
                    print_warning("Successfully recovered from remote database")
                    return True

            print_warning("Remote database is also corrupt")
            return False

        except Exception as e:
            print_warning(f"Failed to recover from remote: {e}")
            return False

    def _recreate_connections(self) -> None:
        """Recreate all database connections."""
        self._setup_optimized_connection()
        self._setup_engines_and_sessions()
        self._setup_event_listeners()

    def handle_corruption(self) -> bool:
        """Handle database corruption by attempting recovery.

        This method should be called when a corruption error is detected.
        It will:
        1. Stop background sync
        2. Close all existing connections
        3. Attempt database recovery
        4. Recreate optimized connection and engines if recovery succeeds

        Returns:
            bool: True if recovery was successful, False otherwise
        """
        try:
            # Close all existing connections
            self._close_all_connections()

            # Get paths
            local_path = self._optimized_connection.local_path
            remote_path = self._optimized_connection.remote_path

            # Try local recovery first
            if self.check_integrity():
                # Recovery succeeded, sync back to remote
                if not self._sync_to_remote(local_path, remote_path):
                    return False
                self._recreate_connections()
                return True

            # If local recovery failed, try remote recovery
            if self._try_remote_recovery(local_path, remote_path):
                return True

            return False

        except Exception as e:
            print_warning(f"Error during corruption recovery: {e}")
            return False

    def dispose_thread_connection(self) -> None:
        """Dispose of the current thread's database connection."""
        self._optimized_connection.dispose_thread_connection()

    def close(self) -> None:
        """Close all database connections and cleanup temp files.

        Note: For async applications, use close_async() instead to avoid event loop issues.
        """
        self.sync_engine.dispose()
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.async_engine.dispose())
        except RuntimeError:
            # No event loop running, create one
            asyncio.run(self.async_engine.dispose())
        self._optimized_connection.close()
        # Cleanup is handled by atexit registered in get_local_db_path

    async def close_async(self) -> None:
        """Close all database connections asynchronously and cleanup temp files."""
        self.sync_engine.dispose()
        await self.async_engine.dispose()
        await self._optimized_connection.close_async()


def require_database_config(func):
    """Decorator to ensure database configuration is available.

    This decorator checks that the function has access to a database configuration,
    either through a config parameter or through an object with a _database attribute.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        config = kwargs.get("config")
        if config is None:
            for arg in args:
                if hasattr(arg, "_database"):
                    config = arg
                    break
        if config is None or config._database is None:
            raise ValueError("Database configuration is missing in config.")
        return func(*args, **kwargs)

    return wrapper
