"""Integration tests for media variants, bundles and preview handling."""

import copy

import pytest

from api.fansly import FanslyApi
from download.downloadstate import DownloadState
from metadata import (
    AccountMediaBundle,
    Media,
    process_media_info,
    process_messages_metadata,
)
from metadata.account import process_media_bundles_data
from tests.fixtures.metadata.metadata_factories import setup_accounts_and_groups


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
        assert isinstance(response, dict)
        messages_raw = response.get("messages", [])
        assert isinstance(messages_raw, list)
        messages = [m for m in messages_raw if isinstance(m, dict)]
        media_items = response.get("accountMedia", [])
        assert isinstance(media_items, list)

        # Set up accounts
        await setup_accounts_and_groups(conversation_data, messages)

        # Process messages
        await process_messages_metadata(mock_config, DownloadState(), response)

        # Process accountMedia (creates Media + variant records)
        if media_items:
            await process_media_info(mock_config, {"batch": media_items})

        # Verify media with variants
        for media_data in media_items:
            assert isinstance(media_data, dict)
            media = media_data.get("media")
            if isinstance(media, dict) and media.get("variants"):
                media_id = media["id"]
                media_obj = await entity_store.get(Media, media_id)
                assert media_obj is not None
                if media_obj.variants:
                    assert len(media_obj.variants) > 0

    @pytest.mark.asyncio
    async def test_media_bundles(self, entity_store, mock_config, conversation_data):
        """Test processing of media bundles."""
        response = FanslyApi.convert_ids_to_int(
            copy.deepcopy(conversation_data["response"])
        )
        assert isinstance(response, dict)
        messages_raw = response.get("messages", [])
        assert isinstance(messages_raw, list)
        messages = [m for m in messages_raw if isinstance(m, dict)]
        bundles = response.get("accountMediaBundles", [])
        assert isinstance(bundles, list)

        if not bundles:
            pytest.skip("No media bundles found in test data")

        # Set up accounts
        await setup_accounts_and_groups(conversation_data, messages)

        # Process messages
        await process_messages_metadata(mock_config, DownloadState(), response)

        # Process accountMedia first (bundles reference these)
        media_items = response.get("accountMedia", [])
        assert isinstance(media_items, list)
        if media_items:
            await process_media_info(mock_config, {"batch": media_items})

        # Process bundles
        await process_media_bundles_data(mock_config, response)

        # Verify bundles
        for bundle_data in bundles:
            assert isinstance(bundle_data, dict)
            bundle = await entity_store.get(AccountMediaBundle, bundle_data["id"])
            assert bundle is not None

    @pytest.mark.asyncio
    async def test_preview_variants(self, entity_store, mock_config, conversation_data):
        """Test processing of preview image variants."""
        response = FanslyApi.convert_ids_to_int(
            copy.deepcopy(conversation_data["response"])
        )
        assert isinstance(response, dict)
        messages_raw = response.get("messages", [])
        assert isinstance(messages_raw, list)
        messages = [m for m in messages_raw if isinstance(m, dict)]
        media_items = response.get("accountMedia", [])
        assert isinstance(media_items, list)

        # Set up accounts
        await setup_accounts_and_groups(conversation_data, messages)

        # Process messages
        await process_messages_metadata(mock_config, DownloadState(), response)

        # Process accountMedia (creates preview Media records too)
        if media_items:
            await process_media_info(mock_config, {"batch": media_items})

        # Verify previews
        for media_data in media_items:
            assert isinstance(media_data, dict)
            preview_data = media_data.get("preview")
            if isinstance(preview_data, dict):
                preview_id = preview_data["id"]
                preview = await entity_store.get(Media, preview_id)
                assert preview is not None
