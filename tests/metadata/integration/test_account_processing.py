"""Integration tests for account processing functionality."""

from datetime import UTC, datetime

import pytest

from api.fansly import FanslyApi
from metadata import (
    Account,
    AccountMediaBundle,
    Media,
    TimelineStats,
    process_account_data,
    process_media_bundles,
)
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_process_account_from_timeline(entity_store, mock_config, timeline_data):
    """Test processing account data from timeline response."""
    account_data = timeline_data["response"]["accounts"][0]

    # Process the account — uses get_store() internally
    await process_account_data(mock_config, account_data)

    # Verify account was created in entity_store
    account_id = int(account_data["id"])
    account = await entity_store.get(Account, account_id)
    assert account is not None
    assert account.username == account_data["username"]

    # Verify timeline stats if present
    if "timelineStats" in account_data:
        stats = await entity_store.get(TimelineStats, account_id)
        assert stats is not None
        assert stats.imageCount == account_data["timelineStats"]["imageCount"]
        assert stats.videoCount == account_data["timelineStats"]["videoCount"]

    # Verify avatar if present
    if "avatar" in account_data:
        assert account.avatar is not None
        avatar_id = int(account_data["avatar"]["id"])
        avatar = await entity_store.get(Media, avatar_id)
        assert avatar is not None

    # Verify banner if present
    if "banner" in account_data:
        assert account.banner is not None
        banner_id = int(account_data["banner"]["id"])
        banner = await entity_store.get(Media, banner_id)
        assert banner is not None


@pytest.mark.asyncio
async def test_update_optimization_integration(entity_store, mock_config):
    """Integration test for update optimization — processing same data twice."""
    account_id = snowflake_id()
    account_data = {
        "id": account_id,
        "username": "test_optimization",
        "displayName": "Test User",
        "timelineStats": {
            "accountId": account_id,
            "imageCount": 10,
            "videoCount": 5,
            "fetchedAt": int(datetime.now(UTC).timestamp() * 1000),
        },
    }

    # Process initial data
    await process_account_data(mock_config, account_data)

    # Verify initial state
    account = await entity_store.get(Account, account_id)
    assert account is not None
    assert account.displayName == "Test User"

    stats = await entity_store.get(TimelineStats, account_id)
    assert stats is not None
    assert stats.imageCount == 10

    # Process same data again — should be a no-op (dirty tracking)
    await process_account_data(mock_config, account_data)

    # Update some values
    account_data["displayName"] = "Updated Name"
    account_data["timelineStats"]["imageCount"] = 15
    await process_account_data(mock_config, account_data)

    # Verify only changed values were updated
    account = await entity_store.get(Account, account_id)
    assert account.displayName == "Updated Name"

    stats = await entity_store.get(TimelineStats, account_id)
    assert stats.imageCount == 15
    assert stats.videoCount == 5  # Should remain unchanged


@pytest.mark.asyncio
async def test_process_account_media_bundles(entity_store, mock_config, timeline_data):
    """Test processing account media bundles from timeline response."""
    if "accountMediaBundles" not in timeline_data["response"]:
        pytest.skip("No bundles found in test data")

    # Convert string IDs to int (mimics what API layer does via convert_ids_to_int)
    import copy

    response = FanslyApi.convert_ids_to_int(copy.deepcopy(timeline_data["response"]))

    account_data = response["accounts"][0]
    bundles_data = response["accountMediaBundles"]

    # Create the account first
    await process_account_data(mock_config, account_data)

    account_id = account_data["id"]

    # Pre-create required Media records that bundles reference
    for bundle in bundles_data:
        for content in bundle.get("bundleContent", []):
            media_id = content["accountMediaId"]
            existing = await entity_store.get(Media, media_id)
            if not existing:
                await entity_store.save(Media(id=media_id, accountId=account_id))

    # Process the bundles via production code
    await process_media_bundles(mock_config, account_id, bundles_data)

    # Verify bundles were created
    for bundle_data in bundles_data:
        bundle_id = bundle_data["id"]
        bundle = await entity_store.get(AccountMediaBundle, bundle_id)
        assert bundle is not None
