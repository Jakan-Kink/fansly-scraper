"""Common test fixtures and configuration."""

import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from alembic.config import Config as AlembicConfig
from config import FanslyConfig
from metadata.base import Base
from metadata.database import Database


@pytest.fixture(autouse=True)
def setup_test_logging():
    """Set up logging for tests and clean up after.

    This fixture is automatically used in all tests to:
    1. Set up logging to a temporary directory
    2. Clean up log files after tests
    3. Properly close all file handlers
    """
    import logging
    import sys

    from textio.logging import SizeTimeRotatingHandler

    # Create a temporary directory for logs
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Store original handlers to restore later
        original_handlers = []
        for handler in logging.root.handlers[:]:
            original_handlers.append(handler)
            logging.root.removeHandler(handler)

        # Get the original log file paths
        original_log = os.getenv("LOGURU_LOG_FILE", "fansly_downloader_ng.log")
        original_json_log = os.getenv(
            "LOGURU_JSON_LOG_FILE", "fansly_downloader_ng_json.log"
        )

        # Set up logging to use temporary files
        os.environ["LOGURU_LOG_FILE"] = str(temp_path / "test.log")
        os.environ["LOGURU_JSON_LOG_FILE"] = str(temp_path / "test_json.log")

        # Remove all existing loguru handlers
        logger.remove()

        # Add stdout handler for test output
        logger.add(
            sys.stdout,
            format="<level>{level}</level> | <white>{time:HH:mm}</white> <level>|</level><light-white>| {message}</light-white>",
            level="INFO",
            filter=lambda record: not record["extra"].get("json", False),
        )

        # Add file handler for regular logs
        logger.add(
            str(temp_path / "test.log"),
            rotation="1 MB",
            retention="1 day",
            compression="gz",
            level="INFO",
            filter=lambda record: not record["extra"].get("json", False),
        )

        # Add handler for JSON logs using loguru's built-in rotation
        logger.add(
            str(temp_path / "test_json.log"),
            format="[ {level} ] [{time:YYYY-MM-DD} | {time:HH:mm}]:\n{message}",
            level="INFO",
            filter=lambda record: record["extra"].get("json", False),
            rotation="50 MB",
            retention="1 day",
            compression="gz",
            enqueue=True,  # Use queue for thread-safety
            catch=True,  # Catch exceptions
            backtrace=False,
            diagnose=False,
        )

        try:
            yield  # Run the test
        finally:
            # Close and remove all loguru handlers
            logger.remove()

            # Close any open file handlers
            for handler in logging.root.handlers[:]:
                try:
                    handler.close()
                except Exception:
                    pass
                logging.root.removeHandler(handler)

            # Restore original handlers
            for handler in original_handlers:
                logging.root.addHandler(handler)

            # Restore original log file paths
            if original_log:
                os.environ["LOGURU_LOG_FILE"] = original_log
            else:
                os.environ.pop("LOGURU_LOG_FILE", None)

            if original_json_log:
                os.environ["LOGURU_JSON_LOG_FILE"] = original_json_log
            else:
                os.environ.pop("LOGURU_JSON_LOG_FILE", None)

            # Clean up any remaining log files
            try:
                for file in temp_path.glob("*.log*"):
                    try:
                        file.close()  # Try to close if it's a file object
                    except Exception:
                        pass
                    try:
                        os.remove(file)  # Try to remove the file
                    except Exception:
                        pass
            except Exception:
                pass


@pytest.fixture
def temp_db_dir():
    """Create a temporary directory for test databases."""
    with TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def test_config():
    """Create a test configuration with in-memory database."""
    config = FanslyConfig(program_version="0.10.0")  # Version from pyproject.toml
    config.metadata_db_file = Path(":memory:")
    return config


@pytest.fixture
def test_database(test_config):
    """Create a test database instance."""
    # Use in-memory database for tests
    test_config.metadata_db_file = Path(":memory:")
    test_config._database = Database(test_config)

    # Create engine and tables
    engine = create_engine("sqlite:///:memory:")

    # Drop all tables and indexes first
    Base.metadata.drop_all(engine)

    # Create tables
    Base.metadata.create_all(engine)

    # Create session factory
    Session = sessionmaker(bind=engine)
    test_config._database.Session = Session

    yield test_config._database

    # Clean up
    try:
        test_config._database.close()
    except Exception:
        pass


@pytest.fixture
def test_session(test_database):
    """Create a test database session."""
    with test_database.get_sync_session() as session:
        yield session


@pytest.fixture
async def test_async_session(test_database):
    """Create an async test database session."""
    async with test_database.get_async_session() as session:
        yield session


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
