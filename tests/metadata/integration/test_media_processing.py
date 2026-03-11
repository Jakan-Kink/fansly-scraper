"""Integration tests for media processing functionality."""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import text

from metadata.account import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    process_media_bundles,
)
from metadata.media import Media, process_media_info


@pytest.mark.asyncio
async def test_process_video_from_timeline(test_database, config, timeline_data):
    """Test processing a video from timeline data."""
    # Find a video in the test data
    media_data = None
    for media in timeline_data["response"]["accountMedia"]:
        if media.get("media", {}).get("mimetype", "").startswith("video/"):
            media_data = media
            break

    if not media_data:
        pytest.skip("No video found in test data")

    # Ensure we have all required fields
    assert "mediaId" in media_data, "Missing mediaId in media_data"
    assert "media" in media_data, "Missing media object in media_data"
    assert "mimetype" in media_data["media"], "Missing mimetype in media object"

    # Process the media
    async with test_database.async_session_scope() as session:
        # Pre-create the Account record to satisfy FK constraint on Media.accountId
        account_id = int(media_data["accountId"])
        account = Account(id=account_id, username=f"test_user_{account_id}")
        session.add(account)
        await session.flush()

        # Process the media with session parameter
        await process_media_info(config, media_data, session=session)

        # Test 1: Basic media record creation
        result = await session.execute(
            text("SELECT * FROM media WHERE id = :media_id"),
            {"media_id": media_data["mediaId"]},
        )
        media = result.fetchone()
        assert media is not None, f"Media {media_data['mediaId']} not found"
        assert media.mimetype == media_data["media"]["mimetype"], "Mimetype mismatch"

        # Test 2: Metadata handling
        if "metadata" in media_data["media"]:
            try:
                metadata = json.loads(media_data["media"]["metadata"])
            except json.JSONDecodeError as e:
                pytest.fail(f"Invalid JSON in metadata: {e}")

            # Test 2.1: Duration
            if "duration" in metadata:
                try:
                    expected_duration = float(metadata["duration"])
                    assert media.duration == expected_duration, (
                        f"Duration mismatch: got {media.duration}, expected {expected_duration}"
                    )
                except (ValueError, TypeError) as e:
                    pytest.fail(f"Invalid duration value: {e}")

            # Test 2.2: Dimensions
            if "original" in metadata:
                original = metadata["original"]
                # Width
                if "width" in original:
                    try:
                        expected_width = int(original["width"])
                        assert media.width == expected_width, (
                            f"Width mismatch: got {media.width}, expected {expected_width}"
                        )
                    except (ValueError, TypeError) as e:
                        pytest.fail(f"Invalid width value: {e}")

                # Height
                if "height" in original:
                    try:
                        expected_height = int(original["height"])
                        assert media.height == expected_height, (
                            f"Height mismatch: got {media.height}, expected {expected_height}"
                        )
                    except (ValueError, TypeError) as e:
                        pytest.fail(f"Invalid height value: {e}")

        # Test 3: Process same media again to test unique constraint handling
        await process_media_info(config, media_data, session=session)
        # Verify only one record exists
        result = await session.execute(
            text("SELECT COUNT(*) FROM media WHERE id = :media_id"),
            {"media_id": media_data["mediaId"]},
        )
        count = result.scalar()
        assert count == 1, "Duplicate media record created"

        # Cleanup (cascade: variants → locations → media → accounts)
        await session.execute(text("DELETE FROM media_variants"))
        await session.execute(text("DELETE FROM media_locations"))
        await session.execute(text("DELETE FROM account_media"))
        await session.execute(text("DELETE FROM media"))
        await session.execute(text("DELETE FROM accounts"))
        await session.commit()


@pytest.mark.asyncio
async def test_process_media_bundle_from_timeline(test_database, config, timeline_data):
    """Test processing a media bundle from timeline data."""
    if "accountMediaBundles" not in timeline_data["response"]:
        pytest.skip("No bundles found in test data")

    bundle_data = timeline_data["response"]["accountMediaBundles"][0]

    # Get the account ID from the timeline data
    account_id = int(timeline_data["response"]["accountMedia"][0]["accountId"])

    async with test_database.async_session_scope() as session:
        # Pre-create the Account record to satisfy FK constraints
        account = Account(id=account_id, username=f"test_user_{account_id}")
        session.add(account)
        await session.flush()

        # Create necessary Media and AccountMedia records (convert string IDs to ints)
        now = datetime.now(UTC)
        for content in bundle_data.get("bundleContent", []):
            am_id = int(content["accountMediaId"])
            # Create Media record first (FK target for AccountMedia.mediaId)
            media_obj = Media(id=am_id, accountId=account_id, mimetype="image/jpeg")
            session.add(media_obj)
        await session.flush()

        for content in bundle_data.get("bundleContent", []):
            am_id = int(content["accountMediaId"])
            am = AccountMedia(
                id=am_id,
                accountId=account_id,
                mediaId=am_id,
                createdAt=now,
            )
            session.add(am)
        await session.commit()

        # Process the bundle
        await process_media_bundles(config, account_id, [bundle_data], session=session)

        # Verify the bundle was created
        bundle_id = int(bundle_data["id"])
        result = await session.execute(
            select(AccountMediaBundle)
            .options(selectinload(AccountMediaBundle.accountMedia))
            .where(AccountMediaBundle.id == bundle_id)
        )
        bundle = result.scalar_one_or_none()
        assert bundle is not None

        # Verify bundle content count
        assert len(bundle.accountMedia) == len(bundle_data["bundleContent"])
