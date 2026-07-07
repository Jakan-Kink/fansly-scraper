"""Integration tests for message processing functionality."""

import copy
from datetime import UTC, datetime

import pytest

from api.fansly import FanslyApi
from download.downloadstate import DownloadState
from helpers.common import JsonDict
from metadata import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Group,
    Media,
    Message,
    process_groups_response,
    process_media_info,
    process_messages_metadata,
)
from metadata.account import process_media_bundles_data
from tests.fixtures.metadata.metadata_factories import setup_accounts_and_groups
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_process_direct_messages(entity_store, mock_config):
    """Test processing direct messages with synthetic data."""
    # Create sender account first (FK constraint)

    sender_id = snowflake_id()
    await entity_store.save(Account(id=sender_id, username="msg_sender"))

    msg_id = snowflake_id()
    message_data: JsonDict = {
        "id": msg_id,
        "senderId": sender_id,
        "content": "Test message content",
        "createdAt": int(datetime.now(UTC).timestamp()),
    }

    # Process via production code
    await process_messages_metadata(
        mock_config, DownloadState(), {"messages": [message_data]}
    )

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
    assert isinstance(response, dict)

    # Process group data via production code
    await process_groups_response(mock_config, DownloadState(), response)

    # Verify groups were created
    groups = await entity_store.find(Group)
    assert len(groups) > 0

    # Check first group
    data_list = response.get("data", [None])
    assert isinstance(data_list, list)
    first_data = data_list[0]
    if isinstance(first_data, dict) and first_data.get("groupId"):
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
    assert isinstance(response, dict)
    messages = response.get("messages", [])
    assert isinstance(messages, list)

    messages_with_attachments = [
        msg for msg in messages if isinstance(msg, dict) and msg.get("attachments")
    ]

    if not messages_with_attachments:
        pytest.skip("No messages with attachments found in test data")

    # Set up accounts and groups (uses entity_store via get_store())
    await setup_accounts_and_groups(conversation_data, messages_with_attachments)

    # Process messages via production code
    await process_messages_metadata(
        mock_config, DownloadState(), messages_with_attachments
    )

    # Verify messages with attachments were created
    for msg_data in messages_with_attachments:
        msg_id = msg_data["id"]
        message = await entity_store.get(Message, msg_id)
        assert message is not None
        attachments = msg_data.get("attachments")
        if attachments:
            assert isinstance(attachments, list)
            assert len(message.attachments) == len(attachments)


@pytest.mark.xdist_group("message_processing")
class TestMessageMediaIngestion:
    """Single-ingest message-processing checks (shared mutable DB)."""

    @pytest.mark.asyncio
    async def test_process_message_media_full(
        self, entity_store, mock_config, conversation_data
    ):
        """Ingest conversation_data once, then assert variants, bundles, flags.

        Runs the shared message-processing prologue (messages + accountMedia +
        bundles) a single time via the production message-processing entry
        points, then verifies three facets in sequence: media variants
        (HLS/DASH), AccountMediaBundle records, and AccountMedia access flags.
        """
        response = FanslyApi.convert_ids_to_int(
            copy.deepcopy(conversation_data["response"])
        )
        assert isinstance(response, dict)
        messages = response.get("messages", [])
        assert isinstance(messages, list)
        media_items = response.get("accountMedia", [])
        assert isinstance(media_items, list)
        bundles = response.get("accountMediaBundles", [])
        assert isinstance(bundles, list)
        message_dicts: list[dict] = [m for m in messages if isinstance(m, dict)]

        # Set up accounts and groups
        await setup_accounts_and_groups(conversation_data, message_dicts)

        # Process messages via production message-processing entry point
        await process_messages_metadata(mock_config, DownloadState(), response)

        # Process accountMedia to create Media + variant + AccountMedia records
        if media_items:
            await process_media_info(mock_config, {"batch": media_items})

        # Process bundles
        await process_media_bundles_data(mock_config, response)

        # Facet 1: media with HLS/DASH variants
        for media_data in media_items:
            assert isinstance(media_data, dict)
            media_obj = media_data.get("media", {})
            assert isinstance(media_obj, dict)
            if media_obj.get("variants"):
                media_id = media_obj["id"]
                media = await entity_store.get(Media, media_id)
                assert media is not None
                # Variants are stored as nested objects on the Media model
                if media.variants:
                    assert len(media.variants) > 0

        # Facet 2: media bundles. The original bundles test skipped the whole
        # test when no bundles were present; converted to an inline conditional
        # so the variant/permission facets still run regardless.
        if bundles:
            for bundle_data in bundles:
                assert isinstance(bundle_data, dict)
                bundle = await entity_store.get(AccountMediaBundle, bundle_data["id"])
                assert bundle is not None

        # Facet 3: AccountMedia records created with access flags. The original
        # permissions test skipped the whole test when no media items were
        # present; converted to an inline conditional.
        if media_items:
            for media_data in media_items:
                assert isinstance(media_data, dict)
                am = await entity_store.get(AccountMedia, media_data["id"])
                assert am is not None
                if "access" in media_data:
                    assert am.access == media_data["access"]
