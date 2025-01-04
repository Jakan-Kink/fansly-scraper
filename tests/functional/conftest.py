"""Fixtures and configuration for functional tests."""

import os
import tempfile
from pathlib import Path

import pytest
from loguru import logger

from config import FanslyConfig


@pytest.fixture(scope="function")
def test_config():
    """Create a test configuration for functional tests."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config = FanslyConfig(program_version="0.10.0")
        config.download_directory = Path(temp_dir)
        config.max_concurrent_downloads = 2
        config.download_timeout = 30
        config.retry_attempts = 2
        config.retry_delay = 1
        yield config


@pytest.fixture(scope="function")
def test_downloads_dir(test_config):
    """Create a temporary directory for test downloads."""
    return test_config.download_directory


@pytest.fixture(scope="function")
def mock_api_response(request):
    """Load mock API response data for tests."""
    # Get the test module path
    test_module = request.module.__file__
    test_dir = os.path.dirname(test_module)

    # Construct path to mock data file
    mock_data_path = os.path.join(
        test_dir, "mock_data", request.function.__name__ + ".json"
    )

    if os.path.exists(mock_data_path):
        with open(mock_data_path) as f:
            return f.read()
    return None


@pytest.fixture(autouse=True)
def setup_test_logging():
    """Configure logging for functional tests."""
    with tempfile.TemporaryDirectory() as temp_dir:
        log_file = Path(temp_dir) / "functional_test.log"

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
