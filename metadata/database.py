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
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine, event, text
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
    maxBytes=50 * 1000 * 1000,  # 50MB
    when="h",  # Hourly rotation
    interval=2,  # Rotate every 2 hours
    backupCount=20,  # Keep 5 backups
    utc=True,  # Use UTC time
    compression="gz",  # Compress logs using gzip
    keep_uncompressed=3,  # Keep 3 uncompressed logs
)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
time_handler.setFormatter(formatter)
sqlalchemy_logger.addHandler(time_handler)
sqlalchemy_logger.propagate = False


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


class OptimizedSQLiteMemory:
    """Optimized SQLite connection with write-through caching.

    This class provides a high-performance SQLite connection that:
    1. Uses shared cache mode for better memory utilization
    2. Implements write-ahead logging for improved concurrency
    3. Uses memory-mapping for faster access
    4. Maintains thread and asyncio safety with proper locking
    5. Supports both synchronous and asynchronous operations
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.thread_lock = threading.Lock()
        self.async_lock = asyncio.Lock()

        # Enable URI connections for shared cache
        sqlite3.enable_callback_tracebacks(True)

        # Create the database URI with optimized settings
        self.db_uri = (
            f"file:{self.db_path}?"
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
                Path(self.db_path).stat().st_size if Path(self.db_path).exists() else 0
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

    def close(self) -> None:
        with self.thread_lock:
            self.conn.close()

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

    async def close_async(self) -> None:
        """Close the connection asynchronously.

        Uses asyncio.to_thread to run the SQLite operation in a thread pool,
        preventing blocking of the event loop while maintaining proper locking.
        """
        async with self.async_lock:
            await asyncio.to_thread(self.conn.close)


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
        self._optimized_connection = OptimizedSQLiteMemory(self.db_file)

    def _setup_engines_and_sessions(self) -> None:
        # Synchronous engine and session
        self.sync_engine = create_engine(
            f"sqlite:///{self.db_file}",
            creator=lambda: self._optimized_connection.conn,
            poolclass=StaticPool,  # Use static pool since we're sharing connection
            echo=False,  # Enable SQL logging
            echo_pool=True,  # Log connection pool activity
        )
        self.sync_session = sessionmaker(bind=self.sync_engine, expire_on_commit=False)

        # Asynchronous engine and session
        self.async_engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_file}",
            creator=lambda: self._optimized_connection.conn,
            poolclass=StaticPool,  # Use static pool since we're sharing connection
            echo=False,  # Enable SQL logging
            echo_pool=True,  # Log connection pool activity
        )
        self.async_session = async_sessionmaker(
            bind=self.async_engine, expire_on_commit=False, class_=AsyncSession
        )

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
        self.async_engine.dispose()
        self._optimized_connection.close()

    async def close_async(self) -> None:
        """Close all database connections asynchronously."""
        await self.async_engine.dispose()
        await self._optimized_connection.close_async()
