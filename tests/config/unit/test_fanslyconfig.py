"""Unit tests for FanslyConfig class

IMPORTANT: These are UNIT tests and should NOT use database fixtures.
They use mocking to avoid requiring a real PostgreSQL database.

This test file uses pytest-specific configuration to prevent loading of
database fixtures from conftest.py that would require PostgreSQL.
"""

import asyncio
from configparser import ConfigParser
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api import FanslyApi
from config.fanslyconfig import FanslyConfig
from config.metadatahandling import MetadataHandling
from config.modes import DownloadMode


# ============================================================================
# Fixtures
# ============================================================================
#
# NOTE: These fixtures override fixtures from tests/fixtures/database_fixtures.py
# to prevent PostgreSQL connection attempts in these unit tests.


@pytest.fixture
def config_path(tmp_path):
    """Create a temporary config file path."""
    return tmp_path / "config.ini"


@pytest.fixture
def mock_parser():
    """Create a mock ConfigParser with required sections."""
    parser = ConfigParser(interpolation=None)

    # Add default sections
    for section in ["TargetedCreator", "MyAccount", "Options", "Cache", "Logic"]:
        parser.add_section(section)

    return parser


@pytest.fixture
def config(config_path, mock_parser):
    """Create a FanslyConfig instance for unit testing (no database).

    This fixture overrides the 'config' fixture from database_fixtures.py
    to prevent PostgreSQL connection attempts in these unit tests.
    """
    config = FanslyConfig(program_version="1.0.0")
    config.config_path = config_path
    config._parser = mock_parser
    # Token must be >= 50 chars to pass token_is_valid() check
    config.token = "test_token_long_enough_to_pass_validation_checks_here"
    config.user_agent = "test_user_agent_long_enough_for_validation"
    config.check_key = "test_check_key"
    config.user_names = {"user1", "user2"}

    return config


class TestFanslyConfig:
    """Tests for the FanslyConfig class."""

    def test_init(self):
        """Test FanslyConfig initialization with required parameters."""
        config = FanslyConfig(program_version="1.0.0")

        # Check default values
        assert config.program_version == "1.0.0"
        assert config.use_following is False
        assert config.DUPLICATE_THRESHOLD == 50
        assert config.BATCH_SIZE == 150
        assert config.token is None
        assert config.user_agent is None
        assert config.debug is False
        assert config.trace is False
        assert config.download_mode == DownloadMode.NORMAL
        assert config.metadata_handling == MetadataHandling.ADVANCED
        assert isinstance(config._parser, ConfigParser)
        assert config._api is None

        # Check default metadata DB file (None until explicitly set)
        # metadata_db_file is deprecated in favor of PostgreSQL configuration
        assert config.metadata_db_file is None

    def test_user_names_str_with_names(self, config):
        """Test user_names_str with valid user names."""
        assert config.user_names_str() in ["user1, user2", "user2, user1"]

    def test_user_names_str_none(self):
        """Test user_names_str with None."""
        config = FanslyConfig(program_version="1.0.0")
        config.user_names = None
        assert config.user_names_str() == "ReplaceMe"

    def test_download_mode_str(self, config):
        """Test download_mode_str method."""
        config.download_mode = DownloadMode.NORMAL
        assert config.download_mode_str() == "Normal"

        config.download_mode = DownloadMode.TIMELINE
        assert config.download_mode_str() == "Timeline"

    def test_metadata_handling_str(self, config):
        """Test metadata_handling_str method."""
        config.metadata_handling = MetadataHandling.ADVANCED
        assert config.metadata_handling_str() == "Advanced"

        config.metadata_handling = MetadataHandling.SIMPLE
        assert config.metadata_handling_str() == "Simple"

    def test_sync_settings(self, config):
        """Test _sync_settings method updates parser values."""
        # Set some config values
        config.user_names = {"test1", "test2"}
        config.token = "test_token_updated"
        config.user_agent = "test_user_agent_updated"
        config.download_directory = Path("/test/path")

        # Call sync settings
        config._sync_settings()

        # Check that parser values were updated
        parser = config._parser
        assert parser.get("TargetedCreator", "username") in [
            "test1, test2",
            "test2, test1",
        ]
        assert parser.get("MyAccount", "authorization_token") == "test_token_updated"
        assert parser.get("MyAccount", "user_agent") == "test_user_agent_updated"
        assert parser.get("Options", "download_directory") == "/test/path"

    def test_sync_settings_none_values(self, config):
        """Test _sync_settings method handles None values."""
        # Set some config values to None
        config.token = None
        config.user_agent = None
        config.download_directory = None

        # Call sync settings
        config._sync_settings()

        # Check that parser values were updated appropriately
        parser = config._parser
        assert parser.get("MyAccount", "authorization_token") == ""
        assert parser.get("MyAccount", "user_agent") == ""
        assert parser.get("Options", "download_directory") == "Local_directory"

    def test_load_raw_config_with_path(self, config, config_path):
        """Test _load_raw_config with valid path."""
        # Create a test config file
        config_path.write_text("[TestSection]\ntest_key=test_value\n")

        with patch.object(
            config._parser, "read", return_value=["test_path"]
        ) as mock_read:
            result = config._load_raw_config()
            mock_read.assert_called_once_with(config_path)
            assert result == ["test_path"]

    def test_load_raw_config_no_path(self, config):
        """Test _load_raw_config with no path."""
        config.config_path = None
        result = config._load_raw_config()
        assert result == []

    def test_save_config_with_path(self, config):
        """Test _save_config with valid path."""
        # Create a proper mock parser with a mock write method
        mock_parser = MagicMock()
        mock_parser.write = MagicMock()

        # Save the original parser
        original_parser = config._parser

        # Replace with our mock
        config._parser = mock_parser

        with (
            patch("pathlib.Path.open") as mock_open,
            patch.object(config, "_sync_settings") as mock_sync,
        ):
            mock_file = MagicMock()
            mock_open.return_value.__enter__.return_value = mock_file

            result = config._save_config()

            mock_sync.assert_called_once()
            mock_open.assert_called_once()
            mock_parser.write.assert_called_once_with(mock_file)
            assert result is True

        # Restore the original parser
        config._parser = original_parser

    def test_save_config_no_path(self, config):
        """Test _save_config with no path."""
        config.config_path = None
        result = config._save_config()
        assert result is False

    def test_token_is_valid(self, config):
        """Test token_is_valid method."""
        # Valid token
        config.token = "a" * 60
        assert config.token_is_valid() is True

        # Invalid token - too short
        config.token = "a" * 40
        assert config.token_is_valid() is False

        # Invalid token - contains ReplaceMe
        config.token = "a" * 40 + "ReplaceMe" + "a" * 10
        assert config.token_is_valid() is False

        # Token is None
        config.token = None
        assert config.token_is_valid() is False

    def test_useragent_is_valid(self, config):
        """Test useragent_is_valid method."""
        # Valid user agent
        config.user_agent = "a" * 50
        assert config.useragent_is_valid() is True

        # Invalid user agent - too short
        config.user_agent = "a" * 30
        assert config.useragent_is_valid() is False

        # Invalid user agent - contains ReplaceMe
        config.user_agent = "a" * 40 + "ReplaceMe" + "a" * 10
        assert config.useragent_is_valid() is False

        # User agent is None
        config.user_agent = None
        assert config.useragent_is_valid() is False

    def test_get_unscrambled_token_regular(self, config):
        """Test get_unscrambled_token with regular token."""
        config.token = "regular_token"
        assert config.get_unscrambled_token() == "regular_token"

    def test_get_unscrambled_token_scrambled(self, config):
        """Test get_unscrambled_token with scrambled token."""
        # Create scrambled token: token ending with 'fNs'
        scrambled_token = "acegikmoqsuwybdf" + "fNs"
        config.token = scrambled_token

        # For this scrambled token, the actual output from the algorithm
        # need to match what the implementation produces
        expected = "agkoswbcimquyde"
        assert config.get_unscrambled_token() == expected

    def test_get_unscrambled_token_none(self, config):
        """Test get_unscrambled_token with None token."""
        config.token = None
        assert config.get_unscrambled_token() is None

    # NOTE: _get_default_metadata_db_file method doesn't exist in FanslyConfig
    # metadata_db_file is deprecated in favor of PostgreSQL configuration
    # Test removed as the method is not implemented

    def test_get_api(self, config):
        """Test get_api method with valid credentials."""
        # Make sure _api is None to force a new instance creation
        config._api = None
        with (
            patch("config.fanslyconfig.FanslyApi") as mock_api_class,
            patch("api.rate_limiter.RateLimiter") as mock_rate_limiter_class,
        ):
            mock_api = MagicMock(spec=FanslyApi)
            mock_api_class.return_value = mock_api
            mock_rate_limiter = MagicMock()
            mock_rate_limiter_class.return_value = mock_rate_limiter

            result = config.get_api()

            # Verify RateLimiter was instantiated with config
            mock_rate_limiter_class.assert_called_once_with(config)

            # Verify FanslyApi was instantiated with proper parameters including rate_limiter
            mock_api_class.assert_called_once_with(
                token=config.token,
                user_agent=config.user_agent,
                check_key=config.check_key,
                device_id=config.cached_device_id,
                device_id_timestamp=config.cached_device_id_timestamp,
                on_device_updated=config._save_config,
                rate_limiter=mock_rate_limiter,
            )
            assert result is mock_api
            assert config._api is mock_api

    def test_get_api_caching(self, config):
        """Test get_api caches the API instance."""
        with patch("config.fanslyconfig.FanslyApi") as mock_api_class:
            mock_api = MagicMock(spec=FanslyApi)
            mock_api_class.return_value = mock_api

            # First call should create a new API instance
            api1 = config.get_api()
            assert api1 is config._api  # Check it's stored in the config
            mock_api_class.assert_called_once()

            # Second call should return the cached instance
            mock_api_class.reset_mock()
            api2 = config.get_api()
            assert api2 is api1  # Check we get the same instance again
            mock_api_class.assert_not_called()  # API constructor shouldn't be called again

    @pytest.mark.asyncio
    async def test_setup_api(self, config):
        """Test setup_api method."""
        mock_api = MagicMock(spec=FanslyApi)
        mock_api.session_id = "null"
        mock_api.setup_session = AsyncMock(return_value=True)
        # Add missing attributes that are accessed in _sync_settings
        mock_api.device_id = "test-device-id"
        mock_api.device_id_timestamp = 12345678

        config._api = mock_api

        await config.setup_api()

        mock_api.setup_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_api_with_existing_session(self, config):
        """Test setup_api method with existing session."""
        mock_api = MagicMock(spec=FanslyApi)
        mock_api.session_id = "existing_session"
        mock_api.setup_session = AsyncMock(return_value=True)

        config._api = mock_api

        result = await config.setup_api()

        mock_api.setup_session.assert_not_called()
        assert result is mock_api

    @pytest.mark.asyncio
    async def test_setup_api_no_api(self, config):
        """Test setup_api method with no API instance."""
        # Mock get_api to return None
        with (
            patch.object(config, "get_api", return_value=None),
            pytest.raises(RuntimeError, match="Token or user agent error"),
        ):
            await config.setup_api()

    def test_get_stash_context_no_data(self, config):
        """Test get_stash_context with no connection data."""
        config._stash = None
        config.stash_context_conn = None

        with pytest.raises(RuntimeError, match="No StashContext connection data"):
            config.get_stash_context()

    def test_get_stash_context(self, config):
        """Test get_stash_context method."""
        config._stash = None
        config.stash_context_conn = {
            "scheme": "http",
            "host": "localhost",
            "port": "9999",  # Ensure this is a string
            "apikey": "test_key",
        }

        with patch("config.fanslyconfig.StashContext") as mock_stash_context_class:
            mock_stash_context = MagicMock()
            mock_stash_context_class.return_value = mock_stash_context

            # Mock the conn property to avoid issues with _sync_settings
            mock_stash_context.conn = config.stash_context_conn.copy()

            # Patch _save_config to avoid ConfigParser issues
            with patch.object(config, "_save_config", return_value=True):
                result = config.get_stash_context()

                mock_stash_context_class.assert_called_once_with(
                    conn=config.stash_context_conn
                )
                assert result is mock_stash_context
                assert config._stash is mock_stash_context

    def test_get_stash_api(self, config):
        """Test get_stash_api method."""
        mock_stash_context = MagicMock()
        mock_stash_client = MagicMock()
        mock_stash_context.client = mock_stash_client

        with patch.object(config, "get_stash_context", return_value=mock_stash_context):
            result = config.get_stash_api()

            assert result is mock_stash_client

    def test_get_stash_api_error(self, config):
        """Test get_stash_api method with error."""
        with (
            patch.object(
                config, "get_stash_context", side_effect=RuntimeError("Test error")
            ),
            pytest.raises(RuntimeError, match="Failed to initialize Stash API"),
        ):
            config.get_stash_api()

    def test_background_tasks(self, config):
        """Test background tasks methods."""
        # Test get_background_tasks
        assert config.get_background_tasks() == []

        # Add some mock tasks
        mock_task1 = MagicMock(spec=asyncio.Task)
        mock_task1.done.return_value = False
        mock_task2 = MagicMock(spec=asyncio.Task)
        mock_task2.done.return_value = True

        config._background_tasks = [mock_task1, mock_task2]

        # Test get_background_tasks returns the tasks
        assert config.get_background_tasks() == [mock_task1, mock_task2]

        # Test cancel_background_tasks
        config.cancel_background_tasks()

        mock_task1.cancel.assert_called_once()
        mock_task2.cancel.assert_not_called()  # Since it's already done
        assert config._background_tasks == []
