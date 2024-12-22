"""Unit tests for metadata.attachment module."""

from datetime import datetime, timezone
from unittest import TestCase

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from metadata.account import Account
from metadata.attachment import Attachment, ContentType
from metadata.base import Base
from metadata.messages import Message
from metadata.post import Post


class TestAttachment(TestCase):
    """Test cases for Attachment class and related functionality."""

    def setUp(self):
        """Set up test database and session."""
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

        # Create test account
        self.account = Account(id=1, username="test_user")
        self.session.add(self.account)
        self.session.commit()

    def tearDown(self):
        """Clean up test database."""
        self.session.close()
        Base.metadata.drop_all(self.engine)

    def test_post_attachment_ordering(self):
        """Test that post attachments are ordered by position."""
        # Create post
        post = Post(
            id=1, accountId=1, content="Test post", createdAt=datetime.now(timezone.utc)
        )
        self.session.add(post)
        self.session.flush()

        # Create attachments with different positions
        attachments = [
            Attachment(
                postId=1, contentId=i, pos=pos, contentType=ContentType.ACCOUNT_MEDIA
            )
            for i, pos in [(1, 3), (2, 1), (3, 2)]  # Out of order positions
        ]
        self.session.add_all(attachments)
        self.session.commit()

        # Verify order
        saved_post = self.session.query(Post).first()
        attachment_positions = [a.pos for a in saved_post.attachments]
        self.assertEqual(attachment_positions, [1, 2, 3])  # Should be ordered
        attachment_content_ids = [a.contentId for a in saved_post.attachments]
        self.assertEqual(
            attachment_content_ids, [2, 3, 1]
        )  # Should match position order

    def test_message_attachment_ordering(self):
        """Test that message attachments are ordered by position."""
        # Create message
        message = Message(
            id=1,
            senderId=1,
            content="Test message",
            createdAt=datetime.now(timezone.utc),
        )
        self.session.add(message)
        self.session.flush()

        # Create attachments with different positions
        attachments = [
            Attachment(
                messageId=1, contentId=i, pos=pos, contentType=ContentType.ACCOUNT_MEDIA
            )
            for i, pos in [(1, 2), (2, 3), (3, 1)]  # Out of order positions
        ]
        self.session.add_all(attachments)
        self.session.commit()

        # Verify order
        saved_message = self.session.query(Message).first()
        attachment_positions = [a.pos for a in saved_message.attachments]
        self.assertEqual(attachment_positions, [1, 2, 3])  # Should be ordered
        attachment_content_ids = [a.contentId for a in saved_message.attachments]
        self.assertEqual(
            attachment_content_ids, [3, 1, 2]
        )  # Should match position order

    def test_attachment_content_resolution(self):
        """Test resolving different types of attachment content."""
        # Create post with different types of attachments
        post = Post(
            id=1, accountId=1, content="Test post", createdAt=datetime.now(timezone.utc)
        )
        self.session.add(post)
        self.session.flush()

        # Create attachments with different content types
        attachments = [
            Attachment(
                postId=1, contentId=1, pos=1, contentType=ContentType.ACCOUNT_MEDIA
            ),
            Attachment(
                postId=1,
                contentId=2,
                pos=2,
                contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
            ),
            Attachment(postId=1, contentId=3, pos=3, contentType=ContentType.STORY),
        ]
        self.session.add_all(attachments)
        self.session.commit()

        # Verify content type properties
        saved_post = self.session.query(Post).first()
        self.assertTrue(saved_post.attachments[0].is_account_media)
        self.assertTrue(saved_post.attachments[1].is_account_media_bundle)
        self.assertFalse(saved_post.attachments[2].is_account_media)
        self.assertFalse(saved_post.attachments[2].is_account_media_bundle)

    def test_attachment_exclusivity(self):
        """Test that attachments can't belong to both post and message."""
        attachment = Attachment(
            contentId=1,
            pos=1,
            contentType=ContentType.ACCOUNT_MEDIA,
            postId=1,
            messageId=1,  # This should violate the constraint
        )

        # Create post and message
        post = Post(id=1, accountId=1, createdAt=datetime.now(timezone.utc))
        message = Message(
            id=1, senderId=1, content="Test", createdAt=datetime.now(timezone.utc)
        )
        self.session.add_all([post, message])
        self.session.flush()

        # Adding the attachment should fail
        with self.assertRaises(Exception):  # Should raise due to check constraint
            self.session.add(attachment)
            self.session.commit()
