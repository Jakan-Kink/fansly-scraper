"""Integration tests for media variants, bundles and preview handling."""

import pytest

from metadata.messages import process_messages_metadata
from tests.metadata.conftest import TestDatabase
from tests.metadata.helpers.utils import (
    verify_media_bundle_content,
    verify_media_variants,
    verify_preview_variants,
)


class TestMediaVariants:
    """Test class for media variants and bundles functionality."""

    @pytest.mark.asyncio
    async def test_hls_dash_variants(
        self, test_database: TestDatabase, config, conversation_data
    ):
        """Test processing of HLS and DASH stream variants."""
        messages = conversation_data["response"]["messages"]
        media_items = conversation_data["response"].get("accountMedia", [])

        async with test_database.async_session_scope() as session:
            await process_messages_metadata(config, None, messages, session=session)

            for media_data in media_items:
                if media_data.get("media", {}).get("variants"):
                    # Verify HLS (302) and DASH (303) variants exist
                    assert await verify_media_variants(
                        session, media_data["id"], expected_variant_types=[302, 303]
                    )

                    # Verify resolutions in variants
                    for variant in media_data["media"]["variants"]:
                        if variant["type"] in (302, 303):
                            assert "1920x1080" in variant["metadata"]
                            assert "1280x720" in variant["metadata"]
                            assert "854x480" in variant["metadata"]

    @pytest.mark.asyncio
    async def test_media_bundles(
        self, test_database: TestDatabase, config, conversation_data
    ):
        """Test processing of media bundles."""
        messages = conversation_data["response"]["messages"]
        bundles = conversation_data["response"].get("accountMediaBundles", [])

        if not bundles:
            pytest.skip("No media bundles found in test data")

        async with test_database.async_session_scope() as session:
            await process_messages_metadata(config, None, messages, session=session)

            for bundle_data in bundles:
                # Verify bundle content and ordering
                expected_media_ids = [
                    content["accountMediaId"]
                    for content in sorted(
                        bundle_data["bundleContent"], key=lambda x: x["pos"]
                    )
                ]
                assert await verify_media_bundle_content(
                    session, bundle_data["id"], expected_media_ids
                )

    @pytest.mark.asyncio
    async def test_preview_variants(
        self, test_database: TestDatabase, config, conversation_data
    ):
        """Test processing of preview image variants."""
        messages = conversation_data["response"]["messages"]
        media_items = conversation_data["response"].get("accountMedia", [])

        async with test_database.async_session_scope() as session:
            await process_messages_metadata(config, None, messages, session=session)

            for media_data in media_items:
                if media_data.get("preview"):
                    preview_data = media_data["preview"]
                    # Verify preview variants with expected resolutions
                    expected_resolutions = [(1280, 720), (854, 480)]
                    assert await verify_preview_variants(
                        session, preview_data["id"], expected_resolutions
                    )

                    # Verify original preview dimensions
                    assert preview_data["width"] == 1920
                    assert preview_data["height"] == 1080
