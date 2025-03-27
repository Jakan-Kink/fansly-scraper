"""Unit tests for metadata.messages module."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from config import FanslyConfig
from metadata.account import Account
from metadata.attachment import Attachment, ContentType
from metadata.base import Base
from metadata.database import Database
from metadata.messages import Group, Message, group_users, process_messages_metadata


@pytest.fixture
def db_session():
    """Set up test database and session."""
    engine = create_engine("sqlite:///:memory:")
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

    message = Message(
        id=1,
        senderId=account1.id,
        recipientId=account2.id,
        content="Message with attachment",
        createdAt=datetime.now(timezone.utc),
    )
    session.add(message)
    session.flush()

    attachment = Attachment(
        contentId="test_content",
        messageId=1,
        contentType=ContentType.ACCOUNT_MEDIA,
        pos=1,
    )
    session.add(attachment)
    session.commit()
    # Pre-create a Message instance so that process_messages_metadata updates an existing message instead of using the class directly
    initial_msg = Message(
        id=1,
        senderId=account1.id,
        recipientId=account2.id,
        content="Initial Content",
        createdAt=int(datetime.now(timezone.utc).timestamp()),
    )
    session.add(initial_msg)
    session.commit()

    saved_message = session.execute(select(Message)).scalar_one_or_none()
    assert len(saved_message.attachments) == 1
    assert saved_message.attachments[0].contentType == ContentType.ACCOUNT_MEDIA


@pytest.mark.asyncio
async def test_process_messages_metadata(db_session):
    """Test processing message metadata."""
    session, account1, account2 = db_session

    # Create a mock config with the test session
    config = MagicMock()
    config._database = MagicMock()
    config._database.async_session = lambda: session

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

    await process_messages_metadata(config, None, messages_data)

    saved_message = session.execute(select(Message)).unique().scalar_one_or_none()
    assert saved_message.content == "Test message"
    assert len(saved_message.attachments) == 1
    assert saved_message.attachments[0].contentId == "test_content"
