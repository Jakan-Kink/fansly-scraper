"""Test configuration and fixtures for metadata tests.

This module provides common fixtures and utilities for testing the metadata package.
It includes database setup, test data loading, and common test scenarios.
"""

import json
import os
import tempfile
from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, text
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
    return config


@pytest.fixture(scope="function")
def database(config: FanslyConfig) -> Database:
    """Create a test database instance."""
    db = Database(config)
    try:
        # Create tables in proper order
        inspector = inspect(db.sync_engine)
        if not inspector.get_table_names():
            # Create tables without foreign keys first
            tables_without_fks = [
                table for table in Base.metadata.sorted_tables if not table.foreign_keys
            ]
            for table in tables_without_fks:
                table.create(db.sync_engine)

            # Create remaining tables
            remaining_tables = [
                table
                for table in Base.metadata.sorted_tables
                if table not in tables_without_fks
            ]
            for table in remaining_tables:
                table.create(db.sync_engine)

        # Verify tables were created
        inspector = inspect(db.sync_engine)
        table_names = inspector.get_table_names()
        if not table_names:
            raise RuntimeError("Failed to create database tables")

        yield db
    finally:
        try:
            # Ensure proper cleanup
            with db.get_sync_session() as session:
                try:
                    # Disable foreign key checks temporarily
                    session.execute(text("PRAGMA foreign_keys = OFF"))

                    # Delete all data in reverse order of dependencies
                    for table in reversed(Base.metadata.sorted_tables):
                        session.execute(table.delete())

                    # Re-enable foreign key checks
                    session.execute(text("PRAGMA foreign_keys = ON"))
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
        except Exception:
            pass  # Ignore cleanup errors
        finally:
            try:
                # Always close the database, even if cleanup fails
                db.close()
            except Exception:
                pass  # Ignore close errors

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
                except Exception:
                    pass  # Ignore cleanup errors


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
            session.commit()  # Commit any pending changes
        except Exception:
            session.rollback()  # Rollback on error
            raise


@pytest.fixture(scope="function")
def test_account(session: Session, request) -> Account:
    """Create a test account with a unique ID based on test name."""
    # Generate a unique ID based on test name and test class
    test_name = request.node.name
    test_class = request.node.cls.__name__ if request.node.cls else "NoClass"
    import hashlib

    unique_id = (
        int(hashlib.sha1(f"{test_class}_{test_name}".encode()).hexdigest()[:8], 16)
        % 1000000
    )

    # Query for existing account first
    account = (
        session.query(Account).filter_by(username=f"test_user_{unique_id}").first()
    )
    if account is None:
        account = Account(id=unique_id, username=f"test_user_{unique_id}")
        session.add(account)
        session.commit()
        session.refresh(account)  # Refresh to ensure all fields are loaded
    return account


@pytest.fixture(scope="function")
def test_media(session: Session, test_account: Account) -> Media:
    """Create a test media item."""
    # Query for existing media first
    media = session.query(Media).filter_by(accountId=test_account.id).first()
    if media is None:
        media = Media(
            id=1,
            accountId=test_account.id,
            mimetype="video/mp4",
            width=1920,
            height=1080,
            duration=30.5,
        )
        session.add(media)
        session.commit()
        session.refresh(media)  # Refresh to ensure all fields are loaded
    return media


@pytest.fixture(scope="function")
def test_account_media(
    session: Session, test_account: Account, test_media: Media
) -> AccountMedia:
    """Create a test account media association."""
    # Query for existing account media first
    account_media = (
        session.query(AccountMedia)
        .filter_by(accountId=test_account.id, mediaId=test_media.id)
        .first()
    )
    if account_media is None:
        account_media = AccountMedia(
            id=1,
            accountId=test_account.id,
            mediaId=test_media.id,
            createdAt=datetime.now(timezone.utc),
        )
        session.add(account_media)
        session.commit()
        session.refresh(account_media)  # Refresh to ensure all fields are loaded
    return account_media


@pytest.fixture(scope="function")
def test_post(session: Session, test_account: Account) -> Post:
    """Create a test post."""
    # Query for existing post first
    post = session.query(Post).filter_by(accountId=test_account.id).first()
    if post is None:
        post = Post(
            id=1,
            accountId=test_account.id,
            content="Test post content",
            createdAt=datetime.now(timezone.utc),
        )
        session.add(post)
        session.commit()
        session.refresh(post)  # Refresh to ensure all fields are loaded
    return post


@pytest.fixture(scope="function")
def test_wall(session: Session, test_account: Account) -> Wall:
    """Create a test wall."""
    # Query for existing wall first
    wall = session.query(Wall).filter_by(accountId=test_account.id).first()
    if wall is None:
        wall = Wall(
            id=1,
            accountId=test_account.id,
            name="Test Wall",
            description="Test wall description",
            pos=1,
        )
        session.add(wall)
        session.commit()
        session.refresh(wall)  # Refresh to ensure all fields are loaded
    return wall


@pytest.fixture(scope="function")
def test_message(session: Session, test_account: Account) -> Message:
    """Create a test message."""
    # Query for existing message first
    message = session.query(Message).filter_by(senderId=test_account.id).first()
    if message is None:
        message = Message(
            id=1,
            senderId=test_account.id,
            content="Test message content",
            createdAt=datetime.now(timezone.utc),
        )
        session.add(message)
        session.commit()
        session.refresh(message)  # Refresh to ensure all fields are loaded
    return message


@pytest.fixture(scope="function")
def test_bundle(
    session: Session, test_account: Account, test_account_media: AccountMedia
) -> AccountMediaBundle:
    """Create a test media bundle."""
    # Query for existing bundle first
    bundle = (
        session.query(AccountMediaBundle).filter_by(accountId=test_account.id).first()
    )
    if bundle is None:
        bundle = AccountMediaBundle(
            id=1, accountId=test_account.id, createdAt=datetime.now(timezone.utc)
        )
        session.add(bundle)
        session.flush()

        # Check if media is already in bundle
        existing = session.execute(
            account_media_bundle_media.select().where(
                account_media_bundle_media.c.bundle_id == bundle.id,
                account_media_bundle_media.c.media_id == test_account_media.id,
            )
        ).first()

        if existing is None:
            # Add media to bundle
            session.execute(
                account_media_bundle_media.insert().values(
                    bundle_id=bundle.id, media_id=test_account_media.id, pos=1
                )
            )
        session.commit()
        session.refresh(bundle)  # Refresh to ensure all fields are loaded
    return bundle
