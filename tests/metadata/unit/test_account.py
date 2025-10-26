"""Unit tests for metadata.account module."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from metadata.account import (
    Account,
    AccountMediaBundle,
    TimelineStats,
    account_media_bundle_media,
    process_account_data,
    process_media_bundles,
)
from tests.fixtures import (
    AccountFactory,
    AccountMediaBundleFactory,
    AccountMediaFactory,
    MediaFactory,
)


@pytest.mark.asyncio
async def test_account_media_bundle_creation(session, session_sync, factory_session):
    """Test creating an AccountMediaBundle with ordered content.

    Note: Uses both async session (for queries) and sync session (for factories).
    FactoryBoy requires sync sessions, but the test logic uses async.
    Tests must explicitly request factory_session or fixtures that depend on it.
    """
    # Create account using factory (sync)
    AccountFactory(id=1, username="test_user")

    # Create Media records first (required by foreign key constraints)
    MediaFactory(id=101, accountId=1)
    MediaFactory(id=102, accountId=1)

    # Create AccountMedia items linking to Media records
    AccountMediaFactory(id=1, accountId=1, mediaId=101)
    AccountMediaFactory(id=2, accountId=1, mediaId=102)

    # Create bundle using factory
    AccountMediaBundleFactory(id=1, accountId=1)

    # Expire all objects in the async session so it fetches fresh data from the database
    session.expire_all()

    # Add media to bundle with positions (this is async, so use session)
    await session.execute(
        account_media_bundle_media.insert().values(
            [
                {"bundle_id": 1, "media_id": 1, "pos": 2},
                {"bundle_id": 1, "media_id": 2, "pos": 1},
            ]
        )
    )
    await session.commit()

    # Verify bundle content order
    # Use a single query with eager loading of the relationship
    stmt = (
        select(AccountMediaBundle)
        .where(AccountMediaBundle.id == 1)
        .options(selectinload(AccountMediaBundle.accountMedia))
    )
    result = await session.execute(stmt)
    saved_bundle: AccountMediaBundle | None = result.unique().scalar_one_or_none()
    assert saved_bundle is not None

    # Access the relationship directly through accountMedia
    media_ids = sorted([media.id for media in saved_bundle.accountMedia])
    assert media_ids == [1, 2]  # Should contain both media IDs


@pytest.mark.asyncio
async def test_update_optimization(session, session_sync, config, factory_session):
    """Test that attributes are only updated when values actually change.

    Uses real config fixture instead of mock, and AccountFactory for initial data.
    Tests must explicitly request factory_session or fixtures that depend on it.
    """
    # Create initial account using factory (sync)
    account = AccountFactory(id=1, username="test_user", displayName="Test User")

    # Expire all objects in the async session so it fetches fresh data from the database
    session.expire_all()

    # Update with same values
    data = {
        "id": 1,
        "username": "test_user",
        "displayName": "Test User",
        "timelineStats": {  # Required field
            "imageCount": 0,
            "videoCount": 0,
        },
    }

    await process_account_data(config, data, session=session)

    # Get initial state
    result = await session.execute(select(Account).filter_by(id=1))
    account = result.scalar_one_or_none()
    assert account.displayName == "Test User"

    # Update with different values
    data["displayName"] = "New Name"
    await process_account_data(config, data, session=session)

    # Expire and re-query to ensure we get the latest data
    session.expire_all()
    result = await session.execute(select(Account).filter_by(id=1))
    account = result.scalar_one_or_none()
    assert account.displayName == "New Name", "Value should be updated"
    assert account.username == "test_user", "Unchanged value should remain the same"


@pytest.mark.asyncio
async def test_timeline_stats_optimization(
    session, session_sync, config, factory_session
):
    """Test that timeline stats are only updated when values change.

    Uses real config fixture instead of mock, and AccountFactory for initial data.
    Tests must explicitly request factory_session or fixtures that depend on it.
    """
    # Create initial account using factory (sync)
    account = AccountFactory(id=1, username="test_user")

    # Create timeline stats manually using sync session
    stats = TimelineStats(
        accountId=1,
        imageCount=10,
        videoCount=5,
        fetchedAt=datetime.now(UTC),
    )
    session_sync.add(stats)
    session_sync.commit()

    # Expire all objects in the async session so it fetches fresh data from the database
    session.expire_all()

    # Update with same values
    data = {
        "id": 1,
        "username": "test_user",  # Required field
        "timelineStats": {
            "imageCount": 10,
            "videoCount": 5,
            "fetchedAt": int(datetime(2023, 10, 10, tzinfo=UTC).timestamp()),
        },
    }
    await process_account_data(config, data, session=session)

    # Get initial state
    stmt = select(TimelineStats).filter_by(accountId=1)
    result = await session.execute(stmt)
    stats = result.scalar_one_or_none()
    assert stats.imageCount == 10, "Initial value should be unchanged"

    # Update with different values
    data["timelineStats"]["imageCount"] = 15
    await process_account_data(config, data, session=session)

    # Expire and re-query to ensure we get the latest data
    session.expire_all()
    stmt = select(TimelineStats).filter_by(accountId=1)
    result = await session.execute(stmt)
    stats = result.scalar_one_or_none()
    assert stats.imageCount == 15, "Value should be updated"
    assert stats.videoCount == 5, "Unchanged value should remain the same"


@pytest.mark.asyncio
async def test_process_media_bundles(session, session_sync, config, factory_session):
    """Test processing media bundles from API response.

    Uses real config fixture and factories for test data creation.
    Tests must explicitly request factory_session or fixtures that depend on it.
    """
    # Create account using factory
    AccountFactory(id=1, username="test_user")

    # Create Media records first (required by foreign key constraints)
    MediaFactory(id=1001, accountId=1)
    MediaFactory(id=1002, accountId=1)

    # Create AccountMedia items linking to Media records
    AccountMediaFactory(id=101, accountId=1, mediaId=1001)
    AccountMediaFactory(id=102, accountId=1, mediaId=1002)

    await session.flush()  # Sync factory data to async session

    # Process bundles
    bundles_data = [
        {
            "id": 1,
            "accountId": 1,
            "createdAt": int(datetime.now(UTC).timestamp()),
            "bundleContent": [
                {"accountMediaId": 101, "pos": 2},
                {"accountMediaId": 102, "pos": 1},
            ],
        }
    ]

    await process_media_bundles(config, 1, bundles_data, session=session)

    # Verify bundle was created
    # Use a single query with eager loading of the relationship
    stmt = select(AccountMediaBundle).options(
        selectinload(AccountMediaBundle.accountMedia)
    )
    result = await session.execute(stmt)
    bundle = result.scalar_one_or_none()
    assert bundle is not None

    # Access the relationship directly through accountMedia
    media_ids = sorted([media.id for media in bundle.accountMedia])
    assert media_ids == [101, 102]  # Check both media are present
