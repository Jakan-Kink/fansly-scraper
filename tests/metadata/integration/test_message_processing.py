"""Integration tests for message processing functionality."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from metadata.account import AccountMedia, account_media_bundle_media
from metadata.messages import (
    Group,
    Message,
    process_groups_response,
    process_messages_metadata,
)
from tests.fixtures import setup_accounts_and_groups
from tests.fixtures.metadata.metadata_factories import AccountFactory


@pytest.fixture(scope="session")
def group_data(test_data_dir: str):
    """Load group messages test data."""
    json_file = Path(test_data_dir) / "messages-group.json"
    if not json_file.exists():
        pytest.skip(f"Test data file not found: {json_file}")
    with json_file.open() as f:
        return json.load(f)


@pytest.mark.asyncio
async def test_process_direct_messages(
    session: AsyncSession, session_sync, config, conversation_data
):
    """Test processing direct messages from conversation data.

    Uses direct account creation to avoid transaction isolation issues.
    """
    if not conversation_data.get("response", {}).get("messages"):
        pytest.skip("No messages found in conversation data")

    # Set up accounts and groups from conversation data
    messages_data = conversation_data["response"]["messages"]
    await setup_accounts_and_groups(session, conversation_data, messages_data)

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
    test_account = AccountFactory.build(id=999, username="test_user")
    session.add(test_account)
    await session.commit()

    # Create test message data with factory account
    test_message_data = {
        "id": 1,
        "senderId": test_account.id,
        "content": "Test message content",
        "createdAt": int(datetime.now(UTC).timestamp()),
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
    Tests must explicitly request factory_session or fixtures that depend on it.
    """
    # if not group_data.get("response", {}).get("data"):
    #     pytest.skip("No group data found in test data")

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

    # Verify lastMessageId was stored (message itself may not exist yet from this endpoint)
    if first_data.get("lastMessageId"):
        assert first_group.lastMessageId == int(first_data["lastMessageId"])
        # Note: The actual Message may not exist yet - this endpoint returns group metadata
        # with FK references before the messages are fetched from the conversation endpoint


@pytest.mark.asyncio
async def test_process_message_attachments(
    session: AsyncSession,
    factory_async_session,
    config,
    conversation_data,
):
    """Test processing messages with attachments.

    Uses centralized fixtures.
    factory_async_session configures factories with the database session for async tests.
    """
    messages_with_attachments = []

    # Look for messages with attachments in conversation data
    if conversation_data.get("response", {}).get("messages"):
        messages_with_attachments.extend(
            msg
            for msg in conversation_data["response"]["messages"]
            if msg.get("attachments")
        )

    if not messages_with_attachments:
        pytest.skip("No messages with attachments found in test data")

    # Set up accounts and groups from conversation data
    await setup_accounts_and_groups(
        session, conversation_data, messages_with_attachments
    )

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
            .where(
                Message.id == int(msg_data["id"])
            )  # Convert to int for bigint column
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
    factory_async_session,
    config,
    conversation_data,
):
    """Test processing messages with media variants like HLS/DASH streams.

    Uses centralized fixtures.
    factory_async_session configures factories with the database session for async tests.
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

    # if not messages_with_variants:
    #     pytest.skip("No messages with media variants found in test data")

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
            .where(
                Message.id == int(msg_data["id"])
            )  # Convert to int for bigint column
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
    factory_async_session,
    config,
    conversation_data,
):
    """Test processing messages with media bundles.

    Uses centralized fixtures.
    factory_async_session configures factories with the database session for async tests.
    """
    from metadata import process_media_info
    from metadata.account import process_media_bundles_data

    messages = conversation_data["response"]["messages"]
    bundles = conversation_data["response"].get("accountMediaBundles", [])

    if not bundles:
        pytest.skip("No media bundles found in test data")

    # Set up accounts and groups from conversation data
    await setup_accounts_and_groups(session, conversation_data, messages)

    # Commit accounts to ensure they're visible for FK checks
    await session.commit()

    # First process messages to create necessary relationships
    await process_messages_metadata(config, None, messages, session=session)

    # Commit messages to ensure clean transaction state
    await session.commit()

    # Process accountMedia items FIRST to create AccountMedia records
    account_media = conversation_data["response"].get("accountMedia", [])
    if account_media:
        await process_media_info(config, {"batch": account_media}, session=session)

    # Commit media to ensure clean transaction state
    await session.commit()

    # Then process the accountMediaBundles (which references the AccountMedia records)
    await process_media_bundles_data(
        config, conversation_data["response"], session=session
    )

    # Verify bundles were created and linked correctly
    session.expire_all()
    for bundle_data in bundles:
        # Check that all media in bundle exists and is properly ordered
        media_ids = [
            int(content["accountMediaId"]) for content in bundle_data["bundleContent"]
        ]
        # Use ORM to query the association table (columns are snake_case in DB)
        stmt = (
            select(account_media_bundle_media.c.media_id)
            .where(account_media_bundle_media.c.bundle_id == int(bundle_data["id"]))
            .order_by(account_media_bundle_media.c.pos)
        )
        result = await session.execute(stmt)
        stored_media_ids = [row[0] for row in result]
        assert stored_media_ids == media_ids


@pytest.mark.asyncio
async def test_process_message_permissions(
    session: AsyncSession,
    factory_async_session,
    config,
    conversation_data,
):
    """Test processing message media permissions.

    Uses centralized fixtures.
    factory_async_session configures factories with the database session for async tests.
    """
    from metadata import process_media_info

    messages = conversation_data["response"]["messages"]
    media_items = conversation_data["response"].get("accountMedia", [])

    if not media_items:
        pytest.skip("No media items found in test data")

    # Set up accounts and groups from conversation data
    await setup_accounts_and_groups(session, conversation_data, messages)

    # Commit accounts to ensure they're visible for FK checks
    await session.commit()

    await process_messages_metadata(config, None, messages, session=session)

    # Commit messages to ensure clean transaction state
    await session.commit()

    # Process accountMedia items to create AccountMedia records
    await process_media_info(config, {"batch": media_items}, session=session)

    # Commit media to ensure clean transaction state
    await session.commit()

    # Verify permission flags were processed correctly
    session.expire_all()
    for media_data in media_items:
        result = await session.execute(
            select(AccountMedia).where(
                AccountMedia.id
                == int(media_data["id"])  # Convert to int for bigint column
            )
        )
        stored_media = result.scalar_one_or_none()

        assert stored_media is not None
        # Verify permission flags match
        permission_flags = media_data["permissions"]["permissionFlags"]
        assert stored_media.permissionFlags == permission_flags[0]["flags"]
        # Verify access status
        assert stored_media.access == media_data["access"]
