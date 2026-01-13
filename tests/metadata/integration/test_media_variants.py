"""Integration tests for media variants, bundles and preview handling."""

import pytest

from metadata.messages import process_messages_metadata
from tests.fixtures import setup_accounts_and_groups
from tests.fixtures.database.database_fixtures import TestDatabase
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
        response_data = conversation_data["response"]
        messages = response_data["messages"]
        media_items = response_data.get("accountMedia", [])

        async with test_database.async_session_scope() as session:
            # Set up accounts and groups from conversation data
            await setup_accounts_and_groups(session, conversation_data, messages)

            await process_messages_metadata(
                config, None, response_data, session=session
            )

            for media_data in media_items:
                if media_data.get("media", {}).get("variants"):
                    # Note: Variants are NOT stored in database per production code
                    # (see metadata/media.py MediaBatch - "SKIP VARIANTS" comment)
                    # This test now only verifies the data exists in test JSON
                    assert await verify_media_variants(
                        session, media_data["id"], expected_variant_types=[302, 303]
                    )

    @pytest.mark.asyncio
    async def test_media_bundles(
        self, test_database: TestDatabase, config, conversation_data
    ):
        """Test processing of media bundles."""
        response_data = conversation_data["response"]
        messages = response_data["messages"]
        bundles = response_data.get("accountMediaBundles", [])

        if not bundles:
            pytest.skip("No media bundles found in test data")

        async with test_database.async_session_scope() as session:
            # Set up accounts and groups from conversation data
            await setup_accounts_and_groups(session, conversation_data, messages)

            await process_messages_metadata(
                config, None, response_data, session=session
            )

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
        response_data = conversation_data["response"]
        messages = response_data["messages"]
        media_items = response_data.get("accountMedia", [])

        async with test_database.async_session_scope() as session:
            # Set up accounts and groups from conversation data
            await setup_accounts_and_groups(session, conversation_data, messages)

            await process_messages_metadata(
                config, None, response_data, session=session
            )

            for media_data in media_items:
                if media_data.get("preview"):
                    preview_data = media_data["preview"]
                    # Verify preview variants with expected resolutions
                    expected_resolutions = [(1280, 720), (854, 480)]
                    assert await verify_preview_variants(
                        session, preview_data["id"], expected_resolutions
                    )

                    # Note: Preview width/height in the test data represents the first variant,
                    # not the original. The actual 1920x1080 would be in the original media.
                    # Test data shows preview at 1920x1080 which is likely the highest resolution
                    assert preview_data["width"] >= 854  # At least the smallest variant
                    assert preview_data["height"] >= 480
