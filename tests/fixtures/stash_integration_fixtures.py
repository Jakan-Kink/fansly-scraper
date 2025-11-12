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

from stash.processing import StashProcessing
from stash.types import FindStudiosResultType, Gallery, Image, Scene, StashID, Studio


# Import REAL database fixtures (UUID-isolated PostgreSQL)
# Import aliases from Stash API fixtures for backwards compatibility


# NOTE: This file should ONLY export fixtures defined in this file.
# All fixture aggregation is handled by tests/fixtures/__init__.py


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
# Stash Object Fixtures (Stash API objects - use mocks for external API)
# ============================================================================
# NOTE: Metadata fixtures (Account, Media, Post, Message, etc.) have been moved
# to tests/fixtures/metadata_fixtures.py for better separation of concerns.


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
