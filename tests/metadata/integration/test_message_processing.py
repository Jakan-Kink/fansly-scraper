"""Integration tests for message processing functionality."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

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


class TestMessageProcessing(TestCase):
    """Integration tests for message processing."""

    @classmethod
    def setUpClass(cls):
        """Load test data."""
        # Load test data
        cls.test_data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "json")

        # Load conversation data
        with open(
            os.path.join(cls.test_data_dir, "conversation-sample-account.json")
        ) as f:
            cls.conversation_data = json.load(f)

        # Load group messages data
        with open(os.path.join(cls.test_data_dir, "messages-group.json")) as f:
            cls.group_data = json.load(f)

    def setUp(self):
        """Set up fresh database and session for each test."""
        # Create test database
        self.engine = create_engine("sqlite:///:memory:")
        # Base.metadata.create_all(self.engine)
        self.Session: sessionmaker = sessionmaker(bind=self.engine)
        self.session: Session = self.Session()

        # Create config with test database
        self.config = FanslyConfig(program_version="0.10.0")
        self.config.metadata_db_file = Path(":memory:")
        self.config._database = Database(self.config)
        self.config._database._sync_engine = self.engine
        self.config._database.session_scope = self.Session

        # Generate unique IDs based on test name
        test_name = self._testMethodName
        import hashlib

        base_id = (
            int(
                hashlib.sha1(
                    f"{self.__class__.__name__}_{test_name}".encode()
                ).hexdigest()[:8],
                16,
            )
            % 1000000
        )

        # Create test accounts with unique IDs
        self.accounts = [
            Account(id=base_id + i, username=f"user{base_id}_{i}") for i in range(1, 3)
        ]
        self.session.add_all(self.accounts)
        self.session.commit()

    def tearDown(self):
        """Clean up after each test."""
        try:
            # Clean up data
            for table in reversed(Base.metadata.sorted_tables):
                self.session.execute(table.delete())
            self.session.commit()
        except Exception:
            self.session.rollback()
        finally:
            self.session.close()
            self.engine.dispose()

    def test_process_direct_messages(self):
        """Test processing direct messages from conversation data."""
        if not self.conversation_data.get("response", {}).get("messages"):
            self.skipTest("No messages found in conversation data")

        messages_data = self.conversation_data["response"]["messages"]

        # Process messages
        process_messages_metadata(self.config, None, messages_data)

        # Verify messages were created
        with self.config._database.session_scope() as session:
            messages = session.query(Message).all()
            self.assertGreater(len(messages), 0)

            # Clear existing messages
            session.query(Message).delete()
            session.commit()

            # Create test message data
            test_message_data = {
                "id": 1,
                "senderId": self.accounts[0].id,
                "content": "Test message content",
                "createdAt": int(datetime.now(timezone.utc).timestamp()),
            }

            # Process test message
            process_messages_metadata(self.config, None, [test_message_data])

            # Verify message was created
            messages = session.query(Message).all()
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].content, test_message_data["content"])
            self.assertEqual(messages[0].senderId, test_message_data["senderId"])

            # Verify message was created with correct data
            self.assertIsNotNone(messages[0].createdAt)
            self.assertFalse(messages[0].deleted)
            self.assertIsNone(messages[0].deletedAt)

            # Check attachments if present
            if "attachments" in test_message_data:
                self.assertEqual(
                    len(messages[0].attachments), len(test_message_data["attachments"])
                )

    def test_process_group_messages(self):
        """Test processing group messages."""
        if not self.group_data.get("response", {}).get("data"):
            self.skipTest("No group data found in test data")

        # Process group data
        process_groups_response(self.config, None, self.group_data["response"])

        # Verify groups were created
        with self.config._database.session_scope() as session:
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
        with self.config._database.session_scope() as session:
            for msg_data in messages_with_attachments:
                message = session.query(Message).get(msg_data["id"])
                self.assertIsNotNone(message)
                self.assertEqual(len(message.attachments), len(msg_data["attachments"]))

                # Verify attachment content IDs match
                attachment_ids = {str(a.contentId) for a in message.attachments}
                expected_ids = {str(a["contentId"]) for a in msg_data["attachments"]}
                self.assertEqual(attachment_ids, expected_ids)
