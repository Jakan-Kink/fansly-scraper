"""Common fixtures for gallery mixin tests."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from metadata import Account, Post
from stash.processing.mixins.gallery import GalleryProcessingMixin
from stash.types import Gallery, GalleryChapter, Image, Scene, Studio, Tag

# Import and re-export fixtures from parent conftest.py
from ..conftest import (
    mock_account,
    mock_gallery,
    mock_image,
    mock_performer,
    mock_scene,
    mock_studio,
)

__all__ = [
    "mock_account",
    "mock_performer",
    "mock_scene",
    "mock_studio",
    "mock_gallery",
    "mock_image",
    "mixin",
    "mock_item",
    "gallery_mock_account",
    "gallery_mock_performer",
    "gallery_mock_studio",
    "gallery_mock_scene",
]


class TestMixinClass(GalleryProcessingMixin):
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


@pytest.fixture
def mixin():
    """Fixture for gallery mixin test class."""
    return TestMixinClass()


@pytest.fixture
def mock_item():
    """Mock item fixture with gallery requirements."""
    item = MagicMock(spec=Post)
    item.id = 12345
    item.content = "Test content"
    item.createdAt = datetime(2024, 4, 1, 12, 0, 0)

    # Create mentions that can be accessed both directly and through awaitable_attrs
    mention1 = MagicMock()
    mention1.username = "mentioned_user1"
    mention2 = MagicMock()
    mention2.username = "mentioned_user2"
    mentions_list = [mention1, mention2]

    item.hashtags = []
    item.accountMentions = mentions_list
    item.awaitable_attrs = MagicMock()
    item.awaitable_attrs.hashtags = AsyncMock(return_value=[])
    item.awaitable_attrs.accountMentions = AsyncMock(return_value=mentions_list)
    item.__class__.__name__ = "Post"
    return item


@pytest.fixture
def gallery_mock_account(mock_account):
    """Fixture for mock account for gallery tests."""
    # Add gallery-specific attributes & methods
    mock_account.awaitable_attrs = MagicMock()
    # Make awaitable attributes actually awaitable
    mock_account.awaitable_attrs.hashtags = AsyncMock(return_value=[])
    mock_account.awaitable_attrs.accountMentions = AsyncMock(return_value=[])
    mock_account.awaitable_attrs.username = AsyncMock(
        return_value=mock_account.username
    )
    mock_account.awaitable_attrs.avatar = AsyncMock(return_value=None)
    return mock_account


@pytest.fixture
def gallery_mock_performer(mock_performer):
    """Fixture for mock performer with gallery-specific attributes."""
    # Add gallery-specific attributes
    mock_performer.awaitable_attrs = MagicMock()
    mock_performer.awaitable_attrs.id = AsyncMock(return_value=mock_performer.id)
    mock_performer.awaitable_attrs.name = AsyncMock(return_value=mock_performer.name)
    return mock_performer


@pytest.fixture
def gallery_mock_studio(mock_studio):
    """Fixture for mock studio with gallery-specific attributes."""
    # Add gallery-specific attributes
    mock_studio.awaitable_attrs = MagicMock()
    mock_studio.awaitable_attrs.id = AsyncMock(return_value=mock_studio.id)
    mock_studio.awaitable_attrs.name = AsyncMock(return_value=mock_studio.name)
    mock_studio.awaitable_attrs.images = AsyncMock(return_value=[])
    mock_studio.awaitable_attrs.image_path = AsyncMock(return_value=None)
    mock_studio.awaitable_attrs.performers = AsyncMock(return_value=[])
    mock_studio.awaitable_attrs.tags = AsyncMock(return_value=[])
    return mock_studio


@pytest.fixture
def gallery_mock_scene(mock_scene):
    """Fixture for mock scene with gallery-specific attributes."""
    # Add gallery-specific attributes
    mock_scene.save = AsyncMock()
    mock_scene.performers = []
    mock_scene.studio = None
    mock_scene.tags = []
    mock_scene.urls = []
    return mock_scene
