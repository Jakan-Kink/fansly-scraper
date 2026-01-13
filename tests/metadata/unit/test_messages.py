"""Unit tests for metadata.messages module."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from metadata.attachment import ContentType
from metadata.messages import Group, Message, group_users, process_messages_metadata
from tests.fixtures import (
    AccountFactory,
    AttachmentFactory,
    MessageFactory,
    MetadataGroupFactory,
)


def test_direct_message_creation(session_sync: Session, factory_session):
    """Test creating a direct message between users.

    Uses AccountFactory and MessageFactory.
    Tests must explicitly request factory_session or fixtures that depend on it.
    """
    # Create test accounts using factories
    account1 = AccountFactory(id=1, username="sender")
    account2 = AccountFactory(id=2, username="recipient")

    # Create direct message using factory (no group)
    message = MessageFactory(
        id=1,
        groupId=None,  # Direct message has no group
        senderId=account1.id,
        recipientId=account2.id,
        content="Test message",
    )
    session_sync.commit()

    saved_message = session_sync.execute(select(Message)).scalar_one_or_none()
    assert saved_message is not None
    assert saved_message.content == "Test message"
    assert saved_message.senderId == account1.id
    assert saved_message.recipientId == account2.id
    assert saved_message.groupId is None


def test_group_creation(session_sync: Session, factory_session):
    """Test creating a message group.

    Uses AccountFactory and GroupFactory.
    Tests must explicitly request factory_session or fixtures that depend on it.
    """
    # Create test accounts using factories
    account1 = AccountFactory(id=1, username="sender")
    account2 = AccountFactory(id=2, username="recipient")

    # Create group using factory
    group = MetadataGroupFactory(id=1, createdBy=account1.id)
    session_sync.flush()

    # Add users to group
    session_sync.execute(
        group_users.insert().values(
            [
                {"groupId": 1, "accountId": account1.id},
                {"groupId": 1, "accountId": account2.id},
            ]
        )
    )
    session_sync.commit()

    saved_group = session_sync.execute(select(Group)).scalar_one_or_none()
    assert saved_group is not None
    assert saved_group.createdBy == account1.id
    assert len(saved_group.users) == 2
    user_ids = {u.id for u in saved_group.users}
    assert user_ids == {account1.id, account2.id}


def test_group_message(session_sync: Session, factory_session):
    """Test creating a message in a group.

    Uses AccountFactory, GroupFactory, and MessageFactory.
    Tests must explicitly request factory_session or fixtures that depend on it.
    """
    # Create test accounts using factories
    account1 = AccountFactory(id=1, username="sender")
    account1_id = account1.id

    # Create group using factory
    group = MetadataGroupFactory(id=1, createdBy=account1_id)
    group_id = group.id
    session_sync.flush()

    # Create message in group using factory
    message = MessageFactory(
        id=1,
        groupId=group_id,
        senderId=account1_id,
        content="Group message",
    )
    message_id = message.id
    session_sync.commit()

    # Update group's last message
    group.lastMessageId = message_id
    session_sync.commit()

    saved_group = session_sync.execute(select(Group)).scalar_one_or_none()
    assert saved_group is not None
    assert saved_group.lastMessageId == 1
    saved_message = session_sync.execute(select(Message)).scalar_one_or_none()
    assert saved_message is not None
    assert saved_message.groupId == 1
    assert saved_message.content == "Group message"


def test_message_with_attachment(session_sync: Session, factory_session):
    """Test message with an attachment.

    Uses AccountFactory, MessageFactory, and AttachmentFactory.
    Tests must explicitly request factory_session or fixtures that depend on it.
    """
    # Create test accounts using factories
    account1 = AccountFactory(id=1, username="sender")
    account2 = AccountFactory(id=2, username="recipient")
    account1_id = account1.id
    account2_id = account2.id

    # Create direct message using factory (no group)
    message = MessageFactory(
        id=1,
        groupId=None,  # Direct message has no group
        senderId=account1_id,
        recipientId=account2_id,
        content="Message with attachment",
    )
    message_id = message.id

    # Add attachment using factory (contentId must be integer, not string)
    AttachmentFactory(
        contentId=1001,
        messageId=message_id,
        contentType=ContentType.ACCOUNT_MEDIA,
        pos=1,
    )
    session_sync.commit()

    # Verify the message has the attachment
    saved_message = session_sync.execute(select(Message)).scalar_one_or_none()
    assert saved_message is not None
    assert saved_message.content == "Message with attachment"
    assert len(saved_message.attachments) == 1
    assert saved_message.attachments[0].contentType == ContentType.ACCOUNT_MEDIA
    assert saved_message.attachments[0].contentId == 1001


@pytest.mark.asyncio
async def test_process_messages_metadata(
    session: AsyncSession, session_sync, config, factory_session
):
    """Test processing message metadata.

    Uses AccountFactory and centralized config/session fixtures.
    Tests must explicitly request factory_session or fixtures that depend on it.
    """
    # Create test accounts using factories
    account1 = AccountFactory(id=1, username="sender")
    account2 = AccountFactory(id=2, username="recipient")
    account1_id = account1.id
    account2_id = account2.id
    session.expire_all()

    messages_data = [
        {
            "id": 1,
            "senderId": account1_id,
            "recipientId": account2_id,
            "content": "Test message",
            "createdAt": int(datetime.now(UTC).timestamp()),
            "attachments": [
                {
                    "contentId": 1001,  # Must be integer, not string
                    "contentType": ContentType.ACCOUNT_MEDIA.value,
                    "pos": 1,
                }
            ],
        }
    ]

    await process_messages_metadata(
        config, None, {"messages": messages_data}, session=session
    )
    await session.commit()

    # Verify the message was created with eager loading for attachments
    session.expire_all()
    result = await session.execute(
        select(Message)
        .options(selectinload(Message.attachments))
        .where(Message.id == 1)
    )
    saved_message = result.unique().scalar_one_or_none()
    assert saved_message is not None
    assert saved_message.content == "Test message"
    assert len(saved_message.attachments) == 1
    assert saved_message.attachments[0].contentId == 1001
