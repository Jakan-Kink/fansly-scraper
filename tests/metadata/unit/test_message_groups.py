"""Unit tests for message group functionality."""

from datetime import UTC, datetime

import pytest

from helpers.common import JsonDict
from metadata import Account, Group, Message, process_groups_response
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.xdist_group("msg_group_basics")
class TestGroupMessageBasics:
    """Group/Message creation and relationship on a shared store."""

    @pytest.mark.asyncio
    async def test_group_message_basics(self, entity_store):
        """Group creation, message creation, and their relationship in sequence."""
        store = entity_store

        account_id = snowflake_id()
        msg_id = snowflake_id()
        group_id = snowflake_id()
        rel_group_id = snowflake_id()

        account = Account(id=account_id, username="test_user")
        await store.save(account)

        # Message creation: createdBy/senderId, content defaults.
        message = Message(
            id=msg_id,
            senderId=account_id,
            content="test",
            createdAt=datetime.now(UTC),
        )
        await store.save(message)

        saved_msg = await store.get(Message, msg_id)
        assert saved_msg is not None
        assert saved_msg.id == msg_id
        assert saved_msg.senderId == account_id
        assert saved_msg.content == "test"

        # Group creation: createdBy set, lastMessageId defaults to None.
        group = Group(id=group_id, createdBy=account_id)
        await store.save(group)

        saved_group = await store.get(Group, group_id)
        assert saved_group is not None
        assert saved_group.id == group_id
        assert saved_group.createdBy == account_id
        assert saved_group.lastMessageId is None

        # Group/Message relationship: lastMessageId references the saved message.
        rel_group = Group(id=rel_group_id, createdBy=account_id, lastMessageId=msg_id)
        await store.save(rel_group)

        saved_rel = await store.get(Group, rel_group_id)
        assert saved_rel is not None
        assert saved_rel.lastMessageId == msg_id


@pytest.mark.xdist_group("msg_group_process")
class TestProcessGroupsResponse:
    """process_groups_response over data-source, users, and repeated payloads."""

    @pytest.mark.asyncio
    async def test_process_groups_response(self, entity_store, config, download_state):
        """Strip, junction, FK, and idempotency facets in sequence.

        process_groups_response uses get_store() internally, wired via
        entity_store.
        """
        store = entity_store

        account_id1 = snowflake_id()
        account_id2 = snowflake_id()

        account1 = Account(id=account_id1, username="test_user1")
        account2 = Account(id=account_id2, username="test_user2")
        await store.save(account1)
        await store.save(account2)

        # --- Facet 1: data source, lastMessageId stripped (message absent) ---
        basic_group_id = snowflake_id()
        basic_last_msg_id = snowflake_id()
        basic_response: JsonDict = {
            "data": [
                {
                    "groupId": basic_group_id,
                    "account_id": account_id1,
                    "partnerAccountId": account_id2,
                    "lastMessageId": basic_last_msg_id,
                }
            ],
            "aggregationData": {"groups": [], "accounts": []},
        }

        await process_groups_response(config, download_state, basic_response)

        saved = await store.get(Group, basic_group_id)
        assert saved is not None
        assert saved.id == basic_group_id
        # lastMessageId stripped — message doesn't exist in the store
        assert saved.lastMessageId is None

        # --- Facet 2: aggregation groups with users → junction rows ---
        users_group_id = snowflake_id()
        users_last_msg_id = snowflake_id()
        users_response: JsonDict = {
            "data": [],
            "aggregationData": {
                "groups": [
                    {
                        "id": users_group_id,
                        "createdBy": account_id1,
                        "lastMessageId": users_last_msg_id,
                        "users": [
                            {"userId": account_id1},
                            {"userId": account_id2},
                        ],
                    }
                ],
                "accounts": [],
            },
        }

        await process_groups_response(config, download_state, users_response)

        saved = await store.get(Group, users_group_id)
        assert saved is not None
        assert saved.id == users_group_id

        # Verify users via raw query on group_users junction
        pool = await store._get_pool()
        rows = await pool.fetch(
            'SELECT "accountId" FROM group_users WHERE "groupId" = $1',
            users_group_id,
        )
        user_ids = sorted([row["accountId"] for row in rows])
        assert user_ids == sorted([account_id1, account_id2])

        # --- Facet 3: multiple commits, no FK violations, idempotency ---
        multi_group_id1 = snowflake_id()
        multi_group_id2 = snowflake_id()
        multi_msg_id1 = snowflake_id()
        multi_msg_id2 = snowflake_id()
        multi_response: JsonDict = {
            "data": [
                {
                    "groupId": multi_group_id1,
                    "account_id": account_id1,
                    "lastMessageId": multi_msg_id1,
                }
            ],
            "aggregationData": {
                "groups": [
                    {
                        "id": multi_group_id2,
                        "createdBy": account_id1,
                        "lastMessageId": multi_msg_id2,
                    }
                ],
                "accounts": [],
            },
        }

        # First process
        await process_groups_response(config, download_state, multi_response)

        # Create one message
        message = Message(
            id=multi_msg_id1,
            senderId=account_id1,
            content="test1",
            createdAt=datetime.now(UTC),
        )
        await store.save(message)

        # Process again (idempotent, no FK violation)
        await process_groups_response(config, download_state, multi_response)

        group1 = await store.get(Group, multi_group_id1)
        group2 = await store.get(Group, multi_group_id2)

        assert group1 is not None
        assert group1.lastMessageId == multi_msg_id1

        # For aggregation_groups source, missing lastMessageId is stripped.
        # On second pass, message multi_msg_id2 still doesn't exist → stripped.
        assert group2 is not None
        assert group2.lastMessageId is None
