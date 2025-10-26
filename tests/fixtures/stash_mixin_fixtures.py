"""Fixtures for Stash mixin testing.

This module provides TestMixinClass definitions and Mock fixtures for Stash types
used in unit tests for StashProcessing mixins.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.fixtures.database_fixtures import AwaitableAttrsMock

from stash.processing.mixins.batch import BatchProcessingMixin
from stash.processing.mixins.content import ContentProcessingMixin
from stash.processing.mixins.gallery import GalleryProcessingMixin
from stash.processing.mixins.media import MediaProcessingMixin
from stash.processing.mixins.tag import TagProcessingMixin
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


__all__ = [
    # Mixin test classes
    "batch_mixin",
    "content_mixin",
    "gallery_mixin",
    "media_mixin",
    "tag_mixin",
    # Batch processing fixtures
    "mock_items",
    "mock_progress_bars",
    "mock_semaphore",
    "mock_process_batch",
    "mock_queue",
    # Client fixtures
    "mock_client_mixin",
    # Stash type mocks (external API objects)
    "mock_image",
    "mock_scene",
    "mock_performer",
    "mock_studio",
    "mock_tag",
    "mock_gallery",
    "mock_image_file",
    "mock_video_file",
    # Gallery test fixture aliases
    "gallery_mock_performer",
    "gallery_mock_studio",
    "mock_item",
]


# ============================================================================
# Mixin Test Classes
# ============================================================================


class TestBatchMixin(BatchProcessingMixin):
    """Test class that implements BatchProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.log = MagicMock()
        self.context = MagicMock()
        self.context.client = MagicMock()
        self._find_existing_performer = AsyncMock()
        self._find_existing_studio = AsyncMock()


class TestContentMixin(ContentProcessingMixin):
    """Test class that implements ContentProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.context = MagicMock()
        self.context.client = MagicMock()
        self.log = MagicMock()

        # Set up database attribute with proper async context manager
        self.database = MagicMock()
        session_mock = MagicMock()

        # Mock async context manager
        async def session_context():
            yield session_mock

        self.database.async_session_scope = MagicMock(
            return_value=session_context().__aiter__()
        )

        # Mock methods this mixin needs from others
        self._setup_worker_pool = AsyncMock(
            return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())
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


class TestGalleryMixin(GalleryProcessingMixin):
    """Test class that implements GalleryProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.context = MagicMock()
        self.context.client = MagicMock()
        self.log = MagicMock()
        self.database = MagicMock()

        # Add process_creator_attachment method
        self.process_creator_attachment = AsyncMock()

        # Mock methods this mixin depends on
        self._find_existing_performer = AsyncMock()
        self._process_hashtags_to_tags = AsyncMock()
        self._generate_title_from_content = MagicMock(return_value="Test Title")
        self._get_file_from_stash_obj = MagicMock()
        self._create_nested_path_or_conditions = MagicMock()
        self._update_account_stash_id = AsyncMock()


class TestMediaMixin(MediaProcessingMixin):
    """Test class that implements MediaProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.context = MagicMock()
        self.context.client = MagicMock()
        self.log = MagicMock()

        # Mock methods this mixin depends on
        self._find_existing_performer = AsyncMock()
        self._find_existing_studio = AsyncMock()
        self._process_hashtags_to_tags = AsyncMock()
        self._generate_title_from_content = MagicMock(return_value="Test Title")
        self._add_preview_tag = AsyncMock()
        self._update_account_stash_id = AsyncMock()

        # Add database attribute since it's used in process_creator_attachment
        self.database = MagicMock()
        self.database.async_session_scope = AsyncMock()
        session_mock = AsyncMock()
        self.database.async_session_scope.return_value.__aenter__.return_value = (
            session_mock
        )

        # Custom implementation of _get_file_from_stash_obj for tests
        def get_file_from_stash_obj(stash_obj):
            if isinstance(stash_obj, Image):
                if stash_obj.visual_files:
                    for file_data in stash_obj.visual_files:
                        if isinstance(file_data, dict):
                            if "basename" not in file_data:
                                file_data["basename"] = "image.jpg"
                            if "parent_folder_id" not in file_data:
                                file_data["parent_folder_id"] = "folder_123"
                            if "fingerprints" not in file_data:
                                file_data["fingerprints"] = []
                            if "mod_time" not in file_data:
                                file_data["mod_time"] = None
                            file = ImageFile(**file_data)
                        else:
                            file = file_data
                        return file
            elif isinstance(stash_obj, Scene):
                if stash_obj.files:
                    return stash_obj.files[0]
            return None

        self._get_file_from_stash_obj = get_file_from_stash_obj

        # Custom implementation for _create_nested_path_or_conditions
        def create_nested_path_or_conditions(media_ids):
            if len(media_ids) == 1:
                return {"path": {"modifier": "INCLUDES", "value": media_ids[0]}}
            conditions = {"OR": {}}
            current = conditions["OR"]
            for i, media_id in enumerate(media_ids):
                if i == 0:
                    current["path"] = {"modifier": "INCLUDES", "value": media_id}
                elif i == len(media_ids) - 1:
                    current["OR"] = {
                        "path": {"modifier": "INCLUDES", "value": media_id}
                    }
                else:
                    current["OR"] = {
                        "path": {"modifier": "INCLUDES", "value": media_id},
                        "OR": {},
                    }
                    current = current["OR"]
            return conditions

        self._create_nested_path_or_conditions = create_nested_path_or_conditions


class TestTagMixin(TagProcessingMixin):
    """Test class that implements TagProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.context = MagicMock()
        self.context.client = MagicMock()
        self.database = MagicMock()
        self.log = MagicMock()


# ============================================================================
# Mixin Fixtures
# ============================================================================


@pytest.fixture
def batch_mixin():
    """Fixture for batch mixin test class."""
    return TestBatchMixin()


@pytest.fixture
def content_mixin():
    """Fixture for content mixin test class."""
    return TestContentMixin()


@pytest.fixture
def gallery_mixin():
    """Fixture for gallery mixin test class."""
    return TestGalleryMixin()


@pytest.fixture
def media_mixin():
    """Fixture for media mixin test class."""
    return TestMediaMixin()


@pytest.fixture
def tag_mixin():
    """Fixture for TagProcessingMixin instance."""
    return TestTagMixin()


# ============================================================================
# Batch Processing Fixtures
# ============================================================================


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


# ============================================================================
# Client Fixtures
# ============================================================================


@pytest.fixture
def mock_client_mixin():
    """Create a mock client mixin for testing StashClient mixins.

    This fixture provides a mock implementation of a client mixin with common
    methods used by various StashClient mixins (like PerformerMixin, SceneMixin, etc.)
    mocked for testing. This allows testing mixin functionality without requiring
    a full client implementation.

    The mock includes essential methods that most mixins would use:
    - execute: For making GraphQL requests
    - find_by_id: For retrieving a single object by ID
    - find_all: For retrieving multiple objects

    Returns:
        MagicMock: A mock client mixin with common methods configured as AsyncMocks
    """
    mixin = MagicMock()
    mixin.execute = AsyncMock()
    mixin.find_by_id = AsyncMock()
    mixin.find_all = AsyncMock()
    return mixin


# ============================================================================
# Stash Type Mocks (External API Objects)
# ============================================================================


@pytest.fixture
def mock_image():
    """Mock image fixture with mockable async save method."""
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
    image.organized = False
    image.__type_name__ = "Image"
    image.is_dirty = MagicMock(return_value=True)

    # Make save an AsyncMock so it's both awaitable and mockable
    image.save = AsyncMock()
    return image


@pytest.fixture
def mock_scene():
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

    scene.save = awaitable_save
    return scene


@pytest.fixture
def mock_performer():
    """Mock performer fixture for unit tests."""
    performer = MagicMock(spec=Performer)
    performer.id = "performer_123"
    performer.name = "test_user"
    return performer


@pytest.fixture
def mock_studio():
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


# ============================================================================
# Gallery Test Fixtures (Aliases for consistency with gallery tests)
# ============================================================================


@pytest.fixture
def gallery_mock_performer(mock_performer):
    """Fixture for mock performer used in gallery tests.

    This is an alias to the standard mock_performer fixture.
    """
    return mock_performer


@pytest.fixture
def gallery_mock_studio(mock_studio):
    """Fixture for mock studio used in gallery tests.

    This is an alias to the standard mock_studio fixture.
    """
    return mock_studio


@pytest.fixture
def mock_item():
    """Fixture for mock content item (Post or Message) used in tests.

    Provides a mock Post/Message with proper attributes and awaitable async attrs.
    Uses the generic AwaitableAttrsMock to handle ANY async relationship access.
    """
    item = MagicMock()
    item.id = 12345
    item.content = "Test content #test #hashtag"
    item.createdAt = datetime(2024, 4, 1, 12, 0, 0)
    item.accountId = 12345
    item.stash_id = None
    item.__class__.__name__ = "Post"

    # Setup default mentions (can be overridden in tests)
    mention1 = MagicMock()
    mention1.id = 67890
    mention1.username = "mentioned_user1"
    mention1.stash_id = None

    mention2 = MagicMock()
    mention2.id = 67891
    mention2.username = "mentioned_user2"
    mention2.stash_id = None

    item.accountMentions = [mention1, mention2]

    # Setup default hashtags (can be overridden in tests)
    item.hashtags = []

    # Use the generic AwaitableAttrsMock - automatically handles ALL attributes!
    item.awaitable_attrs = AwaitableAttrsMock(item)

    return item
