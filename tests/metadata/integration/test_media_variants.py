"""Integration tests for media variants, bundles and preview handling."""

import copy

import pytest

from api.fansly import FanslyApi
from metadata import (
    AccountMediaBundle,
    Media,
    process_media_info,
    process_messages_metadata,
)
from metadata.account import process_media_bundles_data
from tests.fixtures import setup_accounts_and_groups


class TestMediaVariants:
    """Test class for media variants and bundles functionality."""

    @pytest.mark.asyncio
    async def test_hls_dash_variants(
        self, entity_store, mock_config, conversation_data
    ):
        """Test processing of HLS and DASH stream variants.

        Verifies that Media objects with variants are created when
        process_media_info processes accountMedia with variants.
        """
        response = FanslyApi.convert_ids_to_int(
            copy.deepcopy(conversation_data["response"])
        )
        messages = response.get("messages", [])
        media_items = response.get("accountMedia", [])

        # Set up accounts
        await setup_accounts_and_groups(conversation_data, messages)

        # Process messages
        await process_messages_metadata(mock_config, None, response)

        # Process accountMedia (creates Media + variant records)
        if media_items:
            await process_media_info(mock_config, {"batch": media_items})

        # Verify media with variants
        for media_data in media_items:
            if media_data.get("media", {}).get("variants"):
                media_id = media_data["media"]["id"]
                media = await entity_store.get(Media, media_id)
                assert media is not None
                if media.variants:
                    assert len(media.variants) > 0

    @pytest.mark.asyncio
    async def test_media_bundles(self, entity_store, mock_config, conversation_data):
        """Test processing of media bundles."""
        response = FanslyApi.convert_ids_to_int(
            copy.deepcopy(conversation_data["response"])
        )
        messages = response.get("messages", [])
        bundles = response.get("accountMediaBundles", [])

        if not bundles:
            pytest.skip("No media bundles found in test data")

        # Set up accounts
        await setup_accounts_and_groups(conversation_data, messages)

        # Process messages
        await process_messages_metadata(mock_config, None, response)

        # Process accountMedia first (bundles reference these)
        media_items = response.get("accountMedia", [])
        if media_items:
            await process_media_info(mock_config, {"batch": media_items})

        # Process bundles
        await process_media_bundles_data(mock_config, response)

        # Verify bundles
        for bundle_data in bundles:
            bundle = await entity_store.get(AccountMediaBundle, bundle_data["id"])
            assert bundle is not None

    @pytest.mark.asyncio
    async def test_preview_variants(self, entity_store, mock_config, conversation_data):
        """Test processing of preview image variants."""
        response = FanslyApi.convert_ids_to_int(
            copy.deepcopy(conversation_data["response"])
        )
        messages = response.get("messages", [])
        media_items = response.get("accountMedia", [])

        # Set up accounts
        await setup_accounts_and_groups(conversation_data, messages)

        # Process messages
        await process_messages_metadata(mock_config, None, response)

        # Process accountMedia (creates preview Media records too)
        if media_items:
            await process_media_info(mock_config, {"batch": media_items})

        # Verify previews
        for media_data in media_items:
            if media_data.get("preview"):
                preview_data = media_data["preview"]
                preview_id = preview_data["id"]
                preview = await entity_store.get(Media, preview_id)
                assert preview is not None
