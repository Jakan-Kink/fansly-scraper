"""Fixtures for Stash mixin testing.

Provides the shared ``mock_item`` (HasMetadata protocol) fixture plus gallery
test data used by StashProcessing mixin tests. Mixin tests exercise the real
``StashProcessing`` class via the ``respx_stash_processor`` fixture
(@respx.mock intercepts GraphQL HTTP calls at the edge); the old TestMixin*
harness classes and their per-mixin fixtures had no remaining consumers and
were removed.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from metadata import Account, ContentType
from tests.fixtures.metadata.metadata_factories import AttachmentFactory, PostFactory
from tests.fixtures.stash.stash_type_factories import PerformerFactory, StudioFactory
from tests.fixtures.utils.test_isolation import snowflake_id


__all__ = [
    # Gallery test fixture aliases
    "gallery_mock_performer",
    "gallery_mock_studio",
    # Orchestration test data
    "gallery_orchestration_setup",
    # Mock item for Stash unit tests
    "mock_item",
]


# ============================================================================
# Mock Item Fixture (HasMetadata Protocol for Stash Unit Tests)
# ============================================================================


@pytest.fixture
def mock_item():
    """Fixture for Post/Message item used in Stash mixin unit tests.
    Returns:
        Post: Real Post object (detached from database)
    """
    # Create real Post object (detached from database)
    acct_id = snowflake_id()
    item = PostFactory.build(
        id=snowflake_id(),
        accountId=acct_id,
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
    # Use field name 'mentions', not alias 'accountMentions'
    item.mentions = []

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


@pytest.fixture
def gallery_orchestration_setup() -> dict[str, Any]:
    """Common data for _get_or_create_gallery orchestration tests.

    Returns a dict with ``account``, ``post`` (one ACCOUNT_MEDIA attachment),
    ``performer``, ``studio``, and ``url_pattern``.
    """
    account_id = snowflake_id()
    post_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    post = PostFactory.build(
        id=post_id,
        accountId=account_id,
        content="Test post content",
        createdAt=datetime(2024, 4, 1, 12, 0, 0, tzinfo=UTC),
    )
    # Set attachments after construction to bypass _prepare_post_data validator
    # (which filters non-dict items). In production, attachments are de-nested
    # before reaching this point.
    post.attachments = [
        AttachmentFactory.build(
            postId=post_id,
            contentId=post_id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
    ]

    performer = PerformerFactory.build(id="10100", name="test_user")
    studio = StudioFactory.build(id="10200", name="Test Studio")

    return {
        "account": account,
        "post": post,
        "performer": performer,
        "studio": studio,
        "url_pattern": "https://test.com/{username}/post/{id}",
    }
