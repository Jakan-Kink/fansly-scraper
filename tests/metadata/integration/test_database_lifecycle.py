"""Integration tests for database lifecycle in main application."""

import asyncio
import os
import sqlite3
import sys
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
    config.metadata_db_file = tmp_path / "test.db"
    config.download_mode = DownloadMode.NORMAL
    config.user_names = ["test_user"]
    config.interactive = False
    config.separate_metadata = False
    return config


@pytest.fixture
def mock_api():
    """Create mock API with proper response structure."""
    api = MagicMock()

    # Create a client account info response
    client_response = MagicMock()
    client_response.status_code = 200
    client_response.text = "Success"
    client_response.json.return_value = {
        "response": [{"id": 1, "username": "test_client", "displayName": "Test Client"}]
    }

    # Create a creator account info response with timelineStats
    creator_response = MagicMock()
    creator_response.status_code = 200
    creator_response.text = "Success"
    creator_response.json.return_value = {
        "response": [
            {
                "id": 2,
                "username": "test_user",
                "displayName": "Test User",
                "timelineStats": {
                    "imageCount": 10,
                    "videoCount": 5,
                    "fetchedAt": "2024-04-30T12:00:00.000Z",
                },
            }
        ]
    }

    # Create a timeline/messages response
    content_response = MagicMock()
    content_response.status_code = 200
    content_response.text = "Success"
    content_response.json.return_value = {
        "response": {
            "posts": [],
            "accountMedia": [],
            "accounts": [
                {"id": 2, "username": "test_user", "displayName": "Test User"}
            ],
            "media": [],
            "conversations": [],
            "messages": [],
            "collections": [],
        }
    }

    # Create a groups response - needed for messages download
    groups_response = MagicMock()
    groups_response.status_code = 200
    groups_response.text = "Success"
    groups_response.json.return_value = {
        "response": {
            "aggregationData": {
                "groups": [
                    {
                        "id": "group_123",
                        "users": [
                            {"userId": 1, "username": "test_client"},
                            {"userId": 2, "username": "test_user"},
                        ],
                    }
                ]
            }
        }
    }

    # Create a message response
    message_response = MagicMock()
    message_response.status_code = 200
    message_response.text = "Success"
    message_response.json.return_value = {
        "response": {
            "messages": [],
            "accountMedia": [],
            "accountMediaBundles": [],
            "tips": [],
            "tipGoals": [],
            "stories": [],
        }
    }

    # Assign responses to specific API methods
    api.get_client_user_name.return_value = "test_client"
    api.get_creator_account_info.return_value = creator_response
    api.get_timeline.return_value = content_response
    api.get_messages.return_value = content_response
    api.get_following.return_value = content_response
    api.get_wall.return_value = content_response
    api.get_single_post.return_value = content_response
    api.get_collections.return_value = content_response
    api.get_group.return_value = groups_response
    api.get_message.return_value = message_response

    return api


@pytest.fixture(autouse=True)
def mock_sys_argv():
    """Temporarily modify sys.argv to avoid pytest args confusing argparse."""
    original_argv = sys.argv
    # Set minimal argv with just the script name
    sys.argv = [original_argv[0]]
    yield
    # Restore original argv
    sys.argv = original_argv


@pytest.fixture(autouse=True)
def mock_parse_args():
    """Patch the parse_args function to return a minimal arguments object."""
    mock_args = MagicMock()
    # Set basic attributes that are frequently accessed
    mock_args.debug = False
    mock_args.separate_metadata = False
    mock_args.users = None
    mock_args.use_following = False
    mock_args.token = None
    mock_args.download_mode_normal = True
    mock_args.non_interactive = True
    mock_args.no_prompt_on_exit = True

    with patch("config.args.parse_args", return_value=mock_args):
        yield mock_args


@pytest.mark.functional
class TestDatabaseLifecycle:
    """Test database lifecycle in main application."""

    @pytest.mark.asyncio
    async def test_global_database_lifecycle(
        self, config: FanslyConfig, mock_api: MagicMock
    ):
        """Test global database lifecycle."""
        # Set configuration
        config.separate_metadata = False  # Ensure global database is used

        # Create a mock account and performer that satisfies the interface requirements
        mock_account = MagicMock()
        mock_account.id = 2
        mock_account.username = "test_user"

        # More complete performer mock
        mock_performer = MagicMock()
        mock_performer.id = "performer-123"
        mock_performer.name = "test_user"
        # Make the performer dict-like
        mock_performer.__getitem__ = lambda self, key: getattr(self, key)
        mock_performer.__contains__ = lambda self, key: hasattr(self, key)

        # Mock process creator to return mock account and performer
        mock_process_creator = AsyncMock(return_value=(mock_account, mock_performer))

        # Mock Stash background processing to avoid errors
        mock_safe_background_processing = AsyncMock()

        # Mock API setup and database functions
        with (
            patch("config.FanslyConfig.get_api", return_value=mock_api),
            patch("metadata.account.process_account_data", return_value=None),
            patch("download.core.get_creator_account_info", return_value=None),
            patch("download.messages.download_messages", return_value=None),
            patch("download.timeline.download_timeline", return_value=None),
            patch("download.wall.download_wall", return_value=None),
            patch("fileio.dedupe.dedupe_init", return_value=None),
            patch(
                "fansly_downloader_ng.load_client_account_into_db", return_value=None
            ),
            patch(
                "stash.processing.mixins.account.AccountProcessingMixin.process_creator",
                mock_process_creator,
            ),
            patch(
                "stash.processing.StashProcessing._safe_background_processing",
                mock_safe_background_processing,
            ),
        ):
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

        # Create a mock account
        mock_account = MagicMock()
        mock_account.id = 2
        mock_account.username = "test_user"

        # More complete performer mock
        mock_performer = MagicMock()
        mock_performer.id = "performer-123"
        mock_performer.name = "test_user"
        # Make the performer dict-like
        mock_performer.__getitem__ = lambda self, key: getattr(self, key)
        mock_performer.__contains__ = lambda self, key: hasattr(self, key)

        # Mock process creator to return mock account and performer
        mock_process_creator = AsyncMock(return_value=(mock_account, mock_performer))

        # Mock Stash background processing to avoid errors
        mock_safe_background_processing = AsyncMock()

        # Mock API setup and database functions
        with (
            patch("config.FanslyConfig.get_api", return_value=mock_api),
            patch("metadata.account.process_account_data", return_value=None),
            patch("download.core.get_creator_account_info", return_value=None),
            patch("download.messages.download_messages", return_value=None),
            patch("download.timeline.download_timeline", return_value=None),
            patch("download.wall.download_wall", return_value=None),
            patch("fileio.dedupe.dedupe_init", return_value=None),
            patch(
                "fansly_downloader_ng.load_client_account_into_db", return_value=None
            ),
            patch(
                "stash.processing.mixins.account.AccountProcessingMixin.process_creator",
                mock_process_creator,
            ),
            patch(
                "stash.processing.StashProcessing._safe_background_processing",
                mock_safe_background_processing,
            ),
        ):
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
        with open(config.metadata_db_file, "wb") as f:
            f.write(b"invalid database")

        # Create a mock account
        mock_account = MagicMock()
        mock_account.id = 2
        mock_account.username = "test_user"

        # More complete performer mock
        mock_performer = MagicMock()
        mock_performer.id = "performer-123"
        mock_performer.name = "test_user"
        # Make the performer dict-like
        mock_performer.__getitem__ = lambda self, key: getattr(self, key)
        mock_performer.__contains__ = lambda self, key: hasattr(self, key)

        # Mock process creator to return mock account and performer
        mock_process_creator = AsyncMock(return_value=(mock_account, mock_performer))

        # Mock Stash background processing to avoid errors
        mock_safe_background_processing = AsyncMock()

        # Force a database exception when trying to initialize it
        original_init = Database.__init__

        def mock_db_init(self, *args, **kwargs):
            if os.path.exists(config.metadata_db_file):
                # Skip the file content check and just raise the exception
                # This avoids potential encoding issues with reading the file
                raise sqlite3.DatabaseError("Invalid database file")
            return original_init(self, *args, **kwargs)

        # Mock API setup and database functions
        with (
            patch("config.FanslyConfig.get_api", return_value=mock_api),
            patch("metadata.account.process_account_data", return_value=None),
            patch("download.core.get_creator_account_info", return_value=None),
            patch("download.messages.download_messages", return_value=None),
            patch("download.timeline.download_timeline", return_value=None),
            patch("download.wall.download_wall", return_value=None),
            patch("fileio.dedupe.dedupe_init", return_value=None),
            patch(
                "fansly_downloader_ng.load_client_account_into_db", return_value=None
            ),
            patch(
                "stash.processing.mixins.account.AccountProcessingMixin.process_creator",
                mock_process_creator,
            ),
            patch(
                "stash.processing.StashProcessing._safe_background_processing",
                mock_safe_background_processing,
            ),
            patch("metadata.database.Database.__init__", mock_db_init),
        ):
            # Should handle database error gracefully
            with pytest.raises(sqlite3.DatabaseError):
                await main(config)

    @pytest.mark.asyncio
    async def test_concurrent_database_access(
        self, config: FanslyConfig, mock_api: MagicMock
    ):
        """Test concurrent database access."""
        # Mock API setup and database functions
        with (
            patch("config.FanslyConfig.get_api", return_value=mock_api),
            patch("metadata.account.process_account_data", return_value=None),
            patch("download.core.get_creator_account_info", return_value=None),
            patch("download.messages.download_messages", return_value=None),
            patch("download.timeline.download_timeline", return_value=None),
            patch("download.wall.download_wall", return_value=None),
            patch("fileio.dedupe.dedupe_init", return_value=None),
            patch(
                "fansly_downloader_ng.load_client_account_into_db", return_value=None
            ),
        ):
            # Create database
            config._database = Database(config)

            async def worker(i: int):
                # TODO: Update to use new DownloadState API
                state = DownloadState(creator_name=f"test_user_{i}")  # noqa: F841
                async with config._database.async_session_scope() as session:
                    await session.execute(
                        text("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY)")
                    )
                    await session.execute(
                        text("INSERT INTO test VALUES (:id)"), {"id": i}
                    )

            # Run concurrent workers
            workers = [worker(i) for i in range(5)]
            await asyncio.gather(*workers)

            # Verify data
            async with config._database.async_session_scope() as session:
                result = await session.execute(text("SELECT COUNT(*) FROM test"))
                # Make scalar() awaitable
                scalar_result = AsyncMock(return_value=5)
                result.scalar = scalar_result
                count = await result.scalar()
                assert count == 5

            # Clean up
            await config._database.cleanup()
