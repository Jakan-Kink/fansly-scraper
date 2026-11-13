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
import pytest_asyncio

from metadata import Account, ContentType
from metadata.entity_store import PostgresEntityStore
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
)
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
    # Quote post graph (wrapper + original)
    "quoted_post_setup",
]


# ============================================================================
# Mock Item Fixture (HasMetadata Protocol for Stash Unit Tests)
# ============================================================================


@pytest.fixture
def mock_item(mock_account):
    """Fixture for Post/Message item used in Stash mixin unit tests.
    Returns:
        Post: Real Post by ``mock_account`` (detached from database)
    """
    item = PostFactory.build(
        id=snowflake_id(),
        accountId=mock_account.id,
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


@pytest_asyncio.fixture
async def quoted_post_setup(entity_store: PostgresEntityStore) -> dict[str, Any]:
    """A quote: one creator's wrapper post around another creator's post.

    Returns ``quoter`` and ``author`` (Accounts), ``original`` (the author's
    post with one video), ``wrapper`` (the quoter's post quoting it, plus one
    image of its own) and the two Media as ``quoted_media`` / ``own_media``.
    Everything is saved, so attachments resolve through the store.
    """
    quoter = AccountFactory.build(id=snowflake_id(), username="quoter")
    author = AccountFactory.build(id=snowflake_id(), username="original_poster")
    quoted_id, own_id, original_id = snowflake_id(), snowflake_id(), snowflake_id()
    quoted_media = MediaFactory.build(
        id=quoted_id,
        accountId=author.id,
        mimetype="video/mp4",
        is_downloaded=True,
        local_filename=f"2024-03-05_at_03-45_UTC_id_{quoted_id}.mp4",
    )
    own_media = MediaFactory.build(
        id=own_id,
        accountId=quoter.id,
        mimetype="image/jpeg",
        is_downloaded=True,
        local_filename=f"2024-03-06_at_19-19_UTC_id_{own_id}.jpeg",
    )
    wrapper_id = original_id + 1000
    # Attachments go in as the API's dicts, the way a timeline page builds them.
    original = PostFactory.build(
        id=original_id,
        accountId=author.id,
        content="The original post",
        createdAt=datetime(2024, 3, 5, 3, 45, 0, tzinfo=UTC),
        attachments=[
            {
                "postId": original_id,
                "pos": 0,
                "contentType": ContentType.ACCOUNT_MEDIA.value,
                "contentId": quoted_id,
            }
        ],
    )
    wrapper = PostFactory.build(
        id=wrapper_id,
        accountId=quoter.id,
        content="Look at this",
        createdAt=datetime(2024, 3, 6, 19, 19, 0, tzinfo=UTC),
        attachments=[
            {
                "postId": wrapper_id,
                "pos": 0,
                "contentType": ContentType.AGGREGATED_POSTS.value,
                "contentId": original_id,
            },
            {
                "postId": wrapper_id,
                "pos": 1,
                "contentType": ContentType.ACCOUNT_MEDIA.value,
                "contentId": own_id,
            },
        ],
    )
    for entity in (quoter, author, quoted_media, own_media):
        await entity_store.save(entity)
    for media in (quoted_media, own_media):
        await entity_store.save(
            AccountMediaFactory.build(
                id=media.id, accountId=media.accountId, mediaId=media.id
            )
        )
    for post in (original, wrapper):
        await entity_store.save(post)
    return {
        "quoter": quoter,
        "author": author,
        "original": original,
        "wrapper": wrapper,
        "quoted_media": quoted_media,
        "own_media": own_media,
    }
