"""Common fixtures for media mixin tests."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from metadata import Account, AccountMedia, AccountMediaBundle, Attachment, Media, Post
from stash.processing.mixins.media import MediaProcessingMixin
from stash.types import Image, ImageFile, Scene, VideoFile

from .async_mock_helper import patch_async_methods


class TestMixinClass(MediaProcessingMixin):
    """Test class that implements MediaProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.context = MagicMock()
        self.context.client = MagicMock()
        self.database = MagicMock()
        self.log = MagicMock()
        self._find_existing_performer = AsyncMock()
        self._find_existing_studio = AsyncMock()
        self._process_hashtags_to_tags = AsyncMock()
        self._generate_title_from_content = MagicMock(return_value="Test Title")
        self._add_preview_tag = AsyncMock()
        self._update_account_stash_id = AsyncMock()

        # Custom implementation of _get_file_from_stash_obj for tests
        def get_file_from_stash_obj(stash_obj):
            if isinstance(stash_obj, Image):
                if stash_obj.visual_files:
                    for file_data in stash_obj.visual_files:
                        if isinstance(file_data, dict):
                            # Add required fields if missing
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
    """Fixture for MediaProcessingMixin instance."""
    mixinobj = TestMixinClass()
    return patch_async_methods(mixinobj)


@pytest.fixture
def mock_image():
    """Fixture for mock image."""
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
def mock_scene():
    """Fixture for mock scene."""
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
    scene.is_dirty = MagicMock(return_value=True)

    # Make save awaitable
    orig_save = AsyncMock()

    async def awaitable_save(client):
        orig_save(client)
        return None

    scene.save = awaitable_save
    return scene


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
def mock_item():
    """Fixture for mock item (Post or Message)."""
    item = MagicMock(spec=Post)
    item.id = 12345
    item.content = "Test content"
    item.createdAt = datetime(2024, 4, 1, 12, 0, 0)
    item.hashtags = []
    item.accountMentions = []
    item.awaitable_attrs = MagicMock()
    item.awaitable_attrs.hashtags = AsyncMock(return_value=[])
    item.awaitable_attrs.accountMentions = AsyncMock(return_value=[])
    item.__class__.__name__ = "Post"
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
