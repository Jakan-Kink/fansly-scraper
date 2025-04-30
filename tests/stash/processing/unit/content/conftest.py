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
from ..media_mixin.async_mock_helper import (
    AccessibleAsyncMock,
    AsyncContextManagerMock,
    make_awaitable_mock,
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

        # Set up database attribute with proper async context manager
        self.database = MagicMock()
        session_mock = MagicMock()
        self.database.async_session_scope = AsyncContextManagerMock(
            return_value=session_mock
        )

        # Mock methods this mixin needs from others
        self._setup_worker_pool = AsyncMock(
            return_value=(
                MagicMock(),
                MagicMock(),
                AsyncContextManagerMock(),
                MagicMock(),
            )
        )
        self._run_worker_pool = AsyncMock()
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
    # Use AccessibleAsyncMock to ensure both direct and awaitable access
    account_attrs = AccessibleAsyncMock()
    account_attrs.hashtags = []
    account_attrs.accountMentions = []
    account_attrs.id = mock_account.id

    # Make sure awaitable_attrs methods return the right values
    mock_account.awaitable_attrs = MagicMock()
    mock_account.awaitable_attrs.hashtags = account_attrs.hashtags
    mock_account.awaitable_attrs.accountMentions = account_attrs.accountMentions
    mock_account.awaitable_attrs.id = account_attrs.id

    # Make the account itself accessible through await
    mock_account.__await__ = lambda: account_attrs.__await__()

    return mock_account


@pytest.fixture
def content_mock_performer(mock_performer):
    """Fixture for mock performer with content-specific attributes."""
    # Create accessible async mock for awaitable attributes
    performer_attrs = AccessibleAsyncMock()
    performer_attrs.id = mock_performer.id

    # Make sure awaitable_attrs methods return the right values
    mock_performer.awaitable_attrs = MagicMock()
    mock_performer.awaitable_attrs.id = performer_attrs.id

    # Make the performer itself accessible through await
    mock_performer.__await__ = lambda: performer_attrs.__await__()

    return mock_performer


@pytest.fixture
def content_mock_studio(mock_studio):
    """Fixture for mock studio with content-specific attributes."""
    # Create accessible async mock for awaitable attributes
    studio_attrs = AccessibleAsyncMock()
    studio_attrs.id = mock_studio.id

    # Make sure awaitable_attrs methods return the right values
    mock_studio.awaitable_attrs = MagicMock()
    mock_studio.awaitable_attrs.id = studio_attrs.id

    # Make the studio itself accessible through await
    mock_studio.__await__ = lambda: studio_attrs.__await__()

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

    # Set up awaitable attributes
    group_attrs = AccessibleAsyncMock()
    group_attrs.id = group.id
    group_attrs.users = group.users

    # Make sure awaitable_attrs returns proper values
    group.awaitable_attrs = MagicMock()
    group.awaitable_attrs.id = group_attrs.id
    group.awaitable_attrs.users = group_attrs.users

    # Make the group itself awaitable
    group.__await__ = lambda: group_attrs.__await__()

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

    # Set up awaitable attributes
    message_attrs = AccessibleAsyncMock()
    message_attrs.id = message.id
    message_attrs.content = message.content
    message_attrs.createdAt = message.createdAt
    message_attrs.attachments = message.attachments
    message_attrs.group = message.group

    # Make sure awaitable_attrs returns proper values
    message.awaitable_attrs = MagicMock()
    message.awaitable_attrs.id = message_attrs.id
    message.awaitable_attrs.content = message_attrs.content
    message.awaitable_attrs.createdAt = message_attrs.createdAt
    message.awaitable_attrs.attachments = message_attrs.attachments
    message.awaitable_attrs.group = message_attrs.group

    # Make the message itself awaitable
    message.__await__ = lambda: message_attrs.__await__()

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

        # Make awaitable attachment attributes
        attachment_attrs = AccessibleAsyncMock()
        attachment_attrs.id = attachment.id
        attachment_attrs.contentId = attachment.contentId
        attachment_attrs.contentType = attachment.contentType
        attachment_attrs.media = attachment.media
        attachment_attrs.bundle = attachment.bundle

        # Set up awaitable_attrs for attachment
        attachment.awaitable_attrs = MagicMock()
        attachment.awaitable_attrs.id = attachment_attrs.id
        attachment.awaitable_attrs.contentId = attachment_attrs.contentId
        attachment.awaitable_attrs.contentType = attachment_attrs.contentType
        attachment.awaitable_attrs.media = attachment_attrs.media
        attachment.awaitable_attrs.bundle = attachment_attrs.bundle

        # Make attachment itself awaitable
        attachment.__await__ = lambda att=attachment_attrs: att.__await__()

        message.attachments = [attachment]
        message.group = mock_group

        # Set up awaitable attributes for message
        message_attrs = AccessibleAsyncMock()
        message_attrs.id = message.id
        message_attrs.content = message.content
        message_attrs.createdAt = message.createdAt
        message_attrs.attachments = message.attachments
        message_attrs.group = message.group

        # Set up awaitable_attrs for message
        message.awaitable_attrs = MagicMock()
        message.awaitable_attrs.id = message_attrs.id
        message.awaitable_attrs.content = message_attrs.content
        message.awaitable_attrs.createdAt = message_attrs.createdAt
        message.awaitable_attrs.attachments = message_attrs.attachments
        message.awaitable_attrs.group = message_attrs.group

        # Make message itself awaitable
        message.__await__ = lambda msg=message_attrs: msg.__await__()

        messages.append(message)
    return messages


@pytest.fixture
def mock_session():
    """Fixture for mock database session."""
    session = MagicMock()

    # Create result objects with proper async support
    result_mock = AccessibleAsyncMock()
    scalars_mock = AccessibleAsyncMock()
    unique_mock = AccessibleAsyncMock()

    # Set up the session.execute method to return an awaitable result
    session.execute = AsyncMock(return_value=result_mock)

    # Set up the result's methods to return proper values
    result_mock.scalar_one = AccessibleAsyncMock()
    result_mock.unique = MagicMock(return_value=unique_mock)
    unique_mock.scalars = MagicMock(return_value=scalars_mock)
    scalars_mock.all = MagicMock(return_value=[])

    # Make other common session methods properly awaitable
    session.add = MagicMock()
    session.refresh = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()

    # Make session itself usable in an async context
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock()

    return session
