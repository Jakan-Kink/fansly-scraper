"""Common fixtures for content mixin tests."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from metadata import Account, Attachment, Group, Message, Post
from stash.processing.mixins.content import ContentProcessingMixin
from stash.types import Gallery, Performer, Studio

# Import and re-export fixtures from parent conftest.py
from ..conftest import (
    mock_account,
    mock_gallery,
    mock_performer,
    mock_scene,
    mock_studio,
)

__all__ = [
    "mock_account",
    "mock_gallery",
    "mock_performer",
    "mock_studio",
    "mock_scene",
    "mock_attachment",
    "mock_post",
    "mock_posts",
    "mock_group",
    "mock_message",
    "mock_messages",
    "mock_session",
]


class TestMixinClass(ContentProcessingMixin):
    """Test class that implements ContentProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.context = MagicMock()
        self.context.client = MagicMock()
        self.log = MagicMock()

        # Mock methods this mixin needs from others
        self._setup_batch_processing = AsyncMock()
        self._run_batch_processor = AsyncMock()
        self._get_gallery_metadata = AsyncMock(
            return_value=("username", "Test Title", "https://test.com")
        )
        self._get_or_create_gallery = AsyncMock()
        self._process_item_gallery = AsyncMock()
        self._find_existing_performer = AsyncMock()
        self._find_existing_studio = AsyncMock()
        self._process_hashtags_to_tags = AsyncMock()
        self._update_account_stash_id = AsyncMock()


@pytest.fixture
def mixin():
    """Fixture for content mixin test class."""
    return TestMixinClass()


@pytest.fixture
def content_mock_account(mock_account):
    """Fixture for mock account with content-specific attributes."""
    # Add content-specific attributes & methods
    mock_account.awaitable_attrs.hashtags = AsyncMock(return_value=[])
    mock_account.awaitable_attrs.accountMentions = AsyncMock(return_value=[])
    return mock_account


@pytest.fixture
def content_mock_performer(mock_performer):
    """Fixture for mock performer with content-specific attributes."""
    # Add content-specific attributes
    mock_performer.awaitable_attrs = MagicMock()
    mock_performer.awaitable_attrs.id = mock_performer.id
    return mock_performer


@pytest.fixture
def content_mock_studio(mock_studio):
    """Fixture for mock studio with content-specific attributes."""
    # Add content-specific attributes
    mock_studio.awaitable_attrs = MagicMock()
    mock_studio.awaitable_attrs.id = mock_studio.id
    return mock_studio


# Use the parent fixtures but don't redefine them


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
def mock_posts():
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
def mock_messages(mock_group):
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
