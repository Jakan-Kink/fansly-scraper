"""Unit tests for metadata.messages module."""

from datetime import UTC, datetime

import pytest

from metadata import ContentType, Group, Message, process_messages_metadata
from metadata.models import Account, Attachment
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_direct_message_creation(entity_store):
    """Test creating a direct message between users."""
    store = entity_store

    sender_id = snowflake_id()
    recipient_id = snowflake_id()
    msg_id = snowflake_id()

    account1 = Account(id=sender_id, username="sender")
    account2 = Account(id=recipient_id, username="recipient")
    await store.save(account1)
    await store.save(account2)

    message = Message(
        id=msg_id,
        senderId=sender_id,
        recipientId=recipient_id,
        content="Test message",
        createdAt=datetime.now(UTC),
    )
    await store.save(message)

    saved = await store.get(Message, msg_id)
    assert saved is not None
    assert saved.content == "Test message"
    assert saved.senderId == sender_id
    assert saved.recipientId == recipient_id
    assert saved.groupId is None


@pytest.mark.asyncio
async def test_group_creation(entity_store):
    """Test creating a message group."""
    store = entity_store

    sender_id = snowflake_id()
    recipient_id = snowflake_id()
    group_id = snowflake_id()

    account1 = Account(id=sender_id, username="sender")
    account2 = Account(id=recipient_id, username="recipient")
    await store.save(account1)
    await store.save(account2)

    group = Group(id=group_id, createdBy=sender_id)
    await store.save(group)

    # Add users via relationship
    group.users = [account1, account2]
    await store.save(group)

    saved = await store.get(Group, group_id)
    assert saved is not None
    assert saved.createdBy == sender_id
    assert len(saved.users) == 2
    user_ids = {u.id for u in saved.users}
    assert user_ids == {sender_id, recipient_id}


@pytest.mark.asyncio
async def test_group_message(entity_store):
    """Test creating a message in a group."""
    store = entity_store

    sender_id = snowflake_id()
    group_id = snowflake_id()
    msg_id = snowflake_id()

    account1 = Account(id=sender_id, username="sender")
    await store.save(account1)

    group = Group(id=group_id, createdBy=sender_id)
    await store.save(group)

    message = Message(
        id=msg_id,
        groupId=group_id,
        senderId=sender_id,
        content="Group message",
        createdAt=datetime.now(UTC),
    )
    await store.save(message)

    # Update group's lastMessageId
    group.lastMessageId = msg_id
    await store.save(group)

    saved_group = await store.get(Group, group_id)
    assert saved_group is not None
    assert saved_group.lastMessageId == msg_id

    saved_message = await store.get(Message, msg_id)
    assert saved_message is not None
    assert saved_message.groupId == group_id
    assert saved_message.content == "Group message"


@pytest.mark.asyncio
async def test_message_with_attachment(entity_store):
    """Test message with an attachment."""
    store = entity_store

    sender_id = snowflake_id()
    recipient_id = snowflake_id()
    msg_id = snowflake_id()
    content_id = snowflake_id()

    account1 = Account(id=sender_id, username="sender")
    account2 = Account(id=recipient_id, username="recipient")
    await store.save(account1)
    await store.save(account2)

    # Create message first (FK for attachments)
    message = Message(
        id=msg_id,
        senderId=sender_id,
        recipientId=recipient_id,
        content="Message with attachment",
        createdAt=datetime.now(UTC),
    )
    await store.save(message)

    # Add attachment and re-save
    message.attachments = [
        Attachment(
            contentId=content_id,
            messageId=msg_id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=1,
        )
    ]
    await store.save(message)

    saved = await store.get(Message, msg_id)
    assert saved is not None
    assert saved.content == "Message with attachment"
    assert len(saved.attachments) == 1
    assert saved.attachments[0].contentType == ContentType.ACCOUNT_MEDIA
    assert saved.attachments[0].contentId == content_id


@pytest.mark.asyncio
async def test_process_messages_metadata(entity_store, config):
    """Test processing message metadata.

    process_messages_metadata uses get_store() internally, wired via entity_store.
    """
    store = entity_store

    sender_id = snowflake_id()
    recipient_id = snowflake_id()
    msg_id = snowflake_id()
    content_id = snowflake_id()

    account1 = Account(id=sender_id, username="sender")
    account2 = Account(id=recipient_id, username="recipient")
    await store.save(account1)
    await store.save(account2)

    messages_data = [
        {
            "id": msg_id,
            "senderId": sender_id,
            "recipientId": recipient_id,
            "content": "Test message",
            "createdAt": int(datetime.now(UTC).timestamp()),
            "attachments": [
                {
                    "contentId": content_id,
                    "contentType": ContentType.ACCOUNT_MEDIA.value,
                    "pos": 1,
                }
            ],
        }
    ]

    await process_messages_metadata(config, None, {"messages": messages_data})

    saved = await store.get(Message, msg_id)
    assert saved is not None
    assert saved.content == "Test message"
    assert len(saved.attachments) == 1
    assert saved.attachments[0].contentId == content_id
