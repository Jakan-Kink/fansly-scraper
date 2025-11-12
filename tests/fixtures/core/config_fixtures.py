"""Pytest fixtures for FanslyConfig testing.

This module provides pytest fixtures for creating and managing configuration
objects in tests. These fixtures create REAL FanslyConfig instances instead
of using MagicMock.

Usage:
    def test_something(test_config):
        assert test_config.program_version == "0.11.0"

Note:
    - test_config: Basic config without database setup
    - config: Full config with real PostgreSQL database (from database fixtures)
    - temp_config_dir: Temporary directory for config file testing
"""

import gc
import os
import shutil
import tempfile
from configparser import ConfigParser
from contextlib import suppress
from pathlib import Path
from time import sleep

import pytest
from loguru import logger

from tests.fixtures.core.config_factories import FanslyConfigFactory


@pytest.fixture
def test_config():
    """Create a basic FanslyConfig instance without database setup.

    Use this for tests that don't need real database access.
    For tests that need a real database, use the 'config' fixture from database_fixtures.

    Note: Sets pg_database to a non-existent name to prevent accidental connections
    to production databases.

    Returns:
        FanslyConfig: Basic configuration for testing
    """
    return FanslyConfigFactory()


@pytest.fixture
def temp_config_dir():
    """Create a temporary directory and change to it for config file testing.

    This fixture:
    - Creates a temporary directory
    - Changes working directory to it
    - Yields the temp directory path
    - Restores original directory on cleanup
    - Properly closes file handles and removes temp files

    Yields:
        Path: Path to temporary directory
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        original_cwd = Path.cwd()
        os.chdir(temp_dir)
        try:
            yield Path(temp_dir)
        finally:
            os.chdir(original_cwd)

            # Clean up logging handlers (loguru keeps file handles open)
            with suppress(Exception):
                logger.remove()  # Remove all handlers to close log files

            # Force garbage collection to close any open file handles
            gc.collect()
            sleep(0.1)  # Brief delay to ensure handles are released

            # Try to remove any remaining files manually
            with suppress(Exception):
                for item in Path(temp_dir).iterdir():
                    if item.is_file():
                        item.unlink(missing_ok=True)
                    elif item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)


@pytest.fixture
def config_parser():
    """Create a ConfigParser instance for raw config manipulation.

    Returns:
        ConfigParser: Parser instance with no interpolation
    """
    return ConfigParser(interpolation=None)


@pytest.fixture
def mock_config_file(temp_config_dir, request):
    """Create a mock config file with specified content.

    Args:
        temp_config_dir: Temporary directory fixture
        request: Pytest request object (can provide custom content via param)

    Returns:
        Path: Path to created config file

    Usage:
        def test_config(mock_config_file):
            # Uses default config content
            assert mock_config_file.exists()

        @pytest.mark.parametrize(
            "mock_config_file",
            ["[Options]\\ndownload_mode = Timeline"],
            indirect=True
        )
        def test_custom_config(mock_config_file):
            # Uses custom config content
            pass
    """
    config_path = temp_config_dir / "config.ini"

    config_content = getattr(request, "param", None)
    if config_content is None:
        config_content = """
        [Options]
        download_mode = Normal
        metadata_handling = Advanced
        interactive = True
        download_directory = Local_directory
        """

    with config_path.open("w") as f:
        f.write(config_content)

    return config_path


@pytest.fixture
def valid_api_config(mock_config_file):
    """Create a config file with valid API credentials.

    Args:
        mock_config_file: Base config file fixture

    Returns:
        Path: Path to config file with API credentials

    Note:
        The credentials are dummy values for testing only.
    """
    with mock_config_file.open("w") as f:
        f.write(
            """
        [MyAccount]
        Authorization_Token = test_token_long_enough_to_be_valid_token_here_more_chars
        User_Agent = test_user_agent_long_enough_to_be_valid_agent_here_more
        Check_Key = test_check_key

        [Options]
        interactive = True
        download_mode = Normal
        metadata_handling = Advanced
        download_directory = Local_directory
        """
        )
    return mock_config_file


__all__ = [
    "config_parser",
    "mock_config_file",
    "temp_config_dir",
    "test_config",
    "valid_api_config",
]
