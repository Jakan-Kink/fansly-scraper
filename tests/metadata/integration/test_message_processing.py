"""Integration tests for message processing functionality."""

import json
import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from metadata.account import Account
from metadata.messages import (
    Group,
    Message,
    process_groups_response,
    process_messages_metadata,
)
from tests.fixtures import AccountFactory


@pytest.fixture(scope="session")
def group_data(test_data_dir: str):
    """Load group messages test data."""
    with open(os.path.join(test_data_dir, "messages-group.json")) as f:
        return json.load(f)


@pytest.mark.asyncio
async def test_process_direct_messages(
    session: AsyncSession, session_sync, config, conversation_data
):
    """Test processing direct messages from conversation data.

    Uses AccountFactory and centralized fixtures.
    factory_session is autouse=True so it's automatically applied.
    """
    if not conversation_data.get("response", {}).get("messages"):
        pytest.skip("No messages found in conversation data")

    # Extract unique account IDs from conversation data and create accounts
    messages_data = conversation_data["response"]["messages"]
    account_data = conversation_data["response"].get("accounts", [])

    # Create accounts from conversation data
    for acc_data in account_data:
        AccountFactory(
            id=acc_data["id"],
            username=acc_data.get("username", f"user_{acc_data['id']}"),
        )

    session.expire_all()

    # Process messages
    await process_messages_metadata(config, None, messages_data, session=session)

    # Verify messages were created
    session.expire_all()
    result = await session.execute(select(Message))
    messages_list = result.scalars().all()
    initial_count = len(messages_list)
    assert initial_count > 0

    # Clear existing messages for clean test
    for msg in messages_list:
        await session.delete(msg)
    await session.commit()

    # Create a test account for the simple message test
    test_account = AccountFactory(id=999, username="test_user")
    session.expire_all()

    # Create test message data with factory account
    test_message_data = {
        "id": 1,
        "senderId": test_account.id,
        "content": "Test message content",
        "createdAt": int(datetime.now(timezone.utc).timestamp()),
    }

    # Process test message
    await process_messages_metadata(config, None, [test_message_data], session=session)

    # Verify message was created using ORM
    session.expire_all()
    result = await session.execute(select(Message).where(Message.id == 1))
    message = result.scalar_one()

    assert message.content == test_message_data["content"]
    assert message.senderId == test_message_data["senderId"]
    assert message.createdAt is not None
    assert not message.deleted
    assert message.deletedAt is None

    # Check attachments if present
    if "attachments" in test_message_data:
        assert len(message.attachments) == len(test_message_data["attachments"])


@pytest.mark.asyncio
async def test_process_group_messages(
    session: AsyncSession, session_sync, config, group_data
):
    """Test processing group messages.

    Uses centralized fixtures.
    factory_session is autouse=True so it's automatically applied.
    """
    if not group_data.get("response", {}).get("data"):
        pytest.skip("No group data found in test data")

    # Process group data
    await process_groups_response(config, None, group_data["response"], session=session)

    # Verify groups were created using ORM
    session.expire_all()
    result = await session.execute(select(Group))
    groups = result.scalars().all()
    assert len(groups) > 0

    # Check first group
    first_group = groups[0]
    first_data = group_data["response"]["data"][0]

    # Verify group members
    if "users" in first_data:
        assert len(first_group.users) == len(first_data["users"])

    # Verify last message if present
    if first_group.lastMessageId:
        result = await session.execute(
            select(Message).where(Message.id == first_group.lastMessageId)
        )
        last_message = result.scalar_one_or_none()
        assert last_message is not None
        assert last_message.groupId == first_group.id


@pytest.mark.asyncio
async def test_process_message_attachments(
    session: AsyncSession,
    session_sync,
    config,
    conversation_data,
):
    """Test processing messages with attachments.

    Uses centralized fixtures.
    factory_session is autouse=True so it's automatically applied.
    """
    messages_with_attachments = []

    # Look for messages with attachments in conversation data
    if conversation_data.get("response", {}).get("messages"):
        for msg in conversation_data["response"]["messages"]:
            if msg.get("attachments"):
                messages_with_attachments.append(msg)

    if not messages_with_attachments:
        pytest.skip("No messages with attachments found in test data")

    # Create accounts from conversation data
    account_data = conversation_data["response"].get("accounts", [])
    for acc_data in account_data:
        AccountFactory(
            id=acc_data["id"],
            username=acc_data.get("username", f"user_{acc_data['id']}"),
        )
    session.expire_all()

    # Process messages
    await process_messages_metadata(
        config, None, messages_with_attachments, session=session
    )

    # Verify attachments were created
    session.expire_all()
    for msg_data in messages_with_attachments:
        # Use ORM to get message with relationships loaded
        stmt = (
            select(Message)
            .options(selectinload(Message.attachments))
            .where(Message.id == msg_data["id"])
        )

        result = await session.execute(stmt)
        message = result.unique().scalar_one()

        assert message is not None
        # Now attachments is accessed as a relationship property that's already loaded
        assert len(message.attachments) == len(msg_data["attachments"])

        # Verify attachment content IDs match
        attachment_ids = {str(a.contentId) for a in message.attachments}
        expected_ids = {str(a["contentId"]) for a in msg_data["attachments"]}
        assert attachment_ids == expected_ids


@pytest.mark.asyncio
async def test_process_message_media_variants(
    session: AsyncSession,
    session_sync,
    config,
    conversation_data,
):
    """Test processing messages with media variants like HLS/DASH streams.

    Uses centralized fixtures.
    factory_session is autouse=True so it's automatically applied.
    """
    messages = conversation_data["response"]["messages"]
    messages_with_variants = [
        msg
        for msg in messages
        if any(
            am.get("media", {}).get("variants", [])
            for am in conversation_data["response"].get("accountMedia", [])
            if any(
                att["contentId"] == am["mediaId"] for att in msg.get("attachments", [])
            )
        )
    ]

    if not messages_with_variants:
        pytest.skip("No messages with media variants found in test data")

    # Create accounts from conversation data
    account_data = conversation_data["response"].get("accounts", [])
    for acc_data in account_data:
        AccountFactory(
            id=acc_data["id"],
            username=acc_data.get("username", f"user_{acc_data['id']}"),
        )
    session.expire_all()

    await process_messages_metadata(
        config, None, messages_with_variants, session=session
    )

    # Verify media variants were processed correctly
    session.expire_all()
    for msg_data in messages_with_variants:
        stmt = (
            select(Message)
            .options(selectinload(Message.attachments))
            .where(Message.id == msg_data["id"])
        )
        result = await session.execute(stmt)
        message = result.unique().scalar_one()

        for attachment in message.attachments:
            if hasattr(attachment, "media") and attachment.media:
                assert attachment.media.variants is not None
                # Verify HLS/DASH variants exist
                variants = [
                    v for v in attachment.media.variants if v.type in (302, 303)
                ]
                assert len(variants) > 0
                # Verify variant metadata
                for variant in variants:
                    metadata = json.loads(variant.metadata)
                    assert "duration" in metadata
                    assert "frameRate" in metadata
                    assert "variants" in metadata


@pytest.mark.asyncio
async def test_process_message_media_bundles(
    session: AsyncSession,
    session_sync,
    config,
    conversation_data,
):
    """Test processing messages with media bundles.

    Uses centralized fixtures.
    factory_session is autouse=True so it's automatically applied.
    """
    messages = conversation_data["response"]["messages"]
    bundles = conversation_data["response"].get("accountMediaBundles", [])

    if not bundles:
        pytest.skip("No media bundles found in test data")

    # Create accounts from conversation data
    account_data = conversation_data["response"].get("accounts", [])
    for acc_data in account_data:
        AccountFactory(
            id=acc_data["id"],
            username=acc_data.get("username", f"user_{acc_data['id']}"),
        )
    session.expire_all()

    # First process messages to create necessary relationships
    await process_messages_metadata(config, None, messages, session=session)

    # Verify bundles were created and linked correctly
    session.expire_all()
    for bundle_data in bundles:
        # Check that all media in bundle exists and is properly ordered
        media_ids = [
            content["accountMediaId"] for content in bundle_data["bundleContent"]
        ]
        # Use ORM to query the association table
        from metadata.account import account_media_bundle_media

        stmt = (
            select(account_media_bundle_media.c.accountMediaId)
            .where(account_media_bundle_media.c.bundleId == bundle_data["id"])
            .order_by(account_media_bundle_media.c.pos)
        )
        result = await session.execute(stmt)
        stored_media_ids = [row[0] for row in result]
        assert stored_media_ids == media_ids


@pytest.mark.asyncio
async def test_process_message_permissions(
    session: AsyncSession,
    session_sync,
    config,
    conversation_data,
):
    """Test processing message media permissions.

    Uses centralized fixtures.
    factory_session is autouse=True so it's automatically applied.
    """
    messages = conversation_data["response"]["messages"]
    media_items = conversation_data["response"].get("accountMedia", [])

    if not media_items:
        pytest.skip("No media items found in test data")

    # Create accounts from conversation data
    account_data = conversation_data["response"].get("accounts", [])
    for acc_data in account_data:
        AccountFactory(
            id=acc_data["id"],
            username=acc_data.get("username", f"user_{acc_data['id']}"),
        )
    session.expire_all()

    await process_messages_metadata(config, None, messages, session=session)

    # Verify permission flags were processed correctly
    session.expire_all()
    for media_data in media_items:
        from metadata.account import AccountMedia

        result = await session.execute(
            select(AccountMedia).where(AccountMedia.id == media_data["id"])
        )
        stored_media = result.scalar_one_or_none()

        assert stored_media is not None
        # Verify permission flags match
        permission_flags = media_data["permissions"]["permissionFlags"]
        assert stored_media.permissionFlags == permission_flags[0]["flags"]
        # Verify access status
        assert stored_media.access == media_data["access"]
