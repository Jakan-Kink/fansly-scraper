"""Integration tests for account processing functionality."""

import copy
from datetime import UTC, datetime

import httpx
import pytest
import respx

from api.fansly import FanslyApi
from metadata import (
    Account,
    AccountMedia,
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

    response = FanslyApi.convert_ids_to_int(copy.deepcopy(timeline_data["response"]))

    account_data = response["accounts"][0]
    bundles_data = response["accountMediaBundles"]

    # Create the account first
    await process_account_data(mock_config, account_data)

    account_id = account_data["id"]

    # Pre-create Media + AccountMedia records for all bundle items so that
    # _process_single_bundle can resolve them from cache (no API backfill needed).
    for bundle in bundles_data:
        for am_id in bundle.get("accountMediaIds", []):
            media_id = snowflake_id()
            existing = await entity_store.get(AccountMedia, am_id)
            if not existing:
                existing_media = await entity_store.get(Media, media_id)
                if not existing_media:
                    await entity_store.save(Media(id=media_id, accountId=account_id))
                await entity_store.save(
                    AccountMedia(
                        id=am_id,
                        accountId=account_id,
                        mediaId=media_id,
                        createdAt=datetime.now(UTC),
                    )
                )

    # Process the bundles via production code
    await process_media_bundles(mock_config, account_id, bundles_data)

    # Verify bundles were created with all media items
    for bundle_data in bundles_data:
        bundle_id = bundle_data["id"]
        bundle = await entity_store.get(AccountMediaBundle, bundle_id)
        assert bundle is not None
        expected_count = len(bundle_data.get("accountMediaIds", []))
        assert len(bundle.accountMedia) == expected_count


@respx.mock
@pytest.mark.asyncio
async def test_bundle_truncation_backfill(entity_store, config_wired):
    """Test that bundles with >5 items backfill truncated accountMedia via API.

    The Fansly API truncates accountMedia objects at 5 items per bundle in
    timeline responses. _process_single_bundle detects missing IDs and
    fetches them via get_account_media() before saving the junction table.

    Reproduces the pattern from issue #63.
    """
    store = entity_store

    account_id = snowflake_id()
    bundle_id = snowflake_id()

    # 7 accountMedia items — first 5 "present" in response, last 2 "truncated"
    am_ids = [snowflake_id() for _ in range(7)]
    media_ids = [snowflake_id() for _ in range(7)]

    # Create prerequisite account
    account = Account(id=account_id, username="truncation_test")
    await store.save(account)

    # Create all 7 Media records (FK target for AccountMedia.mediaId)
    for mid in media_ids:
        await store.save(Media(id=mid, accountId=account_id))

    # Pre-cache only the first 5 AccountMedia (simulates what process_media_info
    # would have done from the response's truncated accountMedia array)
    for i in range(5):
        am = AccountMedia(
            id=am_ids[i],
            accountId=account_id,
            mediaId=media_ids[i],
            createdAt=datetime.now(UTC),
        )
        await store.save(am)

    # Mock the API call for the 2 missing accountMedia items
    # get_with_ngsw does OPTIONS preflight + GET
    respx.options(url__startswith="https://apiv3.fansly.com/api/v1/account/media").mock(
        side_effect=[httpx.Response(200)],
    )
    respx.get(url__startswith="https://apiv3.fansly.com/api/v1/account/media").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "success": True,
                    "response": [
                        {
                            "id": str(am_ids[5]),
                            "accountId": str(account_id),
                            "mediaId": str(media_ids[5]),
                            "createdAt": int(datetime.now(UTC).timestamp()),
                            "media": {
                                "id": str(media_ids[5]),
                                "accountId": str(account_id),
                                "mimetype": "image/jpeg",
                            },
                        },
                        {
                            "id": str(am_ids[6]),
                            "accountId": str(account_id),
                            "mediaId": str(media_ids[6]),
                            "createdAt": int(datetime.now(UTC).timestamp()),
                            "media": {
                                "id": str(media_ids[6]),
                                "accountId": str(account_id),
                                "mimetype": "image/jpeg",
                            },
                        },
                    ],
                },
            ),
        ],
    )

    # Bundle dict as it comes from the API — all 7 IDs listed,
    # but only the first 5 are in the identity map cache
    bundle_data = [
        {
            "id": bundle_id,
            "accountId": account_id,
            "createdAt": int(datetime.now(UTC).timestamp()),
            "accountMediaIds": am_ids,  # all 7
        }
    ]

    await process_media_bundles(config_wired, account_id, bundle_data)

    # Verify: bundle exists and junction table has all 7 positions
    bundle = await store.get(AccountMediaBundle, bundle_id)
    assert bundle is not None
    assert len(bundle.accountMedia) == 7

    # Verify ordering is preserved (junction table pos column)
    junction_ids = [am.id for am in bundle.accountMedia]
    assert junction_ids == am_ids
