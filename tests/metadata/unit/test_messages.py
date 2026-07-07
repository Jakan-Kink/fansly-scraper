"""Unit tests for metadata.messages module."""

from datetime import UTC, datetime

import pytest
from pydantic import JsonValue

from download.core import DownloadState
from helpers.common import JsonDict
from metadata import ContentType, Group, Message, process_messages_metadata
from metadata.messages import _process_single_group, _process_single_message
from metadata.models import Account, Attachment
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.xdist_group("message_basics")
class TestMessageBasics:
    """Round-trip Message/Group/Attachment shapes on one shared store.

    Merges the former test_direct_message_creation, test_group_creation,
    test_group_message, and test_message_with_attachment into one deeper
    test sharing sender/recipient scaffolding (mirrors TestFullMessagePipeline).
    """

    @pytest.mark.asyncio
    async def test_message_and_group_shapes(self, entity_store):
        """Direct message, group-with-users, group message, message-with-attachment."""
        store = entity_store

        sender_id = snowflake_id()
        recipient_id = snowflake_id()

        account1 = Account(id=sender_id, username="sender")
        account2 = Account(id=recipient_id, username="recipient")
        await store.save(account1)
        await store.save(account2)

        # --- Direct message between users ---
        direct_msg_id = snowflake_id()
        direct = Message(
            id=direct_msg_id,
            senderId=sender_id,
            recipientId=recipient_id,
            content="Test message",
            createdAt=datetime.now(UTC),
        )
        await store.save(direct)

        saved_direct = await store.get(Message, direct_msg_id)
        assert saved_direct is not None
        assert saved_direct.content == "Test message"
        assert saved_direct.senderId == sender_id
        assert saved_direct.recipientId == recipient_id
        assert saved_direct.groupId is None

        # --- Group with users via relationship ---
        group_id = snowflake_id()
        group = Group(id=group_id, createdBy=sender_id)
        await store.save(group)

        group.users = [account1, account2]
        await store.save(group)

        saved_group = await store.get(Group, group_id)
        assert saved_group is not None
        assert saved_group.createdBy == sender_id
        assert len(saved_group.users) == 2
        user_ids = {u.id for u in saved_group.users}
        assert user_ids == {sender_id, recipient_id}

        # --- Message in the group, updating lastMessageId ---
        group_msg_id = snowflake_id()
        group_message = Message(
            id=group_msg_id,
            groupId=group_id,
            senderId=sender_id,
            content="Group message",
            createdAt=datetime.now(UTC),
        )
        await store.save(group_message)

        group.lastMessageId = group_msg_id
        await store.save(group)

        saved_group = await store.get(Group, group_id)
        assert saved_group is not None
        assert saved_group.lastMessageId == group_msg_id

        saved_group_message = await store.get(Message, group_msg_id)
        assert saved_group_message is not None
        assert saved_group_message.groupId == group_id
        assert saved_group_message.content == "Group message"

        # --- Message with an attachment ---
        attach_msg_id = snowflake_id()
        content_id = snowflake_id()
        attach_message = Message(
            id=attach_msg_id,
            senderId=sender_id,
            recipientId=recipient_id,
            content="Message with attachment",
            createdAt=datetime.now(UTC),
        )
        await store.save(attach_message)

        attach_message.attachments = [
            Attachment(
                contentId=content_id,
                messageId=attach_msg_id,
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=1,
            )
        ]
        await store.save(attach_message)

        saved_attach = await store.get(Message, attach_msg_id)
        assert saved_attach is not None
        assert saved_attach.content == "Message with attachment"
        assert len(saved_attach.attachments) == 1
        assert saved_attach.attachments[0].contentType == ContentType.ACCOUNT_MEDIA
        assert saved_attach.attachments[0].contentId == content_id


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

    messages_data: list[JsonValue] = [
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

    payload: JsonDict = {"messages": messages_data}
    await process_messages_metadata(config, DownloadState(), payload)

    saved = await store.get(Message, msg_id)
    assert saved is not None
    assert saved.content == "Test message"
    assert len(saved.attachments) == 1
    assert saved.attachments[0].contentId == content_id


class TestGroupUserResolution:
    """Cover metadata/messages.py 127→121, 129→133."""

    @pytest.mark.asyncio
    async def test_uncached_user_skipped(self, entity_store):
        """Line 127→121: userId valid but not in cache → skipped."""
        creator = Account(id=snowflake_id(), username="grp_nocache")
        await entity_store.save(creator)

        uncached_id = snowflake_id()
        result = await _process_single_group(
            {
                "id": snowflake_id(),
                "createdBy": creator.id,
                "users": [{"userId": uncached_id}],
            },
            "test",
        )
        assert result is not None
        assert all(u.id != uncached_id for u in result.users)

    @pytest.mark.asyncio
    async def test_empty_users_skips_save(self, entity_store):
        """Line 129→133: user_objs empty → skip group.users save."""
        creator = Account(id=snowflake_id(), username="grp_empty")
        await entity_store.save(creator)

        result = await _process_single_group(
            {
                "id": snowflake_id(),
                "createdBy": creator.id,
                "users": [],
            },
            "test",
        )
        assert result is not None


class TestFullMessagePipeline:
    @pytest.mark.asyncio
    async def test_messages_as_list_and_missing_fields(self, entity_store, mock_config):
        """Messages: list input (isinstance branch), valid message, missing fields."""
        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"msg_{acct_id}"))

        msg_id = snowflake_id()
        messages: list[JsonDict] = [
            {
                "id": msg_id,
                "senderId": acct_id,
                "content": "Hello #msg",
                "createdAt": 1700000000,
                "deleted": False,
                "attachments": [
                    {
                        "id": snowflake_id(),
                        "contentId": snowflake_id(),
                        "contentType": 1,
                        "pos": 0,
                    }
                ],
            },
        ]
        state = DownloadState()
        state.creator_id = acct_id
        await process_messages_metadata(
            mock_config, state, messages
        )  # list → isinstance branch

        msg = await entity_store.get(Message, msg_id)
        assert msg is not None
        assert len(msg.attachments) == 1

        # Missing fields
        assert await _process_single_message({"content": "no id"}) is None

    @pytest.mark.asyncio
    async def test_group_processing(self, entity_store):
        """Exercise all group processing paths: missing fields, existing creator
        (hits relationship_logger exists=True branch), lastMessage dict resolution,
        user resolution with dict/int/empty formats, lastMessageId for existing message."""
        # Missing id / createdBy → None
        assert (
            await _process_single_group({"createdBy": snowflake_id()}, "test") is None
        )
        assert await _process_single_group({"id": snowflake_id()}, "test") is None

        # Full group with existing creator (→ relationship_logger exists=True)
        creator = snowflake_id()
        user2 = snowflake_id()
        await entity_store.save(Account(id=creator, username=f"grp_{creator}"))
        await entity_store.save(Account(id=user2, username=f"grp_user_{user2}"))

        msg_id = snowflake_id()
        await entity_store.save(
            Message(
                id=msg_id,
                senderId=creator,
                content="last msg",
                createdAt=datetime.now(UTC),
                deleted=False,
            )
        )

        gid = snowflake_id()
        result = await _process_single_group(
            {
                "id": gid,
                "createdBy": creator,
                "createdAt": 1700000000,
                "lastMessage": {"id": msg_id},
                "lastMessageId": msg_id,
                "users": [
                    {"userId": creator},  # dict format
                    {"userId": str(user2)},  # string userId → int coercion
                    {"userId": None},  # empty userId → skip
                    user2,  # bare int format
                ],
            },
            "test",
        )
        assert result is not None
        assert result.lastMessageId == msg_id
        # Users resolved from cache
        assert len(result.users) >= 1
