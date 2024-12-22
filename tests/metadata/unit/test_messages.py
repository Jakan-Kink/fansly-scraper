"""Unit tests for metadata.messages module."""

from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from metadata.account import Account
from metadata.attachment import Attachment, ContentType
from metadata.base import Base
from metadata.messages import Group, Message, group_users, process_messages_metadata


class TestMessages(TestCase):
    """Test cases for messaging functionality."""

    def setUp(self):
        """Set up test database and session."""
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session: sessionmaker = sessionmaker(bind=self.engine)
        self.session: Session = self.Session()

        # Create test accounts
        self.account1 = Account(id=1, username="sender")
        self.account2 = Account(id=2, username="recipient")
        self.session.add_all([self.account1, self.account2])
        self.session.commit()

    def tearDown(self):
        """Clean up test database."""
        self.session.close()
        Base.metadata.drop_all(self.engine)

    def test_direct_message_creation(self):
        """Test creating a direct message between users."""
        message = Message(
            id=1,
            senderId=1,
            recipientId=2,
            content="Test message",
            createdAt=datetime.now(timezone.utc),
        )
        self.session.add(message)
        self.session.commit()

        saved_message = self.session.query(Message).first()
        self.assertEqual(saved_message.content, "Test message")
        self.assertEqual(saved_message.senderId, 1)
        self.assertEqual(saved_message.recipientId, 2)
        self.assertIsNone(saved_message.groupId)

    def test_group_creation(self):
        """Test creating a message group."""
        group = Group(id=1, createdBy=1)
        self.session.add(group)
        self.session.flush()

        # Add users to group
        self.session.execute(
            group_users.insert().values(
                [{"groupId": 1, "accountId": 1}, {"groupId": 1, "accountId": 2}]
            )
        )
        self.session.commit()

        saved_group = self.session.query(Group).first()
        self.assertEqual(saved_group.createdBy, 1)
        self.assertEqual(len(saved_group.users), 2)
        user_ids = {u.id for u in saved_group.users}
        self.assertEqual(user_ids, {1, 2})

    def test_group_message(self):
        """Test creating a message in a group."""
        # Create group
        group = Group(id=1, createdBy=1)
        self.session.add(group)
        self.session.flush()

        # Add message to group
        message = Message(
            id=1,
            groupId=1,
            senderId=1,
            content="Group message",
            createdAt=datetime.now(timezone.utc),
        )
        self.session.add(message)
        self.session.commit()

        # Update group's last message
        group.lastMessageId = message.id
        self.session.commit()

        saved_group = self.session.query(Group).first()
        self.assertEqual(saved_group.lastMessageId, 1)
        saved_message = self.session.query(Message).first()
        self.assertEqual(saved_message.groupId, 1)
        self.assertEqual(saved_message.content, "Group message")

    def test_message_with_attachment(self):
        """Test message with an attachment."""
        message = Message(
            id=1,
            senderId=1,
            recipientId=2,
            content="Message with attachment",
            createdAt=datetime.now(timezone.utc),
        )
        self.session.add(message)
        self.session.flush()

        attachment = Attachment(
            contentId="test_content",
            messageId=1,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=1,
        )
        self.session.add(attachment)
        self.session.commit()

        saved_message = self.session.query(Message).first()
        self.assertEqual(len(saved_message.attachments), 1)
        self.assertEqual(
            saved_message.attachments[0].contentType, ContentType.ACCOUNT_MEDIA
        )

    def test_process_messages_metadata(self):
        """Test processing message metadata."""
        config_mock = MagicMock()
        messages_data = [
            {
                "id": 1,
                "senderId": 1,
                "recipientId": 2,
                "content": "Test message",
                "createdAt": int(datetime.now(timezone.utc).timestamp()),
                "attachments": [
                    {
                        "contentId": "test_content",
                        "contentType": ContentType.ACCOUNT_MEDIA.value,
                        "pos": 1,
                    }
                ],
            }
        ]

        with patch("metadata.messages.config._database.sync_session") as mock_session:
            mock_session.return_value.__enter__.return_value = self.session
            process_messages_metadata(config_mock, None, messages_data)

        saved_message = self.session.query(Message).first()
        self.assertEqual(saved_message.content, "Test message")
        self.assertEqual(len(saved_message.attachments), 1)
        self.assertEqual(saved_message.attachments[0].contentId, "test_content")
