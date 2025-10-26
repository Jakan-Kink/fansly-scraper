"""Unit tests for message group functionality."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from download.core import DownloadState
from metadata.account import Account
from metadata.messages import Group, Message, group_users, process_groups_response


# We don't need to redefine these fixtures as they're already in conftest.py:
# - database
# - cleanup_database
# - test_engine


@pytest.fixture
def download_state():
    """Create a test download state."""
    return DownloadState(creator_name="test_creator")


@pytest.mark.asyncio
async def test_group_creation(session):
    """Test basic group creation."""
    # Create account first since it's required by foreign key
    account = Account(id=123, username="test_user")
    session.add(account)
    await session.commit()

    # Now create group
    group = Group(id=1, createdBy=123)
    session.add(group)
    await session.commit()

    stmt = select(Group)
    result = await session.execute(stmt)
    group = result.scalar_one_or_none()
    assert group.id == 1
    assert group.createdBy == 123
    assert group.lastMessageId is None


@pytest.mark.asyncio
async def test_message_creation(session):
    """Test basic message creation."""
    # Create account first since it's required by foreign key
    account = Account(id=123, username="test_user")
    session.add(account)
    await session.commit()

    # Now create message
    message = Message(id=1, senderId=123, content="test", createdAt=datetime.now(UTC))
    session.add(message)
    await session.commit()

    stmt = select(Message)
    result = await session.execute(stmt)
    message = result.scalar_one_or_none()
    assert message.id == 1
    assert message.senderId == 123
    assert message.content == "test"


@pytest.mark.asyncio
async def test_group_message_relationship(session):
    """Test relationship between groups and messages."""
    # Create account first since it's required by foreign key
    account = Account(id=123, username="test_user")
    session.add(account)
    await session.commit()

    # Create message first
    message = Message(id=1, senderId=123, content="test", createdAt=datetime.now(UTC))
    session.add(message)
    await session.commit()

    # Create group with lastMessageId
    group = Group(id=1, createdBy=123, lastMessageId=1)
    session.add(group)
    await session.commit()

    stmt = select(Group)
    result = await session.execute(stmt)
    group = result.scalar_one_or_none()
    assert group.lastMessageId == 1


@pytest.mark.asyncio
async def test_process_groups_response_basic(session, config, download_state):
    """Test basic group response processing."""
    # Create required accounts first
    account1 = Account(id=123, username="test_user1")
    account2 = Account(id=456, username="test_user2")
    session.add_all([account1, account2])
    await session.commit()

    response = {
        "data": [
            {
                "groupId": 1,
                "account_id": 123,
                "partnerAccountId": 456,
                "lastMessageId": 789,
            }
        ],
        "aggregationData": {"groups": [], "accounts": []},
    }

    # Process groups (this should store lastMessageId for later)
    await process_groups_response(config, download_state, response, session=session)

    stmt = select(Group)
    result = await session.execute(stmt)
    group = result.scalar_one_or_none()
    assert group.id == 1
    # lastMessageId should be set even though message doesn't exist yet
    assert group.lastMessageId == 789

    # Now create the message
    message = Message(id=789, senderId=123, content="test", createdAt=datetime.now(UTC))
    session.add(message)
    await session.commit()

    # Process groups again to update lastMessageId
    await process_groups_response(config, download_state, response, session=session)
    await session.commit()

    # Now lastMessageId should be set
    stmt = select(Group)
    result = await session.execute(stmt)
    group = result.scalar_one_or_none()
    assert group.lastMessageId == 789


@pytest.mark.asyncio
async def test_process_groups_response_with_users(session, config, download_state):
    """Test group response processing with user relationships."""
    # Create required accounts first
    account1 = Account(id=123, username="test_user1")
    account2 = Account(id=456, username="test_user2")
    session.add_all([account1, account2])
    await session.commit()

    response = {
        "data": [],
        "aggregationData": {
            "groups": [
                {
                    "id": 1,
                    "createdBy": 123,
                    "lastMessageId": 789,
                    "users": [{"userId": 123}, {"userId": 456}],
                }
            ],
            "accounts": [],
        },
    }

    await process_groups_response(config, download_state, response, session=session)

    # Get the group
    stmt = select(Group)
    result = await session.execute(stmt)
    group = result.scalar_one_or_none()
    assert group.id == 1

    # Instead of accessing group.users directly, query the group_users table
    users_query = await session.execute(
        select(group_users.c.accountId).where(group_users.c.groupId == group.id)
    )
    user_ids = [row.accountId for row in users_query]

    # Check that both users are in the group
    assert sorted(user_ids) == [123, 456]  # Users should be added since accounts exist


@pytest.mark.asyncio
async def test_process_groups_response_multiple_commits(
    session, config, download_state
):
    """Test that multiple commits don't cause foreign key violations."""
    # Create required accounts first
    account = Account(id=123, username="test_user")
    session.add(account)
    await session.commit()

    response = {
        "data": [{"groupId": 1, "account_id": 123, "lastMessageId": 789}],
        "aggregationData": {
            "groups": [{"id": 2, "createdBy": 123, "lastMessageId": 790}],
            "accounts": [],
        },
    }

    # First process - should store lastMessageIds but not set them
    await process_groups_response(config, download_state, response, session=session)

    # Create one message but not the other
    message = Message(
        id=789, senderId=123, content="test1", createdAt=datetime.now(UTC)
    )
    session.add(message)
    await session.commit()

    # Process again - should only set lastMessageId for existing message
    await process_groups_response(config, download_state, response, session=session)

    stmt1 = select(Group).where(Group.id == 1)
    result = await session.execute(stmt1)
    group1 = result.scalar_one_or_none()

    stmt2 = select(Group).where(Group.id == 2)
    result = await session.execute(stmt2)
    group2 = result.scalar_one_or_none()

    assert group1.lastMessageId == 789  # Message exists
    assert group2.lastMessageId is None  # Message doesn't exist yet
