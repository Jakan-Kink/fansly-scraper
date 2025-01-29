"""Resource management for database connections.

This module provides classes for managing database resources:
- BaseConnections: Base class for connection management
- ThreadLocalConnections: Manages thread-local database connections
- AsyncConnections: Manages async database connections
- ConnectionManager: Coordinates all connection management
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from threading import local
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.sql import text

from textio import print_debug, print_error, print_info, print_warning

from .decorators import retry_on_locked_db

RT = TypeVar("RT")


class BaseConnections:
    """Base class for connection management.

    This class provides common functionality for both thread-local
    and async connections, ensuring proper cleanup and resource management.

    Attributes:
        _connections: Storage for connections (thread-local or dict)
        _locks: Storage for locks (thread-local or dict)
        _global_lock: Lock for thread-safe operations
    """

    def __init__(self, use_thread_local: bool = True) -> None:
        """Initialize connection storage and locks.

        Args:
            use_thread_local: Whether to use thread-local storage
        """
        self._use_thread_local = use_thread_local
        if use_thread_local:
            self._connections = local()
            self._locks = local()
            self._global_lock = threading.Lock()
        else:
            self._connections: dict[int, tuple[Any, int]] = {}
            self._locks: dict[int, asyncio.Lock] = {}
            self._global_lock = asyncio.Lock()

    def _get_ids(self) -> list[str | int]:
        """Get all IDs with active connections.

        Returns:
            List of thread/task IDs
        """
        if self._use_thread_local:
            return [tid for tid in dir(self._connections) if tid.isdigit()]
        return list(self._connections.keys())

    @asynccontextmanager
    async def _track_lock(self, task_id: int, operation: str) -> AsyncGenerator[None]:
        """Track lock usage with operation info.

        Args:
            task_id: Task ID being locked
            operation: Operation being performed

        Yields:
            None
        """
        from time import time

        try:
            self._lock_info[task_id] = (operation, time())
            yield
        finally:
            if task_id in self._lock_info:
                del self._lock_info[task_id]

    def _get_lock(self, id_: str | int) -> threading.Lock | asyncio.Lock:
        """Get or create lock for a specific ID.

        Args:
            id_: Thread/task ID to get lock for

        Returns:
            Thread/task-specific lock for session management
        """
        print_debug(
            f"[{id_}] Getting lock (thread_local={self._use_thread_local}, class={self.__class__.__name__}, caller=_get_lock)"
        )

        # Base class only handles threading.Lock
        print_debug(f"[{id_}] BRANCH: Using threading.Lock")
        if self._use_thread_local:
            print_debug(
                f"[{id_}] BRANCH: Using thread-local storage for {self.__class__.__name__}"
            )
            if not hasattr(self._locks, str(id_)):
                print_debug(f"[{id_}] BRANCH: Lock not found in thread-local storage")
                with self._global_lock:
                    if not hasattr(self._locks, str(id_)):
                        print_debug(
                            f"[{id_}] BRANCH: Creating new threading.Lock in thread-local"
                        )
                        setattr(self._locks, str(id_), threading.Lock())
            else:
                print_debug(
                    f"[{id_}] BRANCH: Found existing lock in thread-local storage"
                )
            lock = getattr(self._locks, str(id_))
        else:
            print_debug(
                f"[{id_}] BRANCH: Using dict storage for {self.__class__.__name__}"
            )
            if id_ not in self._locks:
                print_debug(f"[{id_}] BRANCH: Lock not found in dict storage")
                with self._global_lock:
                    if id_ not in self._locks:
                        print_debug(
                            f"[{id_}] BRANCH: Creating new threading.Lock in dict"
                        )
                        self._locks[id_] = threading.Lock()
            else:
                print_debug(f"[{id_}] BRANCH: Found existing lock in dict storage")
            lock = self._locks[id_]
        print_debug(
            f"[{id_}] Got lock for {self.__class__.__name__}: {lock}, type={type(lock)}, dir={dir(lock)}"
        )
        return lock

    def _get_query_lock(self, task_id: int) -> asyncio.Lock:
        """Get or create query lock for a specific task.

        Args:
            task_id: Task ID to get lock for

        Returns:
            Task-specific lock for query execution
        """
        print_debug(
            f"[{task_id}] Getting query lock (caller: {self.__class__.__name__}._get_query_lock)"
        )
        if task_id not in self._query_locks:
            print_debug(f"[{task_id}] Creating new query lock")
            with self._global_lock:
                if task_id not in self._query_locks:
                    loop = asyncio.get_running_loop()
                    print_debug(f"[{task_id}] Using event loop: {loop}")
                    self._query_locks[task_id] = asyncio.Lock()
        lock = self._query_locks[task_id]
        print_debug(
            f"[{task_id}] Got query lock: {lock}, type={type(lock)}, dir={dir(lock)}"
        )
        return lock

    def _check_lock_status(self, task_id: int) -> None:
        """Check and log lock status for a task.

        Args:
            task_id: Task ID to check
        """
        from time import time

        current_time = time()

        # Check session lock
        if task_id in self._locks:
            lock = self._locks[task_id]
            if lock.locked():  # type: ignore
                if task_id in self._lock_info:
                    operation, timestamp = self._lock_info[task_id]
                    duration = current_time - timestamp
                    if duration > 1.0:  # Only log if lock held for more than a second
                        print_warning(
                            f"Task {task_id} session lock held by operation '{operation}' "
                            f"for {duration:.1f} seconds"
                        )
                else:
                    print_warning(
                        f"Task {task_id} session lock is held but no operation info"
                    )

        # Check query lock
        if task_id in self._query_locks:
            lock = self._query_locks[task_id]
            if lock.locked():
                print_debug(f"Task {task_id} query lock is held")

    async def _cleanup_id(self, id_: str | int, already_locked: bool = False) -> None:
        """Clean up connections for a specific ID.

        Args:
            id_: Thread/task ID to clean up
            already_locked: Whether the lock is already held by the caller
        """
        try:

            async def do_cleanup():
                await self._cleanup_connection(id_)
                # Clean up query lock if it exists
                if isinstance(id_, int) and id_ in self._query_locks:
                    del self._query_locks[id_]

            if already_locked:
                await do_cleanup()
            else:
                print_debug(
                    f"[{id_}] Getting cleanup lock (caller: {self.__class__.__name__}._cleanup_id)"
                )
                lock = self._get_lock(id_)
                print_debug(
                    f"[{id_}] Using lock: {lock}, type={type(lock)}, dir={dir(lock)}"
                )

                if self._use_thread_local:
                    with lock:  # type: ignore
                        self._cleanup_connection(id_)
                else:
                    try:
                        print_debug(
                            f"[{id_}] Attempting to acquire lock in {self.__class__.__name__}._cleanup_id"
                        )
                        await asyncio.wait_for(lock.acquire(), timeout=2)
                        print_debug(
                            f"[{id_}] Lock acquired in {self.__class__.__name__}._cleanup_id"
                        )

                        try:
                            await do_cleanup()
                        finally:
                            print_debug(
                                f"[{id_}] Releasing lock in {self.__class__.__name__}._cleanup_id"
                            )
                            lock.release()
                    except Exception as e:
                        print_error(
                            f"[{id_}] Error in {self.__class__.__name__}._cleanup_id: {e}"
                        )
                        if lock.locked():
                            print_debug(f"[{id_}] Releasing lock after error")
                            lock.release()
                        raise
        except Exception as e:
            print_error(f"[{id_}] Error cleaning up ID {id_}: {e}")

    async def _cleanup_connection(self, id_: str | int) -> None:
        """Clean up a specific connection.

        Args:
            id_: Thread/task ID of connection to clean up
        """
        if self._use_thread_local:
            conn = getattr(self._connections, str(id_))
            if hasattr(conn, "close"):
                if asyncio.iscoroutinefunction(conn.close):
                    await conn.close()
                else:
                    conn.close()
            delattr(self._connections, str(id_))
            delattr(self._locks, str(id_))
        else:
            if id_ in self._connections:
                conn = self._connections[id_][0]
                if hasattr(conn, "close"):
                    if asyncio.iscoroutinefunction(conn.close):
                        try:
                            async with asyncio.timeout(2):  # 2 second timeout
                                await conn.close()
                        except Exception as e:
                            print_error(f"Error closing async connection: {e}")
                    else:
                        conn.close()
                del self._connections[id_]
            if id_ in self._locks:
                del self._locks[id_]

    async def cleanup(self) -> None:
        """Clean up all connections."""
        try:
            if self._use_thread_local:
                with self._global_lock:  # type: ignore
                    for id_ in self._get_ids():
                        await self._cleanup_id(id_)
            else:
                async with self._global_lock:  # type: ignore
                    for id_ in self._get_ids():
                        await self._cleanup_id(id_)
        except Exception as e:
            print_error(f"Error during database cleanup: {e}")
            # Try to force cleanup of remaining connections
            try:
                for id_ in self._get_ids():
                    if id_ in self._connections:
                        del self._connections[id_]
                    if id_ in self._locks:
                        del self._locks[id_]
            except Exception as cleanup_error:
                print_error(f"Error during forced cleanup: {cleanup_error}")


class ThreadLocalConnections(BaseConnections):
    """Manage thread-local database connections with proper SQLite sharing.

    This class provides thread-safe management of SQLite connections with:
    1. Shared cache mode for better concurrency
    2. Proper locking modes for thread safety
    3. Connection pooling for performance
    4. Resource cleanup across threads
    5. Support for in-memory databases with proper sharing
    """

    def __init__(
        self,
        optimized_storage: Any | None = None,
    ) -> None:
        """Initialize thread-local storage and connection pool.

        Args:
            optimized_storage: Optional OptimizedSQLiteMemory instance to share
        """
        super().__init__(use_thread_local=True)
        self._optimized_storage = optimized_storage
        self._shared_cache_uri = (
            optimized_storage.shared_uri if optimized_storage else None
        )
        self._connection_pool = []
        self._pool_lock = threading.Lock()
        self._is_memory_db = True if optimized_storage else False

    def _create_shared_connection(self) -> sqlite3.Connection:
        """Create a connection with shared cache mode.

        Returns:
            SQLite connection with shared cache

        Raises:
            RuntimeError: If no OptimizedSQLiteMemory instance provided
        """
        if not self._optimized_storage:
            raise RuntimeError("No OptimizedSQLiteMemory instance provided")

        conn = sqlite3.connect(
            self._optimized_storage.shared_uri,
            uri=True,
            isolation_level=None,  # For explicit transaction control
            check_same_thread=False,  # Allow multi-threading
        )
        conn.execute("PRAGMA locking_mode=NORMAL")  # Better concurrency
        conn.execute("PRAGMA busy_timeout=30000")  # 30 second timeout
        return conn

    def get_connection(self, thread_id: str) -> Any | None:
        """Get connection for a specific thread.

        If a connection exists for this thread, return it.
        For in-memory databases, try to reuse an existing connection
        to ensure all threads share the same database. Otherwise,
        try to reuse a connection from the pool or create a new one.

        Args:
            thread_id: Thread ID to get connection for

        Returns:
            Connection object or None if not found
        """
        lock = self._get_lock(thread_id)
        with lock:  # type: ignore
            conn = getattr(self._connections, thread_id, None)
            if conn is not None:
                return conn

            # For in-memory database, try to reuse any existing connection
            if self._is_memory_db:
                # Try to get any existing connection first
                for tid in self._get_ids():
                    if tid != thread_id:
                        existing_conn = getattr(self._connections, tid, None)
                        if existing_conn is not None:
                            try:
                                # Verify connection is good
                                existing_conn.execute("SELECT 1")
                                # Share the same in-memory database
                                setattr(self._connections, thread_id, existing_conn)
                                return existing_conn
                            except Exception:  # nosec B112
                                continue  # Connection failed, try next one

            # Try to get a connection from the pool
            with self._pool_lock:
                if self._connection_pool:
                    conn = self._connection_pool.pop()
                    try:
                        # Verify connection is good
                        conn.execute("SELECT 1")
                        setattr(self._connections, thread_id, conn)
                        return conn
                    except Exception:
                        # Connection is bad, don't reuse it
                        try:
                            conn.close()
                        except Exception:
                            pass

            return None

    def set_connection(self, thread_id: str, connection: Any) -> None:
        """Set connection for a specific thread.

        Args:
            thread_id: Thread ID to set connection for
            connection: Connection object to store
        """
        lock = self._get_lock(thread_id)
        with lock:  # type: ignore
            setattr(self._connections, thread_id, connection)

    def _cleanup_all_connections(self) -> None:
        """Clean up all thread-local connections synchronously.

        This method:
        1. Gets all thread IDs
        2. Removes each connection
        3. Handles cleanup errors
        4. Clears the connection pool
        """
        try:
            # Clean up all thread connections
            for thread_id in self._get_ids():
                try:
                    self.remove_connection(thread_id)
                except Exception as e:
                    print_error(f"Error cleaning up connection {thread_id}: {e}")

            # Clear the connection pool
            with self._pool_lock:
                while self._connection_pool:
                    try:
                        conn = self._connection_pool.pop()
                        try:
                            conn.close()
                        except Exception:
                            pass  # Ignore close errors
                    except Exception as e:
                        print_error(f"Error cleaning up pooled connection: {e}")
        except Exception as e:
            print_error(f"Error during connection cleanup: {e}")

    def remove_connection(self, thread_id: str) -> None:
        """Remove connection for a specific thread.

        Instead of just closing the connection, we:
        1. Add it to the connection pool if healthy
        2. Close it if unhealthy
        3. Update thread-local storage

        Args:
            thread_id: Thread ID to remove connection for
        """
        lock = self._get_lock(thread_id)
        with lock:  # type: ignore
            conn = getattr(self._connections, thread_id, None)
            if conn is not None:
                try:
                    # Test if connection is still good
                    conn.execute("SELECT 1")
                    # Add to pool if healthy
                    with self._pool_lock:
                        self._connection_pool.append(conn)
                except Exception:
                    # Close if unhealthy
                    try:
                        conn.close()
                    except Exception:
                        pass
                finally:
                    delattr(self._connections, thread_id)

    def _cleanup_connection(self, thread_id: str) -> None:
        """Clean up a specific connection.

        This method:
        1. Removes connection from thread-local storage
        2. Closes the connection properly
        3. Cleans up associated resources

        Args:
            thread_id: Thread ID of connection to clean up
        """
        conn = getattr(self._connections, thread_id, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            delattr(self._connections, thread_id)

        # Clean up connection pool
        with self._pool_lock:
            while self._connection_pool:
                try:
                    conn = self._connection_pool.pop()
                    conn.close()
                except Exception:
                    pass


class AsyncConnections(BaseConnections):
    """Manage async database connections with SQLite sharing.

    This class provides async-safe management of database connections with:
    1. Shared cache mode for thread/task safety
    2. Proper locking for concurrent access
    3. Connection pooling for performance
    4. Resource cleanup across tasks
    """

    def __init__(
        self,
        optimized_storage: Any,
    ) -> None:
        """Initialize async connection storage and pool.

        Args:
            optimized_storage: OptimizedSQLiteMemory instance to share
        """
        print_debug("Initializing AsyncConnections")
        # Verify we have a running event loop
        try:
            loop = asyncio.get_running_loop()
            print_debug(f"Using event loop for AsyncConnections: {loop}")
        except RuntimeError as e:
            print_error(f"No running event loop: {e}")
            raise

        # Initialize base class with async storage
        super().__init__(use_thread_local=False)
        # Always use asyncio.Lock for global lock in AsyncConnections
        self._global_lock = asyncio.Lock()
        print_debug("AsyncConnections: Base class initialized")

        # Initialize async-specific attributes
        self._optimized_storage = optimized_storage
        self._shared_cache_uri = optimized_storage.shared_uri
        self._connection_pool = []
        self._pool_lock = asyncio.Lock()
        self._query_locks: dict[int, asyncio.Lock] = {}
        self._lock_info: dict[int, tuple[str, float]] = (
            {}
        )  # task_id -> (operation, timestamp)
        print_debug("AsyncConnections: Initialization complete")

    async def _create_lock(self, id_: str | int) -> asyncio.Lock:
        """Create a new lock with proper async locking.

        Args:
            id_: Task ID to create lock for

        Returns:
            New asyncio.Lock instance
        """
        print_debug(
            f"[{id_}] Creating new lock (caller: {self.__class__.__name__}._create_lock)"
        )
        async with self._global_lock:
            if self._use_thread_local:
                if not hasattr(self._locks, str(id_)):
                    print_debug(f"[{id_}] Creating new asyncio.Lock in thread-local")
                    setattr(self._locks, str(id_), asyncio.Lock())
                return getattr(self._locks, str(id_))
            else:
                if id_ not in self._locks:
                    print_debug(f"[{id_}] Creating new asyncio.Lock in dict")
                    self._locks[id_] = asyncio.Lock()
                return self._locks[id_]

    async def _get_lock(self, id_: str | int) -> asyncio.Lock:
        """Get or create lock for a specific ID.

        This overrides the base class method to handle async lock creation.

        Args:
            id_: Task ID to get lock for

        Returns:
            Task-specific asyncio.Lock
        """
        print_debug(
            f"[{id_}] Getting lock (caller: {self.__class__.__name__}._get_lock)"
        )

        # Check if lock exists first
        if self._use_thread_local:
            if hasattr(self._locks, str(id_)):
                print_debug(f"[{id_}] Found existing lock in thread-local")
                return getattr(self._locks, str(id_))
        else:
            if id_ in self._locks:
                print_debug(f"[{id_}] Found existing lock in dict")
                return self._locks[id_]

        # If no lock exists, create it with proper async locking
        async with self._global_lock:
            # Check again in case another task created it while we were waiting
            if self._use_thread_local:
                if not hasattr(self._locks, str(id_)):
                    print_debug(f"[{id_}] Creating new asyncio.Lock in thread-local")
                    setattr(self._locks, str(id_), asyncio.Lock())
                return getattr(self._locks, str(id_))
            else:
                if id_ not in self._locks:
                    print_debug(f"[{id_}] Creating new asyncio.Lock in dict")
                    self._locks[id_] = asyncio.Lock()
                return self._locks[id_]

    def _create_shared_connection(self) -> sqlite3.Connection:
        """Create a connection with shared cache mode.

        Returns:
            SQLite connection with shared cache

        Raises:
            RuntimeError: If no OptimizedSQLiteMemory instance provided
        """
        if not self._optimized_storage:
            raise RuntimeError("No OptimizedSQLiteMemory instance provided")

        conn = sqlite3.connect(
            self._optimized_storage.shared_uri,
            uri=True,
            isolation_level=None,  # For explicit transaction control
            check_same_thread=False,  # Allow multi-threading
        )
        conn.execute("PRAGMA locking_mode=NORMAL")  # Better concurrency
        conn.execute("PRAGMA busy_timeout=30000")  # 30 second timeout
        return conn

    async def get_session(self, task_id: int) -> tuple[AsyncSession, int] | None:
        """Get session and reference count for a task.

        If a session exists for this task, return it.
        Otherwise, try to reuse a connection from the pool
        or create a new one with shared cache mode.

        Args:
            task_id: Task ID to get session for

        Returns:
            Tuple of (session, ref_count) or None if not found
        """
        if not self._optimized_storage:
            print_error(f"[{task_id}] No database instance available")
            return None

        print_debug(
            f"[{task_id}] Getting session (caller: {self.__class__.__name__}.get_session)"
        )
        lock = await self._get_lock(task_id)
        print_debug(
            f"[{task_id}] Using lock: {lock}, type={type(lock)}, dir={dir(lock)}, methods={[m for m in dir(lock) if not m.startswith('_')]}"
        )

        # Verify we got an asyncio.Lock
        if not isinstance(lock, asyncio.Lock):
            print_error(
                f"[{task_id}] Wrong lock type! Expected asyncio.Lock, got {type(lock)}"
            )
            print_error(f"[{task_id}] Lock attributes: {dir(lock)}")
            print_error(
                f"[{task_id}] Lock state: {lock.__dict__ if hasattr(lock, '__dict__') else 'no __dict__'}"
            )
            raise TypeError(f"Wrong lock type! Expected asyncio.Lock, got {type(lock)}")

        try:
            # Try to acquire lock with timeout
            print_debug(
                f"[{task_id}] Attempting to acquire lock in {self.__class__.__name__}.get_session"
            )
            print_debug(f"[{task_id}] Lock state before acquire: {lock}")
            await asyncio.wait_for(lock.acquire(), timeout=2)
            print_debug(f"[{task_id}] Lock state after acquire: {lock}")
            print_debug(
                f"[{task_id}] Lock acquired in {self.__class__.__name__}.get_session"
            )

            try:
                # Check existing session
                if task_id in self._connections:
                    session, count = self._connections[task_id]  # type: ignore
                    try:
                        print_debug(f"[{task_id}] Testing existing session")
                        await session.execute(text("SELECT 1"))
                        print_debug(
                            f"[{task_id}] Found existing session (ref_count={count})"
                        )
                        return session, count
                    except Exception as e:
                        print_debug(f"[{task_id}] Session health check failed: {e}")
                        # Session is invalid, remove it
                        await self._cleanup_id(task_id)

                # Try to get a connection from the pool
                print_debug(f"[{task_id}] Trying to get connection from pool")
                async with self._pool_lock:
                    if self._connection_pool:
                        conn = self._connection_pool.pop()
                        try:
                            print_debug(f"[{task_id}] Testing pool connection")
                            conn.execute("SELECT 1")
                            session = AsyncSession(conn)
                            self._connections[task_id] = (session, 1)  # type: ignore
                            print_debug(f"[{task_id}] Got connection from pool")
                            return session, 1
                        except Exception as e:
                            print_debug(f"[{task_id}] Pool connection test failed: {e}")
                            try:
                                conn.close()
                            except Exception as e:
                                print_debug(
                                    f"[{task_id}] Error closing bad connection: {e}"
                                )

                print_debug(f"[{task_id}] No session available")
                return None

            finally:
                print_debug(
                    f"[{task_id}] Releasing lock in {self.__class__.__name__}.get_session"
                )
                print_debug(f"[{task_id}] Lock state before release: {lock}")
                lock.release()
                print_debug(f"[{task_id}] Lock state after release: {lock}")

        except TimeoutError:
            print_error(
                f"[{task_id}] Timeout acquiring lock in {self.__class__.__name__}.get_session"
            )
            raise
        except Exception as e:
            print_error(
                f"[{task_id}] Error in {self.__class__.__name__}.get_session: {e}"
            )

    async def add_session(
        self, task_id: int, session: AsyncSession, ref_count: int = 1
    ) -> None:
        """Add or update session for a task.

        Args:
            task_id: Task ID to add session for
            session: Session to store
            ref_count: Initial reference count (default: 1)
        """
        print_debug(
            f"[{task_id}] Adding session (caller: {self.__class__.__name__}.add_session)"
        )
        lock = await self._get_lock(task_id)
        print_debug(
            f"[{task_id}] Using lock: {lock}, type={type(lock)}, dir={dir(lock)}"
        )

        try:
            # Try to acquire lock with timeout
            print_debug(
                f"[{task_id}] Attempting to acquire lock in {self.__class__.__name__}.add_session"
            )
            await asyncio.wait_for(lock.acquire(), timeout=2)
            print_debug(
                f"[{task_id}] Lock acquired in {self.__class__.__name__}.add_session"
            )

            try:
                async with self._track_lock(task_id, "add_session"):
                    self._connections[task_id] = (session, ref_count)  # type: ignore
            finally:
                print_debug(
                    f"[{task_id}] Releasing lock in {self.__class__.__name__}.add_session"
                )
                lock.release()
        except Exception as e:
            print_error(
                f"[{task_id}] Error in {self.__class__.__name__}.add_session: {e}"
            )
            if lock.locked():
                print_debug(f"[{task_id}] Releasing lock after error")
                lock.release()
            raise

    async def remove_session(
        self, task_id: int, existing_lock: asyncio.Lock | None = None
    ) -> None:
        """Remove session for a task.

        Instead of just closing the session, we:
        1. Add its connection to the pool if healthy
        2. Close it if unhealthy
        3. Update task storage

        Args:
            task_id: Task ID to remove session for
            existing_lock: Optional already-acquired lock to use
        """
        print_debug(
            f"[{task_id}] Removing session (caller: {self.__class__.__name__}.remove_session)"
        )
        if existing_lock is None:
            lock = await self._get_lock(task_id)
            print_debug(
                f"[{task_id}] Using lock: {lock}, type={type(lock)}, dir={dir(lock)}"
            )
        else:
            print_debug(f"[{task_id}] Using existing lock")
            lock = existing_lock

        try:
            # Only acquire lock if we don't have one already
            if existing_lock is None:
                print_debug(
                    f"[{task_id}] Attempting to acquire lock in {self.__class__.__name__}.remove_session"
                )
                await asyncio.wait_for(lock.acquire(), timeout=2)
                print_debug(
                    f"[{task_id}] Lock acquired in {self.__class__.__name__}.remove_session"
                )

            try:
                async with self._track_lock(task_id, "remove_session"):
                    if task_id in self._connections:
                        session, _ = self._connections[task_id]  # type: ignore
                        try:
                            # Test if session is still good
                            await session.execute(text("SELECT 1"))
                            # Add connection to pool if healthy
                            async with self._pool_lock:
                                self._connection_pool.append(session.connection())
                        except Exception:
                            # Close if unhealthy
                            try:
                                async with asyncio.timeout(2):  # 2 second timeout
                                    await session.close()
                            except Exception as e:
                                print_error(
                                    f"Error closing session for task {task_id}: {e}"
                                )
                        finally:
                            await self._cleanup_id(task_id, already_locked=True)
            finally:
                # Only release if we acquired it
                if existing_lock is None:
                    print_debug(
                        f"[{task_id}] Releasing lock in {self.__class__.__name__}.remove_session"
                    )
                    lock.release()
        except Exception as e:
            print_error(
                f"[{task_id}] Error in {self.__class__.__name__}.remove_session: {e}"
            )
            if lock.locked():
                print_debug(f"[{task_id}] Releasing lock after error")
                lock.release()
            raise

    async def increment_ref_count(self, task_id: int) -> None:
        """Increment reference count for a task's session.

        Args:
            task_id: Task ID to increment count for
        """
        print_debug(
            f"[{task_id}] Incrementing ref count (caller: {self.__class__.__name__}.increment_ref_count)"
        )
        lock = await self._get_lock(task_id)
        print_debug(
            f"[{task_id}] Using lock: {lock}, type={type(lock)}, dir={dir(lock)}"
        )

        try:
            # Try to acquire lock with timeout
            print_debug(
                f"[{task_id}] Attempting to acquire lock in {self.__class__.__name__}.increment_ref_count"
            )
            await asyncio.wait_for(lock.acquire(), timeout=2)
            print_debug(
                f"[{task_id}] Lock acquired in {self.__class__.__name__}.increment_ref_count"
            )

            try:
                async with self._track_lock(task_id, "increment_ref_count"):
                    if task_id in self._connections:
                        session, count = self._connections[task_id]  # type: ignore
                        self._connections[task_id] = (session, count + 1)  # type: ignore
            finally:
                print_debug(
                    f"[{task_id}] Releasing lock in {self.__class__.__name__}.increment_ref_count"
                )
                lock.release()
        except Exception as e:
            print_error(
                f"[{task_id}] Error in {self.__class__.__name__}.increment_ref_count: {e}"
            )
            if lock.locked():
                print_debug(f"[{task_id}] Releasing lock after error")
                lock.release()
            raise

    async def decrement_ref_count(self, task_id: int) -> None:
        """Decrement reference count for a task's session.

        Args:
            task_id: Task ID to decrement count for
        """
        print_debug(
            f"[{task_id}] Decrementing ref count (caller: {self.__class__.__name__}.decrement_ref_count)"
        )
        lock = await self._get_lock(task_id)
        print_debug(
            f"[{task_id}] Using lock: {lock}, type={type(lock)}, dir={dir(lock)}"
        )

        try:
            # Check lock status before attempting to acquire
            self._check_lock_status(task_id)
            print_debug(
                f"[{task_id}] Attempting to acquire lock in {self.__class__.__name__}.decrement_ref_count"
            )
            await asyncio.wait_for(lock.acquire(), timeout=2)
            print_debug(
                f"[{task_id}] Lock acquired in {self.__class__.__name__}.decrement_ref_count"
            )

            try:
                async with self._track_lock(task_id, "decrement_ref_count"):
                    # Once we have the lock, check if we still need to do anything
                    if task_id not in self._connections:
                        print_debug(
                            f"[{task_id}] No connection found after lock acquisition"
                        )
                        return

                    session, count = self._connections[task_id]  # type: ignore
                    if count <= 1:
                        try:
                            # Separate timeout for remove_session
                            print_debug(f"[{task_id}] Count <= 1, removing session")
                            async with asyncio.timeout(
                                3
                            ):  # 3 second timeout for removal
                                await self.remove_session(task_id, existing_lock=lock)
                        except TimeoutError:
                            print_error(f"[{task_id}] Timeout removing session")
                            # Force cleanup but keep lock state consistent
                            try:
                                print_debug(f"[{task_id}] Forcing cleanup")
                                session.sync_session_factory = (
                                    None  # Break circular refs
                                )
                                session._sync_manager = None  # Break circular refs
                                del self._connections[task_id]
                            except Exception as e:
                                print_error(
                                    f"[{task_id}] Error during forced cleanup: {e}"
                                )
                                raise e
                    else:
                        print_debug(
                            f"[{task_id}] Decreasing ref count from {count} to {count - 1}"
                        )
                        self._connections[task_id] = (session, count - 1)  # type: ignore
            finally:
                print_debug(
                    f"[{task_id}] Releasing lock in {self.__class__.__name__}.decrement_ref_count"
                )
                lock.release()
        except TimeoutError as e:
            print_error(f"[{task_id}] Timeout acquiring lock")
            # Don't force cleanup here - we couldn't get the lock
            raise e
        except Exception as e:
            print_error(
                f"[{task_id}] Error in {self.__class__.__name__}.decrement_ref_count: {e}"
            )
            if lock.locked():
                print_debug(f"[{task_id}] Releasing lock after error")
                lock.release()
            raise

    def _cleanup_all_connections(self) -> None:
        """Clean up all async connections synchronously.

        This method:
        1. Gets all task IDs
        2. Removes each connection
        3. Handles cleanup errors
        4. Clears the connection pool
        """
        try:
            # Clean up all task connections
            for task_id in list(self._connections.keys()):
                try:
                    session, _ = self._connections[task_id]  # type: ignore
                    try:
                        if isinstance(session, AsyncSession):
                            # We can't await here, so just warn
                            print_warning(
                                f"Async session {task_id} may not be properly closed"
                            )
                        else:
                            session.close()
                    except Exception:
                        pass  # Ignore close errors
                    del self._connections[task_id]
                except Exception as e:
                    print_error(f"Error cleaning up connection {task_id}: {e}")

            # Clear the connection pool
            while self._connection_pool:
                try:
                    conn = self._connection_pool.pop()
                    try:
                        conn.close()
                    except Exception:
                        pass  # Ignore close errors
                except Exception as e:
                    print_error(f"Error cleaning up pooled connection: {e}")
        except Exception as e:
            print_error(f"Error during connection cleanup: {e}")


class ConnectionManager:
    """Coordinate management of all database connections.

    This class provides a unified interface for managing both thread-local
    and async connections, ensuring proper cleanup and resource management.

    Attributes:
        thread_connections: Manager for thread-local connections
        async_connections: Manager for async connections
        _cleanup_lock: Lock for coordinating cleanup operations
    """

    def __init__(
        self,
        optimized_storage: Any = None,
    ) -> None:
        """Initialize connection managers and cleanup lock.

        Args:
            optimized_storage: OptimizedSQLiteMemory instance to share between connections
        """
        print_debug("Initializing ConnectionManager")
        self.optimized_storage = optimized_storage
        self.thread_connections = ThreadLocalConnections(
            optimized_storage=optimized_storage,
        )

        # Initialize async connections upfront if we have optimized_storage
        if optimized_storage is not None:
            try:
                loop = asyncio.get_event_loop()
                print_debug(f"Using event loop: {loop}")
                self._async_connections = AsyncConnections(
                    optimized_storage=optimized_storage,
                )
                self._cleanup_lock = asyncio.Lock()
            except Exception as e:
                print_error(f"Error initializing async connections: {e}")
                self._async_connections = None
                self._cleanup_lock = None
        else:
            self._async_connections = None
            self._cleanup_lock = None

    @property
    def async_connections(self) -> AsyncConnections:
        """Get async connections manager, initializing if needed."""
        if self._async_connections is None:
            print_debug("Lazy initializing AsyncConnections")
            try:
                loop = asyncio.get_running_loop()
                print_debug(f"Using event loop: {loop}")
                self._async_connections = AsyncConnections(
                    optimized_storage=self.optimized_storage
                )
                self._cleanup_lock = asyncio.Lock()
            except RuntimeError as e:
                print_error(f"No running event loop: {e}")
                raise
        return self._async_connections

    async def cleanup(self) -> None:
        """Clean up all database connections.

        This method ensures proper cleanup of both thread-local and async
        connections, with proper error handling and timeout management.
        """
        print_debug("Starting connection cleanup")
        try:
            # Clean up thread connections first
            print_debug("Cleaning up thread connections")
            await self.thread_connections.cleanup()

            # Clean up async connections if they were initialized
            if self._async_connections is not None:
                print_debug("Cleaning up async connections")
                if self._cleanup_lock is not None:
                    async with self._cleanup_lock:
                        async with asyncio.timeout(10):  # 10 second timeout
                            await self._async_connections.cleanup()
                else:
                    async with asyncio.timeout(10):  # 10 second timeout
                        await self._async_connections.cleanup()
            else:
                print_debug("No async connections to clean up")
        except TimeoutError:
            print_error("Timeout during connection cleanup")
        except Exception as e:
            print_error(f"Error during connection cleanup: {e}")
            raise

    def cleanup_sync(self) -> None:
        """Clean up all database connections synchronously.

        This method ensures proper cleanup of both thread-local and async
        connections, with proper error handling.
        """
        try:
            # Clean up thread-local connections
            print_debug("Cleaning up thread connections synchronously")
            self.thread_connections._cleanup_all_connections()

            # Clean up async connections if they were initialized
            if self._async_connections is not None:
                print_debug("Cleaning up async connections synchronously")
                self._async_connections._cleanup_all_connections()
            else:
                print_debug("No async connections to clean up")
        except Exception as e:
            print_error(f"Error during sync connection cleanup: {e}")

    def get_thread_connection(self, thread_id: str) -> sqlite3.Connection | None:
        """Get thread-local connection.

        Args:
            thread_id: Thread ID to get connection for

        Returns:
            Connection object or None if not found
        """
        return self.thread_connections.get_connection(thread_id)

    def set_thread_connection(self, thread_id: str, conn: sqlite3.Connection) -> None:
        """Set thread-local connection.

        Args:
            thread_id: Thread ID to set connection for
            conn: Connection object to store
        """
        self.thread_connections.set_connection(thread_id, conn)

    async def get_async_session(self, task_id: int) -> tuple[AsyncSession, int] | None:
        """Get async session and reference count.

        Args:
            task_id: Task ID to get session for

        Returns:
            Tuple of (session, ref_count) or None if not found
        """
        return await self.async_connections.get_session(task_id)
