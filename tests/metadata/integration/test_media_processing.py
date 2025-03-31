"""Integration tests for media processing functionality."""

import json
import os
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload, sessionmaker
from sqlalchemy.orm.session import Session
from sqlalchemy.sql import text

from config import FanslyConfig
from metadata.account import Account, AccountMedia, AccountMediaBundle
from metadata.base import Base
from metadata.database import Database
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
                    assert (
                        media.duration == expected_duration
                    ), f"Duration mismatch: got {media.duration}, expected {expected_duration}"
                except (ValueError, TypeError) as e:
                    pytest.fail(f"Invalid duration value: {e}")

            # Test 2.2: Dimensions
            if "original" in metadata:
                original = metadata["original"]
                # Width
                if "width" in original:
                    try:
                        expected_width = int(original["width"])
                        assert (
                            media.width == expected_width
                        ), f"Width mismatch: got {media.width}, expected {expected_width}"
                    except (ValueError, TypeError) as e:
                        pytest.fail(f"Invalid width value: {e}")

                # Height
                if "height" in original:
                    try:
                        expected_height = int(original["height"])
                        assert (
                            media.height == expected_height
                        ), f"Height mismatch: got {media.height}, expected {expected_height}"
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

        # Cleanup
        await session.execute(text("DELETE FROM media"))
        await session.commit()


@pytest.mark.asyncio
async def test_process_media_bundle_from_timeline(test_database, config, timeline_data):
    """Test processing a media bundle from timeline data."""
    if "accountMediaBundles" not in timeline_data["response"]:
        pytest.skip("No bundles found in test data")

    bundle_data = timeline_data["response"]["accountMediaBundles"][0]

    async with test_database.async_session_scope() as session:
        # Create necessary AccountMedia records
        for content in bundle_data.get("bundleContent", []):
            media = AccountMedia(
                id=content["accountMediaId"],
                accountId=1,
                mediaId=content["accountMediaId"],
            )
            session.add(media)
        await session.commit()

        # Process the bundle
        from metadata.account import process_media_bundles

        await process_media_bundles(config, 1, [bundle_data], session=session)

        # Verify the results
        result = await session.execute(
            select(AccountMediaBundle)
            .options(selectinload(AccountMediaBundle.accountMediaIds))
            .where(AccountMediaBundle.id == bundle_data["id"])
        )
        bundle = result.fetchone()
        assert bundle is not None
        assert len(bundle.accountMediaIds) == len(bundle_data["bundleContent"])

        # Verify order is preserved
        media_ids = [m.id for m in bundle.accountMediaIds]
        expected_order = [
            c["accountMediaId"]
            for c in sorted(bundle_data["bundleContent"], key=lambda x: x["pos"])
        ]
        assert media_ids == expected_order
