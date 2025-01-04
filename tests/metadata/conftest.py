"""Enhanced test configuration and fixtures for metadata tests.

This module provides comprehensive fixtures for database testing, including:
- Transaction management
- Isolation level control
- Performance monitoring
- Error handling
- Cleanup procedures
"""

import json
import os
import tempfile
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TypeVar

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

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

    def __init__(self, config: FanslyConfig, isolation_level: str = "SERIALIZABLE"):
        """Initialize test database with configurable isolation level."""
        super().__init__(config)
        self.isolation_level = isolation_level
        self._setup_engine()

    def _setup_engine(self) -> None:
        """Set up database engine with enhanced configuration."""
        self.sync_engine = create_engine(
            f"sqlite:///{self.config.metadata_db_file}",
            isolation_level=self.isolation_level,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=1800,
            echo=False,
        )

        # Add event listeners for debugging and monitoring
        event.listen(
            self.sync_engine, "before_cursor_execute", self._before_cursor_execute
        )
        event.listen(
            self.sync_engine, "after_cursor_execute", self._after_cursor_execute
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

    def _make_session(self) -> Session:
        """Create a new session with proper typing."""
        return sessionmaker(bind=self.sync_engine)()

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
        finally:
            session.close()  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def test_data_dir() -> str:
    """Get the directory containing test data files."""
    return os.path.join(os.path.dirname(__file__), "..", "json")


@pytest.fixture(scope="session")
def timeline_data(test_data_dir: str) -> dict:
    """Load timeline test data."""
    with open(os.path.join(test_data_dir, "timeline-sample-account.json")) as f:
        return json.load(f)


@pytest.fixture(scope="function")
def temp_db_path() -> Generator[str, None, None]:
    """Create a temporary database file path."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test.db")
    yield db_path
    try:
        # Clean up database file
        if os.path.exists(db_path):
            os.remove(db_path)

        # Clean up any SQLite journal files
        for ext in ["-shm", "-wal", "-journal"]:
            journal_file = db_path + ext
            if os.path.exists(journal_file):
                os.remove(journal_file)

        # Clean up directory if empty
        if os.path.exists(temp_dir):
            try:
                os.rmdir(temp_dir)
            except OSError:
                # Directory not empty, list remaining files for debugging
                remaining = os.listdir(temp_dir)
                print(f"Warning: Could not remove {temp_dir}, contains: {remaining}")
    except Exception as e:
        print(f"Warning: Error during cleanup: {e}")


@pytest.fixture(scope="function")
def config(temp_db_path) -> FanslyConfig:
    """Create a test configuration."""
    config = FanslyConfig(program_version="0.10.0")
    config.metadata_db_file = temp_db_path
    config.db_sync_min_size = 50  # Add required database sync settings
    config.db_sync_commits = 1000
    config.db_sync_seconds = 60

    # Initialize database
    from metadata.base import Base
    from metadata.database import Database

    config._database = Database(config)
    Base.metadata.create_all(config._database.sync_engine)

    return config


@pytest.fixture(scope="function")
def database(config: FanslyConfig) -> Generator[TestDatabase, None, None]:
    """Create a test database instance with enhanced features."""
    db = TestDatabase(config)
    try:
        # Create tables with proper ordering and foreign key handling
        inspector = inspect(db.sync_engine)
        if not inspector.get_table_names():
            # Disable foreign keys during initial creation
            with db.transaction() as session:
                session.execute(text("PRAGMA foreign_keys = OFF"))

                # Create tables in dependency order
                tables_without_fks = [
                    table
                    for table in Base.metadata.sorted_tables
                    if not table.foreign_keys
                ]
                remaining_tables = [
                    table
                    for table in Base.metadata.sorted_tables
                    if table not in tables_without_fks
                ]

                # Create tables in proper order
                for table in tables_without_fks + remaining_tables:
                    table.create(db.sync_engine)

                # Re-enable foreign keys
                session.execute(text("PRAGMA foreign_keys = ON"))

        # Verify database setup
        inspector = inspect(db.sync_engine)
        table_names = inspector.get_table_names()
        if not table_names:
            raise RuntimeError("Failed to create database tables")

        yield db
    finally:
        try:
            # Enhanced cleanup procedure
            with db.transaction() as session:
                try:
                    # Disable foreign keys for cleanup
                    session.execute(text("PRAGMA foreign_keys = OFF"))

                    # Delete data in reverse dependency order
                    for table in reversed(Base.metadata.sorted_tables):
                        session.execute(table.delete())

                    # Re-enable foreign keys
                    session.execute(text("PRAGMA foreign_keys = ON"))
                except Exception as e:
                    print(f"Warning: Error during table cleanup: {e}")

            # Close database connections
            db.close()

            # Clean up temporary files if using a file-based database
            if config.metadata_db_file not in [":memory:", None]:
                try:
                    import os
                    import shutil

                    if os.path.exists(config.metadata_db_file):
                        os.remove(config.metadata_db_file)
                    db_dir = os.path.dirname(config.metadata_db_file)
                    if os.path.exists(db_dir):
                        shutil.rmtree(db_dir)  # Remove directory and all its contents
                except Exception as e:
                    print(f"Warning: Error during file cleanup: {e}")
        except Exception as e:
            print(f"Warning: Error during database cleanup: {e}")


@pytest.fixture(scope="function")
def engine(database: Database):
    """Get the database engine."""
    return database.sync_engine


@pytest.fixture(scope="function")
def session_factory(engine) -> sessionmaker:
    """Create a session factory."""
    return sessionmaker(bind=engine)


@pytest.fixture(scope="function")
def session(database: Database) -> Generator[Session, None, None]:
    """Create a database session."""
    with database.get_sync_session() as session:
        try:
            yield session
            try:
                session.commit()  # Commit any pending changes
            except Exception:
                # Ignore errors during commit if database is closed
                pass
        except Exception:
            try:
                session.rollback()  # Rollback on error
            except Exception:
                # Ignore errors during rollback if database is closed
                pass
            raise


def create_test_entity(
    session: Session,
    entity_class: type[T],
    test_name: str,
    create_func: Callable[[Session, int], T],
) -> T:
    """Generic function to create test entities with proper error handling."""
    # Generate unique ID based on test name
    import hashlib

    unique_id = int(hashlib.sha1(test_name.encode()).hexdigest()[:8], 16) % 1000000

    try:
        # Try to create entity
        entity = create_func(session, unique_id)
        session.add(entity)
        session.commit()
        session.refresh(entity)
        return entity
    except IntegrityError:
        # Handle unique constraint violations
        session.rollback()
        existing = session.query(entity_class).get(unique_id)
        if existing:
            return existing
        raise
    except Exception as e:
        session.rollback()
        raise RuntimeError(f"Failed to create test {entity_class.__name__}: {e}") from e


@pytest.fixture(scope="function")
def test_account(session: Session, request) -> Account:
    """Create a test account with enhanced error handling."""

    def create_account(session: Session, unique_id: int) -> Account:
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
    return create_test_entity(
        session,
        Account,
        test_name,
        create_account,
    )


@pytest.fixture(scope="function")
def test_media(session: Session, test_account: Account) -> Media:
    """Create a test media item with enhanced attributes."""

    def create_media(session: Session, unique_id: int) -> Media:
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

    return create_test_entity(
        session,
        Media,
        f"media_{test_account.id}",
        create_media,
    )


@pytest.fixture(scope="function")
def test_account_media(
    session: Session, test_account: Account, test_media: Media
) -> AccountMedia:
    """Create a test account media association with enhanced attributes."""

    def create_account_media(session: Session, unique_id: int) -> AccountMedia:
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

    return create_test_entity(
        session,
        AccountMedia,
        f"account_media_{test_account.id}_{test_media.id}",
        create_account_media,
    )


@pytest.fixture(scope="function")
def test_post(session: Session, test_account: Account) -> Post:
    """Create a test post with enhanced attributes."""

    def create_post(session: Session, unique_id: int) -> Post:
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

    return create_test_entity(
        session,
        Post,
        f"post_{test_account.id}",
        create_post,
    )


@pytest.fixture(scope="function")
def test_wall(session: Session, test_account: Account) -> Wall:
    """Create a test wall with enhanced attributes."""

    def create_wall(session: Session, unique_id: int) -> Wall:
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

    return create_test_entity(
        session,
        Wall,
        f"wall_{test_account.id}",
        create_wall,
    )


@pytest.fixture(scope="function")
def test_message(session: Session, test_account: Account) -> Message:
    """Create a test message with enhanced attributes."""

    def create_message(session: Session, unique_id: int) -> Message:
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

    return create_test_entity(
        session,
        Message,
        f"message_{test_account.id}",
        create_message,
    )


@pytest.fixture(scope="function")
def test_bundle(
    session: Session,
    test_account: Account,
    test_media: Media,
) -> AccountMediaBundle:
    """Create a test media bundle with enhanced attributes."""

    def create_bundle(session: Session, unique_id: int) -> AccountMediaBundle:
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
        session.flush()

        # Add media to bundle
        session.execute(
            account_media_bundle_media.insert().values(
                bundle_id=bundle.id,
                media_id=test_media.id,
                pos=1,
            )
        )
        return bundle

    return create_test_entity(
        session,
        AccountMediaBundle,
        f"bundle_{test_account.id}",
        create_bundle,
    )
