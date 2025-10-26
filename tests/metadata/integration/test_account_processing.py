"""Integration tests for account processing functionality."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from metadata import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Media,
    TimelineStats,
    process_account_data,
)
from metadata.account import process_media_bundles


@pytest.mark.asyncio
async def test_process_account_from_timeline(test_database, config, timeline_data):
    """Test processing account data from timeline response."""
    async with test_database.async_session_scope() as session:
        # Get first account from timeline data
        account_data = timeline_data["response"]["accounts"][0]

        # Process the account
        await process_account_data(config, account_data, session=session)

        # Verify account was created
        result = await session.execute(select(Account).filter_by(id=account_data["id"]))
        account = result.scalar_one_or_none()
        assert account is not None
        assert account.username == account_data["username"]

        # Verify timeline stats if present
        if "timelineStats" in account_data:
            result = await session.execute(
                select(TimelineStats).filter_by(accountId=account.id)
            )
            stats = result.scalar_one_or_none()
            assert stats is not None
            assert stats.imageCount == account_data["timelineStats"]["imageCount"]
            assert stats.videoCount == account_data["timelineStats"]["videoCount"]

        # Verify avatar if present
        if "avatar" in account_data:
            assert account.avatar is not None
            result = await session.execute(
                select(Media).filter_by(id=account_data["avatar"]["id"])
            )
            avatar_media = result.scalar_one_or_none()
            assert avatar_media is not None

        # Verify banner if present
        if "banner" in account_data:
            assert account.banner is not None
            result = await session.execute(
                select(Media).filter_by(id=account_data["banner"]["id"])
            )
            banner_media = result.scalar_one_or_none()
            assert banner_media is not None


@pytest.mark.asyncio
async def test_update_optimization_integration(test_database, config):
    """Integration test for update optimization."""
    async with test_database.async_session_scope() as session:
        # Create initial account with timeline stats
        account_data = {
            "id": 999999,
            "username": "test_optimization",
            "displayName": "Test User",
            "timelineStats": {
                "imageCount": 10,
                "videoCount": 5,
                "fetchedAt": int(datetime.now(UTC).timestamp() * 1000),
            },
        }

        # Process initial data
        await process_account_data(config, account_data, session=session)

        # Get initial update time of the account and stats
        result = await session.execute(select(Account).filter_by(id=account_data["id"]))
        account = result.scalar_one_or_none()
        result = await session.execute(
            select(TimelineStats).filter_by(accountId=account_data["id"])
        )
        stats = result.scalar_one_or_none()
        initial_account_updated = account._sa_instance_state.modified
        initial_stats_updated = stats._sa_instance_state.modified

        # Process same data again
        await process_account_data(config, account_data, session=session)

        # Check that nothing was updated
        result = await session.execute(select(Account).filter_by(id=account_data["id"]))
        account = result.scalar_one_or_none()
        result = await session.execute(
            select(TimelineStats).filter_by(accountId=account_data["id"])
        )
        stats = result.scalar_one_or_none()
        assert account._sa_instance_state.modified == initial_account_updated, (
            "Account should not be marked as modified when no values changed"
        )
        assert stats._sa_instance_state.modified == initial_stats_updated, (
            "TimelineStats should not be marked as modified when no values changed"
        )

        # Update some values
        account_data["displayName"] = "Updated Name"
        account_data["timelineStats"]["imageCount"] = 15
        await process_account_data(config, account_data, session=session)

        # Check that only changed values were updated
        result = await session.execute(select(Account).filter_by(id=account_data["id"]))
        account = result.scalar_one_or_none()
        result = await session.execute(
            select(TimelineStats).filter_by(accountId=account_data["id"])
        )
        stats = result.scalar_one_or_none()
        assert account.displayName == "Updated Name"
        assert stats.imageCount == 15
        assert stats.videoCount == 5  # Should remain unchanged


@pytest.mark.asyncio
async def test_process_account_media_bundles(test_database, config, timeline_data):
    """Test processing account media bundles from timeline response."""
    if "accountMediaBundles" not in timeline_data["response"]:
        pytest.skip("No bundles found in test data")

    async with test_database.async_session_scope() as session:
        # Get first account and its bundles
        account_data = timeline_data["response"]["accounts"][0]
        bundles_data = timeline_data["response"]["accountMediaBundles"]

        # Create the account first
        await process_account_data(config, account_data, session=session)

        # Process each bundle's media
        for bundle in bundles_data:
            # Create necessary AccountMedia records
            for content in bundle.get("bundleContent", []):
                media = AccountMedia(
                    id=content["accountMediaId"],
                    accountId=account_data["id"],
                    mediaId=content["accountMediaId"],
                )
                session.add(media)
        await session.commit()

        # Process the bundles
        await process_media_bundles(
            config, account_data["id"], bundles_data, session=session
        )

        # Verify bundles were created with correct ordering
        for bundle_data in bundles_data:
            result = await session.execute(
                select(AccountMediaBundle).filter_by(id=bundle_data["id"])
            )
            bundle = result.scalar_one_or_none()
            assert bundle is not None

            # Verify media count
            assert len(bundle.accountMediaIds) == len(bundle_data["bundleContent"])

            # Verify order
            media_ids = [m.id for m in bundle.accountMediaIds]
            expected_order = [
                c["accountMediaId"]
                for c in sorted(bundle_data["bundleContent"], key=lambda x: x["pos"])
            ]
            assert media_ids == expected_order
