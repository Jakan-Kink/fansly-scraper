"""Common test fixtures and configuration."""

import asyncio
import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

import pytest
import pytest_asyncio
from loguru import logger
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from alembic import command
from alembic.config import Config as AlembicConfig
from config import FanslyConfig
from metadata.base import Base
from metadata.database import Database

_migrated_dbs = set()  # Track which DBs have had migrations run


@pytest_asyncio.fixture(autouse=True)
async def cleanup_tasks():
    """Cleanup any remaining tasks after each test."""
    yield
    # Clean up any remaining tasks at the end of each test
    for task in asyncio.all_tasks():
        if not task.done() and task != asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


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
            filter=lambda record: record["extra"].get("logger") in ("textio", "stash"),
        )

        # Add file handler for regular logs
        logger.add(
            str(temp_path / "test.log"),
            rotation="1 MB",
            retention="1 day",
            compression="gz",
            level="INFO",
            filter=lambda record: record["extra"].get("logger") in ("textio", "stash"),
        )

        # Add handler for JSON logs using loguru's built-in rotation
        logger.add(
            str(temp_path / "test_json.log"),
            format="[ {level} ] [{time:YYYY-MM-DD} | {time:HH:mm}]:\n{message}",
            level="INFO",
            filter=lambda record: record["extra"].get("logger") == "json",
            rotation="50 MB",
            retention="1 day",
            compression="gz",
            enqueue=True,  # Use queue for thread-safety
            catch=True,  # Catch exceptions
            backtrace=False,
            diagnose=False,
        )

        # Make sure trace logger gets its own file
        logger.add(
            str(temp_path / "trace.log"),
            format="[{time:YYYY-MM-DD HH:mm:ss.SSSSSS}] [{level.name}] {message}",
            level="TRACE",
            filter=lambda record: record["extra"].get("logger") == "trace",
        )

        # Add DB logger file
        logger.add(
            str(temp_path / "sqlalchemy.log"),
            format="[{time:YYYY-MM-DD HH:mm:ss}] [{level.name}] {message}",
            level="INFO",
            filter=lambda record: record["extra"].get("logger") == "db",
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
def test_config_factory():
    """Create a test configuration factory that allows customizing settings."""
    config = FanslyConfig(program_version="0.10.0")  # Version from pyproject.toml
    config.metadata_db_file = Path(":memory:")
    return config


class TestDatabase(Database):
    """Enhanced database class for testing."""

    def __init__(
        self,
        config: FanslyConfig,
        test_name: str | None = None,
        isolation_level: str = "SERIALIZABLE",
    ):
        """Initialize test database with configurable isolation level."""
        self.test_name = test_name
        super().__init__(config)
        self.isolation_level = isolation_level
        self._setup_engine()

    def _setup_engine(self) -> None:
        """Set up database engine with enhanced test configuration."""
        # Use test name for unique database if provided, otherwise use object id
        safe_name = f"test_{self.test_name}" if self.test_name else f"test_{id(self)}"
        safe_name = safe_name.replace("[", "_").replace("]", "_").replace(".", "_")
        db_uri = f"sqlite:///file:{safe_name}?mode=memory&cache=shared&uri=true"

        # Create sync engine
        self._sync_engine = create_engine(
            db_uri,
            isolation_level=self.isolation_level,
            echo=False,
            connect_args={
                "check_same_thread": False,
                "timeout": 30,  # 30 second timeout
            },
        )

        # Add event listeners for debugging and monitoring
        event.listen(
            self._sync_engine, "before_cursor_execute", self._before_cursor_execute
        )
        event.listen(
            self._sync_engine, "after_cursor_execute", self._after_cursor_execute
        )

        # Configure database for optimal test performance
        with self._sync_engine.connect() as conn:
            # Configure SQLite for optimal test performance
            conn.execute(text("PRAGMA synchronous=OFF"))
            conn.execute(text("PRAGMA temp_store=MEMORY"))
            conn.execute(text("PRAGMA mmap_size=268435456"))  # 256MB
            conn.execute(text("PRAGMA page_size=4096"))
            conn.execute(text("PRAGMA cache_size=-2000"))  # 2MB cache
            conn.execute(text("PRAGMA busy_timeout=30000"))

            # Keep foreign keys disabled as in production
            conn.execute(text("PRAGMA foreign_keys=OFF"))

            # Create all tables in dependency order
            Base.metadata.create_all(bind=conn, checkfirst=True)

            # Use WAL mode for better concurrency in tests
            conn.execute(text("PRAGMA journal_mode=WAL"))

        # Create async engine and session factory
        async_uri = db_uri.replace("sqlite://", "sqlite+aiosqlite://")
        self._async_engine = create_async_engine(
            async_uri,
            isolation_level=self.isolation_level,
            echo=False,
            connect_args={"check_same_thread": False},
        )
        self.async_session_factory = async_sessionmaker(
            bind=self._async_engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    def _before_cursor_execute(
        self, conn, cursor, statement, parameters, context, executemany
    ):
        """Log query execution start time."""
        conn.info.setdefault("query_start_time", []).append(time.time())

    def _after_cursor_execute(
        self, conn, cursor, statement, parameters, context, executemany
    ):
        """Log query execution time."""
        total = time.time() - conn.info["query_start_time"].pop()
        # Log if query takes more than 100ms
        if total > 0.1:
            print(f"Long running query ({total:.2f}s): {statement}")


@pytest_asyncio.fixture(scope="function")
async def test_database(test_config: FanslyConfig, request):
    """Create a test database with tables."""
    # Enable separate databases and use test name as creator
    test_config.separate_metadata = True
    # Create a temporary file that will be deleted when the file is closed
    temp_file = NamedTemporaryFile(suffix=".db", delete=False)
    test_config.metadata_db_file = Path(temp_file.name)
    temp_file.close()  # Close the file handle but don't delete yet

    # Create unique creator name from test name
    test_name = request.node.name
    if request.cls:
        test_name = f"{request.cls.__name__}_{test_name}"

    # Create database with test name for unique in-memory db
    database = TestDatabase(test_config, test_name=test_name)
    test_config._database = database

    yield database

    # Cleanup
    await database.cleanup()

    # Ensure temp file is cleaned up, fail test if cleanup fails
    try:
        if Path(temp_file.name).exists():
            Path(temp_file.name).unlink()
    except OSError as e:
        pytest.fail(f"Failed to clean up temporary database file: {e}")


@pytest.fixture
def test_session(test_database):
    """Create a test database session."""
    with test_database.session_scope() as session:
        yield session


@pytest_asyncio.fixture
async def test_async_session(test_config: FanslyConfig):
    """Create a test async database session with tables always created."""
    # Create unique database name for each test session
    import uuid

    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    db_name = f"test_db_{uuid.uuid4()}"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///file:{db_name}?mode=memory&cache=shared&uri=true",
        future=True,
    )

    # Create all tables
    from metadata.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionMaker = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    async with SessionMaker() as session:
        # Configure session for optimum test performance
        await session.execute(text("PRAGMA synchronous=OFF"))
        await session.execute(text("PRAGMA temp_store=MEMORY"))
        await session.execute(text("PRAGMA foreign_keys=OFF"))
        await session.execute(text("PRAGMA journal_mode=WAL"))
        yield session

    # Clean up
    await engine.dispose()


@pytest.fixture(scope="session")
async def async_engine():
    """Create a test async database engine."""
    # Create a unique in-memory database name for each test session
    db_name = f"test_db_{id(async_engine)}"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///file:{db_name}?mode=memory&cache=shared&uri=true",
        future=True,
        echo=False,
        # Connection health and pooling settings
        pool_pre_ping=True,
        pool_recycle=3600,
        # SQLite specific settings
        connect_args={
            "uri": True,
            "timeout": 120,
            "check_same_thread": False,
        },
        # Execution options
        execution_options={
            "isolation_level": "SERIALIZABLE",
            "autocommit": False,
        },
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="function")
async def async_session(async_engine):
    """Create a test async database session."""
    async_session_factory = async_sessionmaker(
        async_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    async with async_session_factory() as session:
        yield session
        await session.rollback()
