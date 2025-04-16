"""Common fixtures for gallery mixin tests."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from metadata import Account, Post
from stash.processing.mixins.gallery import GalleryProcessingMixin
from stash.types import Gallery, GalleryChapter, Image, Scene, Studio, Tag


class TestMixinClass(GalleryProcessingMixin):
    """Test class that implements GalleryProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.context = MagicMock()
        self.context.client = MagicMock()
        self.database = MagicMock()
        self.log = MagicMock()
        self._find_existing_performer = AsyncMock()
        self._generate_title_from_content = MagicMock(return_value="Test Title")
        self.process_creator_attachment = AsyncMock(
            return_value={"images": [], "scenes": []}
        )


@pytest.fixture
def mixin():
    """Fixture for GalleryProcessingMixin instance."""
    return TestMixinClass()


@pytest.fixture
def mock_gallery():
    """Fixture for mock gallery."""
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
def mock_item():
    """Fixture for mock item (Post or Message)."""
    item = MagicMock(spec=Post)
    item.id = 12345
    item.content = "Test content"
    item.createdAt = datetime(2024, 4, 1, 12, 0, 0)
    item.stash_id = None
    item.attachments = []
    item.hashtags = []
    item.accountMentions = []
    item.awaitable_attrs = MagicMock()
    item.awaitable_attrs.attachments = []
    item.awaitable_attrs.hashtags = []
    item.awaitable_attrs.accountMentions = []
    return item


@pytest.fixture
def mock_account():
    """Fixture for mock account."""
    account = MagicMock(spec=Account)
    account.id = 54321
    account.username = "test_user"
    account.stash_id = None
    account.awaitable_attrs = MagicMock()
    account.awaitable_attrs.username = "test_user"
    return account


@pytest.fixture
def mock_performer():
    """Fixture for mock performer."""
    performer = MagicMock()
    performer.id = "performer_123"
    performer.name = "test_user"
    performer.awaitable_attrs = MagicMock()
    performer.awaitable_attrs.id = "performer_123"
    return performer


@pytest.fixture
def mock_studio():
    """Fixture for mock studio."""
    studio = MagicMock(spec=Studio)
    studio.id = "studio_123"
    studio.name = "Test Studio"
    studio.awaitable_attrs = MagicMock()
    studio.awaitable_attrs.id = "studio_123"
    return studio


@pytest.fixture
def mock_tag():
    """Fixture for mock tag."""
    tag = MagicMock(spec=Tag)
    tag.id = "tag_123"
    tag.name = "test_tag"
    return tag


@pytest.fixture
def mock_image():
    """Fixture for mock image."""
    image = MagicMock(spec=Image)
    image.id = "image_123"
    image.title = "Test Image"
    image.tags = []
    return image


@pytest.fixture
def mock_scene():
    """Fixture for mock scene."""
    scene = MagicMock(spec=Scene)
    scene.id = "scene_123"
    scene.title = "Test Scene"
    scene.tags = []
    return scene
