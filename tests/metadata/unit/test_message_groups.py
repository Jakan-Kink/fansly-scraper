"""Unit tests for message group functionality."""

from datetime import UTC, datetime

import pytest

from download.core import DownloadState
from metadata import Account, Group, Message, process_groups_response
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.fixture
def download_state():
    """Create a test download state."""
    return DownloadState(creator_name="test_creator")


@pytest.mark.asyncio
async def test_group_creation(entity_store):
    """Test basic group creation."""
    store = entity_store

    account_id = snowflake_id()
    group_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    group = Group(id=group_id, createdBy=account_id)
    await store.save(group)

    saved = await store.get(Group, group_id)
    assert saved is not None
    assert saved.id == group_id
    assert saved.createdBy == account_id
    assert saved.lastMessageId is None


@pytest.mark.asyncio
async def test_message_creation(entity_store):
    """Test basic message creation."""
    store = entity_store

    account_id = snowflake_id()
    msg_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    message = Message(
        id=msg_id, senderId=account_id, content="test", createdAt=datetime.now(UTC)
    )
    await store.save(message)

    saved = await store.get(Message, msg_id)
    assert saved is not None
    assert saved.id == msg_id
    assert saved.senderId == account_id
    assert saved.content == "test"


@pytest.mark.asyncio
async def test_group_message_relationship(entity_store):
    """Test relationship between groups and messages."""
    store = entity_store

    account_id = snowflake_id()
    msg_id = snowflake_id()
    group_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    message = Message(
        id=msg_id, senderId=account_id, content="test", createdAt=datetime.now(UTC)
    )
    await store.save(message)

    group = Group(id=group_id, createdBy=account_id, lastMessageId=msg_id)
    await store.save(group)

    saved = await store.get(Group, group_id)
    assert saved is not None
    assert saved.lastMessageId == msg_id


@pytest.mark.asyncio
async def test_process_groups_response_basic(entity_store, config, download_state):
    """Test basic group response processing.

    process_groups_response uses get_store() internally, wired via entity_store.
    """
    store = entity_store

    account_id1 = snowflake_id()
    account_id2 = snowflake_id()
    group_id = snowflake_id()
    last_msg_id = snowflake_id()

    account1 = Account(id=account_id1, username="test_user1")
    account2 = Account(id=account_id2, username="test_user2")
    await store.save(account1)
    await store.save(account2)

    response = {
        "data": [
            {
                "groupId": group_id,
                "account_id": account_id1,
                "partnerAccountId": account_id2,
                "lastMessageId": last_msg_id,
            }
        ],
        "aggregationData": {"groups": [], "accounts": []},
    }

    await process_groups_response(config, download_state, response)

    saved = await store.get(Group, group_id)
    assert saved is not None
    assert saved.id == group_id
    # lastMessageId stripped — message doesn't exist in the store
    assert saved.lastMessageId is None


@pytest.mark.asyncio
async def test_process_groups_response_with_users(entity_store, config, download_state):
    """Test group response processing with user relationships."""
    store = entity_store

    account_id1 = snowflake_id()
    account_id2 = snowflake_id()
    group_id = snowflake_id()
    last_msg_id = snowflake_id()

    account1 = Account(id=account_id1, username="test_user1")
    account2 = Account(id=account_id2, username="test_user2")
    await store.save(account1)
    await store.save(account2)

    response = {
        "data": [],
        "aggregationData": {
            "groups": [
                {
                    "id": group_id,
                    "createdBy": account_id1,
                    "lastMessageId": last_msg_id,
                    "users": [{"userId": account_id1}, {"userId": account_id2}],
                }
            ],
            "accounts": [],
        },
    }

    await process_groups_response(config, download_state, response)

    saved = await store.get(Group, group_id)
    assert saved is not None
    assert saved.id == group_id

    # Verify users via raw query on group_users junction
    pool = await store._get_pool()
    rows = await pool.fetch(
        'SELECT "accountId" FROM group_users WHERE "groupId" = $1', group_id
    )
    user_ids = sorted([row["accountId"] for row in rows])
    assert user_ids == sorted([account_id1, account_id2])


@pytest.mark.asyncio
async def test_process_groups_response_multiple_commits(
    entity_store, config, download_state
):
    """Test that multiple process calls don't cause foreign key violations."""
    store = entity_store

    account_id = snowflake_id()
    group_id1 = snowflake_id()
    group_id2 = snowflake_id()
    msg_id1 = snowflake_id()
    msg_id2 = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    response = {
        "data": [
            {"groupId": group_id1, "account_id": account_id, "lastMessageId": msg_id1}
        ],
        "aggregationData": {
            "groups": [
                {"id": group_id2, "createdBy": account_id, "lastMessageId": msg_id2}
            ],
            "accounts": [],
        },
    }

    # First process
    await process_groups_response(config, download_state, response)

    # Create one message
    message = Message(
        id=msg_id1, senderId=account_id, content="test1", createdAt=datetime.now(UTC)
    )
    await store.save(message)

    # Process again
    await process_groups_response(config, download_state, response)

    group1 = await store.get(Group, group_id1)
    group2 = await store.get(Group, group_id2)

    assert group1 is not None
    assert group1.lastMessageId == msg_id1

    # For aggregation_groups source, missing lastMessageId is stripped
    # On second pass, message msg_id2 still doesn't exist → stripped again
    assert group2 is not None
    # lastMessageId may be None or msg_id2 depending on whether the
    # aggregation_groups source strips it. The current code strips it
    # on each pass when the message doesn't exist.
    assert group2.lastMessageId is None
