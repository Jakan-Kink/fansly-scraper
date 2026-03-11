"""Integration tests for media variants, bundles and preview handling."""

import pytest

from metadata.media import process_media_info
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
        """Test processing of HLS and DASH stream variants.

        Verifies that media_variants junction rows are created when
        process_messages_metadata processes media with variants.
        """
        response_data = conversation_data["response"]
        messages = response_data["messages"]
        media_items = response_data.get("accountMedia", [])

        async with test_database.async_session_scope() as session:
            # Set up accounts and groups from conversation data
            await setup_accounts_and_groups(session, conversation_data, messages)

            await process_messages_metadata(
                config, None, response_data, session=session
            )

            # Process accountMedia (in production this happens in download/common.py)
            # This creates Media records + variant junction rows
            if media_items:
                await process_media_info(
                    config, {"batch": media_items}, session=session
                )

            for media_data in media_items:
                if media_data.get("media", {}).get("variants"):
                    # Verify junction rows were created in media_variants table
                    assert await verify_media_variants(
                        session,
                        media_data["media"]["id"],
                        expected_variant_types=[302, 303],
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
        """Test processing of preview image variants.

        Verifies that media_variants junction rows are created for preview
        media that has variants (different resolutions).
        """
        response_data = conversation_data["response"]
        messages = response_data["messages"]
        media_items = response_data.get("accountMedia", [])

        async with test_database.async_session_scope() as session:
            # Set up accounts and groups from conversation data
            await setup_accounts_and_groups(session, conversation_data, messages)

            await process_messages_metadata(
                config, None, response_data, session=session
            )

            # Process accountMedia (in production this happens in download/common.py)
            if media_items:
                await process_media_info(
                    config, {"batch": media_items}, session=session
                )

            for media_data in media_items:
                if media_data.get("preview"):
                    preview_data = media_data["preview"]
                    if preview_data.get("variants"):
                        # Verify preview variant junction rows were created
                        expected_resolutions = [(1280, 720), (854, 480)]
                        assert await verify_preview_variants(
                            session, preview_data["id"], expected_resolutions
                        )

                    # Preview dimensions should be reasonable
                    assert preview_data["width"] >= 854  # At least the smallest variant
                    assert preview_data["height"] >= 480
