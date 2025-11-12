"""Fixtures for Stash mixin testing.

This module provides TestMixinClass definitions for testing StashProcessing mixins.

MIGRATION NOTE: These test helper classes are being phased out in favor of:
1. Using real StashProcessing instances with real fixtures
2. Using @respx.mock to mock HTTP responses at the edge
3. Using real database fixtures instead of MagicMock

For new tests, prefer:
- Use real StashProcessing with real_stash_processor fixture
- Use @respx.mock to intercept GraphQL HTTP calls
- See /tmp/mock_to_respx_migration_guide.md for examples
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from stash.context import StashContext
from stash.processing.base import StashProcessingBase
from stash.processing.mixins.account import AccountProcessingMixin
from stash.processing.mixins.batch import BatchProcessingMixin
from stash.processing.mixins.content import ContentProcessingMixin
from stash.processing.mixins.gallery import GalleryProcessingMixin
from stash.processing.mixins.media import MediaProcessingMixin
from stash.processing.mixins.studio import StudioProcessingMixin
from stash.processing.mixins.tag import TagProcessingMixin


__all__ = [
    # Mixin test classes
    "account_mixin",
    "batch_mixin",
    "content_mixin",
    "gallery_mixin",
    # Gallery test fixture aliases
    "gallery_mock_performer",
    "gallery_mock_studio",
    "media_mixin",
    # Mock item for Stash unit tests
    "mock_item",
    "studio_mixin",
    "tag_mixin",
]


# ============================================================================
# Mixin Test Classes
# ============================================================================


# Base class that inherits from ALL mixins (like StashProcessing does)
class TestMixinBase(
    StashProcessingBase,
    AccountProcessingMixin,
    StudioProcessingMixin,
    GalleryProcessingMixin,
    MediaProcessingMixin,
    ContentProcessingMixin,
    BatchProcessingMixin,
    TagProcessingMixin,
):
    """Base test class with all mixins.

    Inherits from StashProcessingBase and all processing mixins to provide
    access to all methods, just like the real StashProcessing class does.

    NOTE: This class still uses some MagicMock for infrastructure (database, logging).
    For new tests, prefer real_stash_processor fixture + @respx.mock instead.
    """

    def __init__(self):
        """Initialize test class with real StashContext.

        WARNING: This approach is being phased out. Tests using this class should:
        1. Use @respx.mock to mock GraphQL HTTP responses (not _client)
        2. Use real database fixtures (not MagicMock)
        3. Or use real_stash_processor fixture for full integration
        """
        # Real StashContext with minimal test config
        self.context = StashContext(
            conn={"Scheme": "http", "Host": "localhost", "Port": 9999, "ApiKey": "test"}
        )
        # REMOVED: self.context._client = MagicMock()
        # This mocked INTERNAL StashClient.execute() which violates edge-mocking.
        # Tests should use @respx.mock to intercept HTTP calls instead.
        # The _client will be None until StashContext.initialize() is called,
        # which tests should do (and use respx to mock the HTTP responses).

        # Mock database attribute (from StashProcessingBase)
        # TODO: Replace with real database fixtures
        self.database = MagicMock()

        # Mock log attribute (from StashProcessingBase)
        # NOTE: Logging is infrastructure, this is acceptable to mock
        self.log = MagicMock()


class TestAccountMixin(TestMixinBase):
    """Test class focused on AccountProcessingMixin testing."""

    def __init__(self):
        """Initialize with account-specific state."""
        super().__init__()
        # Import TestState from stash_integration_fixtures
        from tests.fixtures.stash.stash_integration_fixtures import TestState

        # Use REAL TestState instead of MagicMock
        self.state = TestState()
        self.state.creator_id = "12345"
        self.state.creator_name = "test_user"


class TestBatchMixin(TestMixinBase):
    """Test class focused on BatchProcessingMixin testing."""


class TestContentMixin(TestMixinBase):
    """Test class focused on ContentProcessingMixin testing."""


class TestGalleryMixin(TestMixinBase):
    """Test class focused on GalleryProcessingMixin testing."""


class TestMediaMixin(TestMixinBase):
    """Test class focused on MediaProcessingMixin testing."""


class TestStudioMixin(TestMixinBase):
    """Test class focused on StudioProcessingMixin testing."""


class TestTagMixin(TestMixinBase):
    """Test class focused on TagProcessingMixin testing."""


# ============================================================================
# Mixin Fixtures
# ============================================================================


@pytest.fixture
def account_mixin():
    """Fixture for account mixin test class."""
    return TestAccountMixin()


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
def studio_mixin():
    """Fixture for studio mixin test class."""
    return TestStudioMixin()


@pytest.fixture
def tag_mixin():
    """Fixture for TagProcessingMixin instance."""
    return TestTagMixin()


# ============================================================================
# Mock Item Fixture (HasMetadata Protocol for Stash Unit Tests)
# ============================================================================


@pytest.fixture
def mock_item():
    """Fixture for Post/Message item used in Stash mixin unit tests.
    Returns:
        Post: Real Post object (detached from database)
    """
    from tests.fixtures.metadata_factories import PostFactory

    # Create real Post object (detached from database)
    item = PostFactory.build(
        id=12345,
        accountId=12345,
        content="Test content #test #hashtag",
        createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
    )

    # Add stash_id attribute (not in database model by default)
    item.stash_id = None

    # Setup default attachments (can be overridden in tests)
    item.attachments = []

    # Setup default hashtags (can be overridden in tests)
    item.hashtags = []

    # Setup default mentions (can be overridden in tests)
    item.accountMentions = []

    return item


# ============================================================================
# Gallery Test Fixtures (Aliases for consistency with gallery tests)
# ============================================================================


@pytest.fixture
def gallery_mock_performer(mock_performer):
    """Fixture for mock performer used in gallery tests.

    This is an alias to the standard mock_performer fixture from stash_type_factories.
    """
    return mock_performer


@pytest.fixture
def gallery_mock_studio(mock_studio):
    """Fixture for mock studio used in gallery tests.

    This is an alias to the standard mock_studio fixture from stash_type_factories.
    """
    return mock_studio
