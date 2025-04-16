"""Common fixtures for content mixin tests."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from metadata import Account, Attachment, Group, Message, Post
from stash.processing.mixins.content import ContentProcessingMixin
from stash.types import Gallery, Performer, Studio


class TestMixinClass(ContentProcessingMixin):
    """Test class that implements ContentProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.context = MagicMock()
        self.context.client = MagicMock()
        self.database = MagicMock()
        self.log = MagicMock()
        self._process_item_gallery = AsyncMock()
        self._setup_batch_processing = AsyncMock()
        self._run_batch_processor = AsyncMock()


@pytest.fixture
def mixin():
    """Fixture for ContentProcessingMixin instance."""
    return TestMixinClass()


@pytest.fixture
def mock_account():
    """Fixture for mock account."""
    account = MagicMock(spec=Account)
    account.id = 54321
    account.username = "test_user"
    account.stash_id = None
    return account


@pytest.fixture
def mock_performer():
    """Fixture for mock performer."""
    performer = MagicMock(spec=Performer)
    performer.id = "performer_123"
    performer.name = "test_user"
    return performer


@pytest.fixture
def mock_studio():
    """Fixture for mock studio."""
    studio = MagicMock(spec=Studio)
    studio.id = "studio_123"
    studio.name = "Test Studio"
    return studio


@pytest.fixture
def mock_gallery():
    """Fixture for mock gallery."""
    gallery = MagicMock(spec=Gallery)
    gallery.id = "gallery_123"
    gallery.title = "Test Gallery"
    return gallery


@pytest.fixture
def mock_attachment():
    """Fixture for mock attachment."""
    attachment = MagicMock(spec=Attachment)
    attachment.id = "attachment_123"
    attachment.contentId = "content_123"
    attachment.contentType = "ACCOUNT_MEDIA"
    attachment.media = MagicMock()
    attachment.media.media = MagicMock()
    attachment.bundle = None
    return attachment


@pytest.fixture
def mock_post():
    """Fixture for mock post."""
    post = MagicMock(spec=Post)
    post.id = 12345
    post.accountId = 54321
    post.content = "Test post content"
    post.createdAt = datetime(2024, 4, 1, 12, 0, 0)
    post.attachments = []
    post.accountMentions = []
    return post


@pytest.fixture
def mock_posts(mock_attachment):
    """Fixture for mock posts with attachments."""
    posts = []
    for i in range(3):
        post = MagicMock(spec=Post)
        post.id = f"post_{i+1}"
        post.accountId = 54321
        post.content = f"Test post content {i+1}"
        post.createdAt = datetime(2024, 4, 1, 12, 0, 0)

        # Create a unique attachment for each post
        attachment = MagicMock(spec=Attachment)
        attachment.id = f"attachment_{i+1}"
        attachment.contentId = f"content_{i+1}"
        attachment.contentType = "ACCOUNT_MEDIA"
        attachment.media = MagicMock()
        attachment.media.media = MagicMock()
        attachment.bundle = None

        post.attachments = [attachment]
        post.accountMentions = []
        posts.append(post)
    return posts


@pytest.fixture
def mock_group():
    """Fixture for mock group."""
    group = MagicMock(spec=Group)
    group.id = "group_123"
    group.users = []
    return group


@pytest.fixture
def mock_message(mock_group):
    """Fixture for mock message."""
    message = MagicMock(spec=Message)
    message.id = 67890
    message.content = "Test message content"
    message.createdAt = datetime(2024, 4, 1, 12, 0, 0)
    message.attachments = []
    message.group = mock_group
    return message


@pytest.fixture
def mock_messages(mock_group, mock_attachment):
    """Fixture for mock messages with attachments."""
    messages = []
    for i in range(3):
        message = MagicMock(spec=Message)
        message.id = f"message_{i+1}"
        message.content = f"Test message content {i+1}"
        message.createdAt = datetime(2024, 4, 1, 12, 0, 0)

        # Create a unique attachment for each message
        attachment = MagicMock(spec=Attachment)
        attachment.id = f"msg_attachment_{i+1}"
        attachment.contentId = f"msg_content_{i+1}"
        attachment.contentType = "ACCOUNT_MEDIA"
        attachment.media = MagicMock()
        attachment.media.media = MagicMock()
        attachment.bundle = None

        message.attachments = [attachment]
        message.group = mock_group
        messages.append(message)
    return messages


@pytest.fixture
def mock_session():
    """Fixture for mock database session."""
    session = MagicMock()

    # Mock session.execute to return results that can be used with scalar_one and all
    result_mock = MagicMock()
    scalars_mock = MagicMock()
    unique_mock = MagicMock()

    session.execute.return_value = result_mock
    result_mock.scalar_one = MagicMock()
    result_mock.unique.return_value = unique_mock
    unique_mock.scalars.return_value = scalars_mock
    scalars_mock.all.return_value = []

    session.add = MagicMock()
    session.refresh = AsyncMock()

    return session
