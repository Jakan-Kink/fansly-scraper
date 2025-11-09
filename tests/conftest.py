"""Master test configuration and fixtures.

This is the single source of truth for all test fixtures and configuration.
All tests can access fixtures defined here or in the tests/fixtures/ modules.

Organization:
- Auto-use fixtures (logging, cleanup) - Always run
- Database fixtures - From tests/fixtures/database_fixtures.py
- Factory fixtures - FactoryBoy factories for creating test data
- Stash fixtures - From tests/fixtures/stash_fixtures.py
- Test data fixtures - JSON data loaders, sample objects
- Config fixtures - Configuration objects for testing
"""

import asyncio
import gc
import json
import logging
import os
import shutil
import tempfile
from configparser import ConfigParser
from contextlib import contextmanager, suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, sleep, time
from unittest.mock import MagicMock

import psutil
import pytest
import pytest_asyncio
from loguru import logger

from config import FanslyConfig
from config.fanslyconfig import FanslyConfig as FanslyConfigClass
from config.metadatahandling import MetadataHandling
from config.modes import DownloadMode
from download.core import DownloadState

# Import all factories and fixtures using wildcard
from tests.fixtures import *  # noqa: F403


# ============================================================================
# Pytest Hooks
# ============================================================================


def pytest_collection_modifyitems(config, items):
    """Hook to validate fixture usage and add markers.

    Ensures that any test using stash_client also uses stash_cleanup_tracker
    for proper test isolation and cleanup.

    This enforcement applies to ALL tests using stash_client, not just integration tests.
    Even if a test mocks the client, if the mock is incorrectly configured, it could
    make real connections to the Stash server and leave behind objects.

    Tests that violate this requirement are marked with xfail(strict=True),
    which means:
    - If the test would pass, it fails (enforcement)
    - If the test would fail naturally, it fails (expected)
    - If Stash is unavailable and test skips, it skips (expected)
    """
    for item in items:
        if (
            hasattr(item, "fixturenames")
            and "stash_client" in item.fixturenames
            and "stash_cleanup_tracker" not in item.fixturenames
        ):
            item.add_marker(
                pytest.mark.xfail(
                    reason="Tests using stash_client MUST also use stash_cleanup_tracker "
                    "for test isolation and cleanup. See tests/stash/CLEANUP_ENFORCEMENT_SUMMARY.md",
                    strict=True,
                )
            )


# ============================================================================
# Helper Functions
# ============================================================================


def clean_model_data(data_dict):
    """Remove problematic fields from dict before creating model instances.

    This prevents issues with _dirty_attrs and other internal fields
    that might cause problems with mock objects in tests.

    Args:
        data_dict: Dictionary of model data

    Returns:
        Cleaned dictionary
    """
    if not isinstance(data_dict, dict):
        return data_dict

    # Remove internal attributes that could cause issues
    clean_dict = {
        k: v
        for k, v in data_dict.items()
        if not k.startswith("_") and k != "client_mutation_id"
    }
    return clean_dict


# ============================================================================
# Auto-use Fixtures (Always Applied)
# ============================================================================


@pytest_asyncio.fixture(autouse=True)
async def cleanup_tasks():
    """Cleanup any remaining async tasks after each test.

    This prevents task leakage between tests and ensures clean test isolation.
    """
    yield
    # Clean up any remaining tasks at the end of each test
    for task in asyncio.all_tasks():
        if not task.done() and task != asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError, RuntimeError):
                # RuntimeError: cannot reuse already awaited coroutine
                await task


@pytest.fixture(autouse=True)
def setup_test_logging():
    """Set up logging for tests and clean up after.

    This fixture is automatically used in all tests to:
    1. Set up logging to a temporary directory
    2. Clean up log files after tests
    3. Properly close all file handlers
    """
    # Create a temporary directory for logs (manual management for proper cleanup)
    temp_dir = tempfile.mkdtemp()
    temp_path = Path(temp_dir)

    # Store original handlers to restore later
    original_handlers = logging.root.handlers.copy()

    handler_id = None
    try:
        # Remove all existing handlers
        logging.root.handlers.clear()

        # Configure logger to use temp directory
        logger.remove()  # Remove default loguru handler
        handler_id = logger.add(
            temp_path / "test.log",
            rotation="1 MB",
            retention=1,
            level="DEBUG",
        )

        yield temp_path

    finally:
        # CRITICAL: Remove loguru handlers FIRST to close file handles
        # This must happen before temp directory cleanup
        with suppress(ValueError, Exception):
            if handler_id is not None:
                logger.remove(handler_id)
            else:
                logger.remove()  # Remove all if handler_id wasn't captured

        # Restore original logging handlers
        logging.root.handlers.clear()
        for handler in original_handlers:
            logging.root.addHandler(handler)

        # NOW safe to clean up temp directory (all files are closed)
        with suppress(Exception):
            shutil.rmtree(temp_dir, ignore_errors=True)


# ============================================================================
# Test Data Fixtures
# ============================================================================


@pytest.fixture
def json_timeline_data():
    """Load timeline test data from JSON file.

    Returns:
        dict: Timeline API response data
    """
    fixtures_dir = Path(__file__).parent / "fixtures" / "json_data"
    timeline_file = fixtures_dir / "timeline_response.json"

    if not timeline_file.exists():
        return {
            "response": [],
            "aggregationData": {},
        }

    with timeline_file.open() as f:
        return json.load(f)


@pytest.fixture
def json_messages_group_data():
    """Load messages group test data from JSON file.

    Returns:
        dict: Messages API response data
    """
    fixtures_dir = Path(__file__).parent / "fixtures" / "json_data"
    messages_file = fixtures_dir / "messages_group_response.json"

    if not messages_file.exists():
        return {"response": [], "aggregationData": {}}

    with messages_file.open() as f:
        return json.load(f)


@pytest.fixture
def json_conversation_data():
    """Load conversation test data from JSON file.

    Returns:
        dict: Conversation API response data
    """
    fixtures_dir = Path(__file__).parent / "fixtures" / "json_data"
    conversation_file = fixtures_dir / "conversation_response.json"

    if not conversation_file.exists():
        return {"response": [], "aggregationData": {}}

    with conversation_file.open() as f:
        return json.load(f)


# ============================================================================
# Sample Data Fixtures
# ============================================================================


@pytest.fixture
def sample_account():
    """Create a sample account for testing.

    Returns:
        dict: Sample account data
    """
    return {
        "id": "12345",
        "username": "test_user",
        "displayName": "Test User",
        "about": "Test account for testing",
        "location": "Test Location",
    }


@pytest.fixture
def sample_post():
    """Create a sample post for testing.

    Returns:
        dict: Sample post data
    """
    return {
        "id": "post_123",
        "accountId": "12345",
        "content": "Test post content #test",
        "likeCount": 5,
        "replyCount": 1,
        "mediaLikeCount": 3,
    }


@pytest.fixture
def sample_message():
    """Create a sample message for testing.

    Returns:
        dict: Sample message data
    """
    return {
        "id": "message_123",
        "groupId": "group_123",
        "senderId": "12345",
        "content": "Test message content",
    }


# ============================================================================
# Config Fixtures (consolidated from tests/config/conftest.py)
# ============================================================================


# NOTE: The main 'config' fixture is defined in tests/fixtures/database_fixtures.py
# It creates a UUID-based PostgreSQL database for perfect test isolation.
# This legacy fixture is kept as 'mock_config' for tests that don't need real databases.


@pytest.fixture
def mock_config():
    """Create a basic FanslyConfig instance without database setup.

    Use this for tests that mock the database or don't need real database access.
    For tests that need a real database, use the 'config' fixture from database_fixtures.py

    Note: Sets pg_database to a non-existent name to prevent accidental connections
    to the production database.
    """
    config = FanslyConfig(program_version="0.11.0")
    # Override database name to prevent accidental connections to production database
    config.pg_database = "test_mock_should_not_connect"
    return config


@pytest.fixture
def test_config():
    """Alias for mock_config fixture for backwards compatibility."""
    config = FanslyConfig(program_version="0.11.0")
    # Override database name to prevent accidental connections to production database
    config.pg_database = "test_mock_should_not_connect"
    return config


@pytest.fixture
def temp_config_dir():
    """Create a temporary directory and change to it for config file testing."""

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
    """Create a ConfigParser instance for raw config manipulation."""
    return ConfigParser(interpolation=None)


@pytest.fixture
def mock_config_file(temp_config_dir, request):
    """Create a mock config file with specified content."""
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
def mock_download_dir(temp_config_dir):
    """Create a mock download directory for testing."""
    download_dir = temp_config_dir / "downloads"
    download_dir.mkdir()
    return download_dir


@pytest.fixture
def mock_metadata_dir(temp_config_dir):
    """Create a mock metadata directory for testing."""
    metadata_dir = temp_config_dir / "metadata"
    metadata_dir.mkdir()
    return metadata_dir


@pytest.fixture
def mock_temp_dir(temp_config_dir):
    """Create a mock temporary directory for testing."""
    temp_dir = temp_config_dir / "temp"
    temp_dir.mkdir()
    return temp_dir


@pytest.fixture
def valid_api_config(mock_config_file):
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
def download_modes():
    """Get all available download modes."""
    return list(DownloadMode)


@pytest.fixture(scope="session")
def metadata_handling_modes():
    """Get all available metadata handling modes."""
    return list(MetadataHandling)


@pytest.fixture
def temp_db_dir():
    """Create a temporary directory for database testing."""
    with TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


# ============================================================================
# Download Fixtures (consolidated from tests/download/conftest.py)
# ============================================================================


@pytest.fixture
def mock_download_config():
    """Create a mock FanslyConfig for download testing."""
    config = MagicMock(spec=FanslyConfig)
    config.download_path = Path("/test/download/path")
    config.program_version = "0.0.0-test"
    return config


@pytest.fixture
def download_state():
    """Create a real DownloadState for testing."""
    state = DownloadState()
    state.creator_name = "test_creator"
    return state


@pytest.fixture
def mock_download_state():
    """Create a mock DownloadState for testing."""
    state = MagicMock(spec=DownloadState)
    state.creator_id = "12345"
    state.creator_name = "test_user"
    state.messages_enabled = True
    state.verbose_logs = False
    return state


@pytest.fixture
def test_downloads_dir(tmp_path):
    """Create a temporary downloads directory."""
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    return downloads_dir


# ============================================================================
# Performance Fixtures (consolidated from tests/performance/conftest.py)
# ============================================================================


@pytest.fixture(scope="session")
def performance_log_dir():
    """Create a directory for performance test logs."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture(scope="session")
def performance_threshold():
    """Define performance thresholds for tests."""
    return {
        "max_memory_mb": 512,
        "max_cpu_percent": 80,
        "max_response_time": 2.0,
        "max_download_time": 30.0,
    }


@pytest.fixture
def performance_tracker(performance_log_dir, request):
    """Fixture to track and log performance metrics."""

    @contextmanager
    def track_performance():
        start_time = perf_counter()
        process = psutil.Process()
        start_memory = process.memory_info().rss / 1024 / 1024

        metrics = {
            "start_time": time(),
            "start_memory": start_memory,
            "max_memory": start_memory,
            "max_cpu": 0.0,
            "duration": 0.0,
            "memory_change": 0.0,
            "end_memory": start_memory,
            "end_time": time(),
        }

        try:
            yield metrics
        finally:
            end_time = perf_counter()
            end_memory = process.memory_info().rss / 1024 / 1024

            metrics["duration"] = end_time - start_time
            metrics["memory_change"] = end_memory - start_memory
            metrics["end_memory"] = end_memory
            metrics["end_time"] = time()
            metrics["max_memory"] = max(metrics["max_memory"], end_memory)

    class PerformanceContextManager:
        def __init__(self, operation_name: str):
            self.operation_name = operation_name
            self.log_file = (
                performance_log_dir / f"{request.node.name}_{operation_name}.log"
            )
            self.metrics = None
            self.perf_context = None

        def __enter__(self):
            self.perf_context = track_performance()
            self.metrics = self.perf_context.__enter__()
            return self.metrics

        def __exit__(self, exc_type, exc_val, exc_tb):
            result = self.perf_context.__exit__(exc_type, exc_val, exc_tb)

            with self.log_file.open("a") as f:
                f.write(f"Performance metrics for {self.operation_name}:\n")
                f.write(f"Duration: {self.metrics['duration']:.3f} seconds\n")
                f.write(f"Memory change: {self.metrics['memory_change']:.2f} MB\n")
                f.write(f"Max memory: {self.metrics['max_memory']:.2f} MB\n")
                f.write(f"Max CPU: {self.metrics['max_cpu']:.1f}%\n")
                f.write("-" * 50 + "\n")

            return result

    return PerformanceContextManager


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    "clean_model_data",
    "cleanup_tasks",
    "json_conversation_data",
    "json_messages_group_data",
    "json_timeline_data",
    "mock_download_state",
    "performance_log_dir",
    "performance_threshold",
    "performance_tracker",
    "sample_account",
    "sample_message",
    "sample_post",
    "setup_test_logging",
    "temp_config_dir",
    "temp_db_dir",
    "test_config",
    "test_downloads_dir",
    # Note: All factories and fixtures from tests.fixtures are automatically imported
    # via wildcard import and are available in all tests
]
