"""Tests for database integration with main application."""

import asyncio
import os
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from config import FanslyConfig
from config.modes import DownloadMode
from fansly_downloader_ng import cleanup_database, cleanup_database_sync
from metadata.database import Database


@pytest.fixture
def config(tmp_path: Path) -> FanslyConfig:
    """Create test configuration."""
    config = FanslyConfig(program_version="0.10.0")
    config.metadata_db_file = tmp_path / "test.db"
    config.download_mode = DownloadMode.NORMAL
    config.user_names = ["test_user"]
    return config


@pytest.fixture
def database(tmp_path: Path) -> Database:
    """Create test database."""
    return Database(
        FanslyConfig(program_version="0.10.0", metadata_db_file=tmp_path / "test.db")
    )


class TestDatabaseCleanup:
    """Test database cleanup functionality."""

    @pytest.mark.asyncio
    async def test_async_cleanup(self, config: FanslyConfig, database: Database):
        """Test async database cleanup."""
        # Set up database
        config._database = database

        # Create some test data
        async with database.async_session_scope() as session:
            await session.execute(text("CREATE TABLE test (id INTEGER PRIMARY KEY)"))
            await session.execute(text("INSERT INTO test VALUES (1)"))

        # Clean up
        await cleanup_database(config)

        # Verify cleanup
        assert not hasattr(database.optimized_storage, "_thread_connections")
        assert not os.path.exists(database.optimized_storage.local_path)

    def test_sync_cleanup(self, config: FanslyConfig, database: Database):
        """Test sync database cleanup."""
        # Set up database
        config._database = database

        # Create some test data
        with database.session_scope() as session:
            session.execute(text("CREATE TABLE test (id INTEGER PRIMARY KEY)"))
            session.execute(text("INSERT INTO test VALUES (1)"))

        # Clean up
        cleanup_database_sync(config)

        # Verify cleanup
        assert not os.path.exists(database.optimized_storage.local_path)


class TestPerCreatorDatabase:
    """Test per-creator database functionality."""

    @pytest.mark.asyncio
    async def test_creator_database_lifecycle(
        self, config: FanslyConfig, tmp_path: Path
    ):
        """Test creation and cleanup of per-creator database."""
        config.separate_metadata = True
        config.base_directory = str(tmp_path)

        # Mock API response
        api_mock = MagicMock()
        api_mock.get_creator_account_info.return_value.json.return_value = {
            "response": [{"id": 1, "username": "test_user"}]
        }

        # Create creator database
        creator_db_path = tmp_path / "metadata" / "test_user.db"
        config.metadata_db_file = creator_db_path
        creator_database = Database(config)

        # Create some test data
        async with creator_database.async_session_scope() as session:
            await session.execute(text("CREATE TABLE test (id INTEGER PRIMARY KEY)"))
            await session.execute(text("INSERT INTO test VALUES (1)"))

        # Clean up
        await creator_database.cleanup()

        # Verify cleanup
        assert not os.path.exists(creator_db_path)


class TestDatabaseMigrations:
    """Test database migration handling."""

    def test_automatic_migrations(self, tmp_path: Path):
        """Test migrations are run automatically."""
        # Create database with migrations
        db_path = tmp_path / "test.db"
        database = Database(
            FanslyConfig(program_version="test", metadata_db_file=db_path)
        )

        # Verify alembic_version table exists
        with database.session_scope() as session:
            result = session.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='alembic_version'"
                )
            ).scalar()
            assert result == "alembic_version"

    @pytest.mark.asyncio
    async def test_migration_error_handling(self, tmp_path: Path):
        """Test handling of migration errors."""
        # Create invalid migration version
        db_path = tmp_path / "test.db"
        with open(db_path, "w") as f:
            f.write("invalid database")

        # Should handle invalid database gracefully
        with pytest.raises(Exception):
            Database(FanslyConfig(program_version="test", metadata_db_file=db_path))


class TestDatabaseThreading:
    """Test database thread safety."""

    @pytest.mark.asyncio
    async def test_concurrent_access(self, database: Database):
        """Test concurrent database access."""

        async def worker(i: int) -> None:
            async with database.async_session_scope() as session:
                await session.execute(
                    text("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY)")
                )
                await session.execute(text("INSERT INTO test VALUES (?)", (i,)))

        # Run concurrent workers
        workers = [worker(i) for i in range(5)]
        await asyncio.gather(*workers)

        # Verify data
        async with database.async_session_scope() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM test"))
            count = await result.scalar()
            assert count == 5

    def test_thread_local_connections(self, database: Database):
        """Test thread-local connection management."""
        results = []

        def worker():
            with database.session_scope() as session:
                session.execute(
                    text("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY)")
                )
                session.execute(text("INSERT INTO test VALUES (1)"))
                results.append(True)

        # TODO: Update to use new threading API
        # noqa: F821 - threading will be imported in the new version
        threads = [threading.Thread(target=worker) for _ in range(3)]  # noqa: F821
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 3
