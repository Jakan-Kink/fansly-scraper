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
    with open(os.path.join(test_data_dir, "timeline-trainingJ.json")) as f:
        return json.load(f)


@pytest.fixture(scope="function")
def temp_db_path() -> Generator[str, None, None]:
    """Create a temporary database file path."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test.db")
    yield db_path
    if os.path.exists(db_path):
        os.remove(db_path)
    os.rmdir(temp_dir)


@pytest.fixture(scope="function")
def config(temp_db_path: str) -> FanslyConfig:
    """Create a test configuration."""
    config = FanslyConfig(program_version="0.10.0")
    config.metadata_db_file = temp_db_path
    return config


@pytest.fixture(scope="function")
def database(config: FanslyConfig) -> Database:
    """Create a test database instance."""
    return Database(config)


@pytest.fixture(scope="function")
def engine(database: Database):
    """Get the database engine."""
    return database.sync_engine


@pytest.fixture(scope="function")
def session_factory(engine) -> sessionmaker:
    """Create a session factory."""
    return sessionmaker(bind=engine)


@pytest.fixture(scope="function")
def session(engine, session_factory) -> Generator[Session, None, None]:
    """Create a database session."""
    Base.metadata.create_all(engine)
    session = session_factory()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="function")
def test_account(session: Session) -> Account:
    """Create a test account."""
    account = Account(id=1, username="test_user")
    session.add(account)
    session.commit()
    return account


@pytest.fixture(scope="function")
def test_media(session: Session, test_account: Account) -> Media:
    """Create a test media item."""
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
    return media


@pytest.fixture(scope="function")
def test_account_media(
    session: Session, test_account: Account, test_media: Media
) -> AccountMedia:
    """Create a test account media association."""
    account_media = AccountMedia(
        id=1,
        accountId=test_account.id,
        mediaId=test_media.id,
        createdAt=datetime.now(timezone.utc),
    )
    session.add(account_media)
    session.commit()
    return account_media


@pytest.fixture(scope="function")
def test_post(session: Session, test_account: Account) -> Post:
    """Create a test post."""
    post = Post(
        id=1,
        accountId=test_account.id,
        content="Test post content",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(post)
    session.commit()
    return post


@pytest.fixture(scope="function")
def test_wall(session: Session, test_account: Account) -> Wall:
    """Create a test wall."""
    wall = Wall(
        id=1,
        accountId=test_account.id,
        name="Test Wall",
        description="Test wall description",
        pos=1,
    )
    session.add(wall)
    session.commit()
    return wall


@pytest.fixture(scope="function")
def test_message(session: Session, test_account: Account) -> Message:
    """Create a test message."""
    message = Message(
        id=1,
        senderId=test_account.id,
        content="Test message content",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(message)
    session.commit()
    return message


@pytest.fixture(scope="function")
def test_bundle(
    session: Session, test_account: Account, test_account_media: AccountMedia
) -> AccountMediaBundle:
    """Create a test media bundle."""
    bundle = AccountMediaBundle(
        id=1, accountId=test_account.id, createdAt=datetime.now(timezone.utc)
    )
    session.add(bundle)
    session.flush()

    # Add media to bundle
    session.execute(
        account_media_bundle_media.insert().values(
            bundle_id=bundle.id, media_id=test_account_media.id, pos=1
        )
    )
    session.commit()
    return bundle
