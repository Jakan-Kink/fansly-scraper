"""Unit test fixtures for StashProcessing.

This module centralizes fixture imports from all subdirectories to ensure fixtures are available
for all unit tests, regardless of their location in the test hierarchy.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tqdm import tqdm

from metadata import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Attachment,
    Group,
    Media,
    Message,
    Post,
)
from stash.processing.mixins.batch import BatchProcessingMixin
from stash.types import (
    Gallery,
    Image,
    ImageFile,
    Performer,
    Scene,
    Studio,
    Tag,
    VideoFile,
)
from tests.stash.processing.unit.media_mixin.async_mock_helper import (
    AccessibleAsyncMock,
)

# Import and re-export fixtures from parent conftest.py
from ..conftest import (
    json_conversation_data,
    json_messages_group_data,
    json_timeline_data,
    mock_account,
    mock_client,
    mock_performer,
    mock_scene,
    mock_session,
    mock_studio,
    mock_transport,
    stash_cleanup_tracker,
    stash_client,
    stash_context,
    test_query,
)

__all__ = [
    "json_conversation_data",
    "json_messages_group_data",
    "json_timeline_data",
    "mock_account",
    "mock_performer",
    "mock_studio",
    "mock_scene",
    "mock_client",
    "mock_session",
    "mock_transport",
    "stash_client",
    "stash_context",
    "stash_cleanup_tracker",
    "test_query",
    # Unit-specific fixtures
    "unit_mock_account",
    "unit_mock_performer",
    "unit_mock_studio",
    "unit_mock_scene",
    # Batch fixtures
    "mixin",
    "mock_items",
    "mock_progress_bars",
    "mock_semaphore",
    "mock_process_batch",
    "mock_queue",
]


class TestMixinClass(BatchProcessingMixin):
    """Test class that implements BatchProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.log = MagicMock()
        self.context = MagicMock()
        self.context.client = MagicMock()
        self._find_existing_performer = AsyncMock()
        self._find_existing_studio = AsyncMock()


@pytest.fixture
def mixin():
    """Fixture for batch mixin test class."""
    return TestMixinClass()


@pytest.fixture
def mock_items():
    """Fixture for mock items."""
    return [MagicMock() for _ in range(10)]


@pytest.fixture
def mock_progress_bars():
    """Fixture for mock progress bars."""
    task_pbar = MagicMock()
    task_pbar.set_description = MagicMock()
    task_pbar.set_postfix = MagicMock()
    task_pbar.update = MagicMock()
    task_pbar.close = MagicMock()

    process_pbar = MagicMock()
    process_pbar.set_description = MagicMock()
    process_pbar.update = MagicMock()
    process_pbar.close = MagicMock()

    return task_pbar, process_pbar


@pytest.fixture
def mock_semaphore():
    """Fixture for mock asyncio.Semaphore."""
    semaphore = MagicMock()
    semaphore._value = 4  # Max concurrency
    semaphore.__aenter__ = AsyncMock()
    semaphore.__aexit__ = AsyncMock()
    return semaphore


@pytest.fixture
def mock_process_batch():
    """Fixture for mock process_batch function."""
    return AsyncMock()


@pytest.fixture
def mock_queue():
    """Fixture for mock asyncio.Queue."""
    queue = MagicMock()
    queue.get = AsyncMock()
    queue.put = AsyncMock()
    queue.task_done = MagicMock()
    return queue


@pytest.fixture
def mock_item():
    """Mock item fixture."""
    item = AccessibleAsyncMock(spec=Post)
    item.id = 12345
    item.content = "Test content"
    item.createdAt = datetime(2024, 4, 1, 12, 0, 0)

    # Create actual MagicMocks for account mentions that can be accessed as list
    mention1 = MagicMock()
    mention1.username = "mentioned_user1"
    mention2 = MagicMock()
    mention2.username = "mentioned_user2"
    mentions_list = [mention1, mention2]

    # Set up both direct access and awaitable access to the same list
    item.hashtags = []
    item.accountMentions = mentions_list
    item.awaitable_attrs = MagicMock()
    item.awaitable_attrs.hashtags = AsyncMock(return_value=[])
    item.awaitable_attrs.accountMentions = AsyncMock(return_value=mentions_list)
    item.__class__.__name__ = "Post"
    return item


@pytest.fixture
def mock_image():
    """Mock image fixture."""
    image = MagicMock(spec=Image)
    image.id = "image_123"
    image.title = "Test Image"
    image.details = "Test details"
    image.date = "2024-04-01"
    image.code = None
    image.urls = []
    image.visual_files = []
    image.performers = []
    image.studio = None
    image.tags = []
    image.__type_name__ = "Image"
    image.is_dirty = MagicMock(return_value=True)

    # Make save awaitable
    orig_save = AsyncMock()

    async def awaitable_save(client):
        orig_save(client)
        return None

    image.save = awaitable_save
    return image


@pytest.fixture
def unit_mock_scene():
    """Mock scene fixture for unit tests."""
    scene = MagicMock(spec=Scene)
    scene.id = "scene_123"
    scene.title = "Test Scene"
    scene.details = "Test details"
    scene.date = "2024-04-01"
    scene.code = None
    scene.urls = []
    scene.files = []
    scene.performers = []
    scene.studio = None
    scene.tags = []
    scene.__type_name__ = "Scene"

    # Make save awaitable
    orig_save = AsyncMock()

    async def awaitable_save(client):
        orig_save(client)
        return None

    scene.save = awaitable_save
    return scene


@pytest.fixture
def unit_mock_performer():
    """Mock performer fixture for unit tests."""
    performer = MagicMock(spec=Performer)
    performer.id = "performer_123"
    performer.name = "test_user"
    return performer


@pytest.fixture
def unit_mock_studio():
    """Mock studio fixture for unit tests."""
    studio = MagicMock(spec=Studio)
    studio.id = "studio_123"
    studio.name = "Test Studio"
    return studio


@pytest.fixture
def mock_tag():
    """Mock tag fixture."""
    tag = MagicMock(spec=Tag)
    tag.id = "tag_123"
    tag.name = "test_tag"
    return tag


@pytest.fixture
def mock_image_file():
    """Fixture for mock image file."""
    file = MagicMock(spec=ImageFile)
    file.id = "file_123"
    file.path = "/path/to/image.jpg"
    file.size = 12345
    file.width = 1920
    file.height = 1080
    file.fingerprints = []
    file.mod_time = "2024-04-01T12:00:00Z"
    return file


@pytest.fixture
def mock_video_file():
    """Fixture for mock video file."""
    file = MagicMock(spec=VideoFile)
    file.id = "file_456"
    file.path = "/path/to/video.mp4"
    file.size = 123456
    file.duration = 60.0
    file.video_codec = "h264"
    file.audio_codec = "aac"
    file.width = 1920
    file.height = 1080
    file.fingerprints = []
    file.mod_time = "2024-04-01T12:00:00Z"
    return file


@pytest.fixture
def mock_media():
    """Fixture for mock media."""
    media = MagicMock(spec=Media)
    media.id = "media_123"
    media.stash_id = None
    media.mimetype = "image/jpeg"
    media.filename = "test_image.jpg"
    media.is_downloaded = True
    media.variants = set()  # Initialize as empty set
    media.awaitable_attrs = MagicMock()
    media.awaitable_attrs.variants = AsyncMock(return_value=set())  # Return empty set
    media.awaitable_attrs.mimetype = AsyncMock()
    media.awaitable_attrs.is_downloaded = AsyncMock()
    return media


@pytest.fixture
def mock_account_media():
    """Fixture for mock account media."""
    account_media = MagicMock(spec=AccountMedia)
    account_media.id = "account_media_123"
    account_media.media = None
    account_media.preview = None
    return account_media


@pytest.fixture
def mock_media_bundle():
    """Fixture for mock media bundle."""
    bundle = MagicMock(spec=AccountMediaBundle)
    bundle.id = "bundle_123"
    bundle.accountMedia = []
    bundle.preview = None
    bundle.awaitable_attrs = MagicMock()
    bundle.awaitable_attrs.accountMedia = AsyncMock()
    return bundle


@pytest.fixture
def mock_attachment():
    """Fixture for mock attachment."""
    attachment = MagicMock(spec=Attachment)
    attachment.id = "attachment_123"
    attachment.contentId = "content_123"
    attachment.contentType = "ACCOUNT_MEDIA"
    attachment.media = None
    attachment.bundle = None
    attachment.is_aggregated_post = False
    attachment.aggregated_post = None
    attachment.awaitable_attrs = MagicMock()
    attachment.awaitable_attrs.bundle = AsyncMock()
    attachment.awaitable_attrs.is_aggregated_post = AsyncMock()
    attachment.awaitable_attrs.aggregated_post = AsyncMock()
    return attachment


@pytest.fixture
def mock_gallery():
    """Mock gallery fixture."""
    gallery = MagicMock(spec=Gallery)
    gallery.id = "gallery_123"
    gallery.title = "Test Gallery"
    gallery.details = "Test details"
    gallery.code = "12345"
    gallery.date = "2024-04-01"
    gallery.organized = True
    gallery.performers = []
    gallery.tags = []
    gallery.chapters = []
    gallery.scenes = []
    gallery.save = AsyncMock()
    gallery.destroy = AsyncMock()
    return gallery


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
def mock_group():
    """Fixture for mock group."""
    group = MagicMock(spec=Group)
    group.id = "group_123"
    group.users = []
    return group


@pytest.fixture
def unit_mock_account():
    """Base fixture for mock account in unit tests."""
    account = AccessibleAsyncMock(spec=Account)
    account.id = 54321
    account.username = "test_user"
    account.stash_id = None
    account.awaitable_attrs = MagicMock()
    account.awaitable_attrs.username = "test_user"
    return account


@pytest.fixture
def mock_message():
    """Base fixture for mock message."""
    message = AccessibleAsyncMock(spec=Message)
    message.id = 67890
    message.content = "Test message content"
    message.createdAt = datetime(2024, 4, 1, 12, 0, 0)
    message.attachments = []
    message.group = MagicMock()
    message.group.id = "group_123"
    message.awaitable_attrs = MagicMock()
    message.awaitable_attrs.attachments = AsyncMock(return_value=[])
    message.awaitable_attrs.hashtags = AsyncMock(return_value=[])
    message.awaitable_attrs.accountMentions = AsyncMock(return_value=[])
    message.awaitable_attrs.group = AsyncMock(return_value=message.group)
    return message
