"""Common fixtures for StashProcessing integration tests - REFACTORED.

These fixtures use real database objects created with FactoryBoy factories
instead of mocks. This eliminates AsyncMock await errors and makes tests
more reliable and maintainable.

Key changes from original:
- ✅ Real PostgreSQL database with UUID isolation
- ✅ Real Account, Media, Post, Message objects from factories
- ✅ No more AwaitableAttrs complexity
- ✅ No more AsyncMock await errors
- ❌ Removed MockDatabase class
- ❌ Removed AwaitableAttrs class
- ❌ Removed AccessibleAsyncMock usage for database objects
- ✅ Keep mocking Stash API client (external HTTP requests)
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from metadata.account import AccountMedia, AccountMediaBundle
from metadata.attachment import ContentType
from metadata.media import Media
from stash.processing import StashProcessing
from stash.types import Gallery, Image, Scene
from tests.fixtures import MetadataGroupFactory  # Import SQLAlchemy Group factory
from tests.fixtures import (
    AccountFactory,
    AttachmentFactory,
    MediaFactory,
    MessageFactory,
    PostFactory,
)
from tests.fixtures.database_fixtures import config  # Real PostgreSQL config
from tests.fixtures.database_fixtures import session  # Real async session
from tests.fixtures.database_fixtures import (
    session_sync,  # Real sync session (used by factories)
)
from tests.fixtures.stash_api_fixtures import (
    mock_account as stash_mock_account,  # Rename to avoid conflict
)
from tests.fixtures.stash_api_fixtures import (
    mock_client,
)
from tests.fixtures.stash_api_fixtures import mock_performer as base_mock_performer
from tests.fixtures.stash_api_fixtures import mock_scene as base_mock_scene
from tests.fixtures.stash_api_fixtures import mock_studio as base_mock_studio
from tests.fixtures.stash_api_fixtures import (
    mock_transport,
    stash_client,
    stash_context,
    test_query,
)

__all__ = [
    # Re-exported fixtures
    "stash_mock_account",
    "mock_client",
    "base_mock_performer",
    "base_mock_studio",
    "base_mock_scene",
    "mock_transport",
    "stash_client",
    "stash_context",
    "test_query",
    # Database fixtures (imported from metadata conftest)
    "config",
    "session",
    "session_sync",
    # Integration fixtures (refactored with factories)
    "mock_context",
    "mock_config",
    "mock_state",
    "integration_mock_account",
    "integration_mock_performer",
    "integration_mock_studio",
    "mock_gallery",
    "mock_image",
    "integration_mock_scene",
    "mock_permissions",
    "mock_media",
    "mock_attachment",
    "mock_post",
    "mock_posts",
    "mock_group",
    "mock_message",
    "mock_messages",
    "mock_account_media",
    "mock_media_bundle",
    "stash_processor",
]


# ============================================================================
# Stash API Mocks (Keep these - they mock external HTTP requests)
# ============================================================================


class MockContext:
    """Mock StashContext for testing StashProcessing integration.

    This mocks the Stash API client to avoid real HTTP requests.
    We still use mocks for external services!
    """

    def __init__(self):
        """Initialize mock context with a pre-configured mock client."""
        self.client = MagicMock()

        # Mock common client methods for finding objects
        self.client.find_performer = AsyncMock()
        self.client.find_studio = AsyncMock()
        self.client.find_gallery = AsyncMock()
        self.client.find_galleries = AsyncMock()
        self.client.find_scene = AsyncMock()
        self.client.find_scenes = AsyncMock()
        self.client.find_image = AsyncMock()
        self.client.find_images = AsyncMock()
        self.client.find_tags = AsyncMock()

        # Mock common client methods for creating/modifying objects
        self.client.create_tag = AsyncMock()
        self.client.add_gallery_images = AsyncMock(return_value=True)


class MockConfig:
    """Mock configuration for testing StashProcessing integration.

    This provides a config that uses our real database but mocked Stash API.
    """

    def __init__(self, database=None):
        """Initialize mock config with default test values."""
        # Stash configuration section
        self.stash = MagicMock()
        self.stash.enabled = True
        self.stash.url = "http://localhost:9999"
        self.stash.api_key = "test_api_key"

        # Stash connection configuration
        self.stash_context_conn = {
            "scheme": "http",
            "host": "localhost",
            "port": 9999,
            "apikey": "test_api_key",
        }

        # Metadata database configuration
        self.metadata = MagicMock()
        self.metadata.db_path = "test.db"

        # Internal attributes
        self._database = database  # Will be set to real database
        self._stash = None
        self._background_tasks = []

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
        self.creator_id = "12345"
        self.creator_name = "test_user"
        self.messages_enabled = True
        self.verbose_logs = True


@pytest.fixture
def mock_context():
    """Fixture for mock StashClient context for integration testing."""
    return MockContext()


@pytest.fixture
def mock_config(test_database_sync):
    """Fixture for mock configuration with REAL database.

    This is a key change: We now use a real database instead of MockDatabase!
    """
    return MockConfig(database=test_database_sync)


@pytest.fixture
def mock_state():
    """Fixture for mock application state."""
    return MockState()


# ============================================================================
# Database Object Fixtures (Using Factories - No More Mocks!)
# ============================================================================


@pytest.fixture
def integration_mock_account(session_sync, factory_session):
    """Create REAL Account using factory instead of mock.

    This replaces AccessibleAsyncMock with a real SQLAlchemy object.
    No more AwaitableAttrs complexity - SQLAlchemy handles it!

    Args:
        session_sync: Sync database session
        factory_session: Factory session to configure FactoryBoy
    """
    # Use .build() to create without requiring session, then manually add/commit
    account = AccountFactory.build(
        id=54321,
        username="test_user",
    )
    session_sync.add(account)
    session_sync.commit()
    session_sync.refresh(account)  # Ensure object is attached to session
    return account


@pytest.fixture
def mock_media(session_sync, integration_mock_account):
    """Create REAL Media using factory instead of mock.

    No more MagicMock, no more awaitable_attrs complexity!

    Args:
        session_sync: Sync database session
        integration_mock_account: Account that owns this media
    """
    media = MediaFactory.build(
        id=20123,
        accountId=integration_mock_account.id,
        mimetype="video/mp4",
        type=2,  # Video type
        is_downloaded=True,
        width=1920,
        height=1080,
    )
    session_sync.add(media)
    session_sync.commit()
    session_sync.refresh(media)
    return media


@pytest.fixture
def mock_group(session_sync, integration_mock_account):
    """Create REAL Group using factory instead of mock.

    Args:
        session_sync: Database session
        integration_mock_account: Account fixture (required for FK constraint)

    Note:
        Groups require an Account to exist (createdBy foreign key).
        Uses integration_mock_account to satisfy this constraint.
    """
    group = MetadataGroupFactory.build(
        id=40123,
        createdBy=integration_mock_account.id,  # Use real account ID
    )
    session_sync.add(group)
    session_sync.commit()
    session_sync.refresh(group)
    return group


@pytest.fixture
def mock_attachment(session_sync, integration_mock_account, mock_media):
    """Create REAL Attachment using factory instead of mock."""
    attachment = AttachmentFactory.build(
        id=60123,
        contentId=30123,  # Will be updated by tests
        contentType=ContentType.ACCOUNT_MEDIA,  # Use enum, not string
        accountMediaId=mock_media.id,
    )
    session_sync.add(attachment)
    session_sync.commit()
    session_sync.refresh(attachment)
    return attachment


@pytest.fixture
def mock_post(session_sync, integration_mock_account, mock_attachment):
    """Create REAL Post using factory instead of AccessibleAsyncMock."""
    post = PostFactory.build(
        id=12345,
        accountId=integration_mock_account.id,
        content="Test post content #test",
        likeCount=5,
        replyCount=1,
        mediaLikeCount=3,
    )
    session_sync.add(post)
    session_sync.commit()

    # Update attachment to link to this post
    mock_attachment.contentId = post.id
    mock_attachment.contentType = ContentType.ACCOUNT_MEDIA
    session_sync.add(mock_attachment)
    session_sync.commit()

    session_sync.refresh(post)
    return post


@pytest.fixture
def mock_posts(session_sync, integration_mock_account):
    """Create multiple REAL Posts using factories.

    This shows how easy batch creation is with factories!
    """
    posts = []
    for i in range(3):
        # Create media
        media = MediaFactory.build(
            accountId=integration_mock_account.id,
            mimetype="image/jpeg",
        )
        session_sync.add(media)

        # Create post
        post = PostFactory.build(
            accountId=integration_mock_account.id,
            content=f"Test post content {i + 1}",
        )
        session_sync.add(post)

        # Create attachment
        attachment = AttachmentFactory.build(
            contentId=post.id,
            contentType=ContentType.ACCOUNT_MEDIA,
            accountMediaId=media.id,
        )
        session_sync.add(attachment)

        posts.append(post)

    # Commit all at once
    session_sync.commit()

    # Refresh all objects
    for post in posts:
        session_sync.refresh(post)

    return posts


@pytest.fixture
def mock_message(session_sync, mock_group, integration_mock_account, mock_attachment):
    """Create REAL Message using factory instead of AccessibleAsyncMock."""
    message = MessageFactory.build(
        id=67890,
        groupId=mock_group.id,
        senderId=integration_mock_account.id,
        content="Test message content",
    )
    session_sync.add(message)
    session_sync.commit()

    # Update attachment to link to this message
    mock_attachment.contentId = message.id
    mock_attachment.contentType = ContentType.ACCOUNT_MEDIA
    session_sync.add(mock_attachment)
    session_sync.commit()

    session_sync.refresh(message)
    return message


@pytest.fixture
def mock_messages(session_sync, mock_group, integration_mock_account):
    """Create multiple REAL Messages using factories."""
    messages = []
    for i in range(3):
        # Create media
        media = MediaFactory.build(
            accountId=integration_mock_account.id,
            mimetype="image/jpeg" if i % 2 == 0 else "video/mp4",
        )
        session_sync.add(media)

        # Create message
        message = MessageFactory.build(
            groupId=mock_group.id,
            senderId=integration_mock_account.id,
            content=f"Test message content {i + 1}",
        )
        session_sync.add(message)

        # Create attachment (some messages don't have attachments)
        if i % 2 == 0:
            attachment = AttachmentFactory.build(
                contentId=message.id,
                contentType=ContentType.ACCOUNT_MEDIA,
                accountMediaId=media.id,
            )
            session_sync.add(attachment)

        messages.append(message)

    # Commit all at once
    session_sync.commit()

    # Refresh all objects
    for message in messages:
        session_sync.refresh(message)

    return messages


@pytest.fixture
def mock_permissions():
    """Fixture for mock content permissions.

    This is just a dict, not a database object, so we keep it as-is.
    """
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


# ============================================================================
# Stash Object Fixtures (Keep as mocks - these are external API objects)
# ============================================================================


@pytest.fixture
def integration_mock_performer():
    """Fixture for mock Stash performer.

    This is a Stash API object, not a database object, so we keep mocking it.
    """
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
    """Fixture for mock Stash studio."""
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
    """Fixture for mock Stash gallery."""
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
    """Fixture for mock Stash image."""
    image = MagicMock(spec=Image)
    image.id = "image_123"
    image.title = "Test Image"
    image.is_dirty = MagicMock(return_value=True)
    image.save = AsyncMock()
    image.__type_name__ = "Image"
    return image


@pytest.fixture
def integration_mock_scene():
    """Fixture for mock Stash scene."""
    scene = MagicMock(spec=Scene)
    scene.id = "scene_123"
    scene.title = "Test Scene"
    scene.is_dirty = MagicMock(return_value=True)
    scene.save = AsyncMock()
    scene.__type_name__ = "Scene"
    return scene


@pytest.fixture
def mock_account_media():
    """Fixture for mock AccountMedia."""
    account_media = MagicMock(spec=AccountMedia)
    account_media.id = 123456
    account_media.accountId = 12345
    account_media.mediaId = 67890

    # Create associated media mock
    media = MagicMock(spec=Media)
    media.id = 67890
    media.mimetype = "image/jpeg"
    media.local_filename = "test_image.jpg"
    media.content_hash = "abcdef123456"
    media.is_downloaded = True

    account_media.media = media
    return account_media


@pytest.fixture
def mock_media_bundle():
    """Fixture for mock AccountMediaBundle."""
    bundle = MagicMock(spec=AccountMediaBundle)
    bundle.id = 111222
    bundle.accountId = 12345
    bundle.accountMedia = []  # Will be populated by tests if needed
    return bundle


# ============================================================================
# StashProcessor Fixture (Updated to use real database)
# ============================================================================


@pytest.fixture(scope="function")
def stash_processor(mock_config, mock_state, mock_context):
    """Fixture for StashProcessing instance with REAL database.

    Key change: mock_config now contains a REAL database instance,
    not a MockDatabase!
    """
    # mock_config already has real database set
    mock_config._stash = mock_context

    # Disable prints for testing
    with (
        patch("stash.processing.print_info"),
        patch("stash.processing.print_warning"),
        patch("stash.processing.print_error"),
    ):
        processor = StashProcessing.from_config(mock_config, mock_state)

        # Disable progress bars
        processor._setup_worker_pool = AsyncMock(
            return_value=(
                "task_name",  # task_name
                "process_name",  # process_name
                asyncio.Semaphore(2),  # semaphore
                asyncio.Queue(),  # queue
            )
        )

        yield processor
