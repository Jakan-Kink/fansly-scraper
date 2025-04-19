"""Common fixtures for StashProcessing integration tests.

These fixtures extend the unit test fixtures and add integration-specific functionality.
All fixtures in this module build on the base fixtures from unit tests to provide
richer mocks suitable for integration testing.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metadata import Account, Attachment, Group, Media, Message, Post
from stash.processing import StashProcessing
from stash.types import Gallery, Image, Performer, Scene, Studio

# Import and re-export fixtures from parent conftest.py
from ..conftest import (
    AsyncResult,
    AsyncSessionContext,
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
    # Imported fixtures and classes
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
    "AsyncResult",
    # Integration fixtures
    "mock_context",
    "mock_config",
    "mock_state",
    "mock_database",
    "integration_mock_account",
    "integration_mock_performer",
    "integration_mock_studio",
    "mock_gallery",
    "mock_image",
    "integration_mock_scene",
    "mock_item",
    "mock_media_variant",
    "mock_media_bundle",
    "mock_permissions",
    "mock_media",
    "mock_attachment",
    "mock_post",
    "mock_posts",
    "mock_group",
    "mock_message",
    "mock_messages",
    "stash_processor",
    "mock_multiple_posts",
    "mock_multiple_messages",
]


class MockContext:
    """Mock StashContext for testing StashProcessing integration."""

    def __init__(self):
        """Initialize mock context with a pre-configured mock client."""
        self.client = MagicMock()

        # Mock common client methods for finding objects
        self.client.find_performer = AsyncMock()  # Find performer by criteria
        self.client.find_studio = AsyncMock()  # Find studio by criteria
        self.client.find_gallery = AsyncMock()  # Find single gallery by criteria
        self.client.find_galleries = AsyncMock()  # Find multiple galleries
        self.client.find_scene = AsyncMock()  # Find single scene by criteria
        self.client.find_scenes = AsyncMock()  # Find multiple scenes
        self.client.find_image = AsyncMock()  # Find single image by criteria
        self.client.find_images = AsyncMock()  # Find multiple images
        self.client.find_tags = AsyncMock()  # Find tags by criteria

        # Mock common client methods for creating/modifying objects
        self.client.create_tag = AsyncMock()  # Create new tag
        self.client.add_gallery_images = AsyncMock(
            return_value=True
        )  # Add images to gallery


class MockConfig:
    """Mock configuration for testing StashProcessing integration."""

    def __init__(self):
        """Initialize mock config with default test values."""
        # Stash configuration section
        self.stash = MagicMock()
        self.stash.enabled = True  # Enable Stash integration
        self.stash.url = "http://localhost:9999"  # Test server URL
        self.stash.api_key = "test_api_key"  # Test API key

        # Stash connection configuration (used directly by StashContext)
        self.stash_context_conn = {
            "scheme": "http",
            "host": "localhost",
            "port": 9999,
            "apikey": "test_api_key",
        }

        # Metadata database configuration
        self.metadata = MagicMock()
        self.metadata.db_path = "test.db"  # Test database path

        # Internal attributes for instance tracking
        self._database = None  # Database instance
        self._stash = None  # StashContext instance
        self._background_tasks = []  # Background task list

    def get_stash_context(self):
        """Get Stash context."""
        if self._stash is None:
            self._stash = MockContext()
        return self._stash

    def get_background_tasks(self):
        """Get background tasks list."""
        return self._background_tasks


class MockState:
    """Mock application state for testing StashProcessing integration."""

    def __init__(self):
        """Initialize mock state with default test values."""
        self.creator_id = "12345"  # Test creator ID
        self.creator_name = "test_user"  # Test creator username
        self.messages_enabled = True  # Enable message processing
        self.verbose_logs = True  # Enable verbose logging


class MockDatabase:
    """Mock database connection for testing StashProcessing integration."""

    def __init__(self):
        """Initialize mock database with mocked session and operations."""
        # Create mock session with async session spec
        self.session_maker = MagicMock()
        self.session = MagicMock(spec=AsyncSession)

        # Configure session async context manager behavior
        self.session.__aenter__ = AsyncMock(return_value=self.session)
        self.session.__aexit__ = AsyncMock()

        # Create an AsyncResult instance to wrap
        self._async_result = AsyncResult()

        # Configure session execute to return AsyncResult
        async def mock_execute(*args, **kwargs):
            return self._async_result

        self.session.execute = AsyncMock(side_effect=mock_execute)

        # Other session methods
        self.session.add = MagicMock()
        self.session.refresh = AsyncMock()
        self.session.flush = AsyncMock()
        self.session.query = MagicMock()
        self.session.commit = AsyncMock()
        self.session.rollback = AsyncMock()

        # Configure session maker to return the session
        self.get_async_session = MagicMock(return_value=self.session)

    async def async_session_scope(self):
        """Async context manager for database session."""
        return AsyncSessionContext(self.session)

    def reset_session_mocks(self):
        """Reset all session mocks to clean state."""
        # Reset all attributes that have a reset_mock method
        for name, attr in self.session.__dict__.items():
            if hasattr(attr, "reset_mock"):
                attr.reset_mock()

    def set_result(self, result):
        """Set the result to be returned by execute/scalar calls."""
        if isinstance(result, (list, tuple)):
            for item in result:
                if item and not getattr(item, "createdAt", None):
                    item.createdAt = datetime(2024, 4, 1, 12, 0, 0)
        elif result and not getattr(result, "createdAt", None):
            result.createdAt = datetime(2024, 4, 1, 12, 0, 0)
        self._async_result._result = result
        return self


@pytest.fixture
def mock_context():
    """Fixture for mock StashClient context for integration testing."""
    return MockContext()


@pytest.fixture
def mock_config():
    """Fixture for mock configuration for integration testing."""
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
def integration_mock_account():
    """Fixture for mock account in integration tests."""
    account = MagicMock(spec=Account)
    account.id = 54321
    account.username = "test_user"
    account.stash_id = None
    account.awaitable_attrs = MagicMock()
    account.awaitable_attrs.username = AsyncMock(return_value="test_user")
    account.awaitable_attrs.avatar = AsyncMock(return_value=None)
    account.awaitable_attrs.hashtags = AsyncMock(return_value=[])
    account.awaitable_attrs.accountMentions = AsyncMock(return_value=[])
    return account


@pytest.fixture
def integration_mock_performer():
    """Fixture for mock performer in integration tests."""
    performer = MagicMock()
    performer.id = "performer_123"
    performer.name = "test_user"
    performer.url = None
    performer.gender = None
    performer.birthdate = None
    performer.ethnicity = None
    performer.country = None
    performer.eye_color = None
    performer.height = None
    performer.measurements = None
    performer.fake_tits = None
    performer.career_length = None
    performer.tattoos = None
    performer.piercings = None
    performer.aliases = []
    performer.tags = []
    performer.rating = None
    performer.favorite = False
    performer.created_at = datetime(2024, 4, 1, 12, 0, 0)
    return performer


@pytest.fixture
def integration_mock_studio():
    """Fixture for mock studio in integration tests."""
    studio = MagicMock()
    studio.id = "studio_123"
    studio.name = "test_user Studio"
    studio.url = None
    studio.parent_studio = None
    studio.child_studios = []
    studio.aliases = []
    studio.tags = []
    studio.rating = None
    studio.favorite = False
    studio.created_at = datetime(2024, 4, 1, 12, 0, 0)
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
def integration_mock_scene():
    """Fixture for mock scene in integration tests."""
    scene = MagicMock(spec=Scene)
    scene.id = "scene_123"
    scene.title = "Test Scene"
    scene.is_dirty = MagicMock(return_value=True)
    scene.save = AsyncMock()
    scene.__type_name__ = "Scene"
    return scene


@pytest.fixture
def mock_item():
    """Fixture for mock item (generic content item like post or message)."""
    item = MagicMock()
    item.id = "item_123"
    item.content = "Test item content"
    item.createdAt = datetime(2024, 4, 1, 12, 0, 0)
    item.attachments = []
    item.hashtags = []
    item.accountMentions = []
    return item


@pytest.fixture
def mock_media_variant():
    """Fixture for mock media variant."""
    variant = MagicMock()
    variant.id = "variant_123"
    variant.type = 302  # HLS stream type
    variant.status = 1
    variant.mimetype = "application/vnd.apple.mpegurl"
    variant.flags = 0
    variant.width = 1920
    variant.height = 1080
    variant.metadata = '{"variants":[{"w":1920,"h":1080},{"w":1280,"h":720}]}'
    variant.updatedAt = datetime(2024, 4, 1, 12, 0, 0)
    variant.locations = [
        {"locationId": "102", "location": "https://example.com/test.m3u8"}
    ]
    return variant


@pytest.fixture
def mock_media_bundle():
    """Fixture for mock media bundle."""
    bundle = MagicMock()
    bundle.id = "bundle_123"
    bundle.accountId = "54321"
    bundle.previewId = None
    bundle.permissionFlags = 0
    bundle.price = 0
    bundle.createdAt = datetime(2024, 4, 1, 12, 0, 0)
    bundle.deletedAt = None
    bundle.deleted = False
    bundle.accountMediaIds = ["media_1", "media_2"]
    bundle.bundleContent = [
        {"accountMediaId": "media_1", "pos": 0},
        {"accountMediaId": "media_2", "pos": 1},
    ]
    bundle.permissions = {
        "permissionFlags": [
            {
                "id": "perm_123",
                "type": 0,
                "flags": 2,
                "price": 0,
                "metadata": "",
                "verificationFlags": 2,
            }
        ],
        "accountPermissionFlags": {
            "flags": 6,
            "metadata": '{"4":"{\\"subscriptionTierId\\":\\"tier_123\\"}"}',
        },
    }
    bundle.purchased = False
    bundle.whitelisted = False
    bundle.accountPermissionFlags = 6
    bundle.access = True

    # Mock account media to be returned by awaitable_attrs.accountMedia
    account_media1 = MagicMock()
    account_media1.media = MagicMock(id="media_1")
    account_media1.preview = MagicMock(id="preview_1")

    account_media2 = MagicMock()
    account_media2.media = MagicMock(id="media_2")
    account_media2.preview = None

    bundle.accountMedia = [account_media1, account_media2]

    # Set awaitable_attrs properly
    bundle.awaitable_attrs = MagicMock()
    bundle.awaitable_attrs.accountMedia = AsyncMock(return_value=bundle.accountMedia)
    bundle.awaitable_attrs.accountMediaIds = AsyncMock(
        return_value=bundle.accountMediaIds
    )
    bundle.awaitable_attrs.id = AsyncMock(return_value=bundle.id)

    return bundle


@pytest.fixture
def mock_permissions():
    """Fixture for mock content permissions."""
    return {
        "permissionFlags": [
            {
                "id": "perm_123",
                "type": 0,
                "flags": 2,
                "price": 0,
                "metadata": "",
                "validAfter": None,
                "validBefore": None,
                "verificationFlags": 2,
                "verificationMetadata": "{}",
            }
        ],
        "accountPermissionFlags": {
            "flags": 6,
            "metadata": '{"4":"{\\"subscriptionTierId\\":\\"tier_123\\"}"}',
        },
    }


# Update existing mock_media fixture to include variants
@pytest.fixture
def mock_media(mock_media_variant):
    """Updated fixture for mock media with variants."""
    media = MagicMock(spec=Media)
    media.id = "media_123"
    media.stash_id = None
    media.type = 2  # Video type
    media.status = 1
    media.mimetype = "video/mp4"
    media.flags = 298
    media.filename = "test_video.mp4"
    media.width = 1920
    media.height = 1080
    media.metadata = '{"duration":20.667,"frameRate":30}'
    media.is_downloaded = True
    media.variants = {
        mock_media_variant
    }  # Initialize with a set containing the variant
    media.locations = [{"locationId": "1", "location": "https://example.com/test.mp4"}]
    # Set up awaitable_attrs properly
    media.awaitable_attrs = MagicMock()
    # Use a set constructor to ensure we're returning a proper set
    media.awaitable_attrs.variants = AsyncMock(return_value={mock_media_variant})
    media.awaitable_attrs.mimetype = AsyncMock(return_value=media.mimetype)
    media.awaitable_attrs.is_downloaded = AsyncMock(return_value=True)
    media.awaitable_attrs.id = AsyncMock(return_value=media.id)
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
    attachment.awaitable_attrs.bundle = AsyncMock(return_value=None)
    attachment.awaitable_attrs.is_aggregated_post = AsyncMock(return_value=False)
    attachment.awaitable_attrs.aggregated_post = AsyncMock(return_value=None)
    attachment.awaitable_attrs.media = AsyncMock(return_value=attachment.media)
    return attachment


@pytest.fixture
def mock_post(mock_attachment, mock_permissions):
    """Updated fixture for mock post with additional fields."""
    post = MagicMock(spec=Post)
    post.id = 12345
    post.accountId = 54321
    post.content = "Test post content #test"
    post.fypFlags = 0
    post.inReplyTo = None
    post.inReplyToRoot = None
    post.createdAt = datetime(2024, 4, 1, 12, 0, 0)
    post.expiresAt = None
    post.attachments = [mock_attachment]
    post.likeCount = 5
    post.replyCount = 1
    post.mediaLikeCount = 3
    post.totalTipAmount = 0
    post.attachmentTipAmount = 0
    post.hashtags = ["test"]
    post.accountMentions = []
    post.permissions = mock_permissions
    post.awaitable_attrs = MagicMock()
    post.awaitable_attrs.attachments = AsyncMock(return_value=post.attachments)
    post.awaitable_attrs.hashtags = AsyncMock(return_value=post.hashtags)
    post.awaitable_attrs.accountMentions = AsyncMock(return_value=post.accountMentions)
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
        media_mock.media.awaitable_attrs.variants = AsyncMock(
            return_value=set()
        )  # Return empty set instead of list
        media_mock.media.awaitable_attrs.mimetype = AsyncMock(return_value="image/jpeg")
        media_mock.media.awaitable_attrs.is_downloaded = AsyncMock(return_value=True)
        media_mock.media.awaitable_attrs.id = AsyncMock(
            return_value=media_mock.media.id
        )

        attachment.media = media_mock
        attachment.bundle = None
        attachment.is_aggregated_post = False
        attachment.aggregated_post = None
        attachment.awaitable_attrs = MagicMock()
        attachment.awaitable_attrs.bundle = AsyncMock(return_value=None)
        attachment.awaitable_attrs.is_aggregated_post = AsyncMock(return_value=False)
        attachment.awaitable_attrs.aggregated_post = AsyncMock(return_value=None)
        attachment.awaitable_attrs.media = AsyncMock(return_value=attachment.media)

        # Create post with the attachment
        post = MagicMock(spec=Post)
        post.id = f"post_{i+1}"
        post.accountId = mock_account.id
        post.content = f"Test post content {i+1}"
        post.createdAt = datetime(2024, 4, 1, 12, 0, 0)
        post.attachments = [attachment]
        post.hashtags = []
        post.accountMentions = []
        post.permissions = {}
        post.stash_id = None

        # Set up awaitable attributes properly
        post.awaitable_attrs = MagicMock()
        post.awaitable_attrs.attachments = AsyncMock(return_value=post.attachments)
        post.awaitable_attrs.hashtags = AsyncMock(return_value=[])
        post.awaitable_attrs.accountMentions = AsyncMock(return_value=[])
        post.awaitable_attrs.id = AsyncMock(return_value=post.id)
        post.awaitable_attrs.accountId = AsyncMock(return_value=post.accountId)
        post.awaitable_attrs.content = AsyncMock(return_value=post.content)
        post.awaitable_attrs.createdAt = AsyncMock(return_value=post.createdAt)
        post.awaitable_attrs.permissions = AsyncMock(return_value={})
        post.awaitable_attrs.fypFlags = AsyncMock(return_value=0)
        post.awaitable_attrs.inReplyTo = AsyncMock(return_value=None)
        post.awaitable_attrs.inReplyToRoot = AsyncMock(return_value=None)
        post.awaitable_attrs.expiresAt = AsyncMock(return_value=None)
        post.awaitable_attrs.likeCount = AsyncMock(return_value=0)
        post.awaitable_attrs.replyCount = AsyncMock(return_value=0)
        post.awaitable_attrs.mediaLikeCount = AsyncMock(return_value=0)
        post.awaitable_attrs.totalTipAmount = AsyncMock(return_value=0)
        post.awaitable_attrs.attachmentTipAmount = AsyncMock(return_value=0)

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
    message.awaitable_attrs.attachments = AsyncMock(return_value=message.attachments)
    message.awaitable_attrs.hashtags = AsyncMock(return_value=[])
    message.awaitable_attrs.accountMentions = AsyncMock(return_value=[])
    message.awaitable_attrs.group = AsyncMock(return_value=mock_group)
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
        media_mock.media.awaitable_attrs.variants = AsyncMock(
            return_value=set()
        )  # Return empty set instead of list
        media_mock.media.awaitable_attrs.mimetype = AsyncMock(return_value="image/jpeg")
        media_mock.media.awaitable_attrs.is_downloaded = AsyncMock(return_value=True)
        media_mock.media.awaitable_attrs.id = AsyncMock(
            return_value=media_mock.media.id
        )

        attachment.media = media_mock
        attachment.bundle = None
        attachment.is_aggregated_post = False
        attachment.aggregated_post = None
        attachment.awaitable_attrs = MagicMock()
        attachment.awaitable_attrs.bundle = AsyncMock(return_value=None)
        attachment.awaitable_attrs.is_aggregated_post = AsyncMock(return_value=False)
        attachment.awaitable_attrs.aggregated_post = AsyncMock(return_value=None)
        attachment.awaitable_attrs.media = AsyncMock(return_value=attachment.media)

        # Create message with the attachment
        message = MagicMock(spec=Message)
        message.id = f"message_{i+1}"
        message.content = f"Test message content {i+1}"
        message.createdAt = datetime(2024, 4, 1, 12, 0, 0)
        message.attachments = [attachment]
        message.hashtags = []
        message.accountMentions = []
        message.group = mock_group
        message.stash_id = None

        # Set up proper awaitable attributes
        message.awaitable_attrs = MagicMock()
        message.awaitable_attrs.attachments = AsyncMock(
            return_value=message.attachments
        )
        message.awaitable_attrs.hashtags = AsyncMock(return_value=[])
        message.awaitable_attrs.accountMentions = AsyncMock(return_value=[])
        message.awaitable_attrs.group = AsyncMock(return_value=mock_group)
        message.awaitable_attrs.id = AsyncMock(return_value=message.id)
        message.awaitable_attrs.content = AsyncMock(return_value=message.content)
        message.awaitable_attrs.createdAt = AsyncMock(return_value=message.createdAt)

        messages.append(message)
    return messages


@pytest.fixture(scope="function")
def stash_processor(mock_config, mock_state, mock_context, mock_database):
    """Fixture for StashProcessing instance configured for integration testing."""
    # Update config to use our database and context
    mock_config._database = mock_database
    mock_config._stash = mock_context

    # Disable prints for testing
    with (
        patch("stash.processing.print_info"),
        patch("stash.processing.print_warning"),
        patch("stash.processing.print_error"),
    ):
        processor = StashProcessing.from_config(mock_config, mock_state)

        # No need to replace context and database anymore since from_config
        # will use the ones from our mock_config

        # Disable progress bars
        processor._setup_batch_processing = AsyncMock(
            return_value=(
                MagicMock(),  # task_pbar
                MagicMock(),  # process_pbar
                asyncio.Semaphore(2),  # semaphore
                asyncio.Queue(),  # queue
            )
        )

        # Add scan_to_stash method for tests
        async def scan_to_stash(self):
            """Test method to scan for media and process it to stash."""
            try:
                # Find the account
                account = await self._find_account()
                if not account:
                    return

                # Find or create performer
                performer = await self._find_existing_performer(account)

                # Find or create studio
                studio = await self._find_existing_studio(account)

                # Process posts
                await self.process_creator_posts(account, performer, studio)

                # Process messages
                await self.process_creator_messages(account, performer, studio)

                return account, performer, studio
            except Exception as e:
                print(f"Error in scan_to_stash: {str(e)}")

        # Add the method to the processor
        processor.scan_to_stash = scan_to_stash.__get__(processor, StashProcessing)

        # Return the processor
        yield processor


@pytest.fixture
def mock_multiple_posts(mock_account):
    """Fixture for multiple posts with various features for integration testing."""
    posts = []
    for i in range(5):
        # Create a unique attachment for each post
        attachment = MagicMock(spec=Attachment)
        attachment.id = f"multi_attachment_{i+1}"
        attachment.contentId = f"multi_content_{i+1}"
        attachment.contentType = "ACCOUNT_MEDIA"

        # Create media
        media_mock = MagicMock()
        media_mock.media = MagicMock()
        media_mock.media.id = f"multi_media_{i+1}"
        media_mock.media.mimetype = "image/jpeg" if i % 2 == 0 else "video/mp4"
        media_mock.media.stash_id = None
        media_mock.media.awaitable_attrs = MagicMock()
        media_mock.media.awaitable_attrs.variants = AsyncMock(
            return_value=set()
        )  # Return empty set instead of list
        media_mock.media.awaitable_attrs.mimetype = AsyncMock(
            return_value=media_mock.media.mimetype
        )
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
        post.id = f"multi_post_{i+1}"
        post.accountId = mock_account.id
        post.content = f"Test post content {i+1} with #hashtag{i+1}"
        post.createdAt = datetime(2024, 4, 1 + i, 12, 0, 0)  # Different dates
        post.attachments = [attachment]
        post.hashtags = [f"hashtag{i+1}"]

        # Every other post has account mentions
        if i % 2 == 0:
            mention = MagicMock()
            mention.username = f"mentioned_user{i}"
            post.accountMentions = [mention]
        else:
            post.accountMentions = []

        post.awaitable_attrs = MagicMock()
        post.awaitable_attrs.attachments = AsyncMock(return_value=post.attachments)
        post.awaitable_attrs.hashtags = AsyncMock(return_value=post.hashtags)
        post.awaitable_attrs.accountMentions = AsyncMock(
            return_value=post.accountMentions
        )

        posts.append(post)
    return posts


@pytest.fixture
def mock_multiple_messages(mock_group):
    """Fixture for multiple messages with various features for integration testing."""
    messages = []
    for i in range(5):
        # Create a unique attachment for each message
        attachment = MagicMock(spec=Attachment)
        attachment.id = f"multi_msg_attachment_{i+1}"
        attachment.contentId = f"multi_msg_content_{i+1}"
        attachment.contentType = "ACCOUNT_MEDIA"

        # Create media
        media_mock = MagicMock()
        media_mock.media = MagicMock()
        media_mock.media.id = f"multi_msg_media_{i+1}"
        media_mock.media.mimetype = "image/jpeg" if i % 2 == 0 else "video/mp4"
        media_mock.media.stash_id = None
        media_mock.media.awaitable_attrs = MagicMock()
        media_mock.media.awaitable_attrs.variants = AsyncMock(
            return_value=set()
        )  # Return empty set instead of list
        media_mock.media.awaitable_attrs.mimetype = AsyncMock(
            return_value=media_mock.media.mimetype
        )
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
        message.id = f"multi_message_{i+1}"
        message.content = f"Test message content {i+1} with #hashtag{i+1}"
        message.createdAt = datetime(2024, 4, 1 + i, 12, 0, 0)  # Different dates
        message.attachments = (
            [attachment] if i % 2 == 0 else []
        )  # Some messages don't have attachments
        message.hashtags = [f"hashtag{i+1}"] if i % 2 == 0 else []
        message.accountMentions = []
        message.group = mock_group
        message.awaitable_attrs = MagicMock()
        message.awaitable_attrs.attachments = AsyncMock(
            return_value=message.attachments
        )
        message.awaitable_attrs.hashtags = AsyncMock(return_value=message.hashtags)
        message.awaitable_attrs.accountMentions = AsyncMock(
            return_value=message.accountMentions
        )
        message.awaitable_attrs.group = AsyncMock(return_value=mock_group)

        messages.append(message)
    return messages
