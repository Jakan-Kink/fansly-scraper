"""Unit tests for message group functionality."""

import os
import tempfile
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from config import FanslyConfig
from download.core import DownloadState
from metadata.base import Base
from metadata.database import Database
from metadata.messages import Group, Message, process_groups_response


@pytest.fixture
def database(test_database):
    return test_database


@pytest.fixture
def download_state():
    """Create a test download state."""
    return DownloadState(creator_name="test_creator")


def test_group_creation(database):
    """Test basic group creation."""
    with database.get_sync_session() as session:
        group = Group(id=1, createdBy=123)
        session.add(group)
        session.commit()

        result = session.execute(select(Group)).scalar_one_or_none()
        assert result.id == 1
        assert result.createdBy == 123
        assert result.lastMessageId is None


def test_message_creation(database):
    """Test basic message creation."""
    with database.get_sync_session() as session:
        message = Message(
            id=1, senderId=123, content="test", createdAt=datetime.now(timezone.utc)
        )
        session.add(message)
        session.commit()

        result = session.execute(select(Message)).scalar_one_or_none()
        assert result.id == 1
        assert result.senderId == 123
        assert result.content == "test"


def test_group_message_relationship(database):
    """Test relationship between groups and messages."""
    with database.get_sync_session() as session:
        # Create message first
        message = Message(
            id=1, senderId=123, content="test", createdAt=datetime.now(timezone.utc)
        )
        session.add(message)
        session.commit()

        # Create group with lastMessageId
        group = Group(id=1, createdBy=123, lastMessageId=1)
        session.add(group)
        session.commit()

        result = session.execute(select(Group)).scalar_one_or_none()
        assert result.lastMessageId == 1


def test_process_groups_response_basic(database, mock_config, download_state):
    """Test basic group response processing."""
    response = {
        "data": [
            {
                "groupId": "1",
                "account_id": "123",
                "partnerAccountId": "456",
                "lastMessageId": "789",
            }
        ],
        "aggregationData": {"groups": [], "accounts": []},
    }

    # Process groups (this should store lastMessageId for later)
    process_groups_response(mock_config, download_state, response)

    with database.get_sync_session() as session:
        group = session.execute(select(Group)).scalar_one_or_none()
        assert group.id == 1
        # lastMessageId should not be set yet since message doesn't exist
        assert group.lastMessageId is None

        # Now create the message
        message = Message(
            id=789, senderId=123, content="test", createdAt=datetime.now(timezone.utc)
        )
        session.add(message)
        session.commit()

        # Process groups again to update lastMessageId
        process_groups_response(mock_config, download_state, response)
        session.commit()

        # Now lastMessageId should be set
        group = session.execute(select(Group)).scalar_one_or_none()
        assert group.lastMessageId == 789


def test_process_groups_response_with_users(database, mock_config, download_state):
    """Test group response processing with user relationships."""
    response = {
        "data": [],
        "aggregationData": {
            "groups": [
                {
                    "id": "1",
                    "createdBy": "123",
                    "lastMessageId": "789",
                    "users": [{"userId": "123"}, {"userId": "456"}],
                }
            ],
            "accounts": [],
        },
    }

    process_groups_response(mock_config, download_state, response)

    with database.get_sync_session() as session:
        group = session.execute(select(Group)).scalar_one_or_none()
        assert group.id == 1
        assert len(group.users) == 0  # Users won't be added until accounts exist


def test_process_groups_response_multiple_commits(
    database, mock_config, download_state
):
    """Test that multiple commits don't cause foreign key violations."""
    response = {
        "data": [{"groupId": "1", "account_id": "123", "lastMessageId": "789"}],
        "aggregationData": {
            "groups": [{"id": "2", "createdBy": "123", "lastMessageId": "790"}],
            "accounts": [],
        },
    }

    # First process - should store lastMessageIds but not set them
    process_groups_response(mock_config, download_state, response)

    with database.get_sync_session() as session:
        # Create one message but not the other
        message = Message(
            id=789, senderId=123, content="test1", createdAt=datetime.now(timezone.utc)
        )
        session.add(message)
        session.commit()

    # Process again - should only set lastMessageId for existing message
    process_groups_response(mock_config, download_state, response)

    with database.get_sync_session() as session:
        group1 = session.execute(
            select(Group).where(Group.id == 1)
        ).scalar_one_or_none()
        group2 = session.execute(
            select(Group).where(Group.id == 2)
        ).scalar_one_or_none()
        assert group1.lastMessageId == 789  # Message exists
        assert group2.lastMessageId is None  # Message doesn't exist yet
