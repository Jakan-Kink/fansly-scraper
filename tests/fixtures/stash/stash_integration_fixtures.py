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
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
import respx

from stash.processing import StashProcessing
from stash.types import FindStudiosResultType, StashID, Studio


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
def test_state():
    """Fixture for test download state (renamed from mock_state for clarity).

    This is a REAL TestState object, not a mock.
    """
    return TestState()


# REMOVED: mock_stash_context, mock_context
# These mocked INTERNAL StashContext.client API methods (find_performer, create_studio, etc.)
# This violates edge-mocking principles - we should NOT mock internal boundaries.
#
# ✅ Replacement options:
#    1. Use `stash_context` fixture for REAL Docker Stash connection (best for integration tests)
#    2. Use @respx.mock to mock HTTP responses (best for unit tests):
#       @respx.mock
#       def test_something():
#           respx.post("http://localhost:9999/graphql").mock(
#               return_value=httpx.Response(200, json={"data": {...}})
#           )
#           client = await StashClient.create(conn={})
#           result = await client.find_performer(...)
#
# See /tmp/mock_to_respx_migration_guide.md for detailed migration examples.


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


# REMOVED: integration_mock_performer, integration_mock_studio, integration_mock_scene
# REMOVED: mock_gallery, mock_image
# These are duplicate MagicMock fixtures.
# ✅ Use real factories from stash_type_factories.py instead:
#    - PerformerFactory / mock_performer
#    - StudioFactory / mock_studio
#    - SceneFactory / mock_scene
#    - GalleryFactory / mock_gallery
#    - ImageFactory / mock_image


# ============================================================================
# StashProcessing Fixtures
# ============================================================================


# REMOVED: stash_processor fixture
# This fixture used mock_stash_context which violated edge-mocking principles.
#
# ✅ Replacement: Use real_stash_processor with respx to mock HTTP responses:
#    @respx.mock
#    def test_something(real_stash_processor):
#        respx.post("http://localhost:9999/graphql").mock(
#            return_value=httpx.Response(200, json={"data": {...}})
#        )
#        # Now real_stash_processor will use real StashClient with mocked HTTP


@pytest_asyncio.fixture
async def real_stash_processor(config, test_database_sync, test_state, stash_context):
    """Fixture for StashProcessing with REAL database and REAL Docker Stash.

    This is for true integration tests that hit the real Stash instance.
    Can be combined with @respx.mock to mock HTTP responses for unit tests.

    NOTE: Automatically initializes the StashContext client with a default
    respx mock. Tests can add their own respx routes to override defaults.

    Args:
        config: Real FanslyConfig with UUID-isolated database
        test_database_sync: Real Database instance
        test_state: Real TestState (download state)
        stash_context: Real StashContext connected to Docker (localhost:9999)

    Yields:
        StashProcessing: Fully functional processor hitting real services
    """
    # Set up config with real database and real stash
    config._database = test_database_sync
    config._stash = stash_context

    # Set up default respx mock for GraphQL endpoint
    # Tests can add more specific mocks as needed
    with respx.mock:
        # Default response for any GraphQL requests
        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )

        # Initialize the client (will use mocked HTTP)
        await stash_context.get_client()

        # Disable prints for testing
        with (
            patch("textio.textio.print_info"),
            patch("textio.textio.print_warning"),
            patch("textio.textio.print_error"),
        ):
            processor = StashProcessing.from_config(config, test_state)
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
    from tests.fixtures.stash.stash_type_factories import StudioFactory

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
