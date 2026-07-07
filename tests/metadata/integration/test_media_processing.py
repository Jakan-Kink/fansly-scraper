"""Integration tests for media processing functionality."""

import copy
import json

import pytest

from api.fansly import FanslyApi
from metadata import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Media,
    process_media_bundles,
    process_media_info,
)


@pytest.mark.asyncio
async def test_process_video_from_timeline(entity_store, mock_config, timeline_data):
    """Test processing a video from timeline data."""
    response = FanslyApi.convert_ids_to_int(copy.deepcopy(timeline_data["response"]))
    assert isinstance(response, dict)

    # Find a video in the test data
    media_data = None
    account_media = response.get("accountMedia", [])
    assert isinstance(account_media, list)
    for media in account_media:
        assert isinstance(media, dict)
        media_obj = media.get("media")
        if isinstance(media_obj, dict) and str(
            media_obj.get("mimetype", "")
        ).startswith("video/"):
            media_data = media
            break

    if not media_data:
        pytest.skip("No video found in test data")

    assert "mediaId" in media_data, "Missing mediaId in media_data"
    assert "media" in media_data, "Missing media object in media_data"

    # Pre-create the Account (FK constraint: Media.accountId → accounts.id)
    account_id = media_data["accountId"]
    assert isinstance(account_id, int)
    await entity_store.save(Account(id=account_id, username=f"test_user_{account_id}"))

    # Process the media via production code
    await process_media_info(mock_config, media_data)

    # Verify Media record was created
    media_dict = media_data["media"]
    assert isinstance(media_dict, dict)
    media_id = media_dict["id"]
    media = await entity_store.get(Media, media_id)
    assert media is not None
    assert media.mimetype == media_dict["mimetype"]

    # Verify metadata (duration, dimensions)
    if "metadata" in media_dict:
        raw_metadata = media_dict["metadata"]
        assert isinstance(raw_metadata, str)
        try:
            metadata = json.loads(raw_metadata)
        except json.JSONDecodeError:
            metadata = {}

        if "duration" in metadata:
            assert media.duration == float(metadata["duration"])

        if "original" in metadata:
            original = metadata["original"]
            if "width" in original:
                assert media.width == int(original["width"])
            if "height" in original:
                assert media.height == int(original["height"])

    # Verify AccountMedia was created
    am = await entity_store.get(AccountMedia, media_data["id"])
    assert am is not None

    # Process same media again — should be idempotent (identity map dedup)
    await process_media_info(mock_config, media_data)

    # Still only one Media record (identity map prevents duplicates)
    media_again = await entity_store.get(Media, media_id)
    assert media_again is not None


@pytest.mark.asyncio
async def test_process_media_bundle_from_timeline(
    entity_store, mock_config, timeline_data
):
    """Test processing a media bundle from timeline data."""
    response = FanslyApi.convert_ids_to_int(copy.deepcopy(timeline_data["response"]))
    assert isinstance(response, dict)

    if "accountMediaBundles" not in response:
        pytest.skip("No bundles found in test data")

    bundles = response["accountMediaBundles"]
    assert isinstance(bundles, list)
    bundle_data = bundles[0]
    assert isinstance(bundle_data, dict)
    account_media = response["accountMedia"]
    assert isinstance(account_media, list)
    first_am = account_media[0]
    assert isinstance(first_am, dict)
    account_id = first_am["accountId"]
    assert isinstance(account_id, int)

    # Pre-create the Account (FK constraint)
    await entity_store.save(Account(id=account_id, username=f"test_user_{account_id}"))

    # Pre-create Media records that bundles reference
    bundle_content = bundle_data.get("bundleContent", [])
    assert isinstance(bundle_content, list)
    for content in bundle_content:
        assert isinstance(content, dict)
        media_id = content["accountMediaId"]
        assert isinstance(media_id, int)
        await entity_store.save(
            Media(id=media_id, accountId=account_id, mimetype="image/jpeg")
        )

    # Process the bundle via production code
    await process_media_bundles(mock_config, account_id, [bundle_data])

    # Verify bundle was created
    bundle_id = bundle_data["id"]
    bundle = await entity_store.get(AccountMediaBundle, bundle_id)
    assert bundle is not None
