"""Unit tests for metadata.account module."""

from datetime import UTC, datetime

import pytest

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
async def test_account_media_bundle_creation(entity_store):
    """Test creating an AccountMediaBundle with ordered content.

    Saves entities in FK order, then sets the ordered accountMedia
    relationship on the bundle and verifies persistence.
    """
    store = entity_store

    account_id = snowflake_id()
    media_id1 = snowflake_id()
    media_id2 = snowflake_id()
    am_id1 = snowflake_id()
    am_id2 = snowflake_id()
    bundle_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    # Media records (required by AccountMedia FK)
    media1 = Media(id=media_id1, accountId=account_id)
    media2 = Media(id=media_id2, accountId=account_id)
    await store.save(media1)
    await store.save(media2)

    # AccountMedia linking to Media
    am1 = AccountMedia(
        id=am_id1, accountId=account_id, mediaId=media_id1, createdAt=datetime.now(UTC)
    )
    am2 = AccountMedia(
        id=am_id2, accountId=account_id, mediaId=media_id2, createdAt=datetime.now(UTC)
    )
    await store.save(am1)
    await store.save(am2)

    # Bundle with ordered media
    bundle = AccountMediaBundle(
        id=bundle_id, accountId=account_id, createdAt=datetime.now(UTC)
    )
    await store.save(bundle)

    # Assign the ordered relationship and re-save (triggers _sync_associations)
    bundle.accountMedia = [am2, am1]  # pos 0 = am2, pos 1 = am1
    await store.save(bundle)

    # Verify
    saved = await store.get(AccountMediaBundle, bundle_id)
    assert saved is not None
    media_ids = sorted([m.id for m in saved.accountMedia])
    assert media_ids == sorted([am_id1, am_id2])


@pytest.mark.asyncio
async def test_update_optimization(entity_store, config):
    """Test that process_account_data updates only changed values.

    process_account_data uses get_store() internally, wired via entity_store.
    """
    store = entity_store

    account_id = snowflake_id()

    # Create initial account
    account = Account(id=account_id, username="test_user", displayName="Test User")
    await store.save(account)

    # Process with same values — displayName should stay unchanged
    data = {
        "id": account_id,
        "username": "test_user",
        "displayName": "Test User",
        "timelineStats": {"imageCount": 0, "videoCount": 0},
    }
    await process_account_data(config, data)

    saved = await store.get(Account, account_id)
    assert saved.displayName == "Test User"

    # Process with different displayName
    data["displayName"] = "New Name"
    await process_account_data(config, data)

    saved = await store.get(Account, account_id)
    assert saved.displayName == "New Name"
    assert saved.username == "test_user"


@pytest.mark.asyncio
async def test_timeline_stats_optimization(entity_store, config):
    """Test that timeline stats are only updated when values change.

    process_account_data uses get_store() internally, wired via entity_store.
    """
    store = entity_store

    account_id = snowflake_id()

    # Create account + initial timeline stats
    account = Account(id=account_id, username="test_user")
    await store.save(account)

    stats = TimelineStats(accountId=account_id, imageCount=10, videoCount=5)
    await store.save(stats)

    # Process with same stats — should not change
    data = {
        "id": account_id,
        "username": "test_user",
        "timelineStats": {
            "imageCount": 10,
            "videoCount": 5,
            "fetchedAt": int(datetime(2023, 10, 10, tzinfo=UTC).timestamp()),
        },
    }
    await process_account_data(config, data)

    saved = await store.find_one(TimelineStats, accountId=account_id)
    assert saved.imageCount == 10

    # Process with different imageCount
    data["timelineStats"]["imageCount"] = 15
    await process_account_data(config, data)

    saved = await store.find_one(TimelineStats, accountId=account_id)
    assert saved.imageCount == 15
    assert saved.videoCount == 5


@pytest.mark.asyncio
async def test_process_media_bundles(entity_store, config):
    """Test processing media bundles from API response.

    process_media_bundles uses get_store() internally, wired via entity_store.
    """
    store = entity_store

    account_id = snowflake_id()
    media_id1 = snowflake_id()
    media_id2 = snowflake_id()
    am_id1 = snowflake_id()
    am_id2 = snowflake_id()
    bundle_id = snowflake_id()

    # Create prerequisite entities in FK order
    account = Account(id=account_id, username="test_user")
    await store.save(account)

    media1 = Media(id=media_id1, accountId=account_id)
    media2 = Media(id=media_id2, accountId=account_id)
    await store.save(media1)
    await store.save(media2)

    am1 = AccountMedia(
        id=am_id1, accountId=account_id, mediaId=media_id1, createdAt=datetime.now(UTC)
    )
    am2 = AccountMedia(
        id=am_id2, accountId=account_id, mediaId=media_id2, createdAt=datetime.now(UTC)
    )
    await store.save(am1)
    await store.save(am2)

    # Process bundles — use accountMediaIds (the Pydantic alias) directly.
    # NOTE: bundleContent → accountMediaIds conversion is a Phase 5.5
    # TODO in _process_single_bundle; for now the model expects IDs.
    bundles_data = [
        {
            "id": bundle_id,
            "accountId": account_id,
            "createdAt": int(datetime.now(UTC).timestamp()),
            "accountMediaIds": [am_id2, am_id1],  # IDs in desired order
        }
    ]
    await process_media_bundles(config, account_id, bundles_data)

    # Verify bundle was created with media
    bundle = await store.get(AccountMediaBundle, bundle_id)
    assert bundle is not None
    media_ids = sorted([m.id for m in bundle.accountMedia])
    assert media_ids == sorted([am_id1, am_id2])
