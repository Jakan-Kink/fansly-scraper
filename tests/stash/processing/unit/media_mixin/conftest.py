"""Common fixtures for media mixin tests."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from metadata import Account, AccountMedia, AccountMediaBundle, Attachment, Media, Post
from stash.processing.mixins.media import MediaProcessingMixin
from stash.types import Image, ImageFile, Scene, VideoFile

# Import and re-export fixtures from parent conftest.py
from ..conftest import (
    mock_account,
    mock_account_media,
    mock_attachment,
    mock_image,
    mock_image_file,
    mock_media,
    mock_media_bundle,
    mock_scene,
    mock_video_file,
)

__all__ = [
    "mock_account",
    "mock_scene",
    "mock_image",
    "mock_image_file",
    "mock_video_file",
    "mock_media",
    "mock_account_media",
    "mock_media_bundle",
    "mock_attachment",
    "media_mock_scene",
    "mock_item",
    "media_mock_account",
    "mixin",
]


class TestMixinClass(MediaProcessingMixin):
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
            else:
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


@pytest.fixture
def mixin():
    """Fixture for media mixin test class."""
    return TestMixinClass()


# Using parent fixtures without redefinition


@pytest.fixture
def media_mock_scene(mock_scene):
    """Fixture for mock scene with media-specific attributes."""
    # Add media mixin specific attributes
    mock_scene.details = "Test details"
    mock_scene.date = "2024-04-01"
    mock_scene.code = None
    mock_scene.urls = []
    mock_scene.files = []
    mock_scene.performers = []
    mock_scene.studio = None
    mock_scene.tags = []
    mock_scene.__type_name__ = "Scene"

    # Override save method with custom implementation
    orig_save = AsyncMock()

    async def awaitable_save(client):
        orig_save(client)
        return None

    mock_scene.save = awaitable_save
    return mock_scene


# Using parent fixtures without redefinition


@pytest.fixture
def mock_item():
    """Mock item fixture with media requirements."""
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

    # Set up both direct access and awaitable access to the same list
    item.hashtags = []
    item.accountMentions = mentions_list
    item.awaitable_attrs = MagicMock()
    item.awaitable_attrs.hashtags = AsyncMock(return_value=[])
    item.awaitable_attrs.accountMentions = AsyncMock(return_value=mentions_list)
    item.__class__.__name__ = "Post"
    return item


# Using pytest.fixture(name="mock_account") to avoid conflicts with imported fixture
@pytest.fixture
def media_mock_account(mock_account):
    """Fixture for mock account for media tests."""
    # Add any media-specific attributes
    mock_account.awaitable_attrs.hashtags = AsyncMock(return_value=[])
    mock_account.awaitable_attrs.accountMentions = AsyncMock(return_value=[])
    return mock_account


# Using parent fixtures without redefinition
