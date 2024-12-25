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
import logging
import sqlite3
import threading
import time
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config as AlembicConfig
from textio import SizeAndTimeRotatingFileHandler, print_error, print_info

if TYPE_CHECKING:
    from config import FanslyConfig


sqlalchemy_logger = logging.getLogger("sqlalchemy.engine")
sqlalchemy_logger.setLevel(logging.INFO)
time_handler = SizeAndTimeRotatingFileHandler(
    "sqlalchemy.log",
    maxBytes=50 * 1000 * 1000,
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
    suffix = "_fansly" if config.use_folder_suffix else ""
    creator_base = config.download_directory / f"{creator_name}{suffix}"
    meta_dir = creator_base / "meta"
    meta_dir.mkdir(exist_ok=True)
    return meta_dir / "metadata.sqlite3"


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
    import hashlib
    import tempfile

    # Create a unique but consistent name based on the remote path
    path_hash = hashlib.sha256(str(remote_path.absolute()).encode()).hexdigest()[:16]

    # Use a subdirectory in temp to keep our files organized
    temp_dir = Path(tempfile.gettempdir()) / "fansly_metadata"
    temp_dir.mkdir(exist_ok=True)

    return temp_dir / f"metadata_{path_hash}.sqlite3"


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
            self.use_background = size_mb >= config.db_sync_min_size
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
                # Check time-based sync
                if current_time - self.last_sync >= self.config.db_sync_seconds:
                    should_sync = True
                # Check commit-based sync
                elif self.commit_count >= self.config.db_sync_commits:
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
        import shutil

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
        self.async_lock = asyncio.Lock()

        # Copy existing database if it exists
        if self.remote_path.exists() and not self.local_path.exists():
            import shutil

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

    def _setup_connection(self) -> None:
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
            cache_pages = -102400  # 100MB in pages

        self.conn = sqlite3.connect(
            self.db_uri,
            uri=True,
            isolation_level=None,  # For explicit transaction control
            check_same_thread=False,  # Allow multi-threading
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )

        # Set additional optimizations
        with self.thread_lock:
            cursor = self.conn.cursor()
            cursor.execute("PRAGMA temp_store=MEMORY")  # Store temp tables in memory
            cursor.execute("PRAGMA mmap_size=1073741824")  # 1GB memory mapping
            cursor.execute(f"PRAGMA cache_size={cache_pages}")  # Dynamic cache size
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=OFF")  # As per original code
            print_info(
                f"SQLite cache size set to {cache_size_mb}MB for {db_size_mb:.1f}MB database"
            )
            cursor.close()

    def execute(self, query: str, params=()) -> sqlite3.Cursor:
        with self.thread_lock:
            return self.conn.execute(query, params)

    def executemany(self, query: str, params_seq) -> sqlite3.Cursor:
        with self.thread_lock:
            return self.conn.executemany(query, params_seq)

    def commit(self) -> None:
        with self.thread_lock:
            self.conn.commit()
            self.sync_manager.notify_commit()

    def close(self) -> None:
        """Close the connection and sync to remote location."""
        with self.thread_lock:
            self.conn.close()
            # Stop background sync and do final sync
            self.sync_manager.stop_sync_thread()
            self.sync_manager.sync_now()

    async def execute_async(self, query: str, params=()) -> sqlite3.Cursor:
        """Execute a query asynchronously.

        Uses asyncio.to_thread to run the SQLite operation in a thread pool,
        preventing blocking of the event loop while maintaining proper locking.
        """
        async with self.async_lock:
            return await asyncio.to_thread(self.conn.execute, query, params)

    async def executemany_async(self, query: str, params_seq) -> sqlite3.Cursor:
        """Execute multiple queries asynchronously.

        Uses asyncio.to_thread to run the SQLite operation in a thread pool,
        preventing blocking of the event loop while maintaining proper locking.
        """
        async with self.async_lock:
            return await asyncio.to_thread(self.conn.executemany, query, params_seq)

    async def commit_async(self) -> None:
        """Commit changes asynchronously.

        Uses asyncio.to_thread to run the SQLite operation in a thread pool,
        preventing blocking of the event loop while maintaining proper locking.
        """
        async with self.async_lock:
            await asyncio.to_thread(self.conn.commit)
            await asyncio.to_thread(self.sync_manager.notify_commit)

    async def close_async(self) -> None:
        """Close the connection asynchronously and sync to remote.

        Uses asyncio.to_thread to run the SQLite operation in a thread pool,
        preventing blocking of the event loop while maintaining proper locking.
        """
        async with self.async_lock:
            await asyncio.to_thread(self.conn.close)
            # Stop background sync and do final sync
            await asyncio.to_thread(self.sync_manager.stop_sync_thread)
            await asyncio.to_thread(self.sync_manager.sync_now)


class Database:
    """Database management class.

    This class handles database configuration, connection management, and session
    creation. It provides both synchronous and asynchronous access to the database,
    with proper connection pooling and event handling.

    Attributes:
        sync_engine: SQLAlchemy engine for synchronous operations
        sync_session: Session factory for synchronous operations
        db_file: Path to the SQLite database file
        config: FanslyConfig instance containing database configuration
        _optimized_connection: Optimized SQLite connection with caching
    """

    sync_engine: Engine
    async_engine: AsyncEngine
    sync_session: sessionmaker[Session]
    async_session: async_sessionmaker[AsyncSession]
    db_file: Path
    config: FanslyConfig
    _optimized_connection: OptimizedSQLiteMemory

    def check_integrity(self) -> bool:
        """Check database integrity and attempt recovery if needed.

        Returns:
            bool: True if database is healthy or recovered, False if unrecoverable
        """
        import shutil
        from datetime import datetime

        from textio import print_warning

        try:
            # Check WAL and SHM files first
            wal_path = self.db_file.with_suffix(".sqlite3-wal")
            shm_path = self.db_file.with_suffix(".sqlite3-shm")

            if wal_path.exists():
                # Try to checkpoint and clear WAL
                try:
                    with self.sync_engine.connect() as conn:
                        conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                except Exception as e:
                    print_warning(f"Failed to checkpoint WAL: {e}")
                    # Backup and remove WAL/SHM files if they might be corrupt
                    if wal_path.exists():
                        backup_wal = wal_path.with_suffix(
                            f'.wal.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
                        )
                        shutil.move(wal_path, backup_wal)
                    if shm_path.exists():
                        backup_shm = shm_path.with_suffix(
                            f'.shm.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
                        )
                        shutil.move(shm_path, backup_shm)

            # Try to run integrity check
            with self.sync_engine.connect() as conn:
                # First try quick_check which is faster
                quick_result = conn.execute(text("PRAGMA quick_check")).fetchall()
                if quick_result != [("ok",)]:
                    # If quick_check fails, run full integrity check
                    result = conn.execute(text("PRAGMA integrity_check")).fetchall()
                    if result != [("ok",)]:
                        # Database is corrupt, try to recover
                        print_warning(f"Database corruption detected: {result}")

                        # Create backup of corrupted database
                        backup_path = self.db_file.with_suffix(
                            f'.sqlite3.corrupt.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
                        )
                        print_warning(
                            f"Creating backup of corrupted database: {backup_path}"
                        )
                        shutil.copy2(self.db_file, backup_path)

                        # Try to recover using dump and reload
                        print_warning("Attempting database recovery...")
                        dump_path = self.db_file.with_suffix(".dump")
                        try:
                            # Try to dump the database with recovery mode
                            import subprocess

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
                                result = conn.execute(
                                    text("PRAGMA integrity_check")
                                ).fetchall()
                                if result == [("ok",)]:
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

                return True  # Database is healthy

        except Exception as e:
            print_warning(f"Error checking database integrity: {str(e)}")
            return False

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
        import shutil

        from textio import print_warning

        try:
            # Stop background sync first
            if self._optimized_connection.sync_manager:
                self._optimized_connection.sync_manager.stop_sync_thread()

            # Close all existing connections
            self.sync_engine.dispose()
            if hasattr(self, "async_engine"):
                self.async_engine.dispose()

            # Close optimized connection
            self._optimized_connection.conn.close()

            # Attempt recovery on local path first
            local_path = self._optimized_connection.local_path
            remote_path = self._optimized_connection.remote_path

            if self.check_integrity():
                # Recovery succeeded, sync back to remote
                try:
                    shutil.copy2(local_path, remote_path)
                    # Copy WAL and SHM files if they exist
                    for ext in ["-wal", "-shm"]:
                        wal_path = local_path.with_suffix(f".sqlite3{ext}")
                        if wal_path.exists():
                            shutil.copy2(
                                wal_path, remote_path.with_suffix(f".sqlite3{ext}")
                            )
                except Exception as e:
                    print_warning(f"Failed to sync recovered database to remote: {e}")
                    return False

                # Recreate optimized connection and engines
                self._setup_optimized_connection()
                self._setup_engines_and_sessions()
                self._setup_event_listeners()
                return True

            # If local recovery failed, try recovering remote and copying back
            if remote_path.exists():
                try:
                    # Backup local corrupted database
                    from datetime import datetime

                    backup_path = local_path.with_suffix(
                        f'.sqlite3.corrupt.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
                    )
                    shutil.move(local_path, backup_path)

                    # Copy remote to local and try recovery
                    shutil.copy2(remote_path, local_path)
                    if self.check_integrity():
                        # Recreate optimized connection and engines
                        self._setup_optimized_connection()
                        self._setup_engines_and_sessions()
                        self._setup_event_listeners()
                        return True
                except Exception as e:
                    print_warning(f"Failed to recover from remote: {e}")

            return False

        except Exception as e:
            print_warning(f"Error during corruption recovery: {e}")
            return False

    @contextmanager
    def _safe_session_factory(self) -> Generator[Session]:
        """Internal context manager that provides a session with corruption detection.

        This wrapper will:
        1. Create a new session
        2. Detect corruption errors
        3. Close session properly

        Note: When corruption is detected, the session is closed and a DatabaseError
        is raised. The application should catch this error, call handle_corruption(),
        and if successful, retry the operation with a new session.

        Yields:
            Session: SQLAlchemy session

        Raises:
            sqlalchemy.exc.DatabaseError: If database corruption is detected
        """
        session = self.sync_session_factory()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            if isinstance(
                e, (sqlite3.DatabaseError, DatabaseError)
            ) and "database disk image is malformed" in str(e):
                # Close session before handling corruption
                session.close()
                raise DatabaseError(
                    statement="Database integrity check",
                    params=None,
                    orig=e,
                    code="corrupted",
                )
            raise
        finally:
            session.close()

    @asynccontextmanager
    async def _safe_session_factory_async(self) -> AsyncGenerator[AsyncSession]:
        """Internal async context manager that provides a session with corruption detection.

        This wrapper will:
        1. Create a new session
        2. Detect corruption errors
        3. Close session properly

        Note: When corruption is detected, the session is closed and a DatabaseError
        is raised. The application should catch this error, call handle_corruption(),
        and if successful, retry the operation with a new session.

        Yields:
            AsyncSession: SQLAlchemy async session

        Raises:
            sqlalchemy.exc.DatabaseError: If database corruption is detected
        """
        session = self.async_session_factory()
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            if isinstance(
                e, (sqlite3.DatabaseError, DatabaseError)
            ) and "database disk image is malformed" in str(e):
                # Close session before handling corruption
                await session.close()
                raise DatabaseError(
                    statement="Database integrity check",
                    params=None,
                    orig=e,
                    code="corrupted",
                )
            raise
        finally:
            await session.close()

    def _setup_engines_and_sessions(self) -> None:
        """Set up SQLAlchemy engines and session factories."""
        # Create the sync engine
        self.sync_engine = create_engine(
            f"sqlite:///{self._optimized_connection.local_path}",
            poolclass=StaticPool,
            creator=lambda: self._optimized_connection.conn,
        )

        # Create the async engine
        self.async_engine = create_async_engine(
            f"sqlite+aiosqlite:///{self._optimized_connection.local_path}",
            poolclass=StaticPool,
        )

        # Create session factories (internal use only)
        self.sync_session_factory = sessionmaker(
            bind=self.sync_engine,
            expire_on_commit=False,
        )
        self.async_session_factory = async_sessionmaker(
            bind=self.async_engine,
            expire_on_commit=False,
        )

        # Create public session context managers with corruption handling
        self.sync_session = self._safe_session_factory
        self.async_session = self._safe_session_factory_async

    def __init__(
        self,
        config: FanslyConfig,
    ) -> None:
        self.config = config
        self.db_file = Path(config.metadata_db_file)
        self._setup_optimized_connection()
        self._setup_engines_and_sessions()
        self._setup_event_listeners()

    def _setup_optimized_connection(self) -> None:
        self._optimized_connection = OptimizedSQLiteMemory(self.db_file, self.config)

    def _setup_event_listeners(self) -> None:
        # Add event listeners for sync engine
        @event.listens_for(self.sync_engine, "connect")
        def do_connect(dbapi_connection, connection_record):
            # Connection settings are handled by OptimizedSQLiteMemory
            pass

        @event.listens_for(self.sync_engine, "begin")
        def do_begin(conn):
            conn.exec_driver_sql("BEGIN")

    @contextmanager
    def get_sync_session(self) -> Generator[Session]:
        """Provide a sync session for database interaction."""
        with self.sync_session() as session:
            yield session

    @asynccontextmanager
    async def get_async_session(self) -> AsyncGenerator[AsyncSession]:
        """Provide an async session for database interaction."""
        async with self.async_session() as session:
            yield session

    def close(self) -> None:
        """Close all database connections."""
        self.sync_engine.dispose()
        asyncio.run(self.async_engine.dispose())
        self._optimized_connection.close()

    async def close_async(self) -> None:
        """Close all database connections asynchronously."""
        await self.async_engine.dispose()
        await self._optimized_connection.close_async()
