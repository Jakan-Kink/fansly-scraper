"""Fixtures for Stash mixin testing.

This module provides TestMixinClass definitions for testing StashProcessing mixins.
These classes use REAL objects and can accept real database/logger instances.

For new tests, prefer:
- Use real StashProcessing with real_stash_processor fixture
- Use @respx.mock to intercept GraphQL HTTP calls
- See /tmp/mock_to_respx_migration_guide.md for examples
"""

import asyncio
from datetime import UTC, datetime

import pytest
from stash_graphql_client import StashContext

from config.fanslyconfig import FanslyConfig
from download.downloadstate import DownloadState
from metadata.database import Database
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
    """

    def __init__(
        self,
        config: FanslyConfig,
        state: DownloadState,
        context: StashContext,
        database: Database,
        _background_task: asyncio.Task | None = None,
        _cleanup_event: asyncio.Event | None = None,
        _owns_db: bool = False,
    ) -> None:
        """Initialize test class with real StashContext.

        Args:
            database: Optional Database instance (defaults to None for tests that don't use it)
            log: Optional Logger instance (defaults to None for tests that don't use it)
        """
        # Real StashContext with minimal test config
        super().__init__(
            config, state, context, database, _background_task, _cleanup_event, _owns_db
        )
        AccountProcessingMixin.__init__(self)
        StudioProcessingMixin.__init__(self)
        GalleryProcessingMixin.__init__(self)
        MediaProcessingMixin.__init__(self)
        ContentProcessingMixin.__init__(self)
        BatchProcessingMixin.__init__(self)
        TagProcessingMixin.__init__(self)


class TestAccountMixin(TestMixinBase):
    """Test class focused on AccountProcessingMixin testing."""


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
async def account_mixin(config, test_state, stash_context, test_database_sync):
    """Fixture for account mixin test class."""
    mixin = TestAccountMixin(config, test_state, stash_context, test_database_sync)
    await stash_context.get_client()
    return mixin


@pytest.fixture
async def batch_mixin(config, test_state, stash_context, test_database_sync):
    """Fixture for batch mixin test class."""
    mixin = TestBatchMixin(config, test_state, stash_context, test_database_sync)
    await stash_context.get_client()
    return mixin


@pytest.fixture
async def content_mixin(config, test_state, stash_context, test_database_sync):
    """Fixture for content mixin test class."""
    mixin = TestContentMixin(config, test_state, stash_context, test_database_sync)
    await stash_context.get_client()
    return mixin


@pytest.fixture
async def gallery_mixin(config, test_state, stash_context, test_database_sync):
    """Fixture for gallery mixin test class."""
    mixin = TestGalleryMixin(config, test_state, stash_context, test_database_sync)
    await stash_context.get_client()
    return mixin


@pytest.fixture
async def media_mixin(config, test_state, stash_context, test_database_sync):
    """Fixture for media mixin test class with initialized client and database."""
    mixin = TestMediaMixin(
        config=config,
        state=test_state,
        context=stash_context,
        database=test_database_sync,
    )
    await stash_context.get_client()
    return mixin


@pytest.fixture
async def studio_mixin(config, test_state, stash_context, test_database_sync):
    """Fixture for studio mixin test class."""
    mixin = TestStudioMixin(config, test_state, stash_context, test_database_sync)
    await stash_context.get_client()
    return mixin


@pytest.fixture
async def tag_mixin(config, test_state, stash_context, test_database_sync):
    """Fixture for TagProcessingMixin instance."""
    mixin = TestTagMixin(config, test_state, stash_context, test_database_sync)
    await stash_context.get_client()
    return mixin


# ============================================================================
# Mock Item Fixture (HasMetadata Protocol for Stash Unit Tests)
# ============================================================================


@pytest.fixture
def mock_item():
    """Fixture for Post/Message item used in Stash mixin unit tests.
    Returns:
        Post: Real Post object (detached from database)
    """
    from tests.fixtures.metadata.metadata_factories import PostFactory

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
