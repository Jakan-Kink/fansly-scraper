"""Common fixtures for StashProcessing integration tests - REAL OBJECTS.

These fixtures use REAL database objects and REAL Stash API connections:
- ✅ Real PostgreSQL database with UUID isolation per test
- ✅ Real FanslyConfig (not mocked)
- ✅ Real Database instances
- ✅ Real Account, Media, Post, Message objects from FactoryBoy
- ✅ Real StashContext connecting to Docker Stash (localhost:9999)
- ✅ No MockConfig, no MockDatabase, no fake attributes
- ✅ Stash API calls hit real Docker instance (or can be mocked per test)

Philosophy:
- Mock ONLY external services when necessary (Stash API can be mocked OR real)
- Use REAL database objects everywhere
- Use factories for test data creation
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from metadata.account import AccountMedia
from metadata.attachment import ContentType
from metadata.media import Media
from stash.processing import StashProcessing
from stash.types import FindStudiosResultType, Gallery, Image, Scene, StashID, Studio
from tests.fixtures import (
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    MessageFactory,
    MetadataGroupFactory,
    PostFactory,
)

# Import REAL database fixtures (UUID-isolated PostgreSQL)
from tests.fixtures.database_fixtures import (
    config,  # Real FanslyConfig with UUID database
    session,  # Real async session
    session_sync,  # Real sync session (used by factories)
    test_database_sync,  # Real Database instance
)

# Import REAL Stash fixtures (connects to Docker)
from tests.fixtures.stash_api_fixtures import (
    mock_account as stash_mock_account,
)
from tests.fixtures.stash_api_fixtures import (
    mock_client,
    mock_transport,
    stash_client,
    stash_context,
    test_query,
)
from tests.fixtures.stash_api_fixtures import (
    mock_performer as base_mock_performer,
)
from tests.fixtures.stash_api_fixtures import (
    mock_scene as base_mock_scene,
)
from tests.fixtures.stash_api_fixtures import (
    mock_studio as base_mock_studio,
)


__all__ = [
    # Stash API mocks (for unit tests)
    "base_mock_performer",
    "base_mock_scene",
    "base_mock_studio",
    # Real database fixtures (UUID-isolated PostgreSQL)
    "config",  # Real FanslyConfig
    # Production data fixtures
    "fansly_network_studio",  # Production Fansly network studio fixture
    # Database object fixtures (using FactoryBoy)
    "integration_mock_account",
    "integration_mock_performer",
    "integration_mock_scene",
    "integration_mock_studio",
    "mock_account_media",
    "mock_attachment",
    "mock_client",
    "mock_context",  # Backwards compat alias for mock_stash_context
    "mock_gallery",
    "mock_group",
    "mock_image",
    "mock_media",
    "mock_media_bundle",
    "mock_message",
    "mock_messages",
    "mock_permissions",
    "mock_post",
    "mock_posts",
    "mock_stash_context",  # Mocked StashContext
    # Test state
    "mock_state",
    "mock_studio_finder",  # Mock find_studios function and creator studio factory
    "mock_transport",
    "real_stash_processor",  # With real Docker Stash
    "session",  # Real async session
    "session_sync",  # Real sync session
    # Real Stash fixtures (for integration tests)
    "stash_client",  # Real StashClient connected to Docker
    "stash_context",  # Real StashContext connected to Docker
    "stash_mock_account",
    # StashProcessing fixtures
    "stash_processor",  # With mocked Stash API
    "test_database_sync",  # Real Database instance
    "test_query",
]


# ============================================================================
# Test State and Context Fixtures
# ============================================================================


class TestState:
    """Real download state for testing."""

    def __init__(self):
        """Initialize test state with default values."""
        self.creator_id = "12345"
        self.creator_name = "test_user"
        self.messages_enabled = True
        self.verbose_logs = False  # Keep logs quiet in tests


@pytest.fixture
def mock_state():
    """Fixture for test download state."""
    return TestState()


@pytest.fixture
def mock_stash_context():
    """Fixture for MOCKED StashContext (for tests that don't need real Stash).

    This creates a mock StashContext with all API methods mocked.
    Use `stash_context` fixture for real Docker Stash connection.
    """
    context = MagicMock()
    context.client = MagicMock()

    # Mock common client methods for finding objects
    context.client.find_performer = AsyncMock()
    context.client.find_studio = AsyncMock()
    context.client.find_gallery = AsyncMock()
    context.client.find_galleries = AsyncMock()
    context.client.find_scene = AsyncMock()
    context.client.find_scenes = AsyncMock()
    context.client.find_image = AsyncMock()
    context.client.find_images = AsyncMock()
    context.client.find_tags = AsyncMock()

    # Mock common client methods for creating/modifying objects
    context.client.create_tag = AsyncMock()
    context.client.create_performer = AsyncMock()
    context.client.create_studio = AsyncMock()
    context.client.create_gallery = AsyncMock()
    context.client.create_scene = AsyncMock()
    context.client.add_gallery_images = AsyncMock(return_value=True)

    return context


@pytest.fixture
def mock_context():
    """Backwards compatibility alias for mock_stash_context.

    Provides a mocked StashContext for tests that don't need real Stash.
    """
    context = MagicMock()
    context.client = MagicMock()

    # Mock common client methods for finding objects
    context.client.find_performer = AsyncMock()
    context.client.find_studio = AsyncMock()
    context.client.find_gallery = AsyncMock()
    context.client.find_galleries = AsyncMock()
    context.client.find_scene = AsyncMock()
    context.client.find_scenes = AsyncMock()
    context.client.find_image = AsyncMock()
    context.client.find_images = AsyncMock()
    context.client.find_tags = AsyncMock()

    # Mock common client methods for creating/modifying objects
    context.client.create_tag = AsyncMock()
    context.client.create_performer = AsyncMock()
    context.client.create_studio = AsyncMock()
    context.client.create_gallery = AsyncMock()
    context.client.create_scene = AsyncMock()
    context.client.add_gallery_images = AsyncMock(return_value=True)

    return context


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
    # First create an AccountMedia that links the Media to an Account
    account_media = AccountMediaFactory.build(
        id=70123,
        accountId=integration_mock_account.id,
        mediaId=mock_media.id,
    )
    session_sync.add(account_media)
    session_sync.commit()

    # Create attachment that references the AccountMedia
    attachment = AttachmentFactory.build(
        id=60123,
        contentId=account_media.id,  # References AccountMedia.id
        contentType=ContentType.ACCOUNT_MEDIA,
        postId=None,  # Will be updated by tests if needed
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
    )
    session_sync.add(post)
    session_sync.commit()

    # Update attachment to link to this post
    mock_attachment.postId = post.id
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

        # Create AccountMedia to link Media to Account
        account_media = AccountMediaFactory.build(
            accountId=integration_mock_account.id,
            mediaId=media.id,
        )
        session_sync.add(account_media)

        # Create post
        post = PostFactory.build(
            accountId=integration_mock_account.id,
            content=f"Test post content {i + 1}",
        )
        session_sync.add(post)

        # Create attachment that references the AccountMedia
        attachment = AttachmentFactory.build(
            contentId=account_media.id,  # References AccountMedia.id
            contentType=ContentType.ACCOUNT_MEDIA,
            postId=post.id,
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
            # Create AccountMedia to link Media to Account
            account_media = AccountMediaFactory.build(
                accountId=integration_mock_account.id,
                mediaId=media.id,
            )
            session_sync.add(account_media)

            attachment = AttachmentFactory.build(
                contentId=account_media.id,  # References AccountMedia.id
                contentType=ContentType.ACCOUNT_MEDIA,
                messageId=message.id,
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
    performer.created_at = datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC)
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
    studio.created_at = datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC)
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
def mock_media_bundle(session_sync, integration_mock_account):
    """Create REAL AccountMediaBundle using factory instead of mock."""
    bundle = AccountMediaBundleFactory.build(
        id=111222,
        accountId=integration_mock_account.id,
    )
    session_sync.add(bundle)
    session_sync.commit()
    session_sync.refresh(bundle)
    return bundle


# ============================================================================
# StashProcessing Fixtures
# ============================================================================


@pytest.fixture
def stash_processor(config, test_database_sync, mock_state, mock_stash_context):
    """Fixture for StashProcessing with REAL database and MOCKED Stash API.

    This is for tests that need real database operations but can mock Stash API.

    Args:
        config: Real FanslyConfig with UUID-isolated database
        test_database_sync: Real Database instance
        mock_state: Test download state
        mock_stash_context: Mocked StashContext (no real HTTP calls)

    Yields:
        StashProcessing: Processor with real DB, mocked Stash
    """
    # Set up config with real database and mocked stash
    config._database = test_database_sync
    config._stash = mock_stash_context

    # Disable prints for testing
    with (
        patch("textio.textio.print_info"),
        patch("textio.textio.print_warning"),
        patch("textio.textio.print_error"),
    ):
        processor = StashProcessing.from_config(config, mock_state)

        # Disable progress bars
        processor._setup_worker_pool = AsyncMock(
            return_value=(
                "task_name",
                "process_name",
                asyncio.Semaphore(2),
                asyncio.Queue(),
            )
        )

        yield processor


@pytest_asyncio.fixture
async def real_stash_processor(config, test_database_sync, mock_state, stash_context):
    """Fixture for StashProcessing with REAL database and REAL Docker Stash.

    This is for true integration tests that hit the real Stash instance.

    Args:
        config: Real FanslyConfig with UUID-isolated database
        test_database_sync: Real Database instance
        mock_state: Test download state
        stash_context: Real StashContext connected to Docker (localhost:9999)

    Yields:
        StashProcessing: Fully functional processor hitting real services
    """
    # Set up config with real database and real stash
    config._database = test_database_sync
    config._stash = stash_context

    # Disable prints for testing
    with (
        patch("textio.textio.print_info"),
        patch("textio.textio.print_warning"),
        patch("textio.textio.print_error"),
    ):
        processor = StashProcessing.from_config(config, mock_state)
        yield processor
        # Cleanup happens via fixtures


@pytest.fixture
def fansly_network_studio():
    """Fixture providing the 'Fansly (network)' studio from production.

    This matches the real studio data from production Stash instance.
    Used to mock find_studios("Fansly (network)") calls.
    """
    return Studio(
        id="246",
        name="Fansly (network)",
        url="",
        parent_studio=None,
        aliases=[],
        tags=[],
        stash_ids=[
            StashID(
                stash_id="f03173b3-1c0e-43bc-ac30-0cc445316c80",
                endpoint="https://fansdb.cc/graphql",
            )
        ],
        details="",
    )


@pytest.fixture
def mock_studio_finder(fansly_network_studio):
    """Fixture providing a mock find_studios function and creator studio factory.

    Returns a tuple of (mock_find_studios_fn, creator_studio_factory) where:
    - mock_find_studios_fn: async function that returns Fansly network studio or empty result
    - creator_studio_factory: function that creates a mock creator studio for an account

    Usage:
        mock_find_studios_fn, create_creator_studio = mock_studio_finder
        mock_creator_studio = create_creator_studio(account)

        with patch.object(client, 'find_studios', new=AsyncMock(side_effect=mock_find_studios_fn)):
            ...
    """
    from tests.fixtures.stash_type_factories import StudioFactory

    async def mock_find_studios_fn(q=None, **kwargs):
        """Mock find_studios that returns Fansly network studio or empty result."""
        if q == "Fansly (network)":
            import strawberry

            fansly_studio_dict = strawberry.asdict(fansly_network_studio)
            return FindStudiosResultType(count=1, studios=[fansly_studio_dict])
        # For creator-specific studio search, return empty (will create new)
        return FindStudiosResultType(count=0, studios=[])

    def create_creator_studio(account, studio_id="999"):
        """Create a mock creator studio for the given account."""
        return StudioFactory(
            id=studio_id,
            name=f"{account.username} (Fansly)",
            url=f"https://fansly.com/{account.username}",
            parent_studio=fansly_network_studio,
        )

    return mock_find_studios_fn, create_creator_studio
