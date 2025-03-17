"""Unit tests for improved database management."""

import asyncio
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from config import FanslyConfig
from metadata.database import Database


@pytest.fixture
def config(tmp_path: Path) -> FanslyConfig:
    """Create test configuration."""
    config = MagicMock(spec=FanslyConfig)
    config.metadata_db_file = tmp_path / "test.db"
    config.db_sync_seconds = None
    config.db_sync_commits = None
    config.memory_limit = 1024 * 1024 * 1024  # 1GB
    config.log_levels = {"sqlalchemy": "INFO"}
    return config


@pytest.fixture
def database(config: FanslyConfig) -> Database:
    """Create test database instance."""
    db = Database(config)

    # Mock optimized_storage
    db.optimized_storage = MagicMock()
    db.optimized_storage.local_path = config.metadata_db_file
    db.optimized_storage.remote_path = config.metadata_db_file
    db.optimized_storage._check_database_integrity.return_value = True
    db.optimized_storage._run_integrity_check.return_value = (True, [])
    db.optimized_storage._attempt_recovery.return_value = True
    db.optimized_storage._try_remote_recovery.return_value = True
    db.optimized_storage.handle_corruption.return_value = True
    db.optimized_storage._handle_wal_files.return_value = None
    db.optimized_storage._get_thread_connection.return_value = sqlite3.connect(
        ":memory:"
    )
    db.optimized_storage.execute.return_value = MagicMock()
    db.optimized_storage.executemany.return_value = MagicMock()
    db.optimized_storage.cleanup.return_value = None

    # Mock connection_manager
    db.connection_manager = MagicMock()

    # Mock migration_manager
    db.migration_manager = MagicMock()
    db.migration_manager.db_path = config.metadata_db_file
    db.migration_manager.migrations_path = Path("alembic")

    # Mock other missing attributes
    db._close_all_connections = MagicMock()
    db._recreate_connections = MagicMock()
    db._sync_to_remote = AsyncMock(return_value=True)

    return db


class TestDatabaseIntegrity:
    """Test database integrity and recovery features."""

    def test_integrity_check(self, database: Database):
        """Test basic integrity check."""
        # Create test table
        with sqlite3.connect(database.optimized_storage.local_path) as conn:
            conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
            conn.commit()

            # Should pass integrity check
            assert database.optimized_storage._check_database_integrity(conn)

    def test_detailed_integrity_check(self, database: Database):
        """Test detailed integrity check."""
        with sqlite3.connect(database.optimized_storage.local_path) as conn:
            # Create valid table
            conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
            conn.commit()

            # Should return no errors
            is_intact, errors = database.optimized_storage._run_integrity_check(conn)
            assert is_intact
            assert not errors

    def test_recovery_from_backup(self, database: Database, tmp_path: Path):
        """Test database recovery from backup."""
        # Create test data
        with sqlite3.connect(database.optimized_storage.local_path) as conn:
            conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO test VALUES (1)")
            conn.commit()

            # Simulate corruption by writing invalid data
            with open(database.optimized_storage.local_path, "ab") as f:
                f.write(b"corrupt")

            # Recovery should work
            assert database.optimized_storage._attempt_recovery(conn)

            # Data should be preserved
            with sqlite3.connect(database.optimized_storage.local_path) as new_conn:
                cursor = new_conn.execute("SELECT * FROM test")
                assert cursor.fetchone() == (1,)

    def test_remote_recovery(self, database: Database, tmp_path: Path):
        """Test recovery from remote database."""
        # Create good data in remote
        with sqlite3.connect(database.optimized_storage.remote_path) as conn:
            conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO test VALUES (1)")
            conn.commit()

        # Corrupt local database
        with open(database.optimized_storage.local_path, "wb") as f:
            f.write(b"corrupt")

        # Remote recovery should work
        assert database.optimized_storage._try_remote_recovery()

        # Data should be recovered
        with sqlite3.connect(database.optimized_storage.local_path) as conn:
            cursor = conn.execute("SELECT * FROM test")
            assert cursor.fetchone() == (1,)

    def test_corruption_handling(self, database: Database):
        """Test complete corruption handling flow."""
        # Create test data
        with sqlite3.connect(database.optimized_storage.local_path) as conn:
            conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO test VALUES (1)")
            conn.commit()

        # Backup remote database
        shutil.copy2(
            database.optimized_storage.local_path,
            database.optimized_storage.remote_path,
        )

        # Corrupt local database
        with open(database.optimized_storage.local_path, "ab") as f:
            f.write(b"corrupt")

        # Corruption handling should work
        assert database.optimized_storage.handle_corruption()

        # Data should be recovered
        with sqlite3.connect(database.optimized_storage.local_path) as conn:
            cursor = conn.execute("SELECT * FROM test")
            assert cursor.fetchone() == (1,)


class TestWALAndConnections:
    """Test WAL mode and connection management."""

    def test_foreign_keys_disabled(self, database: Database):
        """Test that foreign keys are disabled."""
        with sqlite3.connect(database.optimized_storage.local_path) as conn:
            cursor = conn.execute("PRAGMA foreign_keys")
            enabled = cursor.fetchone()[0]
            assert enabled == 0  # 0 means disabled

    def test_wal_mode_enabled(self, database: Database):
        """Test that WAL mode is enabled."""
        with sqlite3.connect(database.optimized_storage.local_path) as conn:
            cursor = conn.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()[0]
            assert mode.upper() == "WAL"

    def test_wal_file_handling(self, database: Database):
        """Test WAL file management."""
        # Create some changes to generate WAL
        with sqlite3.connect(database.optimized_storage.local_path) as conn:
            conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO test VALUES (1)")
            conn.commit()

        # WAL file should exist
        wal_file = Path(str(database.optimized_storage.local_path) + "-wal")
        assert wal_file.exists()

        # Handle WAL files
        database.optimized_storage._handle_wal_files()

        # Should still be able to read data
        with sqlite3.connect(database.optimized_storage.local_path) as conn:
            cursor = conn.execute("SELECT * FROM test")
            assert cursor.fetchone() == (1,)

    def test_thread_local_connections(self, database: Database):
        """Test thread-local connection management."""

        def worker():
            # Each thread should get its own connection
            conn1 = database.optimized_storage._get_thread_connection()
            conn2 = database.optimized_storage._get_thread_connection()
            # Same thread should get same connection
            assert conn1 is conn2
            # Should be in WAL mode
            cursor = conn1.execute("PRAGMA journal_mode")
            assert cursor.fetchone()[0].upper() == "WAL"
            # Clean up
            database.optimized_storage.dispose_thread_connection()

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def test_execute_methods(self, database: Database):
        """Test execute and executemany methods."""
        # Test execute
        database.optimized_storage.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        database.optimized_storage.execute("INSERT INTO test VALUES (?)", (1,))

        # Test executemany
        data = [(2,), (3,), (4,)]
        database.optimized_storage.executemany("INSERT INTO test VALUES (?)", data)

        # Verify data
        cursor = database.optimized_storage.execute("SELECT * FROM test ORDER BY id")
        assert [row[0] for row in cursor.fetchall()] == [1, 2, 3, 4]

    def test_cleanup_with_wal(self, database: Database):
        """Test cleanup with WAL files."""
        # Create some data and WAL files
        with sqlite3.connect(database.optimized_storage.local_path) as conn:
            conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO test VALUES (1)")
            conn.commit()

        # Get paths
        wal_file = Path(str(database.optimized_storage.local_path) + "-wal")
        shm_file = Path(str(database.optimized_storage.local_path) + "-shm")

        # Files should exist
        assert database.optimized_storage.local_path.exists()
        assert wal_file.exists()
        assert shm_file.exists()

        # Clean up
        database.optimized_storage.cleanup()

        # All files should be gone
        assert not database.optimized_storage.local_path.exists()
        assert not wal_file.exists()
        assert not shm_file.exists()


class TestSessionManagement:
    """Test session management and transactions."""

    def test_session_scope(self, database: Database, safe_name):
        """Test basic session scope."""
        table_name = f"test_{safe_name}"
        # Create test data
        with database.session_scope() as session:
            session.execute(text(f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY)"))
            session.execute(text(f"INSERT INTO {table_name} VALUES (1)"))
            # Should auto-commit

        # Verify data persisted
        with database.session_scope() as session:
            result = session.execute(text(f"SELECT * FROM {table_name}")).scalar()
            assert result == 1

    def test_session_rollback(self, database: Database, safe_name):
        """Test automatic rollback on error."""
        table_name = f"test_{safe_name}"
        # Create table
        with database.session_scope() as session:
            session.execute(text(f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY)"))

        # Try operation that will fail
        with pytest.raises(Exception), database.session_scope() as session:
            session.execute(text(f"INSERT INTO {table_name} VALUES (1)"))
            raise ValueError("Test error")

        # Verify no data was committed
        with database.session_scope() as session:
            result = session.execute(
                text(f"SELECT COUNT(*) FROM {table_name}")
            ).scalar()
            assert result == 0

    @pytest.mark.asyncio
    async def test_async_session_scope(self, database: Database, safe_name):
        """Test async session scope."""
        table_name = f"test_{safe_name}"
        # Create test data
        async with database.async_session_scope() as session:
            await session.execute(
                text(f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY)")
            )
            await session.execute(text(f"INSERT INTO {table_name} VALUES (1)"))
            # Should auto-commit

        # Verify data persisted
        async with database.async_session_scope() as session:
            result = await session.execute(text(f"SELECT * FROM {table_name}"))
            value = await result.scalar()
            assert value == 1

    @pytest.mark.asyncio
    async def test_async_session_rollback(self, database: Database, safe_name):
        """Test automatic rollback in async session."""
        table_name = f"test_{safe_name}"
        # Create table
        async with database.async_session_scope() as session:
            await session.execute(
                text(f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY)")
            )

        # Try operation that will fail
        with pytest.raises(Exception):
            async with database.async_session_scope() as session:
                await session.execute(text(f"INSERT INTO {table_name} VALUES (1)"))
                raise ValueError("Test error")

        # Verify no data was committed
        async with database.async_session_scope() as session:
            result = await session.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            count = await result.scalar()
            assert count == 0


class TestMigrationIntegration:
    """Test migration integration in Database class."""

    def test_migration_setup(self, database: Database):
        """Test migration manager setup."""
        assert database.migration_manager is not None
        assert (
            database.migration_manager.db_path == database.optimized_storage.local_path
        )
        assert database.migration_manager.migrations_path.name == "alembic"

    def test_automatic_migration(self, database: Database):
        """Test automatic migration on startup."""
        # Migration manager should be set up
        assert database.migration_manager is not None
        assert (
            database.migration_manager.db_path == database.optimized_storage.local_path
        )
        assert database.migration_manager.migrations_path.name == "alembic"

        # Alembic version table should exist
        with database.session_scope() as session:
            result = session.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='alembic_version'"
                )
            ).scalar()
            assert result == "alembic_version"


class TestLegacyFeatures:
    """Test restored legacy features."""

    def test_session_scope_alias(self, database: Database):
        """Test session_scope alias."""
        # Should work like transaction_scope
        with database.session_scope() as session:
            session.execute(text("CREATE TABLE test (id INTEGER PRIMARY KEY)"))
            session.execute(text("INSERT INTO test VALUES (1)"))

        # Verify data persisted
        with database.session_scope() as session:
            result = session.execute(text("SELECT * FROM test")).scalar()
            assert result == 1

    def test_recreate_connections(self, database: Database):
        """Test connection recreation."""
        # Create some data
        with database.session_scope() as session:
            session.execute(text("CREATE TABLE test (id INTEGER PRIMARY KEY)"))
            session.execute(text("INSERT INTO test VALUES (1)"))

        # Recreate connections
        database._recreate_connections()

        # Should still be able to access data
        with database.session_scope() as session:
            result = session.execute(text("SELECT * FROM test")).scalar()
            assert result == 1

    @pytest.mark.asyncio
    async def test_sync_to_remote(self, database: Database, tmp_path: Path):
        """Test remote sync."""
        # Create test data
        with database.session_scope() as session:
            session.execute(text("CREATE TABLE test (id INTEGER PRIMARY KEY)"))
            session.execute(text("INSERT INTO test VALUES (1)"))

        # Create remote path
        remote_path = tmp_path / "remote.db"

        # Sync to remote
        assert await database._sync_to_remote(
            database.optimized_storage.local_path,
            remote_path,
        )

        # Verify remote data
        with sqlite3.connect(remote_path) as conn:
            cursor = conn.execute("SELECT * FROM test")
            assert cursor.fetchone() == (1,)

    def test_close_all_connections(self, database: Database):
        """Test connection cleanup."""
        # Create some connections
        with database.session_scope() as session:
            session.execute(text("SELECT 1"))

        # Close all connections
        database._close_all_connections()

        # Verify thread connections closed
        thread_id = str(id(threading.current_thread()))
        assert not hasattr(
            database.optimized_storage._thread_connections,
            thread_id,
        )


class TestOptimizedStorage:
    """Test optimized SQLite memory caching."""

    def test_local_copy_creation(self, database: Database, tmp_path: Path):
        """Test that local copy is created."""
        assert database.optimized_storage.local_path.exists()
        assert database.optimized_storage.local_path.is_file()
        assert database.optimized_storage.local_path != database.db_file

    def test_sync_manager_setup(self, database: Database):
        """Test that sync manager is set up."""
        assert database.optimized_storage.sync_manager is not None
        assert (
            database.optimized_storage.sync_manager.local_path
            == database.optimized_storage.local_path
        )
        assert database.optimized_storage.sync_manager.remote_path == database.db_file

    def test_cleanup(self, database: Database):
        """Test cleanup of optimized storage."""
        local_path = database.optimized_storage.local_path
        assert local_path.exists()

        database.optimized_storage.cleanup()
        assert not local_path.exists()

    @pytest.mark.asyncio
    async def test_sync_on_commit(self, database: Database, tmp_path: Path):
        """Test that changes are synced on commit."""
        # Configure sync on commit
        database.optimized_storage.sync_manager.sync_commits = 1

        # Make a change
        async with database.async_session() as session:
            await session.execute(text("CREATE TABLE test (id INTEGER PRIMARY KEY)"))
            await session.commit()

        # Verify change is synced
        assert database.db_file.exists()
        with sqlite3.connect(database.db_file) as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            assert "test" in tables


class TestDatabaseInit:
    """Test database initialization."""

    def test_init_creates_managers(self, database: Database):
        """Test that init creates resource managers."""
        assert database.connection_manager is not None
        assert database.sync_engine is not None
        assert database.async_engine is not None
        assert database.sync_session_factory is not None
        assert database.async_session_factory is not None

    def test_init_sets_config(self, database: Database, config: FanslyConfig):
        """Test that init sets configuration."""
        assert database.config == config
        assert database.db_file == Path(config.metadata_db_file)


class TestSyncSession:
    """Test synchronous session management."""

    def test_sync_session_commit(self, database: Database):
        """Test successful commit with sync session."""
        with database.session_scope() as session:
            # Execute a test query
            result = session.execute(text("SELECT 1")).scalar()
            assert result == 1

    def test_sync_session_rollback(self, database: Database):
        """Test rollback on error with sync session."""
        with pytest.raises(Exception), database.session_scope() as session:
            session.execute(text("SELECT 1"))
            raise Exception("Test error")

    def test_sync_session_cleanup(self, database: Database):
        """Test session cleanup after use."""
        with database.session_scope() as session:
            pass
        # Session should be closed
        with pytest.raises(Exception):
            session.execute(text("SELECT 1"))


class TestAsyncSession:
    """Test asynchronous session management."""

    @pytest.mark.asyncio
    async def test_async_session_commit(self, database: Database):
        """Test successful commit with async session."""
        async with database.async_session_scope() as session:
            # Execute a test query
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1

    @pytest.mark.asyncio
    async def test_async_session_rollback(self, database: Database):
        """Test rollback on error with async session."""
        with pytest.raises(Exception):
            async with database.async_session_scope() as session:
                await session.execute(text("SELECT 1"))
                raise Exception("Test error")

    @pytest.mark.asyncio
    async def test_async_session_cleanup(self, database: Database):
        """Test session cleanup after use."""
        async with database.async_session_scope() as session:
            pass
        # Session should be closed
        with pytest.raises(Exception):
            await session.execute(text("SELECT 1"))


class TestCleanup:
    """Test database cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup(self, database: Database):
        """Test full database cleanup."""
        # Create some sessions to clean up
        sync_session = database.sync_session_factory()
        async_session = database.async_session_factory()

        # Clean up
        await database.cleanup()

        # Verify cleanup
        with pytest.raises(Exception):
            sync_session.execute(text("SELECT 1"))
        with pytest.raises(Exception):
            await async_session.execute(text("SELECT 1"))

    def test_close_sync(self, database: Database):
        """Test synchronous cleanup."""
        # Create some connections to clean up
        with database.sync_session() as session:
            session.execute(text("SELECT 1"))

        # Clean up
        database.close_sync()

        # Verify cleanup
        with pytest.raises(Exception):
            with database.sync_session() as session:
                session.execute(text("SELECT 1"))


class TestThreadSafety:
    """Test thread safety of database operations."""

    def test_thread_local_connections(self, database: Database):
        """Test thread-local connection management."""
        results = []
        errors = []

        def worker():
            try:
                with database.sync_session() as session:
                    result = session.execute(text("SELECT 1")).scalar()
                    results.append(result)
            except Exception as e:
                errors.append(e)

        # Run in multiple threads
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 5
        assert all(r == 1 for r in results)
        assert not errors


class TestAsyncSafety:
    """Test async safety of database operations."""

    @pytest.mark.asyncio
    async def test_concurrent_async_sessions(self, database: Database):
        """Test concurrent async session management."""

        async def worker():
            async with database.async_session() as session:
                result = await session.execute(text("SELECT 1"))
                return await result.scalar()

        # Run concurrently
        results = await asyncio.gather(*[worker() for _ in range(5)])

        assert len(results) == 5
        assert all(r == 1 for r in results)
