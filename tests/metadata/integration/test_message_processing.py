"""Integration tests for message processing functionality."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, selectinload, sessionmaker
from sqlalchemy.sql import text

from config import FanslyConfig
from metadata.account import Account
from metadata.base import Base
from metadata.database import Database
from metadata.messages import (
    Group,
    Message,
    process_groups_response,
    process_messages_metadata,
)
from tests.metadata.conftest import TestDatabase


@pytest.fixture(scope="session")
def conversation_data():
    """Load conversation test data."""
    test_data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "json")
    with open(os.path.join(test_data_dir, "conversation-sample-account.json")) as f:
        return json.load(f)


@pytest.fixture(scope="session")
def group_data():
    """Load group messages test data."""
    test_data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "json")
    with open(os.path.join(test_data_dir, "messages-group.json")) as f:
        return json.load(f)


@pytest.fixture(autouse=True)
async def setup_accounts(test_database: TestDatabase, request):
    """Set up test accounts."""
    # Generate unique IDs based on test name
    test_name = request.node.name
    import hashlib

    base_id = (
        int(
            hashlib.sha1(f"TestMessageProcessing_{test_name}".encode()).hexdigest()[:8],
            16,
        )
        % 1000000
    )

    async with test_database.async_session_scope() as session:
        # Create test accounts with unique IDs
        accounts = [
            Account(id=base_id + i, username=f"user{base_id}_{i}") for i in range(1, 3)
        ]
        for account in accounts:
            session.add(account)
        await session.commit()
        return accounts


@pytest.mark.asyncio
async def test_process_direct_messages(
    test_database: TestDatabase, config, conversation_data, setup_accounts
):
    """Test processing direct messages from conversation data."""
    if not conversation_data.get("response", {}).get("messages"):
        pytest.skip("No messages found in conversation data")
    setup_accounts = (
        await setup_accounts if hasattr(setup_accounts, "__await__") else setup_accounts
    )

    messages_data = conversation_data["response"]["messages"]

    async with test_database.async_session_scope() as session:
        # Process messages
        await process_messages_metadata(config, None, messages_data, session=session)

    # Verify messages were created
    async with test_database.async_session_scope() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM messages"))
        count = result.scalar()
        assert count > 0

        # Clear existing messages
        await session.execute(text("DELETE FROM messages"))
        await session.commit()

        # Create test message data
        test_message_data = {
            "id": 1,
            "senderId": setup_accounts[0].id,
            "content": "Test message content",
            "createdAt": int(datetime.now(timezone.utc).timestamp()),
        }

        # Process test message
        await process_messages_metadata(
            config, None, [test_message_data], session=session
        )

        # Verify message was created
        result = await session.execute(text("SELECT * FROM messages"))
        messages = result.fetchall()
        assert len(messages) == 1
        assert messages[0].content == test_message_data["content"]
        assert messages[0].senderId == test_message_data["senderId"]

        # Verify message was created with correct data
        assert messages[0].createdAt is not None
        assert not messages[0].deleted
        assert messages[0].deletedAt is None

        # Check attachments if present
        if "attachments" in test_message_data:
            assert len(messages[0].attachments) == len(test_message_data["attachments"])


@pytest.mark.asyncio
async def test_process_group_messages(test_database: TestDatabase, config, group_data):
    """Test processing group messages."""
    if not group_data.get("response", {}).get("data"):
        pytest.skip("No group data found in test data")

    async with test_database.async_session_scope() as session:
        # Process group data
        await process_groups_response(
            config, None, group_data["response"], session=session
        )

    # Verify groups were created
    async with test_database.async_session_scope() as session:
        result = await session.execute(text("SELECT * FROM groups"))
        groups = result.fetchall()
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
                text("SELECT * FROM messages WHERE id = :message_id"),
                {"message_id": first_group.lastMessageId},
            )
            last_message = result.fetchone()
            assert last_message is not None
            assert last_message.groupId == first_group.id


@pytest.mark.asyncio
async def test_process_message_attachments(
    test_database: TestDatabase,
    config,
    conversation_data,
):
    """Test processing messages with attachments."""
    messages_with_attachments = []

    # Look for messages with attachments in conversation data
    if conversation_data.get("response", {}).get("messages"):
        for msg in conversation_data["response"]["messages"]:
            if msg.get("attachments"):
                messages_with_attachments.append(msg)

    if not messages_with_attachments:
        pytest.skip("No messages with attachments found in test data")

    async with test_database.async_session_scope() as session:
        # Process messages
        await process_messages_metadata(
            config, None, messages_with_attachments, session=session
        )

    # Verify attachments were created
    async with test_database.async_session_scope() as session:
        for msg_data in messages_with_attachments:
            # Use ORM to get message with relationships loaded
            # Include explicit loading of the attachments relationship
            stmt = (
                select(Message)
                .options(selectinload(Message.attachments))
                .where(Message.id == msg_data["id"])
            )

            result = await session.execute(stmt)
            message = result.scalar_one()

            assert message is not None
            # Now attachments is accessed as a relationship property that's already loaded
            assert len(message.attachments) == len(msg_data["attachments"])

            # Verify attachment content IDs match
            attachment_ids = {str(a.contentId) for a in message.attachments}
            expected_ids = {str(a["contentId"]) for a in msg_data["attachments"]}
            assert attachment_ids == expected_ids
