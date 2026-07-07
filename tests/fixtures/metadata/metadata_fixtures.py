"""Pytest fixtures for metadata (database) objects.

This module provides fixtures for creating metadata test objects using FactoryBoy factories.
All fixtures use REAL SQLAlchemy objects with 60-bit Snowflake-style IDs for realistic testing.

Philosophy:
- Use REAL database objects, never mocks
- Use factories from metadata_factories.py for object creation
- Use 60-bit BigInt IDs to test PostgreSQL BIGINT handling
- Each fixture is independent and can be used standalone
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from helpers.common import JsonDict
from metadata import ContentType
from metadata.models import Account, FanslyObject, Media
from tests.fixtures.utils.test_isolation import snowflake_id

from .metadata_factories import (
    ACCOUNT_ID_BASE,
    ACCOUNT_MEDIA_BUNDLE_ID_BASE,
    ACCOUNT_MEDIA_ID_BASE,
    GROUP_ID_BASE,
    MEDIA_ID_BASE,
    MESSAGE_ID_BASE,
    POST_ID_BASE,
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    GroupFactory,
    MediaFactory,
    MessageFactory,
    PostFactory,
)


__all__ = [
    "group_data",
    "media_items",
    "saved_account",
    "store_with_account",
    "test_account",
    "test_account_media",
    "test_attachment",
    "test_group",
    "test_media",
    "test_media_bundle",
    "test_message",
    "test_messages",
    "test_post",
    "test_posts",
]


# ============================================================================
# Async (entity_store) Account Fixture
# ============================================================================


@pytest_asyncio.fixture
async def saved_account(entity_store):
    """Create and persist a test Account via the async entity_store.

    Most other Account fixtures here use the sync ``session_sync`` path;
    this one is for tests driving async code paths (daemon orchestration,
    Pydantic-based metadata pipelines) where ``entity_store`` is the
    natural persistence boundary.
    """
    account = AccountFactory.build()
    await entity_store.save(account)
    return account


# ============================================================================
# Core Metadata Fixtures
# ============================================================================


@pytest.fixture
def test_account(session_sync):
    """Create a REAL Account using factory with 60-bit BigInt ID.

    This replaces integration_mock_account and provides a real SQLAlchemy object.

    Args:
        session_sync: Sync database session from database_fixtures

    Returns:
        Account: Real Account object with id in 60-bit range
    """
    account = AccountFactory.build(
        id=ACCOUNT_ID_BASE + 54321,  # Snowflake-style 60-bit ID
        username="test_user",
        displayName="Test User",
    )
    session_sync.add(account)
    session_sync.commit()
    session_sync.refresh(account)
    return account


@pytest.fixture
def test_media(session_sync, test_account):
    """Create REAL Media using factory with 60-bit BigInt ID.

    Args:
        session_sync: Sync database session
        test_account: Account that owns this media

    Returns:
        Media: Real Media object with id in 60-bit range
    """
    media = MediaFactory.build(
        id=MEDIA_ID_BASE + 20123,  # Snowflake-style 60-bit ID
        accountId=test_account.id,
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
def test_group(session_sync, test_account):
    """Create REAL Group using factory with 60-bit BigInt ID.

    Args:
        session_sync: Database session
        test_account: Account fixture (required for FK constraint)

    Returns:
        Group: Real Group object with id in 60-bit range

    Note:
        Groups require an Account to exist (createdBy foreign key).
    """
    group = GroupFactory.build(
        id=GROUP_ID_BASE + 40123,  # Snowflake-style 60-bit ID
        createdBy=test_account.id,
    )
    session_sync.add(group)
    session_sync.commit()
    session_sync.refresh(group)
    return group


@pytest.fixture
def test_attachment(session_sync, test_account, test_media):
    """Create REAL Attachment using factory with 60-bit BigInt ID.

    Creates the full chain: Account -> Media -> AccountMedia -> Attachment

    Args:
        session_sync: Database session
        test_account: Account that owns the media
        test_media: Media to attach

    Returns:
        Attachment: Real Attachment object with auto-increment id
    """
    # First create an AccountMedia that links the Media to an Account
    account_media = AccountMediaFactory.build(
        id=ACCOUNT_MEDIA_ID_BASE + 70123,  # Snowflake-style 60-bit ID
        accountId=test_account.id,
        mediaId=test_media.id,
    )
    session_sync.add(account_media)
    session_sync.commit()

    # Create attachment that references the AccountMedia
    attachment = AttachmentFactory.build(
        contentId=account_media.id,  # References AccountMedia.id
        contentType=ContentType.ACCOUNT_MEDIA,
        postId=None,  # Will be updated by tests if needed
    )
    session_sync.add(attachment)
    session_sync.commit()
    session_sync.refresh(attachment)
    return attachment


@pytest.fixture
def test_post(session_sync, test_account, test_attachment):
    """Create REAL Post using factory with 60-bit BigInt ID.

    Args:
        session_sync: Database session
        test_account: Account that created the post
        test_attachment: Attachment to link to the post

    Returns:
        Post: Real Post object with id in 60-bit range
    """
    post = PostFactory.build(
        id=POST_ID_BASE + 12345,  # Snowflake-style 60-bit ID
        accountId=test_account.id,
        content="Test post content #test",
    )
    session_sync.add(post)
    session_sync.commit()

    # Update attachment to link to this post
    test_attachment.postId = post.id
    session_sync.add(test_attachment)
    session_sync.commit()

    session_sync.refresh(post)
    return post


@pytest.fixture
def test_posts(session_sync, test_account):
    """Create multiple REAL Posts using factories with 60-bit BigInt IDs.

    This shows how easy batch creation is with factories!

    Args:
        session_sync: Database session
        test_account: Account that created the posts

    Returns:
        list[Post]: List of Post objects with ids in 60-bit range
    """
    posts = []
    for i in range(3):
        # Create media
        media = MediaFactory.build(
            id=MEDIA_ID_BASE + 30000 + i,  # Snowflake-style 60-bit ID
            accountId=test_account.id,
            mimetype="image/jpeg",
        )
        session_sync.add(media)

        # Create AccountMedia to link Media to Account
        account_media = AccountMediaFactory.build(
            id=ACCOUNT_MEDIA_ID_BASE + 70000 + i,  # Snowflake-style 60-bit ID
            accountId=test_account.id,
            mediaId=media.id,
        )
        session_sync.add(account_media)

        # Create post
        post = PostFactory.build(
            id=POST_ID_BASE + 30000 + i,  # Snowflake-style 60-bit ID
            accountId=test_account.id,
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
def test_message(session_sync, test_group, test_account, test_attachment):
    """Create REAL Message using factory with 60-bit BigInt ID.

    Args:
        session_sync: Database session
        test_group: Group (conversation) for the message
        test_account: Account that sent the message
        test_attachment: Attachment to link to the message

    Returns:
        Message: Real Message object with id in 60-bit range
    """
    message = MessageFactory.build(
        id=MESSAGE_ID_BASE + 67890,  # Snowflake-style 60-bit ID
        groupId=test_group.id,
        senderId=test_account.id,
        content="Test message content",
    )
    session_sync.add(message)
    session_sync.commit()

    # Update attachment to link to this message
    test_attachment.contentId = message.id
    test_attachment.contentType = ContentType.ACCOUNT_MEDIA
    session_sync.add(test_attachment)
    session_sync.commit()

    session_sync.refresh(message)
    return message


@pytest.fixture
def test_messages(session_sync, test_group, test_account):
    """Create multiple REAL Messages using factories with 60-bit BigInt IDs.

    Args:
        session_sync: Database session
        test_group: Group (conversation) for the messages
        test_account: Account that sent the messages

    Returns:
        list[Message]: List of Message objects with ids in 60-bit range
    """
    messages = []
    for i in range(3):
        # Create media
        media = MediaFactory.build(
            id=MEDIA_ID_BASE + 50000 + i,  # Snowflake-style 60-bit ID
            accountId=test_account.id,
            mimetype="image/jpeg" if i % 2 == 0 else "video/mp4",
        )
        session_sync.add(media)

        # Create message
        message = MessageFactory.build(
            id=MESSAGE_ID_BASE + 50000 + i,  # Snowflake-style 60-bit ID
            groupId=test_group.id,
            senderId=test_account.id,
            content=f"Test message content {i + 1}",
        )
        session_sync.add(message)

        # Create attachment (some messages don't have attachments)
        if i % 2 == 0:
            # Create AccountMedia to link Media to Account
            account_media = AccountMediaFactory.build(
                id=ACCOUNT_MEDIA_ID_BASE + 80000 + i,  # Snowflake-style 60-bit ID
                accountId=test_account.id,
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
def test_account_media(session_sync, test_account, test_media):
    """Create REAL AccountMedia using factory with 60-bit BigInt ID.

    This replaces the mock_account_media fixture that was using MagicMock.

    Args:
        session_sync: Database session
        test_account: Account that owns the media
        test_media: Media to link

    Returns:
        AccountMedia: Real AccountMedia object with id in 60-bit range
    """
    account_media = AccountMediaFactory.build(
        id=ACCOUNT_MEDIA_ID_BASE + 123456,  # Snowflake-style 60-bit ID
        accountId=test_account.id,
        mediaId=test_media.id,
    )
    session_sync.add(account_media)
    session_sync.commit()
    session_sync.refresh(account_media)
    return account_media


@pytest.fixture
def messages_page_data():
    """API-shaped /api/v1/message page payload with 3 messages sharing one sender.

    Used by tests that exercise check_page_duplicates(page_type="messages")
    and the message-pagination dedup loop.
    """
    sender_id = snowflake_id()
    account_id = snowflake_id()
    return {
        "messages": [
            {"id": snowflake_id(), "senderId": sender_id, "accountId": account_id},
            {"id": snowflake_id(), "senderId": sender_id, "accountId": account_id},
            {"id": snowflake_id(), "senderId": sender_id, "accountId": account_id},
        ],
    }


@pytest.fixture
def test_media_bundle(session_sync, test_account):
    """Create REAL AccountMediaBundle using factory with 60-bit BigInt ID.

    Args:
        session_sync: Database session
        test_account: Account that owns the bundle

    Returns:
        AccountMediaBundle: Real AccountMediaBundle object with id in 60-bit range
    """
    bundle = AccountMediaBundleFactory.build(
        id=ACCOUNT_MEDIA_BUNDLE_ID_BASE + 111222,  # Snowflake-style 60-bit ID
        accountId=test_account.id,
    )
    session_sync.add(bundle)
    session_sync.commit()
    session_sync.refresh(bundle)
    return bundle


@pytest.fixture
def store_with_account():
    """Set up a minimal fake store with a cached Account for sync tests."""

    class FakeStore:
        def __init__(self):
            self._cache = {}

        def get_from_cache(self, cls, eid):
            return self._cache.get((cls, eid))

        def get_from_cache_by_type_name(self, name, eid):
            for (c, i), obj in self._cache.items():
                if c.__name__ == name and i == eid:
                    return obj
            return None

        def cache_instance(self, obj):
            self._cache[(type(obj), obj.id)] = obj

        def invalidate(self, cls, eid):
            self._cache.pop((cls, eid), None)

    store = FakeStore()
    # The sole deliberate type:ignore in tests/fixtures. FakeStore is a no-DB-pool
    # in-memory cache double for sync-path tests, so it cannot be a real
    # PostgresEntityStore and no in-contract value expresses "no-pool store".
    # One-time exception, not a pattern.
    FanslyObject._store = store  # type: ignore[assignment]  # intentional no-pool store double

    acct_id = snowflake_id()
    account = Account(id=acct_id, username="sync_test_user")
    store.cache_instance(account)

    yield store, account

    FanslyObject._store = None


@pytest.fixture
def media_items():
    """Create 3 Media items with different IDs and timestamps."""
    base_id = 1000000000000000000
    m1 = Media(id=base_id + 1, accountId=base_id + 100)
    m1.createdAt = datetime(2024, 1, 15, tzinfo=UTC)
    m1.mimetype = "image/jpeg"

    m2 = Media(id=base_id + 2, accountId=base_id + 100)
    m2.createdAt = datetime(2024, 6, 1, tzinfo=UTC)
    m2.mimetype = "video/mp4"

    m3 = Media(id=base_id + 3, accountId=base_id + 100)
    m3.createdAt = datetime(2024, 3, 10, tzinfo=UTC)
    m3.mimetype = "image/jpeg"

    return m1, m2, m3


@pytest.fixture(scope="session")
def group_data(test_data_dir: str) -> JsonDict:
    """Load group messages test data."""
    json_file = Path(test_data_dir) / "messages-group.json"
    if not json_file.exists():
        pytest.skip(f"Test data file not found: {json_file}")
    with json_file.open() as f:
        return json.load(f)
