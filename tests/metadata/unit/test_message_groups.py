"""Unit tests for message group functionality."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import FanslyConfig
from config.version import get_project_version
from download.core import DownloadState
from metadata.account import Account
from metadata.base import Base
from metadata.database import Database
from metadata.messages import Group, Message, process_groups_response


@pytest_asyncio.fixture(autouse=True)
async def cleanup_database(test_engine):
    """Clean up database before and after each test."""
    # Clean up before test
    async with async_sessionmaker(bind=test_engine)() as session:
        # Disable foreign key checks for cleanup
        await session.execute(text("PRAGMA foreign_keys=OFF"))
        # Delete data in reverse dependency order
        for table in reversed(Base.metadata.sorted_tables):
            await session.execute(table.delete())
        await session.commit()

    yield

    # Clean up after test
    async with async_sessionmaker(bind=test_engine)() as session:
        # Disable foreign key checks for cleanup
        await session.execute(text("PRAGMA foreign_keys=OFF"))
        # Delete data in reverse dependency order
        for table in reversed(Base.metadata.sorted_tables):
            await session.execute(table.delete())
        await session.commit()


@pytest_asyncio.fixture
async def database(test_engine):
    """Create a test database instance."""
    # Create a test config
    config = FanslyConfig(program_version="test")
    # Create a temporary file path for the database
    temp_db = Path(tempfile.gettempdir()) / "test_metadata.sqlite3"
    config.metadata_db_file = temp_db

    # Create database instance
    db = Database(config)
    # Override the engine and session factory to use our test engine
    db._async_engine = test_engine
    db.async_session_factory = async_sessionmaker(
        bind=test_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    # Verify database is ready
    async with db.async_session_scope() as session:
        await session.execute(select(1))
        await session.commit()

    try:
        yield db
    finally:
        # Clean up the temporary database file
        if temp_db.exists():
            temp_db.unlink()


@pytest_asyncio.fixture
async def mock_config(database):
    """Create a mock config for testing."""
    config = FanslyConfig(program_version=get_project_version())
    config._database = database

    # Verify database connection works
    async with database.async_session_scope() as session:
        await session.execute(select(1))
        await session.commit()

    return config


@pytest.fixture
def download_state():
    """Create a test download state."""
    return DownloadState(creator_name="test_creator")


@pytest.mark.asyncio
async def test_group_creation(database):
    """Test basic group creation."""
    async with database.async_session_scope() as session:
        # Create account first since it's required by foreign key
        account = Account(id=123, username="test_user")
        session.add(account)
        await session.commit()

        # Now create group
        group = Group(id=1, createdBy=123)
        session.add(group)
        await session.commit()

        stmt = select(Group)
        result = await session.execute(stmt)
        group = result.scalar_one_or_none()
        assert group.id == 1
        assert group.createdBy == 123
        assert group.lastMessageId is None


@pytest.mark.asyncio
async def test_message_creation(database):
    """Test basic message creation."""
    async with database.async_session_scope() as session:
        # Create account first since it's required by foreign key
        account = Account(id=123, username="test_user")
        session.add(account)
        await session.commit()

        # Now create message
        message = Message(
            id=1, senderId=123, content="test", createdAt=datetime.now(timezone.utc)
        )
        session.add(message)
        await session.commit()

        stmt = select(Message)
        result = await session.execute(stmt)
        message = result.scalar_one_or_none()
        assert message.id == 1
        assert message.senderId == 123
        assert message.content == "test"


@pytest.mark.asyncio
async def test_group_message_relationship(database):
    """Test relationship between groups and messages."""
    async with database.async_session_scope() as session:
        # Create account first since it's required by foreign key
        account = Account(id=123, username="test_user")
        session.add(account)
        await session.commit()

        # Create message first
        message = Message(
            id=1, senderId=123, content="test", createdAt=datetime.now(timezone.utc)
        )
        session.add(message)
        await session.commit()

        # Create group with lastMessageId
        group = Group(id=1, createdBy=123, lastMessageId=1)
        session.add(group)
        await session.commit()

        stmt = select(Group)
        result = await session.execute(stmt)
        group = result.scalar_one_or_none()
        assert group.lastMessageId == 1


@pytest.mark.asyncio
async def test_process_groups_response_basic(database, mock_config, download_state):
    """Test basic group response processing."""
    # Create required accounts first
    async with database.async_session_scope() as session:
        account1 = Account(id=123, username="test_user1")
        account2 = Account(id=456, username="test_user2")
        session.add_all([account1, account2])
        await session.commit()

    response = {
        "data": [
            {
                "groupId": 1,
                "account_id": 123,
                "partnerAccountId": 456,
                "lastMessageId": 789,
            }
        ],
        "aggregationData": {"groups": [], "accounts": []},
    }

    # Process groups (this should store lastMessageId for later)
    await process_groups_response(mock_config, download_state, response)

    async with database.async_session_scope() as session:
        stmt = select(Group)
        result = await session.execute(stmt)
        group = result.scalar_one_or_none()
        assert group.id == 1
        # lastMessageId should be set even though message doesn't exist yet
        assert group.lastMessageId == 789

        # Now create the message
        message = Message(
            id=789, senderId=123, content="test", createdAt=datetime.now(timezone.utc)
        )
        session.add(message)
        await session.commit()

        # Process groups again to update lastMessageId
        await process_groups_response(mock_config, download_state, response)
        await session.commit()

        # Now lastMessageId should be set
        stmt = select(Group)
        result = await session.execute(stmt)
        group = result.scalar_one_or_none()
        assert group.lastMessageId == 789


@pytest.mark.asyncio
async def test_process_groups_response_with_users(
    database, mock_config, download_state
):
    """Test group response processing with user relationships."""
    # Create required accounts first
    async with database.async_session_scope() as session:
        account1 = Account(id=123, username="test_user1")
        account2 = Account(id=456, username="test_user2")
        session.add_all([account1, account2])
        await session.commit()

    response = {
        "data": [],
        "aggregationData": {
            "groups": [
                {
                    "id": 1,
                    "createdBy": 123,
                    "lastMessageId": 789,
                    "users": [{"userId": 123}, {"userId": 456}],
                }
            ],
            "accounts": [],
        },
    }

    await process_groups_response(mock_config, download_state, response)

    async with database.async_session_scope() as session:
        stmt = select(Group)
        result = await session.execute(stmt)
        group = result.scalar_one_or_none()
        assert group.id == 1
        assert len(group.users) == 2  # Users should be added since accounts exist


@pytest.mark.asyncio
async def test_process_groups_response_multiple_commits(
    database, mock_config, download_state
):
    """Test that multiple commits don't cause foreign key violations."""
    # Create required accounts first
    async with database.async_session_scope() as session:
        account = Account(id=123, username="test_user")
        session.add(account)
        await session.commit()

    response = {
        "data": [{"groupId": 1, "account_id": 123, "lastMessageId": 789}],
        "aggregationData": {
            "groups": [{"id": 2, "createdBy": 123, "lastMessageId": 790}],
            "accounts": [],
        },
    }

    # First process - should store lastMessageIds but not set them
    await process_groups_response(mock_config, download_state, response)

    async with database.async_session_scope() as session:
        # Create one message but not the other
        message = Message(
            id=789, senderId=123, content="test1", createdAt=datetime.now(timezone.utc)
        )
        session.add(message)
        await session.commit()

    # Process again - should only set lastMessageId for existing message
    await process_groups_response(mock_config, download_state, response)

    async with database.async_session_scope() as session:
        stmt1 = select(Group).where(Group.id == 1)
        result = await session.execute(stmt1)
        group1 = result.scalar_one_or_none()

        stmt2 = select(Group).where(Group.id == 2)
        result = await session.execute(stmt2)
        group2 = result.scalar_one_or_none()

        assert group1.lastMessageId == 789  # Message exists
        assert group2.lastMessageId is None  # Message doesn't exist yet
