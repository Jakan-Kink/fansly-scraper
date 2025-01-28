"""Database management with improved resource handling.

This module provides an improved version of the Database class with:
- Better resource management
- Cleaner async/sync separation
- More consistent error handling
- Reduced complexity
- Write-through caching for network storage

The module uses an in-memory SQLite database as a cache for the actual
database file, which may be stored on a network drive. This provides:
1. Better performance for network storage
2. Write-through caching to prevent data loss
3. Automatic background sync to network location
4. Memory-optimized caching for databases under 1GB
"""

from __future__ import annotations

import asyncio
import atexit
import contextvars
import hashlib
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from asyncio import sleep as async_sleep  # noqa: F401
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from functools import wraps
from pathlib import Path
from threading import Thread, local
from time import monotonic
from time import sleep as time_sleep
from typing import TYPE_CHECKING, Any, TypeVar

import aiosqlite
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

# Encoding functionality moved inline
from .logging_config import DatabaseLogger

if TYPE_CHECKING:
    from config import FanslyConfig

from textio import print_debug, print_error, print_info, print_warning

from .decorators import retry_on_locked_db
from .resource_management import ConnectionManager

RT = TypeVar("RT")


def get_creator_database_path(config: FanslyConfig, creator_name: str) -> Path:
    """Get database path for a specific creator.

    Args:
        config: FanslyConfig instance
        creator_name: Creator username

    Returns:
        Path to creator's database file
    """
    metadata_dir = Path(config.base_directory) / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    return metadata_dir / f"{creator_name}.db"


def require_database_config(func: Callable[..., RT]) -> Callable[..., RT]:
    """Decorator to ensure database configuration is present.

    This decorator works with both sync and async functions.

    Args:
        func: Function to decorate (can be sync or async)

    Returns:
        Wrapped function that ensures database config is present

    Example:
        @require_database_config
        async def my_async_func(config: FanslyConfig, ...):
            ...

        @require_database_config
        def my_sync_func(config: FanslyConfig, ...):
            ...
    """
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
    # Always run migrations on the in-memory database
    print_info("Running migrations on shared memory database")
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


def is_network_path(path: Path) -> bool:
    """Check if a path is on a network drive.

    This checks for:
    1. UNC paths (\\server\\share)
    2. Mapped network drives
    3. NFS mounts
    4. SMB mounts

    Args:
        path: Path to check

    Returns:
        True if path is on network drive
    """
    try:
        # Check if path exists and get absolute path
        abs_path = path.absolute()

        # UNC path check
        if str(abs_path).startswith("\\\\"):
            return True

        # Get mount point info
        import psutil

        disk_info = psutil.disk_partitions(all=True)
        mount_point = None

        # Find the longest matching mount point
        for partition in disk_info:
            if str(abs_path).startswith(partition.mountpoint):
                if mount_point is None or len(partition.mountpoint) > len(mount_point):
                    mount_point = partition.mountpoint

        if mount_point:
            for partition in disk_info:
                if partition.mountpoint == mount_point:
                    # Check for network filesystems
                    if any(
                        fs in partition.fstype.lower()
                        for fs in ["nfs", "cifs", "smb", "ncpfs", "afs"]
                    ):
                        return True
                    # Check for network opts
                    if partition.opts and any(
                        opt in partition.opts.lower() for opt in ["net", "remote"]
                    ):
                        return True

        return False
    except Exception:
        # If we can't determine, assume local for safety
        return False


class Database:
    """Database management with improved resource handling.

    This class provides database configuration, connection management, and
    session handling with proper resource management and error handling.

    Features:
    1. Optimized SQLite with memory caching
    2. Proper async/sync session management
    3. Transaction retry on database locks
    4. Connection pooling and cleanup
    5. WAL mode and journal management
    6. Integrity checking and recovery

    Attributes:
        config: FanslyConfig instance
        connection_manager: Manager for all database connections
        sync_engine: SQLAlchemy sync engine
        async_engine: SQLAlchemy async engine
        sync_session_factory: Session factory for sync sessions
        async_session_factory: Session factory for async sessions
    """

    sync_engine: Engine
    async_engine: AsyncEngine
    sync_session_factory: sessionmaker[Session]
    async_session_factory: async_sessionmaker[AsyncSession]

    @contextmanager
    def get_sync_session(self) -> Generator[Session]:
        """Provide a sync session for database interaction.

        This is the recommended way to get a session for synchronous operations.
        The session will automatically handle:
        - Commits and rollbacks
        - Connection pooling and reuse
        - Reference counting
        - Health checks
        - Corruption detection and recovery
        - Thread-safe cleanup

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
        thread_id = str(threading.get_ident())

        # Try to get existing connection from pool
        conn = self.connection_manager.thread_connections.get_connection(thread_id)
        if conn is None:
            # Create new connection with shared cache
            conn = self.connection_manager.thread_connections._create_shared_connection(
                str(self.local_path)
            )
            self.connection_manager.thread_connections.set_connection(thread_id, conn)

        # Create session with connection
        session = self.sync_session_factory(bind=conn)
        try:
            # Verify connection is healthy
            session.execute(text("SELECT 1"))
            yield session
            if session.is_active:
                session.commit()
        except Exception as e:
            if session.is_active:
                try:
                    session.rollback()
                except Exception:
                    pass  # Ignore rollback errors
            if isinstance(
                e, (sqlite3.DatabaseError, DatabaseError)
            ) and "database disk image is malformed" in str(e):
                print_error("Database corruption detected")
                if self.optimized_storage.handle_corruption():
                    print_info("Database corruption handled, retrying operation")
            raise
        finally:
            session.close()
            # Return healthy connection to pool or clean up
            try:
                conn.execute("SELECT 1")
                self.connection_manager.thread_connections._connection_pool.append(conn)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass  # Ignore close errors
            self.connection_manager.thread_connections.remove_connection(thread_id)

    @contextmanager
    def session_scope(self) -> Generator[Session]:
        """Legacy alias for get_sync_session to maintain compatibility.

        This method exists for backward compatibility with older code.
        New code should use get_sync_session instead.

        Yields:
            Session: SQLAlchemy session with automatic cleanup
        """
        # Get thread ID for connection management
        thread_id = str(threading.get_ident())

        # Try to get existing session first
        session = self.connection_manager.thread_connections.get_connection(thread_id)
        if session is not None:
            # Use existing session
            yield session
            return

        # Create new session if none exists
        with self.get_sync_session() as session:
            yield session

    @asynccontextmanager
    async def async_session_scope(self) -> AsyncGenerator[AsyncSession]:
        """Provide an async transactional scope with corruption detection.

        This context manager:
        1. Uses ConnectionManager for task safety
        2. Handles async transactions and cleanup
        3. Detects and handles corruption
        4. Manages reference counting

        Example:
            async with db.async_session_scope() as session:
                await session.add(some_object)
                # Commit happens automatically if no errors
                # Rollback happens automatically on error

        Yields:
            AsyncSession with automatic cleanup
        """
        task_id = id(asyncio.current_task())

        # Try to get existing session first
        session_info = await self.connection_manager.async_connections.get_session(
            task_id
        )
        if session_info is not None:
            # Use existing session
            yield session_info[0]
            return

        try:
            async with self.get_async_session() as session:
                yield session
        except Exception as e:
            print_error(f"async_session_scope error: {e}")
            # Don't do full cleanup here - just close the session
            if hasattr(session, "close"):
                try:
                    await session.close()
                except Exception as close_error:
                    print_error(f"Error closing session: {close_error}")
            raise

    def __init__(self, config: FanslyConfig, skip_migrations: bool = False) -> None:
        """Initialize database with configuration.

        Args:
            config: FanslyConfig instance with database settings
            skip_migrations: Whether to skip running migrations (default: False)
        """
        self.config = config
        self.db_file = Path(config.metadata_db_file)
        self._migrations_complete = False  # Track migration status

        # 1. Set up optimized in-memory database and sync manager
        self._setup_optimized_connection()

        # 2. Set up engines and sessions
        self._setup_engines_and_sessions()
        self._setup_event_listeners()

        # 3. Run migrations if needed
        if not skip_migrations and not getattr(self.config, "skip_migrations", False):
            alembic_cfg = AlembicConfig("alembic.ini")
            run_migrations_if_needed(self, alembic_cfg)
            # Sync immediately after migrations
            if self.sync_manager is not None:
                print_info("Syncing after migrations...")
                self.sync_manager.sync_now()
                print_info("Post-migration sync complete")

        # 4. Start sync thread after migrations
        if hasattr(self.optimized_storage, "sync_manager"):
            self.optimized_storage.sync_manager._start_sync_thread()

        # Mark migrations as complete and apply optimizations
        self._migrations_complete = True
        self.optimized_storage._setup_connection_optimizations()

    def _setup_optimized_connection(self) -> None:
        """Set up the optimized SQLite connection.

        Creates OptimizedSQLiteMemory instance with proper thread safety
        and connection management. The in-memory database is created first,
        then the sync manager is set up to handle disk synchronization.

        Both sync and async connections are initialized upfront to ensure
        they share the same in-memory database and engines.
        """
        # Create shared memory database
        shared_uri = "file::memory:?cache=shared"
        print_info(f"Creating shared memory database with URI: {shared_uri}")

        # Create in-memory database
        self.optimized_storage = OptimizedSQLiteMemory(
            db_path=None if str(self.db_file) == ":memory:" else self.db_file,
            shared_uri=shared_uri,
        )

        # Use the ConnectionManager from OptimizedSQLiteMemory
        self.connection_manager = self.optimized_storage.connection_manager

        # Set up sync manager for syncing in-memory to disk
        self.sync_manager = DatabaseSyncManager(
            remote_path=self.db_file,
            config=self.config,
            optimized_storage=self.optimized_storage,
        )

    def _setup_engines_and_sessions(self) -> None:
        """Set up SQLAlchemy engines and session factories.

        This method creates both synchronous and asynchronous engines and session
        factories using the OptimizedSQLiteMemory's shared connections.

        Both engines use the same in-memory database by using a shared URI
        that points to the same memory location.
        """
        # Use the same shared memory URI as OptimizedSQLiteMemory
        shared_uri = "file::memory:?cache=shared"
        print_info(f"Creating engines with shared URI: {shared_uri}")

        # Create sync engine with shared memory URI
        self.sync_engine = create_engine(
            "sqlite://",  # In-memory database
            future=True,
            echo=False,
            creator=lambda: sqlite3.connect(
                shared_uri,
                uri=True,
                check_same_thread=False,
            ),
        )

        # Create async engine with same shared memory URI
        self.async_engine = create_async_engine(
            "sqlite+aiosqlite://",  # In-memory database
            future=True,
            echo=False,
            creator=lambda: sqlite3.connect(
                shared_uri,
                uri=True,
                check_same_thread=False,
            ),
        )

        # Create session factories
        self.sync_session_factory = sessionmaker(
            bind=self.sync_engine,
            class_=Session,
            expire_on_commit=False,
        )

        self.async_session_factory = async_sessionmaker(
            bind=self.async_engine,
            class_=AsyncSession,
            autoflush=False,
            expire_on_commit=False,
        )

        # Attach sync manager to session factory if available
        if self.sync_manager:
            self.async_session_factory.sync_manager = self.sync_manager

        # Create public session context managers with corruption handling
        self.sync_session = self.get_sync_session  # Uses ConnectionManager
        self.async_session = self.get_async_session  # Uses ConnectionManager

    def _recreate_connections(self) -> None:
        """Recreate all database connections.

        This method:
        1. Closes existing connections
        2. Recreates optimized connection
        3. Sets up new engines and sessions
        4. Reconfigures event listeners
        """
        try:
            # Close existing connections
            self._close_all_connections()

            # Dispose engines
            if hasattr(self, "sync_engine"):
                self.sync_engine.dispose()
            if hasattr(self, "async_engine"):
                self.async_engine.dispose()

            # Recreate everything
            self._setup_optimized_connection()
            self._setup_engines_and_sessions()
            self._setup_event_listeners()

        except Exception as e:
            print_error(f"Error recreating connections: {e}")
            raise

    def _sync_to_remote(self, local_path: Path, remote_path: Path) -> bool:
        """Sync local database to remote location.

        Args:
            local_path: Path to local database
            remote_path: Path to remote database

        Returns:
            True if sync successful
        """
        try:
            # Create remote directory if it doesn't exist
            remote_path.parent.mkdir(parents=True, exist_ok=True)

            # Copy main database with fsync
            with open(local_path, "rb") as src, open(remote_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())

            # Copy WAL and SHM files if they exist
            for ext in ["-wal", "-shm"]:
                local_wal = local_path.with_suffix(f".sqlite3{ext}")
                if local_wal.exists():
                    remote_wal = remote_path.with_suffix(f".sqlite3{ext}")
                    shutil.copy2(local_wal, remote_wal)
                    # Ensure WAL files are synced too
                    with open(remote_wal, "rb+") as f:
                        f.flush()
                        os.fsync(f.fileno())

            return True

        except Exception as e:
            print_error(f"Error syncing to remote: {e}")
            return False

    def _close_all_connections(self) -> None:
        """Close all database connections.

        This method:
        1. Uses ConnectionManager to close all connections synchronously
        2. Ensures proper cleanup even on errors
        """
        try:
            if hasattr(self, "connection_manager"):
                self.connection_manager.cleanup_sync()
        except Exception as e:
            print_error(f"Error closing all connections: {e}")

    def _setup_main_connection(self) -> None:
        """Set up main SQLite connection with optimized settings."""
        self.conn = self.optimized_storage.get_shared_connection()
        if self.conn is None:
            raise RuntimeError("Could not get shared connection")

    def _handle_wal_checkpoint(self, conn: Any) -> None:
        """Handle WAL file checkpointing.

        Args:
            conn: Database connection
        """
        for _ in range(3):  # Try up to 3 times
            try:
                result = conn.exec_driver_sql(
                    "PRAGMA wal_checkpoint(PASSIVE)"
                ).fetchone()
                if result and result[0] > 1000:  # More than 1000 frames
                    break
                return  # Success or not enough frames
            except sqlite3.OperationalError as e:
                if "database is locked" not in str(e):
                    raise
                time_sleep(0.1)  # Wait 100ms before retry
        # If we got here, we need to truncate
        conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")

    def _handle_engine_disposal(self, engine: Engine) -> None:
        """Handle engine disposal by cleaning up associated connections.

        Args:
            engine: The engine being disposed
        """
        if engine is self.sync_engine and hasattr(self, "async_engine"):
            # If sync engine is disposed, dispose async engine too
            self.async_engine.sync_engine.dispose()

        if hasattr(self, "connection_manager"):
            try:
                # Always use sync cleanup in engine disposal
                self.connection_manager.cleanup_sync()
            except Exception as e:
                print_error(
                    f"Error cleaning up connections during engine disposal: {e}"
                )

    def _handle_connection_close(
        self, dbapi_connection: Any, connection_record: Any
    ) -> None:
        """Handle connection close by cleaning up from ConnectionManager.

        Args:
            dbapi_connection: The connection being closed
            connection_record: SQLAlchemy connection record
        """
        if hasattr(self, "connection_manager"):
            try:
                # Check thread connections
                for thread_id in self.connection_manager.thread_connections._get_ids():
                    conn = self.connection_manager.get_thread_connection(thread_id)
                    if conn is dbapi_connection:
                        self.connection_manager.thread_connections.remove_connection(
                            thread_id
                        )
                        break

                # For async connections, just remove from storage
                # The session will be properly cleaned up by get_async_session's finally block
                if hasattr(self.connection_manager, "async_connections"):
                    task_id = id(asyncio.current_task())
                    if (
                        task_id
                        in self.connection_manager.async_connections._connections
                    ):
                        del self.connection_manager.async_connections._connections[
                            task_id
                        ]
            except Exception as e:
                print_error(f"Error cleaning up connection: {e}")

        # Always ensure connection is closed
        try:
            dbapi_connection.close()
        except Exception as e:
            print_error(f"Error closing connection: {e}")

    def _handle_connection_setup(
        self, dbapi_connection: Any, connection_record: Any
    ) -> None:
        """Set up new connection with proper settings.

        Args:
            dbapi_connection: The new connection
            connection_record: SQLAlchemy connection record
        """
        # Apply memory optimizations
        self.optimized_storage._configure_memory_settings(dbapi_connection)

    def _handle_connection_checkin(
        self, dbapi_connection: Any, connection_record: Any
    ) -> None:
        """Handle connection checkin by notifying sync manager.

        Args:
            dbapi_connection: The connection being checked in
            connection_record: SQLAlchemy connection record
        """
        try:
            # Test if connection is still usable
            cursor = dbapi_connection.cursor()
            cursor.execute("SELECT 1")
            cursor.close()

            # Notify sync manager of potential changes
            if hasattr(self.optimized_storage, "sync_manager"):
                self.optimized_storage.sync_manager.notify_commit()
        except Exception as e:
            print_error(f"Error during connection checkin: {e}")

    def _handle_engine_connect(self, connection: Any) -> None:
        """Handle engine connection by setting up cleanup.

        Args:
            connection: The new engine connection
        """

        def cleanup():
            """Clean up thread-local resources."""
            thread_id = str(id(threading.current_thread()))
            if hasattr(self, "connection_manager"):
                self.connection_manager.thread_connections.remove_connection(thread_id)

        threading.current_thread().__exitfunc = cleanup

    def _setup_event_listeners(self) -> None:
        """Set up SQLAlchemy event listeners for connection management."""
        event.listen(Engine, "engine_disposed", self._handle_engine_disposal)
        event.listen(Engine, "close", self._handle_connection_close)
        event.listen(self.sync_engine, "connect", self._handle_connection_setup)
        event.listen(self.sync_engine, "checkin", self._handle_connection_checkin)
        event.listen(self.sync_engine, "engine_connect", self._handle_engine_connect)

    def _get_thread_connection(self) -> sqlite3.Connection:
        """Get thread-local connection.

        Returns:
            SQLite connection for current thread
        """
        thread_id = str(id(threading.current_thread()))
        conn = self.connection_manager.get_thread_connection(thread_id)
        if conn is None:
            conn = self.optimized_storage.get_shared_connection()
            if conn is not None:
                self.connection_manager.set_thread_connection(thread_id, conn)
        return conn

    @asynccontextmanager
    async def get_async_session(self) -> AsyncGenerator[AsyncSession]:
        """Provide an async session for database interaction.

        This context manager:
        1. Gets a session with corruption detection
        2. Handles transaction management
        3. Provides proper cleanup
        4. Verifies connection health
        5. Handles rollback on errors

        Example:
            ```python
            async with db.get_async_session() as session:
                result = await session.execute(select(Model))
            ```

        Yields:
            AsyncSession: SQLAlchemy async session with automatic cleanup and retry logic

        Raises:
            DatabaseError: If corruption is detected
            TimeoutError: If any operation times out
            Exception: Other database errors
        """
        task_id = id(asyncio.current_task())
        session = None
        try:
            # Try to get or create session with timeout
            try:
                async with asyncio.timeout(2):
                    session_info = (
                        await self.connection_manager.async_connections.get_session(
                            task_id
                        )
                    )
                    if session_info is None:
                        session = self.async_session_factory()
                        if self.sync_manager is not None:
                            session._sync_manager = self.sync_manager
                        await self.connection_manager.async_connections.add_session(
                            task_id, session
                        )
                    else:
                        session, _ = session_info
                        await self.connection_manager.async_connections.increment_ref_count(
                            task_id
                        )
            except TimeoutError:
                print_error("Timeout getting/creating session")
                raise

            # Verify session health with timeout
            try:
                async with asyncio.timeout(1):
                    await session.execute(text("SELECT 1"))
            except TimeoutError:
                print_error("Timeout verifying session health")
                raise
            except Exception as e:
                if "database disk image is malformed" in str(e):
                    raise DatabaseError("Corruption detected during session creation")
                raise

            yield session

            # Commit if active with timeout
            if session.is_active:
                try:
                    async with asyncio.timeout(1):
                        await session.commit()
                except TimeoutError:
                    print_error("Timeout committing session")
                    raise
                except Exception as e:
                    if "database disk image is malformed" in str(e):
                        print_error("Database corruption detected during commit")
                        raise DatabaseError("Corruption detected during commit")
                    raise

        except Exception as e:
            # Handle rollback with timeout
            if session and session.is_active:
                try:
                    async with asyncio.timeout(1):
                        await session.rollback()
                except Exception as rollback_error:
                    print_error(f"Error during rollback: {rollback_error}")

            # Check for corruption
            if isinstance(e, DatabaseError):
                raise  # Already a DatabaseError
            if "database disk image is malformed" in str(e):
                print_error("Database corruption detected during operation")
                raise DatabaseError("Corruption detected during operation")
            raise  # Re-raise original error

        finally:
            # Always try to clean up session
            if session:
                try:
                    # Close session with timeout
                    async with asyncio.timeout(1):
                        await session.close()
                except Exception as e:
                    print_error(f"Error closing session: {e}")

                try:
                    # Decrement ref count with timeout
                    async with asyncio.timeout(2):
                        await self.connection_manager.async_connections.decrement_ref_count(
                            task_id
                        )
                except (TimeoutError, Exception) as e:
                    print_error(f"Error decrementing ref count: {e}")
                    # Force cleanup on timeout/error
                    if (
                        task_id
                        in self.connection_manager.async_connections._connections
                    ):
                        try:
                            session, _ = self.connection_manager.async_connections._connections[task_id]  # type: ignore
                            del self.connection_manager.async_connections._connections[
                                task_id
                            ]
                            # Try to close session with short timeout
                            try:
                                async with asyncio.timeout(0.5):
                                    await session.close()
                            except Exception:
                                pass  # Ignore close errors on force cleanup
                        except Exception as cleanup_error:
                            print_error(f"Error during force cleanup: {cleanup_error}")

    async def cleanup(self) -> None:
        """Clean up all database resources.

        This method ensures proper cleanup of:
        - OptimizedSQLiteMemory
        - DatabaseSyncManager
        - SQLAlchemy engines and sessions
        """
        try:
            # 1. Stop sync manager if active
            if self.sync_manager is not None:
                self.sync_manager.stop_sync_thread()

            # 2. Clean up SQLAlchemy resources
            if hasattr(self, "async_engine"):
                await self.async_engine.dispose()
            if hasattr(self, "sync_engine"):
                self.sync_engine.dispose()

            # 3. Clean up optimized storage
            if hasattr(self, "optimized_storage"):
                self.optimized_storage.cleanup()

        except Exception as e:
            print_error(f"Error during database cleanup: {e}")
            raise

    def close(self) -> None:
        """Close all database connections and cleanup resources.

        This method will:
        1. Use async cleanup if in an async context
        2. Fall back to sync cleanup if needed
        3. Ensure proper cleanup even on errors
        """
        try:
            # Try to get the current event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    print_info("Using async cleanup")

                    async def _cleanup():
                        try:
                            # Ensure final sync before cleanup
                            if (
                                hasattr(self, "sync_manager")
                                and self.sync_manager is not None
                            ):
                                print_info("Performing final sync before cleanup...")
                                self.sync_manager.sync_now()

                                # Verify sync was successful
                                if not self._verify_sync():
                                    print_error("Initial sync verification failed")
                                    # Try one more time
                                    print_info("Retrying final sync...")
                                    self.sync_manager.sync_now()
                                    if not self._verify_sync():
                                        print_error(
                                            "Final sync verification failed - data may be lost!"
                                        )
                                        raise RuntimeError(
                                            "Failed to verify final sync"
                                        )
                                else:
                                    print_info("Final sync verified successfully")

                            # Now do the cleanup
                            await self.cleanup()
                        except Exception as e:
                            print_error(f"Error during async cleanup: {e}")
                            raise

                    # Create and wait for cleanup task
                    cleanup_task = loop.create_task(_cleanup())
                    try:
                        loop.run_until_complete(cleanup_task)
                    except Exception as e:
                        print_error(f"Error waiting for cleanup: {e}")
                    return
            except RuntimeError:
                print_info("No event loop - using sync cleanup")

            # If we get here, use sync cleanup
            try:
                # Ensure final sync before cleanup
                if hasattr(self, "sync_manager") and self.sync_manager is not None:
                    print_info("Performing final sync before cleanup...")
                    self.sync_manager.sync_now()

                    # Verify sync was successful
                    if not self._verify_sync():
                        print_error("Initial sync verification failed")
                        # Try one more time
                        print_info("Retrying final sync...")
                        self.sync_manager.sync_now()
                        if not self._verify_sync():
                            print_error(
                                "Final sync verification failed - data may be lost!"
                            )
                            raise RuntimeError("Failed to verify final sync")
                    else:
                        print_info("Final sync verified successfully")

                    self.sync_manager.stop_sync_thread()

                # Now do the cleanup
                if hasattr(self, "sync_engine"):
                    self.sync_engine.dispose()

                if hasattr(self, "optimized_storage"):
                    self.optimized_storage.cleanup()
            except Exception as e:
                print_error(f"Error during sync cleanup: {e}")
                raise

        except Exception as e:
            print_error(f"Error during database close: {e}")
            raise

    def _stop_background_sync(self) -> None:
        """Stop background sync if enabled."""
        if hasattr(self, "optimized_storage") and hasattr(
            self.optimized_storage, "sync_manager"
        ):
            self.optimized_storage.sync_manager.stop_sync_thread()

    def _cleanup_thread_connections(self) -> None:
        """Clean up all thread-local connections."""
        if hasattr(self, "connection_manager"):
            thread_ids = self.connection_manager.thread_connections._get_ids()
            for thread_id in thread_ids:
                try:
                    self.connection_manager.thread_connections.remove_connection(
                        thread_id
                    )
                except Exception as e:
                    print_error(f"Error closing thread connection {thread_id}: {e}")

    def _cleanup_storage(self) -> None:
        """Clean up optimized storage."""
        if hasattr(self, "optimized_storage"):
            try:
                self.optimized_storage.close_sync()
            except Exception as e:
                print_error(f"Error closing optimized storage: {e}")

    def _cleanup_engines(self) -> None:
        """Clean up database engines."""
        if hasattr(self, "sync_engine"):
            try:
                self.sync_engine.dispose()
            except Exception as e:
                print_error(f"Error disposing sync engine: {e}")
        if hasattr(self, "async_engine"):
            try:
                self.async_engine.sync_engine.dispose()
            except Exception as e:
                print_error(f"Error disposing async engine: {e}")

    def _verify_sync(self) -> bool:
        """Verify that sync was successful by comparing tables and data.

        Returns:
            True if sync was successful, False otherwise
        """
        if not hasattr(self, "optimized_storage"):
            return False

        try:
            # Get memory connection
            memory_conn = self.optimized_storage.get_shared_connection()
            if memory_conn is None:
                print_error("Could not get memory connection for verification")
                return False

            # Get disk connection
            disk_conn = sqlite3.connect(self.db_file)

            try:
                # Get memory tables
                memory_tables = memory_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                memory_tables = [t[0] for t in memory_tables]

                # Get disk tables
                disk_tables = disk_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                disk_tables = [t[0] for t in disk_tables]

                # Compare tables
                if set(memory_tables) != set(disk_tables):
                    print_error("Memory and disk tables don't match!")
                    print_info(f"Memory tables: {memory_tables}")
                    print_info(f"Disk tables: {disk_tables}")
                    return False

                # Compare row counts for each table
                for table in memory_tables:
                    memory_count = memory_conn.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                    disk_count = disk_conn.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                    if memory_count != disk_count:
                        print_error(f"Row count mismatch for table {table}!")
                        print_info(f"Memory rows: {memory_count}")
                        print_info(f"Disk rows: {disk_count}")
                        return False

                print_info("Sync verification successful")
                return True

            finally:
                disk_conn.close()

        except Exception as e:
            print_error(f"Error during sync verification: {e}")
            return False

    def _final_sync_attempt(self) -> None:
        """Attempt one final sync if possible."""
        if hasattr(self, "optimized_storage"):
            try:
                print_info("Attempting final sync...")
                self.optimized_storage.force_sync()
                if not self._verify_sync():
                    print_error("Final sync verification failed")
                    # Try one more time
                    print_info("Retrying final sync...")
                    self.optimized_storage.force_sync()
                    if not self._verify_sync():
                        print_error("Final sync retry failed")
            except Exception as sync_error:
                print_error(f"Error during final sync attempt: {sync_error}")

    def close_sync(self) -> None:
        """Synchronous cleanup for shutdown.

        This method ensures proper cleanup by:
        1. Stopping background sync
        2. Performing final sync with verification
        3. Closing connections only after successful sync
        4. Cleaning up temp files
        """
        try:
            print_info("Starting sync cleanup")

            # Stop background sync first
            self._stop_background_sync()

            # Ensure final sync before any cleanup
            if hasattr(self, "sync_manager") and self.sync_manager is not None:
                print_info("Performing final sync before cleanup...")
                self.sync_manager.sync_now()

                # Verify sync was successful
                if not self._verify_sync():
                    print_error("Initial sync verification failed")
                    # Try one more time
                    print_info("Retrying final sync...")
                    self.sync_manager.sync_now()
                    if not self._verify_sync():
                        print_error(
                            "Final sync verification failed - data may be lost!"
                        )
                else:
                    print_info("Final sync verified successfully")

            # Only clean up after successful sync
            self._cleanup_thread_connections()
            self._cleanup_storage()
            self._cleanup_engines()

            print_info("Database cleanup completed")
        except Exception as e:
            print_error(f"Error during database cleanup: {e}")
            # Try one last sync
            self._final_sync_attempt()


class OptimizedSQLiteMemory:
    """SQLite database that operates entirely in memory.

    This class manages a SQLite database that exists only in memory,
    optionally loading initial data from a disk file. It uses
    ConnectionManager to handle thread and async access safely.

    The database can be accessed both synchronously and asynchronously
    through the connection_manager, which handles proper thread/task
    safety and connection sharing.

    Attributes:
        remote_path: Path to source database file (for initial loading)
        connection_manager: Manager for database connections
    """

    def __init__(self, db_path: str | Path | None, shared_uri: str):
        """Initialize the in-memory database.

        Args:
            db_path: Path to the database file to load initially, or None for empty
            shared_uri: URI for shared memory database

        Raises:
            sqlite3.DatabaseError: If database cannot be loaded
        """
        self.remote_path = Path(db_path) if db_path else None
        self.connection_manager = ConnectionManager(optimized_storage=self)

        # Create initial in-memory database
        temp_conn = None
        try:
            # Create and configure in-memory database with shared URI
            temp_conn = sqlite3.connect(shared_uri, uri=True)
            self._configure_memory_settings(temp_conn)

            # Load existing database if path provided
            if self.remote_path and self.remote_path.exists():
                disk_conn = sqlite3.connect(str(self.remote_path))
                disk_conn.backup(temp_conn)
                disk_conn.close()

            # Store as initial connection
            self.connection_manager.set_thread_connection(
                str(threading.get_ident()), temp_conn
            )
        except Exception:
            if temp_conn:
                temp_conn.close()
            raise

        # Enable URI connections for shared cache
        sqlite3.enable_callback_tracebacks(True)

    def _configure_memory_settings(self, conn: sqlite3.Connection) -> None:
        """Configure SQLite connection for optimal memory performance.

        Args:
            conn: SQLite connection to configure
        """
        # Memory-specific optimizations
        conn.execute("PRAGMA journal_mode=MEMORY")  # In-memory journal
        conn.execute("PRAGMA synchronous=OFF")  # No disk syncs needed
        conn.execute("PRAGMA cache_size=-2000")  # 2MB cache
        conn.execute("PRAGMA temp_store=MEMORY")  # Store temp tables in memory
        conn.execute("PRAGMA page_size=4096")  # Optimal page size
        conn.execute("PRAGMA mmap_size=0")  # Disable memory mapping
        conn.execute("PRAGMA threads=4")  # Use multiple threads

        # General optimizations
        conn.execute("PRAGMA locking_mode=NORMAL")  # Better concurrency
        conn.execute("PRAGMA busy_timeout=30000")  # 30 second timeout

    def get_shared_connection(self) -> sqlite3.Connection | None:
        """Get a shared connection to the in-memory database.

        This method returns a connection that can be used by multiple threads.
        The connection uses SQLite's shared cache mode for thread safety.

        Returns:
            SQLite connection or None if no connections available
        """
        # Try to get any existing connection first
        thread_ids = self.connection_manager.thread_connections._get_ids()
        for thread_id in thread_ids:
            conn = self.connection_manager.get_thread_connection(thread_id)
            if conn is not None:
                return conn

        # Create a new shared connection if none exists
        try:
            conn = sqlite3.connect(
                "file::memory:?cache=shared",
                uri=True,
                isolation_level=None,  # For explicit transaction control
                check_same_thread=False,  # Allow multi-threading
            )
            self._configure_memory_settings(conn)

            # Store in connection manager
            thread_id = str(threading.get_ident())
            self.connection_manager.set_thread_connection(thread_id, conn)
            return conn
        except Exception as e:
            print_error(f"Error creating shared connection: {e}")
            return None

    def _setup_connection_optimizations(self) -> None:
        """Set up optimizations for all connections.

        This method:
        1. Gets all current connections
        2. Applies memory optimizations to each
        3. Handles missing connections gracefully
        """
        # Get all current connections
        thread_ids = self.connection_manager.thread_connections._get_ids()
        for thread_id in thread_ids:
            conn = self.connection_manager.get_thread_connection(thread_id)
            if conn is not None:
                self._configure_memory_settings(conn)

    @retry_on_locked_db
    def execute(self, query: str, params=()) -> sqlite3.Cursor:
        """Execute a query synchronously.

        Args:
            query: SQL query to execute
            params: Query parameters

        Returns:
            SQLite cursor with query results
        """
        thread_id = str(threading.get_ident())
        conn = self.connection_manager.get_thread_connection(thread_id)
        if conn is None:
            conn = self.get_shared_connection()
        return conn.execute(query, params)

    @retry_on_locked_db
    async def execute_async(self, query: str, params=()) -> Any:
        """Execute a query asynchronously.

        Uses asyncio.to_thread to run the SQLite operation in a thread pool,
        preventing blocking of the event loop while maintaining proper locking.

        Args:
            query: SQL query to execute
            params: Query parameters

        Returns:
            Query result
        """
        task_id = id(asyncio.current_task())
        query_lock = self.connection_manager.async_connections._get_query_lock(task_id)
        async with query_lock:
            session = self.connection_manager.async_connections._connections[task_id][0]  # type: ignore
            return await asyncio.to_thread(session.execute, query, params)

    @retry_on_locked_db
    async def executemany_async(self, query: str, params_seq) -> Any:
        """Execute multiple queries asynchronously.

        Uses asyncio.to_thread to run the SQLite operation in a thread pool,
        preventing blocking of the event loop while maintaining proper locking.

        Args:
            query: SQL query to execute
            params_seq: Sequence of query parameters

        Returns:
            Query result
        """
        task_id = id(asyncio.current_task())
        query_lock = self.connection_manager.async_connections._get_query_lock(task_id)
        async with query_lock:
            session = self.connection_manager.async_connections._connections[task_id][0]  # type: ignore
            return await asyncio.to_thread(session.executemany, query, params_seq)

    def cleanup(self) -> None:
        """Clean up resources asynchronously."""
        try:
            self.connection_manager.cleanup_sync()
        except Exception as e:
            print_error(f"Error cleaning up connections: {e}")

    def close_sync(self) -> None:
        """Clean up resources synchronously."""
        try:
            self.connection_manager.cleanup_sync()
        except Exception as e:
            print_error(f"Error cleaning up connections: {e}")


class DatabaseSyncManager:
    """Manage synchronization between in-memory and disk databases.

    This class handles the background synchronization of the in-memory
    database with its on-disk copy, particularly important for network
    storage locations.

    The manager provides:
    1. Time-based sync (from config.db_sync_seconds)
    2. Commit-based sync (from config.db_sync_commits)
    3. Progress tracking and error handling
    4. Network-aware sync optimization
    5. Network path detection
    6. Atomic disk writes using SQLite's backup API

    The manager uses OptimizedSQLiteMemory's shared connection feature
    to access the in-memory database, and SQLite's backup API to perform
    atomic writes to disk.

    Attributes:
        remote_path: Path to actual database file
        sync_interval: Seconds between syncs (from config)
        sync_commits: Number of commits between syncs (from config)
        _stop_event: Event to signal sync thread to stop
        _sync_thread: Background thread for syncing
        commit_count: Number of commits since last sync
        _sync_stats: Dictionary tracking sync statistics
        _is_network: Whether remote_path is on network drive
        optimized_storage: OptimizedSQLiteMemory instance for in-memory access
    """

    def __init__(
        self,
        remote_path: Path,
        config: FanslyConfig,
        optimized_storage: OptimizedSQLiteMemory,
    ) -> None:
        """Initialize sync manager.

        Args:
            remote_path: Path to actual database file
            config: FanslyConfig containing sync settings
            optimized_storage: OptimizedSQLiteMemory instance to sync from

        The manager will use the optimized_storage's shared connection feature
        to access the in-memory database, and SQLite's backup API to perform
        atomic writes to disk.
        """
        self.remote_path = remote_path
        self._is_network = is_network_path(remote_path)
        self._stop_event = threading.Event()
        self._sync_thread = None
        self.commit_count = 0
        self._sync_lock = threading.Lock()  # Lock to coordinate syncs with transactions
        self._active_transactions = 0  # Count of active transactions
        self.optimized_storage = optimized_storage
        self._transaction_lock = threading.Lock()  # Lock for transaction counter

        # Initialize sync statistics
        self._sync_stats = {
            "total_syncs": 0,
            "failed_syncs": 0,
            "last_sync_time": None,
            "last_sync_duration": None,
            "last_error": None,
            "errors": [],  # List of error messages
            "network_errors": 0,
            "bytes_synced": 0,
            "is_network": self._is_network,
        }

        # Set sync settings based on path type and config
        if self._is_network:
            # Network paths need more frequent syncs
            self.sync_interval = (
                config.db_sync_seconds if hasattr(config, "db_sync_seconds") else 30
            )
            self.sync_commits = (
                config.db_sync_commits if hasattr(config, "db_sync_commits") else 100
            )
        else:
            # Local paths can use longer intervals
            self.sync_interval = (
                config.db_sync_seconds if hasattr(config, "db_sync_seconds") else 60
            )
            self.sync_commits = (
                config.db_sync_commits if hasattr(config, "db_sync_commits") else 1000
            )

        if self._is_network:
            print_info(
                f"Network path detected for {remote_path}, using optimized sync settings "
                f"(interval: {self.sync_interval}s, commits: {self.sync_commits})"
            )

        # Start sync thread
        self._start_sync_thread()

    def _start_sync_thread(self) -> None:
        """Start background sync thread."""
        if self._sync_thread is None:
            self._sync_thread = Thread(target=self._sync_loop, daemon=True)
            self._sync_thread.start()

    def _sync_loop(self) -> None:
        """Background sync loop maintaining accurate intervals using monotonic time."""
        last_sync_attempt = monotonic()  # Time of last sync attempt
        last_sync_success = last_sync_attempt  # Time of last successful sync
        last_status = last_sync_attempt  # Time of last status message

        print_info(
            f"Starting sync thread (interval: {self.sync_interval}s, commits: {self.sync_commits})"
        )

        while not self._stop_event.is_set():
            try:
                current_time = monotonic()

                # Log status every 30 seconds
                if current_time - last_status >= 30:
                    print_info(
                        f"Sync status: {current_time - last_sync_success:.1f}s since last sync, "
                        f"{self.commit_count} commits pending"
                    )
                    last_status = current_time

                # Only attempt sync if enough time has passed since last attempt
                time_since_attempt = current_time - last_sync_attempt
                if time_since_attempt < 1.0:  # Minimum 1 second between attempts
                    self._stop_event.wait(1.0 - time_since_attempt)
                    continue

                should_sync = False
                sync_reason = None

                # Check time-based sync
                time_since_sync = current_time - last_sync_success
                if self.sync_interval and time_since_sync >= self.sync_interval:
                    should_sync = True
                    sync_reason = f"time-based sync after {time_since_sync:.1f}s"

                # Check commit-based sync
                elif self.sync_commits and self.commit_count >= self.sync_commits:
                    should_sync = True
                    sync_reason = f"commit-based sync after {self.commit_count} commits"

                # Attempt sync if needed
                if should_sync:
                    print_debug(f"Triggering {sync_reason}")
                    last_sync_attempt = current_time
                    try:
                        self.sync_now()
                        last_sync_success = current_time
                        if "commits" in sync_reason:
                            self.commit_count = 0
                    except Exception:
                        # Error already logged in sync_now
                        pass

                # Short sleep to prevent CPU spin
                time_sleep(0.1)

            except Exception as e:
                print_error(f"Error in sync thread: {e}")
                self._sync_stats["errors"].append(str(e))

    def begin_transaction(self) -> None:
        """Track start of a transaction."""
        with self._transaction_lock:
            self._active_transactions += 1

    def end_transaction(self) -> None:
        """Track end of a transaction."""
        with self._transaction_lock:
            self._active_transactions = max(0, self._active_transactions - 1)

    def notify_commit(self) -> None:
        """Notify of a new commit."""
        if self.sync_commits:
            self.commit_count += 1
            if self.commit_count % 10 == 0:  # Log every 10 commits
                print_info(f"Database commits: {self.commit_count}/{self.sync_commits}")

    def get_sync_stats(self) -> dict[str, Any]:
        """Get current sync statistics.

        Returns:
            Dictionary with sync statistics
        """
        return self._sync_stats.copy()

    def _wait_for_transactions(self, max_wait: int = 5) -> bool:
        """Wait for active transactions to complete.

        Args:
            max_wait: Maximum time to wait in seconds

        Returns:
            True if no active transactions, False if timed out
        """
        wait_start = monotonic()
        while self._active_transactions > 0:
            if monotonic() - wait_start > max_wait:
                print_info("Transactions still active after wait, skipping sync")
                return False
            time_sleep(0.1)  # Short sleep to prevent CPU spin
        return True

    def sync_now(self) -> None:
        """Synchronize in-memory database to disk now.

        This method:
        1. Gets a shared connection from OptimizedSQLiteMemory
        2. Uses SQLite's backup API for atomic writes
        3. Tracks sync timing and progress
        4. Handles network errors
        5. Reports sync status
        6. Verifies sync success

        Note: Uses a lock to coordinate with active transactions.
        If the database is busy, will skip this sync attempt.
        """
        # Try to acquire sync lock with timeout
        try:
            if not self._sync_lock.acquire(blocking=False):
                print_info("Another sync in progress, skipping")
                return
        except KeyboardInterrupt:
            print_info("Sync interrupted by Ctrl+C")
            if self._sync_lock.locked():
                self._sync_lock.release()
            raise

        try:
            # Get memory connection first to check tables
            memory_conn = self.optimized_storage.get_shared_connection()
            if memory_conn is None:
                print_error("Could not get memory connection for table check")
                return

            # Check if there are any tables to sync
            memory_tables = memory_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if not memory_tables:
                print_info("No tables in memory database, skipping sync")
                return
            print_debug(f"Found tables to sync: {[t[0] for t in memory_tables]}")

            # Wait for active transactions to complete
            if not self._wait_for_transactions():
                return

            start_time = monotonic()
            print_debug("Starting database sync...")

            # Get a shared connection to the in-memory database
            memory_conn = self.optimized_storage.get_shared_connection()
            if memory_conn is None:
                print_error("Could not get memory connection")
                return

            # Create remote directory if it doesn't exist
            self.remote_path.parent.mkdir(parents=True, exist_ok=True)

            # Perform sync with retries
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Create a new disk database connection
                    disk_conn = sqlite3.connect(self.remote_path)

                    try:
                        # Configure disk connection for sync
                        disk_conn.execute(
                            "PRAGMA journal_mode=DELETE"
                        )  # Simpler journal for sync
                        disk_conn.execute(
                            "PRAGMA synchronous=FULL"
                        )  # Full sync for safety

                        # Get memory tables before backup
                        print_debug("Checking memory tables...")
                        memory_tables = memory_conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                        memory_tables = [t[0] for t in memory_tables]
                        print_debug(f"Memory tables: {memory_tables}")

                        # Backup in-memory to disk atomically
                        print_debug("Starting memory to disk backup...")
                        memory_conn.backup(disk_conn)

                        # Ensure data is written to disk
                        print_debug("Committing disk changes...")
                        disk_conn.commit()

                        # Verify tables exist
                        print_debug("Verifying disk tables...")
                        disk_tables = disk_conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                        disk_tables = [t[0] for t in disk_tables]
                        print_debug(f"Disk tables: {disk_tables}")

                        # Get final size for stats
                        final_size = self.remote_path.stat().st_size
                        print_debug(f"Final file size: {final_size} bytes")

                        # Verify backup
                        if final_size == 0 or set(disk_tables) != set(memory_tables):
                            if attempt < max_retries - 1:
                                print_error(
                                    f"Sync verification failed (attempt {attempt + 1}/{max_retries}), retrying..."
                                )
                                continue
                            raise OSError(
                                "Failed to verify sync - remote file is empty"
                            )

                        # Update sync stats
                        self._sync_stats["total_syncs"] += 1
                        self._sync_stats["last_sync_time"] = start_time
                        self._sync_stats["last_sync_duration"] = (
                            monotonic() - start_time
                        )
                        self._sync_stats["bytes_synced"] += final_size

                        print_debug(
                            f"Database sync completed in {self._sync_stats['last_sync_duration']:.1f}s"
                        )
                        break

                    finally:
                        disk_conn.close()

                except Exception as e:
                    if attempt < max_retries - 1:
                        print_error(
                            f"Sync attempt {attempt + 1} failed: {e}, retrying..."
                        )
                        time_sleep(1)  # Wait before retry
                        continue
                    raise

        except OSError as e:
            print_error(f"Network error during sync: {e}")
            self._sync_stats["network_errors"] += 1
            self._sync_stats["failed_syncs"] += 1
            self._sync_stats["last_error"] = str(e)
            raise
        except Exception as e:
            print_error(f"Error syncing database: {e}")
            self._sync_stats["failed_syncs"] += 1
            self._sync_stats["last_error"] = str(e)
            raise
        finally:
            self._sync_lock.release()

    def stop_sync_thread(self) -> None:
        """Stop background sync thread.

        This method:
        1. Signals thread to stop
        2. Waits for completion
        3. Updates statistics
        4. Cleans up resources
        """
        if self._sync_thread:
            try:
                self._stop_event.set()
                self._sync_thread.join(timeout=5)
                if self._sync_thread.is_alive():
                    print_error("Sync thread did not stop cleanly")
                self._sync_thread = None
            except Exception as e:
                print_error(f"Error stopping sync thread: {e}")
                self._sync_stats["last_error"] = str(e)
