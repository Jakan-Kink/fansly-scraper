"""Common fixtures for StashProcessing integration tests."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metadata import Account, Attachment, Group, Media, Message, Post
from stash.processing import StashProcessing
from stash.types import Gallery, Image, Performer, Scene, Studio


class MockContext:
    """Mock context for testing."""

    def __init__(self):
        """Initialize mock context."""
        self.client = MagicMock()

        # Mock common client methods
        self.client.find_performer = AsyncMock()
        self.client.find_studio = AsyncMock()
        self.client.find_gallery = AsyncMock()
        self.client.find_galleries = AsyncMock()
        self.client.find_scene = AsyncMock()
        self.client.find_scenes = AsyncMock()
        self.client.find_image = AsyncMock()
        self.client.find_images = AsyncMock()
        self.client.add_gallery_images = AsyncMock(return_value=True)
        self.client.find_tags = AsyncMock()
        self.client.create_tag = AsyncMock()


class MockConfig:
    """Mock configuration for testing."""

    def __init__(self):
        """Initialize mock config."""
        self.stash = MagicMock()
        self.stash.enabled = True
        self.stash.url = "http://localhost:9999"
        self.stash.api_key = "test_api_key"

        self.metadata = MagicMock()
        self.metadata.db_path = "test.db"


class MockState:
    """Mock application state for testing."""

    def __init__(self):
        """Initialize mock state."""
        self.creator_id = "12345"
        self.creator_name = "test_user"
        self.messages_enabled = True
        self.verbose_logs = True


class MockDatabase:
    """Mock database connection for testing."""

    def __init__(self):
        """Initialize mock database."""
        self.session_maker = MagicMock()
        self.session = MagicMock(spec=AsyncSession)

        # Mock session context manager
        self.session.__aenter__ = AsyncMock(return_value=self.session)
        self.session.__aexit__ = AsyncMock()

        # Mock session operations
        self.session.execute = AsyncMock()
        self.session.scalar_one_or_none = AsyncMock()
        self.session.scalar_one = AsyncMock()
        self.session.add = MagicMock()
        self.session.refresh = AsyncMock()
        self.session.flush = AsyncMock()
        self.session.query = MagicMock()

        # Set up get_async_session to return the session
        self.get_async_session = MagicMock(return_value=self.session)

    def reset_session_mocks(self):
        """Reset all session mocks."""
        for name, attr in self.session.__dict__.items():
            if hasattr(attr, "reset_mock"):
                attr.reset_mock()


@pytest.fixture
def mock_context():
    """Fixture for mock StashClient context."""
    return MockContext()


@pytest.fixture
def mock_config():
    """Fixture for mock config."""
    return MockConfig()


@pytest.fixture
def mock_state():
    """Fixture for mock application state."""
    return MockState()


@pytest.fixture
def mock_database():
    """Fixture for mock database connection."""
    return MockDatabase()


@pytest.fixture
def mock_account():
    """Fixture for mock account."""
    account = MagicMock(spec=Account)
    account.id = 54321
    account.username = "test_user"
    account.stash_id = None
    account.awaitable_attrs = MagicMock()
    account.awaitable_attrs.username = "test_user"
    account.awaitable_attrs.avatar = None
    return account


@pytest.fixture
def mock_performer():
    """Fixture for mock performer."""
    performer = MagicMock(spec=Performer)
    performer.id = "performer_123"
    performer.name = "test_user"
    performer.update_avatar = AsyncMock()
    performer.save = AsyncMock()
    return performer


@pytest.fixture
def mock_studio():
    """Fixture for mock studio."""
    studio = MagicMock(spec=Studio)
    studio.id = "studio_123"
    studio.name = "Test Studio"
    studio.save = AsyncMock()
    return studio


@pytest.fixture
def mock_gallery():
    """Fixture for mock gallery."""
    gallery = MagicMock(spec=Gallery)
    gallery.id = "gallery_123"
    gallery.title = "Test Gallery"
    gallery.save = AsyncMock()
    gallery.destroy = AsyncMock()
    gallery.performers = []
    gallery.tags = []
    gallery.chapters = []
    gallery.scenes = []
    gallery.urls = []
    return gallery


@pytest.fixture
def mock_image():
    """Fixture for mock image."""
    image = MagicMock(spec=Image)
    image.id = "image_123"
    image.title = "Test Image"
    image.is_dirty = MagicMock(return_value=True)
    image.save = AsyncMock()
    image.__type_name__ = "Image"
    return image


@pytest.fixture
def mock_scene():
    """Fixture for mock scene."""
    scene = MagicMock(spec=Scene)
    scene.id = "scene_123"
    scene.title = "Test Scene"
    scene.is_dirty = MagicMock(return_value=True)
    scene.save = AsyncMock()
    scene.__type_name__ = "Scene"
    return scene


@pytest.fixture
def mock_media():
    """Fixture for mock media."""
    media = MagicMock(spec=Media)
    media.id = "media_123"
    media.stash_id = None
    media.mimetype = "image/jpeg"
    media.filename = "test_image.jpg"
    media.is_downloaded = True
    media.variants = []
    media.awaitable_attrs = MagicMock()
    media.awaitable_attrs.variants = AsyncMock()
    media.awaitable_attrs.mimetype = AsyncMock()
    media.awaitable_attrs.is_downloaded = AsyncMock()
    return media


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
    attachment.is_aggregated_post = False
    attachment.aggregated_post = None
    attachment.awaitable_attrs = MagicMock()
    attachment.awaitable_attrs.bundle = AsyncMock()
    attachment.awaitable_attrs.is_aggregated_post = AsyncMock()
    attachment.awaitable_attrs.aggregated_post = AsyncMock()
    return attachment


@pytest.fixture
def mock_post(mock_attachment):
    """Fixture for mock post."""
    post = MagicMock(spec=Post)
    post.id = 12345
    post.accountId = 54321
    post.content = "Test post content"
    post.createdAt = datetime(2024, 4, 1, 12, 0, 0)
    post.attachments = [mock_attachment]
    post.hashtags = []
    post.accountMentions = []
    post.awaitable_attrs = MagicMock()
    post.awaitable_attrs.attachments = post.attachments
    post.awaitable_attrs.hashtags = []
    post.awaitable_attrs.accountMentions = []
    return post


@pytest.fixture
def mock_posts(mock_account):
    """Fixture for mock posts with attachments."""
    posts = []
    for i in range(3):
        # Create a unique attachment for each post
        attachment = MagicMock(spec=Attachment)
        attachment.id = f"attachment_{i+1}"
        attachment.contentId = f"content_{i+1}"
        attachment.contentType = "ACCOUNT_MEDIA"

        # Create media
        media_mock = MagicMock()
        media_mock.media = MagicMock()
        media_mock.media.id = f"media_{i+1}"
        media_mock.media.mimetype = "image/jpeg"
        media_mock.media.stash_id = None
        media_mock.media.awaitable_attrs = MagicMock()
        media_mock.media.awaitable_attrs.variants = AsyncMock(return_value=[])
        media_mock.media.awaitable_attrs.mimetype = AsyncMock(return_value="image/jpeg")
        media_mock.media.awaitable_attrs.is_downloaded = AsyncMock(return_value=True)

        attachment.media = media_mock
        attachment.bundle = None
        attachment.is_aggregated_post = False
        attachment.aggregated_post = None
        attachment.awaitable_attrs = MagicMock()
        attachment.awaitable_attrs.bundle = AsyncMock()
        attachment.awaitable_attrs.is_aggregated_post = AsyncMock()
        attachment.awaitable_attrs.aggregated_post = AsyncMock()

        # Create post with the attachment
        post = MagicMock(spec=Post)
        post.id = f"post_{i+1}"
        post.accountId = mock_account.id
        post.content = f"Test post content {i+1}"
        post.createdAt = datetime(2024, 4, 1, 12, 0, 0)
        post.attachments = [attachment]
        post.hashtags = []
        post.accountMentions = []
        post.awaitable_attrs = MagicMock()
        post.awaitable_attrs.attachments = AsyncMock(return_value=post.attachments)
        post.awaitable_attrs.hashtags = AsyncMock(return_value=[])
        post.awaitable_attrs.accountMentions = AsyncMock(return_value=[])

        posts.append(post)
    return posts


@pytest.fixture
def mock_group(mock_account):
    """Fixture for mock group."""
    group = MagicMock(spec=Group)
    group.id = "group_123"
    group.users = [mock_account]
    return group


@pytest.fixture
def mock_message(mock_group, mock_attachment):
    """Fixture for mock message."""
    message = MagicMock(spec=Message)
    message.id = 67890
    message.content = "Test message content"
    message.createdAt = datetime(2024, 4, 1, 12, 0, 0)
    message.attachments = [mock_attachment]
    message.hashtags = []
    message.accountMentions = []
    message.group = mock_group
    message.awaitable_attrs = MagicMock()
    message.awaitable_attrs.attachments = message.attachments
    message.awaitable_attrs.hashtags = []
    message.awaitable_attrs.accountMentions = []
    return message


@pytest.fixture
def mock_messages(mock_group):
    """Fixture for mock messages with attachments."""
    messages = []
    for i in range(3):
        # Create a unique attachment for each message
        attachment = MagicMock(spec=Attachment)
        attachment.id = f"msg_attachment_{i+1}"
        attachment.contentId = f"msg_content_{i+1}"
        attachment.contentType = "ACCOUNT_MEDIA"

        # Create media
        media_mock = MagicMock()
        media_mock.media = MagicMock()
        media_mock.media.id = f"msg_media_{i+1}"
        media_mock.media.mimetype = "image/jpeg"
        media_mock.media.stash_id = None
        media_mock.media.awaitable_attrs = MagicMock()
        media_mock.media.awaitable_attrs.variants = AsyncMock(return_value=[])
        media_mock.media.awaitable_attrs.mimetype = AsyncMock(return_value="image/jpeg")
        media_mock.media.awaitable_attrs.is_downloaded = AsyncMock(return_value=True)

        attachment.media = media_mock
        attachment.bundle = None
        attachment.is_aggregated_post = False
        attachment.aggregated_post = None
        attachment.awaitable_attrs = MagicMock()
        attachment.awaitable_attrs.bundle = AsyncMock()
        attachment.awaitable_attrs.is_aggregated_post = AsyncMock()
        attachment.awaitable_attrs.aggregated_post = AsyncMock()

        # Create message with the attachment
        message = MagicMock(spec=Message)
        message.id = f"message_{i+1}"
        message.content = f"Test message content {i+1}"
        message.createdAt = datetime(2024, 4, 1, 12, 0, 0)
        message.attachments = [attachment]
        message.hashtags = []
        message.accountMentions = []
        message.group = mock_group
        message.awaitable_attrs = MagicMock()
        message.awaitable_attrs.attachments = AsyncMock(
            return_value=message.attachments
        )
        message.awaitable_attrs.hashtags = AsyncMock(return_value=[])
        message.awaitable_attrs.accountMentions = AsyncMock(return_value=[])

        messages.append(message)
    return messages


@pytest.fixture
def stash_processor(mock_config, mock_state, mock_context, mock_database):
    """Fixture for StashProcessing instance."""
    # Disable prints for testing
    with (
        patch("stash.processing.print_info"),
        patch("stash.processing.print_warning"),
        patch("stash.processing.print_error"),
    ):
        processor = StashProcessing.from_config(mock_config, mock_state)

        # Replace context and database
        processor.context = mock_context
        processor.database = mock_database

        # Disable progress bars
        processor._setup_batch_processing = AsyncMock(
            return_value=(
                MagicMock(),  # task_pbar
                MagicMock(),  # process_pbar
                asyncio.Semaphore(2),  # semaphore
                asyncio.Queue(),  # queue
            )
        )

        # Return the processor
        yield processor
