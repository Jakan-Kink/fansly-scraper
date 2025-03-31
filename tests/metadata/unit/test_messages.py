"""Unit tests for metadata.messages module."""

import asyncio
import re
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from config import FanslyConfig
from metadata.account import Account
from metadata.attachment import Attachment, ContentType
from metadata.base import Base
from metadata.database import Database
from metadata.messages import Group, Message, group_users, process_messages_metadata


@pytest.fixture
def db_session(request):
    """Set up test database and session with a unique in-memory database per test."""
    # Create a unique database name based on the test name
    test_name = request.node.name.replace("[", "_").replace("]", "_")
    db_name = f"test_messages_{test_name}_{id(request)}"
    # Use URI format for in-memory database to ensure thread safety
    db_uri = f"sqlite:///file:{db_name}?mode=memory&cache=shared&uri=true"

    engine = create_engine(db_uri, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Create test accounts
    account1 = Account(id=1, username="sender")
    account2 = Account(id=2, username="recipient")
    session.add_all([account1, account2])
    session.commit()

    yield session, account1, account2

    # Cleanup
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_direct_message_creation(db_session):
    """Test creating a direct message between users."""
    session, account1, account2 = db_session

    message = Message(
        id=1,
        senderId=account1.id,
        recipientId=account2.id,
        content="Test message",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(message)
    session.commit()

    saved_message = session.execute(select(Message)).scalar_one_or_none()
    assert saved_message.content == "Test message"
    assert saved_message.senderId == account1.id
    assert saved_message.recipientId == account2.id
    assert saved_message.groupId is None


def test_group_creation(db_session):
    """Test creating a message group."""
    session, account1, account2 = db_session

    group = Group(id=1, createdBy=account1.id)
    session.add(group)
    session.flush()

    # Add users to group
    session.execute(
        group_users.insert().values(
            [
                {"groupId": 1, "accountId": account1.id},
                {"groupId": 1, "accountId": account2.id},
            ]
        )
    )
    session.commit()

    saved_group = session.execute(select(Group)).scalar_one_or_none()
    assert saved_group.createdBy == account1.id
    assert len(saved_group.users) == 2
    user_ids = {u.id for u in saved_group.users}
    assert user_ids == {account1.id, account2.id}


def test_group_message(db_session):
    """Test creating a message in a group."""
    session, account1, account2 = db_session

    # Create group
    group = Group(id=1, createdBy=account1.id)
    session.add(group)
    session.flush()

    # Add message to group
    message = Message(
        id=1,
        groupId=1,
        senderId=account1.id,
        content="Group message",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(message)
    session.commit()

    # Update group's last message
    group.lastMessageId = message.id
    session.commit()

    saved_group = session.execute(select(Group)).scalar_one_or_none()
    assert saved_group.lastMessageId == 1
    saved_message = session.execute(select(Message)).scalar_one_or_none()
    assert saved_message.groupId == 1
    assert saved_message.content == "Group message"


def test_message_with_attachment(db_session):
    """Test message with an attachment."""
    session, account1, account2 = db_session

    # Create a message with attachment
    message = Message(
        id=1,
        senderId=account1.id,
        recipientId=account2.id,
        content="Message with attachment",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(message)
    session.flush()

    # Add attachment to the message
    attachment = Attachment(
        contentId="test_content",
        messageId=1,
        contentType=ContentType.ACCOUNT_MEDIA,
        pos=1,
    )
    session.add(attachment)
    session.commit()

    # Verify the message has the attachment
    saved_message = session.execute(select(Message)).scalar_one_or_none()
    assert saved_message.content == "Message with attachment"
    assert len(saved_message.attachments) == 1
    assert saved_message.attachments[0].contentType == ContentType.ACCOUNT_MEDIA
    assert saved_message.attachments[0].contentId == "test_content"


@pytest.mark.asyncio
async def test_process_messages_metadata(db_session):
    """Test processing message metadata."""
    session, account1, account2 = db_session

    # Since we need an async session but have a sync one, we need to create a proper
    # async session that works with the same database

    # Extract the database path from the sync session
    engine = session.get_bind()
    url = str(engine.url)

    # Create an async engine pointing to the same database - use replace for simplicity
    async_url = url.replace("sqlite://", "sqlite+aiosqlite://")
    async_engine = create_async_engine(async_url)

    # Create the async session
    async_session_factory = async_sessionmaker(
        bind=async_engine, expire_on_commit=False
    )
    async_session = async_session_factory()

    try:
        # Create a mock config that returns our async session
        config = MagicMock()
        config._database = MagicMock()
        config._database.async_session = lambda: async_session

        messages_data = [
            {
                "id": 1,
                "senderId": account1.id,
                "recipientId": account2.id,
                "content": "Test message",
                "createdAt": int(datetime.now(timezone.utc).timestamp()),
                "attachments": [
                    {
                        "contentId": "test_content",
                        "contentType": ContentType.ACCOUNT_MEDIA.value,
                        "pos": 1,
                    }
                ],
            }
        ]

        await process_messages_metadata(
            config, None, messages_data, session=async_session
        )
        await async_session.commit()

        # Use the sync session to verify the results since we already have it set up with data
        saved_message = session.execute(select(Message)).unique().scalar_one_or_none()
        assert saved_message.content == "Test message"
        assert len(saved_message.attachments) == 1
        assert saved_message.attachments[0].contentId == "test_content"
    finally:
        await async_session.close()
        await async_engine.dispose()
