"""Integration tests for message processing functionality."""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from api.fansly import FanslyApi
from metadata import (
    AccountMedia,
    Group,
    Media,
    Message,
    process_groups_response,
    process_media_info,
    process_messages_metadata,
)
from metadata.account import process_media_bundles_data
from tests.fixtures import setup_accounts_and_groups
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.fixture(scope="session")
def group_data(test_data_dir: str):
    """Load group messages test data."""
    json_file = Path(test_data_dir) / "messages-group.json"
    if not json_file.exists():
        pytest.skip(f"Test data file not found: {json_file}")
    with json_file.open() as f:
        return json.load(f)


@pytest.mark.asyncio
async def test_process_direct_messages(entity_store, mock_config):
    """Test processing direct messages with synthetic data."""
    # Create sender account first (FK constraint)
    from metadata import Account

    sender_id = snowflake_id()
    await entity_store.save(Account(id=sender_id, username="msg_sender"))

    msg_id = snowflake_id()
    message_data = {
        "id": msg_id,
        "senderId": sender_id,
        "content": "Test message content",
        "createdAt": int(datetime.now(UTC).timestamp()),
    }

    # Process via production code
    await process_messages_metadata(mock_config, None, {"messages": [message_data]})

    # Verify message was created
    message = await entity_store.get(Message, msg_id)
    assert message is not None
    assert message.content == "Test message content"
    assert message.senderId == sender_id
    assert message.createdAt is not None


@pytest.mark.asyncio
async def test_process_group_messages(entity_store, mock_config, group_data):
    """Test processing group messages."""
    response = FanslyApi.convert_ids_to_int(copy.deepcopy(group_data["response"]))

    # Process group data via production code
    await process_groups_response(mock_config, None, response)

    # Verify groups were created
    groups = await entity_store.find(Group)
    assert len(groups) > 0

    # Check first group
    first_data = response.get("data", [None])[0]
    if first_data and first_data.get("groupId"):
        group = await entity_store.get(Group, first_data["groupId"])
        assert group is not None


@pytest.mark.asyncio
async def test_process_message_attachments(
    entity_store, mock_config, conversation_data
):
    """Test processing messages with attachments."""
    response = FanslyApi.convert_ids_to_int(
        copy.deepcopy(conversation_data["response"])
    )
    messages = response.get("messages", [])

    messages_with_attachments = [msg for msg in messages if msg.get("attachments")]

    if not messages_with_attachments:
        pytest.skip("No messages with attachments found in test data")

    # Set up accounts and groups (uses entity_store via get_store())
    await setup_accounts_and_groups(conversation_data, messages_with_attachments)

    # Process messages via production code
    await process_messages_metadata(
        mock_config, None, {"messages": messages_with_attachments}
    )

    # Verify messages with attachments were created
    for msg_data in messages_with_attachments:
        msg_id = msg_data["id"]
        message = await entity_store.get(Message, msg_id)
        assert message is not None
        if msg_data.get("attachments"):
            assert len(message.attachments) == len(msg_data["attachments"])


@pytest.mark.asyncio
async def test_process_message_media_variants(
    entity_store, mock_config, conversation_data
):
    """Test processing messages with media variants like HLS/DASH streams."""
    response = FanslyApi.convert_ids_to_int(
        copy.deepcopy(conversation_data["response"])
    )
    messages = response.get("messages", [])
    media_items = response.get("accountMedia", [])

    # Set up accounts
    await setup_accounts_and_groups(conversation_data, messages)

    # Process messages
    await process_messages_metadata(mock_config, None, {"messages": messages})

    # Process accountMedia to create Media + variant records
    if media_items:
        await process_media_info(mock_config, {"batch": media_items})

    # Verify media with variants were processed
    for media_data in media_items:
        if media_data.get("media", {}).get("variants"):
            media_id = media_data["media"]["id"]
            media = await entity_store.get(Media, media_id)
            assert media is not None
            # Variants are stored as nested objects on the Media model
            if media.variants:
                assert len(media.variants) > 0


@pytest.mark.asyncio
async def test_process_message_media_bundles(
    entity_store, mock_config, conversation_data
):
    """Test processing messages with media bundles."""
    response = FanslyApi.convert_ids_to_int(
        copy.deepcopy(conversation_data["response"])
    )
    messages = response.get("messages", [])
    bundles = response.get("accountMediaBundles", [])

    if not bundles:
        pytest.skip("No media bundles found in test data")

    # Set up accounts and groups
    await setup_accounts_and_groups(conversation_data, messages)

    # Process messages first
    await process_messages_metadata(mock_config, None, response)

    # Process accountMedia to create Media + AccountMedia records
    account_media = response.get("accountMedia", [])
    if account_media:
        await process_media_info(mock_config, {"batch": account_media})

    # Process bundles
    await process_media_bundles_data(mock_config, response)

    # Verify bundles were created
    from metadata import AccountMediaBundle

    for bundle_data in bundles:
        bundle = await entity_store.get(AccountMediaBundle, bundle_data["id"])
        assert bundle is not None


@pytest.mark.asyncio
async def test_process_message_permissions(
    entity_store, mock_config, conversation_data
):
    """Test processing message media permissions."""
    response = FanslyApi.convert_ids_to_int(
        copy.deepcopy(conversation_data["response"])
    )
    messages = response.get("messages", [])
    media_items = response.get("accountMedia", [])

    if not media_items:
        pytest.skip("No media items found in test data")

    # Set up accounts and groups
    await setup_accounts_and_groups(conversation_data, messages)

    # Process messages
    await process_messages_metadata(mock_config, None, response)

    # Process accountMedia
    await process_media_info(mock_config, {"batch": media_items})

    # Verify AccountMedia records were created with access flags
    for media_data in media_items:
        am = await entity_store.get(AccountMedia, media_data["id"])
        assert am is not None
        if "access" in media_data:
            assert am.access == media_data["access"]
