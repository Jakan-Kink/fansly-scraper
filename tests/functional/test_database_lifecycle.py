"""Integration tests for database lifecycle in main application."""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from config import FanslyConfig
from config.modes import DownloadMode
from download.core import DownloadState
from fansly_downloader_ng import main
from metadata.database import Database


@pytest.fixture
def config(tmp_path: Path) -> FanslyConfig:
    """Create test configuration."""
    config = FanslyConfig(program_version="0.10.0")
    config.metadata_db_file = str(tmp_path / "test.db")
    config.download_mode = DownloadMode.NORMAL
    config.user_names = ["test_user"]
    config.interactive = False
    config.separate_metadata = False
    return config


@pytest.fixture
def mock_api():
    """Create mock API."""
    api = MagicMock()
    api.get_creator_account_info.return_value.json.return_value = {
        "response": [{"id": 1, "username": "test_user"}]
    }
    api.get_client_user_name.return_value = "test_client"
    return api


class TestDatabaseLifecycle:
    """Test database lifecycle in main application."""

    @pytest.mark.asyncio
    async def test_global_database_lifecycle(
        self, config: FanslyConfig, mock_api: MagicMock
    ):
        """Test global database lifecycle."""
        # Mock API setup
        with patch("config.FanslyConfig.get_api", return_value=mock_api):
            # Run main application
            exit_code = await main(config)

            # Verify database was created
            assert os.path.exists(config.metadata_db_file)
            assert hasattr(config, "_database")

            # Verify database is functional
            with config._database.session_scope() as session:
                # Should have alembic_version table
                result = session.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name='alembic_version'"
                    )
                ).scalar()
                assert result == "alembic_version"

            assert exit_code == 0

    @pytest.mark.asyncio
    async def test_per_creator_database_lifecycle(
        self, config: FanslyConfig, mock_api: MagicMock, tmp_path: Path
    ):
        """Test per-creator database lifecycle."""
        config.separate_metadata = True
        config.base_directory = str(tmp_path)

        # Mock API setup
        with patch("config.FanslyConfig.get_api", return_value=mock_api):
            # Run main application
            exit_code = await main(config)

            # Verify creator database was created and cleaned up
            creator_db = tmp_path / "metadata" / "test_user.db"
            assert not os.path.exists(creator_db)

            assert exit_code == 0

    @pytest.mark.asyncio
    async def test_database_error_handling(
        self, config: FanslyConfig, mock_api: MagicMock
    ):
        """Test database error handling."""
        # Create invalid database file
        with open(config.metadata_db_file, "w") as f:
            f.write("invalid database")

        # Mock API setup
        with patch("config.FanslyConfig.get_api", return_value=mock_api):
            # Should handle database error gracefully
            with pytest.raises(Exception):
                await main(config)

    @pytest.mark.asyncio
    async def test_concurrent_database_access(
        self, config: FanslyConfig, mock_api: MagicMock
    ):
        """Test concurrent database access."""
        # Mock API setup
        with patch("config.FanslyConfig.get_api", return_value=mock_api):
            # Create database
            config._database = Database(config)

            async def worker(i: int):
                # TODO: Update to use new DownloadState API
                state = DownloadState(creator_name=f"test_user_{i}")  # noqa: F841
                async with config._database.async_session_scope() as session:
                    await session.execute(
                        text("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY)")
                    )
                    await session.execute(text("INSERT INTO test VALUES (?)", (i,)))

            # Run concurrent workers
            workers = [worker(i) for i in range(5)]
            await asyncio.gather(*workers)

            # Verify data
            async with config._database.async_session_scope() as session:
                result = await session.execute(text("SELECT COUNT(*) FROM test"))
                count = await result.scalar()
                assert count == 5

            # Clean up
            await config._database.cleanup()
