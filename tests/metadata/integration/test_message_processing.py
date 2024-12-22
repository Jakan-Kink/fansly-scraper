"""Integration tests for message processing functionality."""

import json
import os
from datetime import datetime, timezone
from unittest import TestCase

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import FanslyConfig
from metadata.account import Account
from metadata.base import Base
from metadata.messages import (
    Group,
    Message,
    process_groups_response,
    process_messages_metadata,
)


class TestMessageProcessing(TestCase):
    """Integration tests for message processing."""

    @classmethod
    def setUpClass(cls):
        """Set up test database and load test data."""
        # Create test database
        cls.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

        # Load test data
        cls.test_data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "json")

        # Load conversation data
        with open(os.path.join(cls.test_data_dir, "conversation-trainingJ.json")) as f:
            cls.conversation_data = json.load(f)

        # Load group messages data
        with open(os.path.join(cls.test_data_dir, "messages-group.json")) as f:
            cls.group_data = json.load(f)

    def setUp(self):
        """Set up fresh session and config for each test."""
        self.session = self.Session()
        self.config = FanslyConfig(program_version="0.10.0")
        self.config._database.engine = self.engine

        # Create test accounts
        self.accounts = [
            Account(id=1, username="user1"),
            Account(id=2, username="user2"),
        ]
        self.session.add_all(self.accounts)
        self.session.commit()

    def tearDown(self):
        """Clean up after each test."""
        self.session.close()

    def test_process_direct_messages(self):
        """Test processing direct messages from conversation data."""
        if not self.conversation_data.get("response", {}).get("messages"):
            self.skipTest("No messages found in conversation data")

        messages_data = self.conversation_data["response"]["messages"]

        # Process messages
        process_messages_metadata(self.config, None, messages_data)

        # Verify messages were created
        with self.config._database.sync_session() as session:
            messages = session.query(Message).all()
            self.assertGreater(len(messages), 0)

            # Check first message
            first_message = messages[0]
            first_data = messages_data[0]
            self.assertEqual(first_message.content, first_data["content"])
            self.assertEqual(first_message.senderId, first_data["senderId"])

            # Check attachments if present
            if "attachments" in first_data:
                self.assertEqual(
                    len(first_message.attachments), len(first_data["attachments"])
                )

    def test_process_group_messages(self):
        """Test processing group messages."""
        if not self.group_data.get("response", {}).get("data"):
            self.skipTest("No group data found in test data")

        # Process group data
        process_groups_response(self.config, None, self.group_data["response"])

        # Verify groups were created
        with self.config._database.sync_session() as session:
            groups = session.query(Group).all()
            self.assertGreater(len(groups), 0)

            # Check first group
            first_group = groups[0]
            first_data = self.group_data["response"]["data"][0]

            # Verify group members
            if "users" in first_data:
                self.assertEqual(len(first_group.users), len(first_data["users"]))

            # Verify last message if present
            if first_group.lastMessageId:
                last_message = session.query(Message).get(first_group.lastMessageId)
                self.assertIsNotNone(last_message)
                self.assertEqual(last_message.groupId, first_group.id)

    def test_process_message_attachments(self):
        """Test processing messages with attachments."""
        messages_with_attachments = []

        # Look for messages with attachments in both conversation and group data
        if self.conversation_data.get("response", {}).get("messages"):
            for msg in self.conversation_data["response"]["messages"]:
                if msg.get("attachments"):
                    messages_with_attachments.append(msg)

        if not messages_with_attachments:
            self.skipTest("No messages with attachments found in test data")

        # Process messages
        process_messages_metadata(self.config, None, messages_with_attachments)

        # Verify attachments were created
        with self.config._database.sync_session() as session:
            for msg_data in messages_with_attachments:
                message = session.query(Message).get(msg_data["id"])
                self.assertIsNotNone(message)
                self.assertEqual(len(message.attachments), len(msg_data["attachments"]))

                # Verify attachment content IDs match
                attachment_ids = {a.contentId for a in message.attachments}
                expected_ids = {a["contentId"] for a in msg_data["attachments"]}
                self.assertEqual(attachment_ids, expected_ids)
