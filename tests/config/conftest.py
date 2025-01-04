"""Common fixtures and configuration for config tests."""

import os
import tempfile
from collections.abc import Generator
from configparser import ConfigParser
from pathlib import Path

import pytest
from loguru import logger

from config.fanslyconfig import FanslyConfig
from config.metadatahandling import MetadataHandling
from config.modes import DownloadMode


@pytest.fixture(scope="function")
def config() -> FanslyConfig:
    """Create a fresh FanslyConfig instance for each test."""
    return FanslyConfig(program_version="0.10.0")


@pytest.fixture(scope="function")
def temp_config_dir() -> Generator[Path, None, None]:
    """Create a temporary directory and change to it for config file testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        original_cwd = os.getcwd()
        os.chdir(temp_dir)
        yield Path(temp_dir)
        os.chdir(original_cwd)


@pytest.fixture(scope="function")
def config_parser() -> ConfigParser:
    """Create a ConfigParser instance for raw config manipulation."""
    return ConfigParser(interpolation=None)


@pytest.fixture(scope="function")
def mock_config_file(temp_config_dir: Path, request) -> Generator[Path, None, None]:
    """Create a mock config file with specified content.

    Usage:
        @pytest.mark.parametrize("config_content", [
            '''
            [Options]
            download_mode = Normal
            metadata_handling = Advanced
            ''',
        ])
        def test_something(mock_config_file):
            ...
    """
    config_path = temp_config_dir / "config.ini"

    # Get config_content from test parameter or use default minimal config
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

    yield config_path


@pytest.fixture(scope="function")
def mock_download_dir(temp_config_dir: Path) -> Generator[Path, None, None]:
    """Create a mock download directory for testing."""
    download_dir = temp_config_dir / "downloads"
    download_dir.mkdir()
    yield download_dir


@pytest.fixture(scope="function")
def mock_metadata_dir(temp_config_dir: Path) -> Generator[Path, None, None]:
    """Create a mock metadata directory for testing."""
    metadata_dir = temp_config_dir / "metadata"
    metadata_dir.mkdir()
    yield metadata_dir


@pytest.fixture(scope="function")
def mock_temp_dir(temp_config_dir: Path) -> Generator[Path, None, None]:
    """Create a mock temporary directory for testing."""
    temp_dir = temp_config_dir / "temp"
    temp_dir.mkdir()
    yield temp_dir


@pytest.fixture(scope="function")
def valid_api_config(mock_config_file: Path) -> Path:
    """Create a config file with valid API credentials."""
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


@pytest.fixture(scope="session")
def download_modes() -> list[DownloadMode]:
    """Get all available download modes."""
    return list(DownloadMode)


@pytest.fixture(scope="session")
def metadata_handling_modes() -> list[MetadataHandling]:
    """Get all available metadata handling modes."""
    return list(MetadataHandling)


@pytest.fixture(autouse=True)
def setup_test_logging():
    """Configure logging for config tests."""
    with tempfile.TemporaryDirectory() as temp_dir:
        log_file = Path(temp_dir) / "config_test.log"

        # Remove existing handlers
        logger.remove()

        # Add test-specific handler
        logger.add(
            str(log_file),
            level="DEBUG",
            format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            rotation="1 MB",
        )

        yield

        # Cleanup
        logger.remove()


def pytest_collection_modifyitems(items):
    """Add markers to tests based on their location."""
    for item in items:
        if "/unit/" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
        elif "/integration/" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
