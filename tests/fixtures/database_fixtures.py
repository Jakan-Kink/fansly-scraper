"""Enhanced test configuration and fixtures for metadata tests.

This module provides comprehensive fixtures for database testing, including:
- UUID-based database isolation (each test gets its own PostgreSQL database)
- Transaction management
- Isolation level control
- Performance monitoring
- Error handling
- Automatic cleanup procedures
"""

import asyncio
import hashlib
import json
import os
import time
import uuid
from collections.abc import (
    AsyncGenerator,
    Awaitable,
    Callable,
    Coroutine,
    Generator,
    Sequence,
)
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import quote_plus

import pytest
import pytest_asyncio
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.engine import Connection, ExecutionContext
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

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
from tests.fixtures import metadata_factories
from tests.fixtures.metadata_factories import AccountFactory


T = TypeVar("T")

# Export all fixtures for wildcard import
__all__ = [
    "cleanup_database",
    "config",
    "conversation_data",
    "factory_async_session",
    "factory_session",
    "json_conversation_data",
    "mock_account",
    "safe_name",
    "session",
    "session_factory",
    "session_sync",
    "test_account",
    "test_account_media",
    "test_async_session",
    "test_bundle",
    "test_data_dir",
    "test_database",
    "test_database_sync",
    "test_engine",
    "test_media",
    "test_message",
    "test_post",
    "test_wall",
    "timeline_data",
    "uuid_test_db_factory",
]


# ============================================================================
# Utility Classes for Mocking SQLAlchemy Async Patterns
# ============================================================================


class AwaitableAttrsMock:
    """Generic mock for SQLAlchemy's awaitable_attrs pattern.

    This utility class mocks SQLAlchemy's awaitable_attrs, which provides async
    access to relationship attributes. It dynamically handles ANY attribute access,
    returning a fresh coroutine each time, allowing unlimited reuse.

    This is completely generic - no need to hardcode attribute names.

    Usage:
        # Works with any attributes
        post = MagicMock()
        post.hashtags = [...]
        post.accountMentions = [...]
        post.attachments = [...]
        post.awaitable_attrs = AwaitableAttrsMock(post)

        # All attributes are automatically awaitable:
        tags = await post.awaitable_attrs.hashtags
        mentions = await post.awaitable_attrs.accountMentions
        attachments = await post.awaitable_attrs.attachments

        # Can await the same attribute multiple times:
        tags1 = await post.awaitable_attrs.hashtags
        tags2 = await post.awaitable_attrs.hashtags  # Creates fresh coroutine!

    Design:
        Uses __getattr__ to intercept ANY attribute access and return a coroutine
        that fetches the actual value from the parent object.
    """

    def __init__(self, parent_item: Any) -> None:
        """Initialize with reference to parent item.

        Args:
            parent_item: The parent object (mock or real) containing the actual data.
                        Can be a MagicMock, a factory-created model, or any object.
        """
        object.__setattr__(self, "_item", parent_item)

    def __getattr__(self, name: str) -> Awaitable[Any]:
        """Intercept any attribute access and return a fresh coroutine.

        Args:
            name: The attribute name being accessed

        Returns:
            A coroutine that will return the attribute value from the parent
        """

        async def get_attr() -> Any:
            return getattr(self._item, name, None)

        return get_attr()

    def __setattr__(self, name: str, value: Any) -> None:
        """Prevent setting attributes directly (maintain clean interface)."""
        if name == "_item":
            object.__setattr__(self, name, value)
        else:
            raise AttributeError(
                f"Cannot set attribute '{name}' on AwaitableAttrsMock. "
                f"Set it on the parent object instead."
            )


# ============================================================================
# UUID Database Factory - Provides perfect test isolation
# ============================================================================


@pytest.fixture
def uuid_test_db_factory(request: Any) -> Generator[FanslyConfig, None, None]:
    """Factory fixture that creates isolated PostgreSQL databases for each test.

    This fixture provides perfect test isolation by:
    1. Creating a unique PostgreSQL database per test (using UUID)
    2. Running migrations on the fresh database
    3. Automatically dropping the database after test completion

    Usage in test fixtures:
        config = uuid_test_db_factory()
        database = Database(config)

    Returns:
        FanslyConfig configured with a unique test database
    """
    # Generate unique database name using UUID
    test_db_name = f"test_{uuid.uuid4().hex[:8]}"

    # Get PostgreSQL connection parameters
    # Use current system user as default (usually has superuser access locally)
    pg_host = os.getenv("FANSLY_PG_HOST", "localhost")
    pg_port = int(os.getenv("FANSLY_PG_PORT", "5432"))
    pg_user = os.getenv("FANSLY_PG_USER", os.getenv("USER", "postgres"))
    pg_password = os.getenv("FANSLY_PG_PASSWORD", "")

    # Build admin connection URL (to postgres database)
    password_encoded = quote_plus(pg_password) if pg_password else ""
    admin_url = (
        f"postgresql://{pg_user}:{password_encoded}@{pg_host}:{pg_port}/postgres"
    )

    # Create the test database
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f"CREATE DATABASE {test_db_name}"))
    finally:
        admin_engine.dispose()

    # Create config pointing to the new test database
    config = FanslyConfig(program_version="0.11.0")
    config.pg_host = pg_host
    config.pg_port = pg_port
    config.pg_database = test_db_name
    config.pg_user = pg_user
    config.pg_password = pg_password
    config.metadata_db_file = None  # Use PostgreSQL, not SQLite

    yield config

    # Cleanup - drop the test database
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with suppress(Exception), admin_engine.connect() as conn:
        # Terminate any remaining connections
        terminate_stmt = text(
            "SELECT pg_terminate_backend(pid) "
            "FROM pg_stat_activity "
            "WHERE datname = :db_name AND pid <> pg_backend_pid()"
        )
        conn.execute(terminate_stmt, {"db_name": test_db_name})

        # Drop the database with FORCE (Postgres 13+) or fallback
        try:
            conn.execute(text(f"DROP DATABASE IF EXISTS {test_db_name} WITH (FORCE)"))
        except Exception:
            # Fallback for older Postgres versions
            conn.execute(text(f"DROP DATABASE IF EXISTS {test_db_name}"))
    admin_engine.dispose()


class TestDatabase(Database):
    """Enhanced database class for testing with PostgreSQL."""

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

    def _verify_tables_created(self, inspector: Any) -> None:
        """Verify that database tables were created successfully."""
        table_names = inspector.get_table_names()
        if not table_names:
            raise RuntimeError("Failed to create database tables")
        # Log created tables for debugging
        print(f"Created tables: {', '.join(sorted(table_names))}")

    def _setup_engine(self) -> None:
        """Set up database engine with enhanced test configuration for PostgreSQL."""
        # Build PostgreSQL connection URL
        pg_password = self.config.pg_password or os.getenv("FANSLY_PG_PASSWORD", "")
        db_url = f"postgresql://{self.config.pg_user}:{pg_password}@{self.config.pg_host}:{self.config.pg_port}/{self.config.pg_database}"

        # Create sync engine for PostgreSQL
        self._sync_engine = create_engine(
            db_url,
            isolation_level=self.isolation_level,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=3600,
        )

        # Add event listeners for debugging and monitoring
        event.listen(
            self._sync_engine, "before_cursor_execute", self._before_cursor_execute
        )
        event.listen(
            self._sync_engine, "after_cursor_execute", self._after_cursor_execute
        )

        # Create all tables in dependency order
        try:
            with self._sync_engine.connect() as conn:
                if not self.skip_migrations:
                    Base.metadata.create_all(bind=conn, checkfirst=True)

                    # Verify tables were created
                    inspector = inspect(self._sync_engine)
                    self._verify_tables_created(inspector)

                conn.commit()
        except Exception as e:
            # Ignore "already exists" errors that can occur with parallel test execution
            if "already exists" not in str(e).lower():
                raise

        # Create async engine and session factory for PostgreSQL
        async_url = f"postgresql+asyncpg://{self.config.pg_user}:{pg_password}@{self.config.pg_host}:{self.config.pg_port}/{self.config.pg_database}"
        self._async_engine = create_async_engine(
            async_url,
            isolation_level=self.isolation_level,
            echo=False,
            pool_pre_ping=True,
        )
        self._async_session_factory = async_sessionmaker(
            bind=self._async_engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    @property
    def async_session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._async_session_factory

    @async_session_factory.setter
    def async_session_factory(self, value: async_sessionmaker[AsyncSession]) -> None:
        self._async_session_factory = value

    def _before_cursor_execute(
        self,
        conn: Connection,
        cursor: Any,  # DBAPI cursor type varies by driver
        statement: str,
        parameters: dict[str, Any] | Sequence[Any],
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        """Log query execution start time."""
        conn.info.setdefault("query_start_time", []).append(time.time())

    def _after_cursor_execute(
        self,
        conn: Connection,
        cursor: Any,  # DBAPI cursor type varies by driver
        statement: str,
        parameters: dict[str, Any] | Sequence[Any],
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
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
        """Create a transaction with specific isolation level.

        Note: PostgreSQL isolation levels are set at the engine level,
        not per-session. The readonly parameter is not currently implemented
        for PostgreSQL.
        """
        session: Session = self._make_session()  # type: ignore[no-untyped-call]

        try:
            # PostgreSQL: isolation level is set at engine creation
            # readonly mode would require SET TRANSACTION READ ONLY
            if readonly:
                session.execute(text("SET TRANSACTION READ ONLY"))  # type: ignore[attr-defined]
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
        super()._run_migrations_if_needed(alembic_cfg)  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def test_data_dir() -> str:
    """Get the directory containing test data files."""
    return str(Path(__file__).parent.parent / "json")


@pytest.fixture(scope="session")
def timeline_data(test_data_dir: str) -> dict[str, Any]:
    """Load timeline test data."""
    with (Path(test_data_dir) / "timeline-sample-account.json").open() as f:
        return json.load(f)  # type: ignore[no-any-return]


@pytest.fixture(scope="session")
def json_conversation_data(test_data_dir: str) -> dict[str, Any]:
    """Load conversation test data."""
    with (Path(test_data_dir) / "conversation-sample-account.json").open() as f:
        return json.load(f)  # type: ignore[no-any-return]


@pytest.fixture(scope="session")
def conversation_data(test_data_dir: str) -> dict[str, Any]:
    """Load test message variants data for testing media variants and bundles."""
    with (Path(test_data_dir) / "test_message_variants.json").open() as f:
        return json.load(f)  # type: ignore[no-any-return]


def run_async(func: Callable[..., Coroutine[Any, Any, Any]]) -> Callable[..., Any]:
    """Decorator to run async functions in sync tests."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(func(*args, **kwargs))

    return wrapper


@pytest.fixture
def safe_name(request) -> str:
    """Generate a safe name for the test database based on the test name."""
    # Get the full test name to ensure uniqueness
    test_id = request.node.nodeid.encode("utf-8")
    safe_name = f"test_{abs(hash(test_id))}"
    return safe_name


@pytest_asyncio.fixture
async def test_engine(uuid_test_db_factory) -> AsyncGenerator[AsyncEngine, None]:
    """Create a test database engine with isolated PostgreSQL database (UUID-based).

    Each test gets its own database for perfect isolation.
    """
    config = uuid_test_db_factory
    password_encoded = quote_plus(config.pg_password) if config.pg_password else ""

    db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
    async_url = f"postgresql+asyncpg://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"

    # Create sync engine for table creation
    sync_engine = create_engine(
        db_url,
        isolation_level="SERIALIZABLE",
        echo=False,
        pool_pre_ping=True,
    )

    # Create tables
    try:
        Base.metadata.create_all(sync_engine, checkfirst=True)
    except Exception as e:
        # Ignore "already exists" errors that can occur with parallel test execution
        if "already exists" not in str(e).lower():
            raise

    # Create async engine
    engine = create_async_engine(
        async_url,
        isolation_level="SERIALIZABLE",
        echo=False,
        pool_pre_ping=True,
    )

    yield engine

    # Cleanup
    await engine.dispose()
    sync_engine.dispose()


@pytest_asyncio.fixture
async def test_async_session(
    uuid_test_db_factory,
) -> AsyncGenerator[AsyncSession, None]:
    """Create a test async database session with isolated PostgreSQL database (UUID-based).

    Each test gets its own database for perfect isolation.
    """
    config = uuid_test_db_factory
    password_encoded = quote_plus(config.pg_password) if config.pg_password else ""

    db_url = f"postgresql://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"
    async_url = f"postgresql+asyncpg://{config.pg_user}:{password_encoded}@{config.pg_host}:{config.pg_port}/{config.pg_database}"

    # Create sync engine for table creation
    sync_engine = create_engine(
        db_url,
        isolation_level="SERIALIZABLE",
        echo=False,
        pool_pre_ping=True,
    )

    # Create all tables
    try:
        Base.metadata.create_all(sync_engine, checkfirst=True)
    except Exception as e:
        # Ignore "already exists" errors that can occur with parallel test execution
        if "already exists" not in str(e).lower():
            raise

    # Create async engine
    engine = create_async_engine(
        async_url,
        isolation_level="SERIALIZABLE",
        echo=False,
        pool_pre_ping=True,
    )

    # Create session factory
    async_session_factory = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    # Create session
    session = async_session_factory()
    try:
        # PostgreSQL: No PRAGMA statements needed
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()
        sync_engine.dispose()


@pytest.fixture
def config(uuid_test_db_factory) -> FanslyConfig:
    """Create a test configuration with isolated PostgreSQL database (UUID-based)."""
    config = uuid_test_db_factory
    # Database sync settings (deprecated for PostgreSQL but kept for compatibility)
    config.db_sync_min_size = 50
    config.db_sync_commits = 1000
    config.db_sync_seconds = 60

    return config


@pytest.fixture
def test_sync_engine(test_database_sync: Database):
    """Get the sync database engine from test database."""
    return test_database_sync._sync_engine


@pytest.fixture
def session_factory(test_sync_engine) -> sessionmaker:
    """Create a session factory."""
    return sessionmaker(bind=test_sync_engine)


@pytest.fixture
def test_database_sync(
    config: FanslyConfig, test_engine
) -> Generator[Database, None, None]:
    """Create a test database instance with enhanced features (sync version).

    Depends on test_engine to ensure tables are created before database initialization.
    """
    # Skip migrations since test_engine already created tables
    db = TestDatabase(config, skip_migrations=True)
    try:
        yield db
    finally:
        # Always clean up database connections
        try:
            if hasattr(db, "_sync_engine"):
                db.close()
        except Exception as cleanup_error:
            print(f"Warning: Error during database cleanup: {cleanup_error}")


@pytest_asyncio.fixture
async def test_database(
    config: FanslyConfig, test_engine: AsyncEngine
) -> AsyncGenerator[Database, None]:
    """Create a test database instance with enhanced features (async version)."""
    # Skip migrations since test_engine already created tables with create_all()
    db = TestDatabase(config, skip_migrations=True)
    try:
        # Use the test engine (don't dispose it - test_engine fixture will handle that)
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

        yield db
    except Exception as e:
        print(f"Warning: Error during database setup: {e}")
        raise
    finally:
        # Cleanup: Only close sync engine if it exists
        # Don't dispose async engine - test_engine fixture owns it
        try:
            if hasattr(db, "_sync_engine") and db._sync_engine is not None:
                db._sync_engine.dispose()
        except Exception as cleanup_error:
            print(f"Warning: Error during database cleanup: {cleanup_error}")


@pytest_asyncio.fixture(scope="function", autouse=True)
async def cleanup_database(request):
    """Clean up database after each test."""
    yield
    try:
        # Get the test database fixture from the request
        if "test_database" in request.fixturenames:
            try:
                db = request.getfixturevalue("test_database")
                async with db.async_session_scope() as session:
                    # PostgreSQL: No PRAGMA statements needed
                    # Delete data in reverse dependency order
                    for table in reversed(Base.metadata.sorted_tables):
                        await session.execute(table.delete())
                    await session.commit()
                await db.close_async()
            except (ValueError, RuntimeError) as e:
                # Fixture may have been torn down already - silently ignore
                if "not available" not in str(
                    e
                ) and "already been torn down" not in str(e):
                    raise
        elif "test_database_sync" in request.fixturenames:
            try:
                db = request.getfixturevalue("test_database_sync")
                with db.session_scope() as session:
                    # PostgreSQL: No PRAGMA statements needed
                    # Delete data in reverse dependency order
                    for table in reversed(Base.metadata.sorted_tables):
                        session.execute(table.delete())
                    session.commit()
                db.close()
            except (ValueError, RuntimeError) as e:
                # Fixture may have been torn down already - silently ignore
                if "not available" not in str(
                    e
                ) and "already been torn down" not in str(e):
                    raise
    except Exception as e:
        # Only print warning for unexpected errors (not fixture teardown errors)
        if "not available" not in str(e) and "already been torn down" not in str(e):
            print(f"Warning: Error during database cleanup: {e}")


@pytest_asyncio.fixture
async def session(test_database: Database) -> AsyncGenerator[AsyncSession, None]:
    """Create an async database session."""
    async with test_database.async_session_scope() as session:
        try:
            yield session
        finally:
            with suppress(Exception):
                await session.rollback()  # Rollback on error


@pytest.fixture
def session_sync(test_database_sync: Database) -> Generator[Session, None, None]:
    """Create a sync database session."""
    with test_database_sync.session_scope() as session:
        try:
            yield session
        finally:
            with suppress(Exception):
                session.rollback()  # Rollback on error


async def create_test_entity(
    session: AsyncSession,
    entity_class: type[T],
    test_name: str,
    create_func: Callable[[AsyncSession, int], Awaitable[T]],
) -> T:
    """Generic function to create test entities with proper error handling."""
    # Generate unique ID based on test name
    # Generate unique ID based on full test name and class name
    test_name = test_name.replace("::", "_")  # Replace :: with _ for class methods
    unique_id = (
        int(hashlib.sha1(test_name.encode(), usedforsecurity=False).hexdigest()[:8], 16)
        % 1000000
    )

    # Check if entity already exists
    result = await session.execute(
        select(entity_class).where(entity_class.id == unique_id)  # type: ignore[attr-defined]
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    # Create new entity
    try:
        entity = await create_func(session, unique_id)
        session.add(entity)
        await session.commit()
        await session.refresh(entity)
    except Exception as e:
        await session.rollback()
        raise RuntimeError(f"Failed to create test {entity_class.__name__}: {e}") from e
    else:
        return entity


@pytest.fixture
async def test_account(session: AsyncSession, request) -> Account:
    """Create a test account with enhanced error handling."""

    async def create_account(session: AsyncSession, unique_id: int) -> Account:
        return Account(
            id=unique_id,
            username=f"test_user_{unique_id}",
            displayName=f"Test User {unique_id}",
            about="Test account for automated testing",
            location="Test Location",
            createdAt=datetime.now(UTC),
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


@pytest.fixture
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
            createdAt=datetime.now(UTC),
        )

    return await create_test_entity(
        session,
        Media,
        f"media_{test_account.id}",
        create_media,
    )


@pytest.fixture
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
            createdAt=datetime.now(UTC),
            updatedAt=datetime.now(UTC),
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


@pytest.fixture
async def test_post(session: AsyncSession, test_account: Account) -> Post:
    """Create a test post with enhanced attributes."""

    async def create_post(session: AsyncSession, unique_id: int) -> Post:
        return Post(
            id=unique_id,
            accountId=test_account.id,
            content="Test post content",
            createdAt=datetime.now(UTC),
            updatedAt=datetime.now(UTC),
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


@pytest.fixture
async def test_wall(session: AsyncSession, test_account: Account) -> Wall:
    """Create a test wall with enhanced attributes."""

    async def create_wall(session: AsyncSession, unique_id: int) -> Wall:
        return Wall(
            id=unique_id,
            accountId=test_account.id,
            name=f"Test Wall {unique_id}",
            description="Test wall description",
            pos=1,
            createdAt=datetime.now(UTC),
            updatedAt=datetime.now(UTC),
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


@pytest.fixture
async def test_message(session: AsyncSession, test_account: Account) -> Message:
    """Create a test message with enhanced attributes."""

    async def create_message(session: AsyncSession, unique_id: int) -> Message:
        return Message(
            id=unique_id,
            senderId=test_account.id,
            content="Test message content",
            createdAt=datetime.now(UTC),
            updatedAt=datetime.now(UTC),
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


@pytest.fixture
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
            createdAt=datetime.now(UTC),
            updatedAt=datetime.now(UTC),
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


@pytest.fixture
def mock_account():
    """Create a lightweight mock Account for unit tests (no database).

    Uses AccountFactory.build() to create a real Account SQLAlchemy object
    without requiring database persistence. Perfect for unit tests that need
    Account objects but don't need database operations.

    Returns:
        Account: A built (not persisted) Account instance
    """
    return AccountFactory.build(
        id=12345,
        username="test_user",
        displayName="Test User",
    )


@pytest.fixture
def factory_session(session_sync: Session):
    """Configure FactoryBoy factories to use the test database session.

    This fixture configures all factories to use the test database session.
    Tests that use factories must explicitly request this fixture or request
    fixtures that depend on it (like integration_mock_account).

    Args:
        session_sync: The sync database session fixture

    Yields:
        The configured session for use by factories
    """
    # Get all factory classes (BaseFactory and all subclasses)
    factory_classes = [
        metadata_factories.AccountFactory,
        metadata_factories.MediaFactory,
        metadata_factories.MediaLocationFactory,
        metadata_factories.PostFactory,
        metadata_factories.GroupFactory,
        metadata_factories.MessageFactory,
        metadata_factories.AttachmentFactory,
        metadata_factories.AccountMediaFactory,
        metadata_factories.AccountMediaBundleFactory,
    ]

    # Configure all factory classes to use this session
    for factory_class in factory_classes:
        factory_class._meta.sqlalchemy_session = session_sync

    yield session_sync

    # Reset after test
    for factory_class in factory_classes:
        factory_class._meta.sqlalchemy_session = None


@pytest_asyncio.fixture
async def factory_async_session(test_engine: AsyncEngine, session: AsyncSession):
    """Configure FactoryBoy factories for use with async sessions.

    This fixture solves the session attachment conflict when using factories
    in async tests. It creates a sync session from the same engine as the
    async session, configures factories to use it, and commits changes so
    they're visible to the async session.

    Usage:
        async def test_something(factory_async_session, session):
            # Create objects with factories
            account = AccountFactory(username="test")
            # Objects are committed and available in async session
            result = await session.execute(select(Account).where(Account.username == "test"))
            found = result.scalar_one()

    Args:
        test_engine: The async test engine
        session: The async session fixture

    Yields:
        A helper object with methods for factory operations
    """
    # Create a sync engine from the async engine's URL
    sync_url = str(test_engine.url).replace("+asyncpg", "")
    sync_engine = create_engine(
        sync_url,
        isolation_level="SERIALIZABLE",
        echo=False,
        pool_pre_ping=True,
    )

    # Create sync session factory
    SyncSessionFactory = sessionmaker(bind=sync_engine)  # noqa: N806
    sync_session = SyncSessionFactory()

    # Get all factory classes
    factory_classes = [
        metadata_factories.AccountFactory,
        metadata_factories.MediaFactory,
        metadata_factories.MediaLocationFactory,
        metadata_factories.PostFactory,
        metadata_factories.GroupFactory,
        metadata_factories.MessageFactory,
        metadata_factories.AttachmentFactory,
        metadata_factories.AccountMediaFactory,
        metadata_factories.AccountMediaBundleFactory,
    ]

    # Configure all factory classes to use the sync session
    for factory_class in factory_classes:
        factory_class._meta.sqlalchemy_session = sync_session
        factory_class._meta.sqlalchemy_session_persistence = "commit"

    class FactoryHelper:
        """Helper class for factory operations in async tests."""

        def __init__(self, sync_session, async_session):
            self.sync_session = sync_session
            self.async_session = async_session

        def commit(self):
            """Commit sync session so changes are visible to async session."""
            self.sync_session.commit()

    helper = FactoryHelper(sync_session, session)

    # Auto-commit after factory operations
    sync_session.commit()

    yield helper

    # Cleanup
    with suppress(Exception):
        sync_session.close()

    with suppress(Exception):
        sync_engine.dispose()

    # Reset factory configuration
    for factory_class in factory_classes:
        factory_class._meta.sqlalchemy_session = None
