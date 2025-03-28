"""Enhanced test configuration and fixtures for metadata tests.

This module provides comprehensive fixtures for database testing, including:
- Transaction management
- Isolation level control
- Performance monitoring
- Error handling
- Cleanup procedures
"""

import asyncio
import json
import os
import tempfile
import threading
import time
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import TypeVar

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool, StaticPool

from alembic import command
from alembic.config import Config as AlembicConfig
from config import FanslyConfig
from metadata import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Base,
    Database,
    Media,
    Message,
    Post,
    Wall,
    account_media_bundle_media,
)

T = TypeVar("T")


class TestDatabase(Database):
    """Enhanced database class for testing."""

    def __init__(
        self,
        config: FanslyConfig,
        isolation_level: str = "SERIALIZABLE",
        skip_migrations: bool = False,
    ):
        """Initialize test database with configurable isolation level."""
        self.skip_migrations = skip_migrations
        self.isolation_level = isolation_level
        super().__init__(config, skip_migrations=skip_migrations)
        self._setup_engine()

    def _setup_engine(self) -> None:
        """Set up database engine with enhanced test configuration."""
        # Use the unique URI from config to prevent cross-test pollution
        safe_name = f"test_{id(self)}"
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

            # Verify tables were created
            inspector = inspect(self._sync_engine)
            table_names = inspector.get_table_names()
            if not table_names:
                raise RuntimeError("Failed to create database tables")

            # Log created tables for debugging
            print(f"Created tables: {', '.join(sorted(table_names))}")

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
        self._async_session_factory = async_sessionmaker(
            bind=self._async_engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    @property
    def async_session_factory(self):
        return self._async_session_factory

    @async_session_factory.setter
    def async_session_factory(self, value):
        self._async_session_factory = value

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

    def _make_session(self) -> Session:
        """Create a new session with proper typing."""
        return sessionmaker(bind=self._sync_engine)()

    def get_sync_session(self) -> Session:
        """Get a sync session."""
        return self._make_session()

    def get_async_session(self) -> AsyncSession:
        """Get an async session."""
        return self.async_session_factory()

    @contextmanager
    def transaction(
        self,
        *,
        isolation_level: str | None = None,
        readonly: bool = False,
    ) -> Generator[Session, None, None]:
        """Create a transaction with specific isolation level."""
        session: Session = self._make_session()  # type: ignore[no-untyped-call]

        try:
            if isolation_level:
                session.execute(text(f"PRAGMA isolation_level = {isolation_level}"))  # type: ignore[attr-defined]
            if readonly:
                session.execute(text("PRAGMA query_only = ON"))  # type: ignore[attr-defined]
            yield session
            session.commit()  # type: ignore[attr-defined]
        except Exception:
            session.rollback()  # type: ignore[attr-defined]
            raise

    def close(self) -> None:
        """Close database connections."""
        if hasattr(self, "_sync_engine"):
            self._sync_engine.dispose()

    async def close_async(self) -> None:
        """Close database connections asynchronously."""
        if hasattr(self, "_sync_engine"):
            self._sync_engine.dispose()
        if hasattr(self, "_async_engine"):
            await self._async_engine.dispose()

    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        """Get a sync session."""
        with self.transaction() as session:
            yield session

    @asynccontextmanager
    async def async_session_scope(self) -> AsyncGenerator[AsyncSession, None]:
        """Get an async session with automatic commit/rollback."""
        session = self.async_session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    def _run_migrations_if_needed(self, alembic_cfg: AlembicConfig) -> None:
        """Override to optionally skip migrations."""
        if self.skip_migrations:
            return
        super()._run_migrations_if_needed(alembic_cfg)


@pytest.fixture(scope="session")
def test_data_dir() -> str:
    """Get the directory containing test data files."""
    return os.path.join(os.path.dirname(__file__), "..", "json")


@pytest.fixture(scope="session")
def timeline_data(test_data_dir: str) -> dict:
    """Load timeline test data."""
    with open(os.path.join(test_data_dir, "timeline-sample-account.json")) as f:
        return json.load(f)


def run_async(func):
    """Decorator to run async functions in sync tests."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wrapper


@pytest.fixture
def safe_name(request) -> str:
    """Generate a safe name for the test database based on the test name."""
    # Get the full test name and replace invalid characters
    test_name = request.node.name.replace("[", "_").replace("]", "_")
    test_name = test_name.replace(".", "_").replace("::", "_")
    return test_name


@pytest.fixture(scope="session")
def temp_db_path(request) -> Generator[str, None, None]:
    """Create a temporary database file path with unique URI."""
    # Use unique URI for each test to prevent cross-test pollution
    import uuid

    safe_name = f"creator_{uuid.uuid4().hex}"
    db_path = f"sqlite:///file:{safe_name}?mode=memory&cache=shared&uri=true"
    yield db_path


@pytest_asyncio.fixture
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create a test database engine.

    Uses SQLite in-memory database with shared cache to ensure proper transaction isolation
    and connection pooling.
    """
    # Create unique database name
    safe_name = f"test_{id(test_engine)}"
    db_uri = f"sqlite:///file:{safe_name}?mode=memory&cache=shared&uri=true"
    async_uri = db_uri.replace("sqlite://", "sqlite+aiosqlite://")

    # Create sync engine for migrations
    sync_engine = create_engine(
        db_uri,
        isolation_level="SERIALIZABLE",
        echo=False,
        connect_args={
            "check_same_thread": False,
            "timeout": 30,  # 30 second timeout
        },
    )

    # Configure SQLite for optimal test performance
    with sync_engine.connect() as conn:
        conn.execute(text("PRAGMA synchronous=OFF"))
        conn.execute(text("PRAGMA temp_store=MEMORY"))
        conn.execute(text("PRAGMA mmap_size=268435456"))  # 256MB
        conn.execute(text("PRAGMA page_size=4096"))
        conn.execute(text("PRAGMA cache_size=-2000"))  # 2MB cache
        conn.execute(text("PRAGMA busy_timeout=30000"))
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(text("PRAGMA journal_mode=WAL"))

    # Create tables
    Base.metadata.create_all(sync_engine)

    # Create async engine
    engine = create_async_engine(
        async_uri,
        isolation_level="SERIALIZABLE",
        echo=False,
        connect_args={
            "check_same_thread": False,
            "timeout": 30,
        },
    )

    yield engine

    # Cleanup
    Base.metadata.drop_all(sync_engine)
    await engine.dispose()
    sync_engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    async_session_factory = async_sessionmaker(
        bind=test_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    session = async_session_factory()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()


@pytest_asyncio.fixture
async def test_async_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test async database session."""
    # Create session factory
    async_session_factory = async_sessionmaker(
        bind=test_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    # Create session
    session = async_session_factory()
    try:
        # Configure session
        await session.execute(text("PRAGMA foreign_keys=OFF"))
        await session.execute(text("PRAGMA journal_mode=WAL"))
        yield session
    finally:
        await session.rollback()
        await session.close()


@pytest.fixture(scope="function")
def config(temp_db_path) -> FanslyConfig:
    """Create a test configuration."""
    config = FanslyConfig(program_version="0.10.0")
    config.metadata_db_file = Path(temp_db_path)  # Use the unique URI from temp_db_path
    config.db_sync_min_size = 50  # Add required database sync settings
    config.db_sync_commits = 1000
    config.db_sync_seconds = 60

    # Initialize database

    # # Skip migrations during tests
    # config.skip_migrations = True

    config._database = TestDatabase(config, skip_migrations=True)
    return config


@pytest.fixture(scope="function")
def event_loop_policy():
    """Create a policy for test event loops."""
    policy = asyncio.get_event_loop_policy()
    return policy


@pytest_asyncio.fixture(scope="function")
async def database(config: FanslyConfig) -> AsyncGenerator[TestDatabase, None]:
    """Create a test database instance with enhanced features."""
    db = TestDatabase(config)
    try:
        # Verify database setup
        inspector = inspect(db._sync_engine)
        table_names = inspector.get_table_names()
        if not table_names:
            raise RuntimeError("Failed to create database tables")

        yield db
    finally:
        try:
            # Enhanced cleanup procedure
            async with db.async_session_scope() as session:
                try:
                    # Keep foreign keys disabled for cleanup
                    await session.execute(text("PRAGMA foreign_keys = OFF"))

                    # Delete data in reverse dependency order
                    for table in reversed(Base.metadata.sorted_tables):
                        await session.execute(table.delete())
                    await session.commit()
                except Exception as e:
                    print(f"Warning: Error during table cleanup: {e}")

            # Close database connections
            await db.close_async()
        except Exception as e:
            print(f"Warning: Error during database cleanup: {e}")


@pytest.fixture(scope="function")
def engine(database: Database):
    """Get the database engine."""
    return database._sync_engine


@pytest.fixture(scope="function")
def session_factory(engine) -> sessionmaker:
    """Create a session factory."""
    return sessionmaker(bind=engine)


@pytest_asyncio.fixture(scope="function")
async def test_database_sync(config: FanslyConfig) -> Database:
    """Create a test database instance with enhanced features (sync version)."""
    db = TestDatabase(config)
    try:
        # Verify database setup
        inspector = inspect(db._sync_engine)
        table_names = inspector.get_table_names()
        if not table_names:
            raise RuntimeError("Failed to create database tables")

        return db
    except Exception as e:
        print(f"Warning: Error during database setup: {e}")
        if hasattr(db, "close"):
            db.close()
        raise


@pytest_asyncio.fixture(scope="function")
async def test_database(config: FanslyConfig, test_engine: AsyncEngine) -> Database:
    """Create a test database instance with enhanced features (async version)."""
    db = TestDatabase(config, skip_migrations=True)  # Skip migrations by default
    try:
        # Use the test engine
        db._async_engine = test_engine
        db.async_session_factory = async_sessionmaker(
            bind=test_engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

        # Verify async session works
        async with db.async_session_scope() as session:
            await session.execute(text("SELECT 1"))
            await session.commit()

        return db
    except Exception as e:
        print(f"Warning: Error during database setup: {e}")
        if hasattr(db, "close"):
            await db.close_async()
        raise


@pytest_asyncio.fixture(scope="function", autouse=True)
async def cleanup_database(request):
    """Clean up database after each test."""
    yield
    try:
        # Get the test database fixture from the request
        if "test_database" in request.fixturenames:
            db = request.getfixturevalue("test_database")
            async with db.async_session_scope() as session:
                # Keep foreign keys disabled for cleanup
                await session.execute(text("PRAGMA foreign_keys = OFF"))

                # Delete data in reverse dependency order
                for table in reversed(Base.metadata.sorted_tables):
                    await session.execute(table.delete())
                await session.commit()
            await db.close_async()
        elif "test_database_sync" in request.fixturenames:
            db = request.getfixturevalue("test_database_sync")
            with db.session_scope() as session:
                # Keep foreign keys disabled for cleanup
                session.execute(text("PRAGMA foreign_keys = OFF"))

                # Delete data in reverse dependency order
                for table in reversed(Base.metadata.sorted_tables):
                    session.execute(table.delete())
                session.commit()
            db.close_sync()
    except Exception as e:
        print(f"Warning: Error during database cleanup: {e}")


@pytest_asyncio.fixture(scope="function")
async def session(test_database: Database) -> AsyncSession:
    """Create an async database session."""
    async with test_database.async_session_scope() as session:
        try:
            yield session
        finally:
            try:
                await session.rollback()  # Rollback on error
            except Exception:
                # Ignore errors during rollback if database is closed
                pass


@pytest.fixture(scope="function")
def session_sync(test_database_sync: Database) -> Session:
    """Create a sync database session."""
    with test_database_sync.session_scope() as session:
        try:
            yield session
        finally:
            try:
                session.rollback()  # Rollback on error
            except Exception:
                # Ignore errors during rollback if database is closed
                pass


async def create_test_entity(
    session: AsyncSession,
    entity_class: type[T],
    test_name: str,
    create_func: Callable[[AsyncSession, int], T],
) -> T:
    """Generic function to create test entities with proper error handling."""
    # Generate unique ID based on test name
    import hashlib

    # Generate unique ID based on full test name and class name
    test_name = test_name.replace("::", "_")  # Replace :: with _ for class methods
    unique_id = int(hashlib.sha1(test_name.encode()).hexdigest()[:8], 16) % 1000000

    try:
        # Check if entity already exists
        result = await session.execute(
            select(entity_class).where(entity_class.id == unique_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        # Create new entity
        entity = await create_func(session, unique_id)
        session.add(entity)
        await session.commit()
        await session.refresh(entity)
        return entity
    except Exception as e:
        await session.rollback()
        raise RuntimeError(f"Failed to create test {entity_class.__name__}: {e}") from e


@pytest.fixture(scope="function")
async def test_account(session: AsyncSession, request) -> Account:
    """Create a test account with enhanced error handling."""

    async def create_account(session: AsyncSession, unique_id: int) -> Account:
        return Account(
            id=unique_id,
            username=f"test_user_{unique_id}",
            displayName=f"Test User {unique_id}",
            about="Test account for automated testing",
            location="Test Location",
            createdAt=datetime.now(timezone.utc),
        )

    # Handle both class and function test cases
    test_name = request.node.name
    if request.node.cls is not None:
        test_name = f"{request.node.cls.__name__}_{test_name}"
    return await create_test_entity(
        session,
        Account,
        test_name,
        create_account,
    )


@pytest.fixture(scope="function")
async def test_media(session: AsyncSession, test_account: Account) -> Media:
    """Create a test media item with enhanced attributes."""

    async def create_media(session: AsyncSession, unique_id: int) -> Media:
        return Media(
            id=unique_id,
            accountId=test_account.id,
            mimetype="video/mp4",
            width=1920,
            height=1080,
            duration=30.5,
            size=1024 * 1024,  # 1MB
            hash="test_hash",
            url="https://example.com/test.mp4",
            createdAt=datetime.now(timezone.utc),
        )

    return await create_test_entity(
        session,
        Media,
        f"media_{test_account.id}",
        create_media,
    )


@pytest.fixture(scope="function")
async def test_account_media(
    session: AsyncSession, test_account: Account, test_media: Media
) -> AccountMedia:
    """Create a test account media association with enhanced attributes."""

    async def create_account_media(
        session: AsyncSession, unique_id: int
    ) -> AccountMedia:
        return AccountMedia(
            id=unique_id,
            accountId=test_account.id,
            mediaId=test_media.id,
            createdAt=datetime.now(timezone.utc),
            updatedAt=datetime.now(timezone.utc),
            status="active",
            type="video",
            title="Test Media Title",
            description="Test media description",
        )

    return await create_test_entity(
        session,
        AccountMedia,
        f"account_media_{test_account.id}_{test_media.id}",
        create_account_media,
    )


@pytest.fixture(scope="function")
async def test_post(session: AsyncSession, test_account: Account) -> Post:
    """Create a test post with enhanced attributes."""

    async def create_post(session: AsyncSession, unique_id: int) -> Post:
        return Post(
            id=unique_id,
            accountId=test_account.id,
            content="Test post content",
            createdAt=datetime.now(timezone.utc),
            updatedAt=datetime.now(timezone.utc),
            type="text",
            status="published",
            title="Test Post Title",
            description="Test post description",
            likes=0,
            comments=0,
        )

    return await create_test_entity(
        session,
        Post,
        f"post_{test_account.id}",
        create_post,
    )


@pytest.fixture(scope="function")
async def test_wall(session: AsyncSession, test_account: Account) -> Wall:
    """Create a test wall with enhanced attributes."""

    async def create_wall(session: AsyncSession, unique_id: int) -> Wall:
        return Wall(
            id=unique_id,
            accountId=test_account.id,
            name=f"Test Wall {unique_id}",
            description="Test wall description",
            pos=1,
            createdAt=datetime.now(timezone.utc),
            updatedAt=datetime.now(timezone.utc),
            status="active",
            type="default",
            postCount=0,
        )

    return await create_test_entity(
        session,
        Wall,
        f"wall_{test_account.id}",
        create_wall,
    )


@pytest.fixture(scope="function")
async def test_message(session: AsyncSession, test_account: Account) -> Message:
    """Create a test message with enhanced attributes."""

    async def create_message(session: AsyncSession, unique_id: int) -> Message:
        return Message(
            id=unique_id,
            senderId=test_account.id,
            content="Test message content",
            createdAt=datetime.now(timezone.utc),
            updatedAt=datetime.now(timezone.utc),
            type="text",
            status="sent",
            isEdited=False,
            isDeleted=False,
            hasAttachments=False,
        )

    return await create_test_entity(
        session,
        Message,
        f"message_{test_account.id}",
        create_message,
    )


@pytest.fixture(scope="function")
async def test_bundle(
    session: AsyncSession,
    test_account: Account,
    test_media: Media,
) -> AccountMediaBundle:
    """Create a test media bundle with enhanced attributes."""

    async def create_bundle(
        session: AsyncSession, unique_id: int
    ) -> AccountMediaBundle:
        bundle = AccountMediaBundle(
            id=unique_id,
            accountId=test_account.id,
            createdAt=datetime.now(timezone.utc),
            updatedAt=datetime.now(timezone.utc),
            name=f"Test Bundle {unique_id}",
            description="Test bundle description",
            status="active",
            type="collection",
            mediaCount=1,
        )
        session.add(bundle)
        await session.flush()

        # Add media to bundle
        await session.execute(
            account_media_bundle_media.insert().values(
                bundle_id=bundle.id,
                media_id=test_media.id,
                pos=1,
            )
        )
        return bundle

    return await create_test_entity(
        session,
        AccountMediaBundle,
        f"bundle_{test_account.id}",
        create_bundle,
    )
