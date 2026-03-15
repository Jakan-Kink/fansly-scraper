"""Unit tests for metadata.media module."""

import json

import pytest

from metadata import Media
from metadata.media import process_media_item_dict
from metadata.models import Account, MediaLocation
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_media_creation(entity_store):
    """Test creating a Media object with basic attributes via the EntityStore."""
    store = entity_store

    account_id = snowflake_id()
    media_id = snowflake_id()

    account = Account(id=account_id, username="test_user_123")
    await store.save(account)

    media = Media(
        id=media_id,
        accountId=account_id,
        mimetype="video/mp4",
        width=1920,
        height=1080,
        duration=30.5,
    )
    await store.save(media)

    saved_media = await store.get(Media, media_id)
    assert saved_media is not None
    assert saved_media.id == media_id
    assert saved_media.accountId == account_id
    assert saved_media.mimetype == "video/mp4"
    assert saved_media.width == 1920
    assert saved_media.height == 1080
    assert saved_media.duration == 30.5


@pytest.mark.asyncio
async def test_process_video_metadata(entity_store, config):
    """Test processing video metadata with duration and dimensions.

    process_media_item_dict uses get_store() internally, which is wired
    to the entity_store fixture via register_models().
    """
    store = entity_store

    account_id = snowflake_id()
    media_id = snowflake_id()

    account = Account(id=account_id, username="test_user_123")
    await store.save(account)

    media_item = {
        "id": media_id,
        "accountId": account_id,
        "mimetype": "video/mp4",
        "metadata": json.dumps(
            {"original": {"width": 1920, "height": 1080}, "duration": 30.5}
        ),
    }

    await process_media_item_dict(config, media_item)

    saved_media = await store.get(Media, media_id)
    assert saved_media is not None
    assert saved_media.width == 1920
    assert saved_media.height == 1080
    assert saved_media.duration == 30.5


@pytest.mark.asyncio
async def test_media_location(entity_store):
    """Test creating Media with associated MediaLocation objects.

    Locations are added after the initial save so the media row exists
    before _sync_associations inserts into media_locations (FK requirement).
    """
    store = entity_store

    account_id = snowflake_id()
    media_id = snowflake_id()

    account = Account(id=account_id, username="test_user_123")
    await store.save(account)

    # Save media first (creates the row)
    media = Media(id=media_id, accountId=account_id, mimetype="video/mp4")
    await store.save(media)

    # Then add locations and save again (triggers _sync_associations)
    media.locations = [
        MediaLocation(
            mediaId=media_id,
            locationId=102,
            location="https://example.com/video.mp4",
        )
    ]
    await store.save(media)

    assert len(media.locations) == 1
    assert media.locations[0].locationId == 102
    # normalize_cdn_url strips query params; plain path stays unchanged
    assert "example.com/video.mp4" in media.locations[0].location


@pytest.mark.asyncio
async def test_invalid_metadata(entity_store, config):
    """Test handling invalid metadata JSON.

    When metadata is not valid JSON, the model stores it as meta_info
    and leaves dimension/duration fields as None.
    """
    store = entity_store

    account_id = snowflake_id()
    media_id = snowflake_id()

    account = Account(id=account_id, username="test_user_123")
    await store.save(account)

    media_item = {
        "id": media_id,
        "accountId": account_id,
        "mimetype": "video/mp4",
        "metadata": "invalid json",
    }

    await process_media_item_dict(config, media_item)

    saved_media = await store.get(Media, media_id)
    assert saved_media is not None
    assert saved_media.meta_info == "invalid json"
    assert saved_media.duration is None
    assert saved_media.width is None
    assert saved_media.height is None
