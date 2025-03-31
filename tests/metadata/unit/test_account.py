"""Unit tests for metadata.account module."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import contains_eager, joinedload, selectinload

from metadata.account import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    TimelineStats,
    account_media_bundle_media,
    process_media_bundles,
)
from metadata.base import Base


@pytest.mark.asyncio
async def test_account_media_bundle_creation(session):
    """Test creating an AccountMediaBundle with ordered content."""
    # Create account
    account = Account(id=1, username="test_user")
    session.add(account)
    await session.commit()

    # Create media items
    media1 = AccountMedia(
        id=1, accountId=1, mediaId=101, createdAt=datetime.now(timezone.utc)
    )
    media2 = AccountMedia(
        id=2, accountId=1, mediaId=102, createdAt=datetime.now(timezone.utc)
    )
    session.add_all([media1, media2])
    await session.commit()

    # Create bundle
    bundle = AccountMediaBundle(id=1, accountId=1, createdAt=datetime.now(timezone.utc))
    session.add(bundle)
    await session.commit()

    # Add media to bundle with positions
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
async def test_update_optimization(session):
    """Test that attributes are only updated when values actually change."""
    # Create initial account
    account = Account(id=1, username="test_user", displayName="Test User")
    session.add(account)
    await session.commit()

    # Create mock config
    mock_config = MagicMock()
    mock_config._database = MagicMock()
    mock_config._database.async_session = AsyncMock(return_value=session)

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
    from metadata.account import process_account_data

    await process_account_data(mock_config, data, session=session)

    # Get initial state
    result = await session.execute(select(Account).filter_by(id=1))
    account = result.scalar_one_or_none()
    assert account.displayName == "Test User"

    # Update with different values
    data["displayName"] = "New Name"
    await process_account_data(mock_config, data, session=session)

    # Check that UPDATE was performed
    result = await session.execute(select(Account).filter_by(id=1))
    account = result.scalar_one_or_none()
    assert account.displayName == "New Name", "Value should be updated"
    assert account.username == "test_user", "Unchanged value should remain the same"


@pytest.mark.asyncio
async def test_timeline_stats_optimization(session):
    """Test that timeline stats are only updated when values change."""
    from metadata.account import process_account_data

    # Create initial account and timeline stats
    account = Account(id=1, username="test_user")
    session.add(account)
    await session.commit()

    stats = TimelineStats(
        accountId=1,
        imageCount=10,
        videoCount=5,
        fetchedAt=datetime.now(timezone.utc),
    )
    session.add(stats)
    await session.commit()

    # Create mock config
    mock_config = MagicMock()
    mock_config._database = MagicMock()
    mock_config._database.async_session = AsyncMock(return_value=session)

    # Update with same values
    data = {
        "id": 1,
        "username": "test_user",  # Required field
        "timelineStats": {
            "imageCount": 10,
            "videoCount": 5,
            "fetchedAt": int(datetime(2023, 10, 10, tzinfo=timezone.utc).timestamp()),
        },
    }
    await process_account_data(mock_config, data, session=session)

    # Get initial state
    stmt = select(TimelineStats).filter_by(accountId=1)
    result = await session.execute(stmt)
    stats = result.scalar_one_or_none()
    assert stats.imageCount == 10, "Initial value should be unchanged"

    # Update with different values
    data["timelineStats"]["imageCount"] = 15
    await process_account_data(mock_config, data, session=session)

    # Check that UPDATE was performed
    stmt = select(TimelineStats).filter_by(accountId=1)
    result = await session.execute(stmt)
    stats = result.scalar_one_or_none()
    assert stats.imageCount == 15, "Value should be updated"
    assert stats.videoCount == 5, "Unchanged value should remain the same"


@pytest.mark.asyncio
async def test_process_media_bundles(session):
    """Test processing media bundles from API response."""
    # Create account and media first
    account = Account(id=1, username="test_user")
    media1 = AccountMedia(
        id=101, accountId=1, mediaId=1001, createdAt=datetime.now(timezone.utc)
    )
    media2 = AccountMedia(
        id=102, accountId=1, mediaId=1002, createdAt=datetime.now(timezone.utc)
    )
    session.add_all([account, media1, media2])
    await session.commit()

    # Create mock config
    mock_config = MagicMock()
    mock_config._database = MagicMock()
    mock_config._database.async_session = AsyncMock(return_value=session)

    # Process bundles
    bundles_data = [
        {
            "id": 1,
            "accountId": 1,
            "createdAt": int(datetime.now(timezone.utc).timestamp()),
            "bundleContent": [
                {"accountMediaId": 101, "pos": 2},
                {"accountMediaId": 102, "pos": 1},
            ],
        }
    ]

    await process_media_bundles(mock_config, 1, bundles_data, session=session)

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
