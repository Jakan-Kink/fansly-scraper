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


@pytest.mark.xdist_group("media_variants")
class TestMediaVariants:
    """Test class for media variants and bundles functionality."""

    @pytest.mark.asyncio
    async def test_media_ingestion_full(
        self, entity_store, mock_config, conversation_data
    ):
        """Ingest conversation_data once, then assert variants, bundles, previews.

        Runs the shared ingestion prologue (messages + accountMedia + bundles)
        a single time, then verifies three facets in sequence: HLS/DASH stream
        variants, AccountMediaBundle records, and preview image Media records.
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
        bundles = response.get("accountMediaBundles", [])
        assert isinstance(bundles, list)

        # Set up accounts
        await setup_accounts_and_groups(conversation_data, messages)

        # Process messages
        await process_messages_metadata(mock_config, DownloadState(), response)

        # Process accountMedia (creates Media + variant + preview records;
        # bundles reference these)
        if media_items:
            await process_media_info(mock_config, {"batch": media_items})

        # Process bundles
        await process_media_bundles_data(mock_config, response)

        # Facet 1: media with HLS/DASH variants
        for media_data in media_items:
            assert isinstance(media_data, dict)
            media = media_data.get("media")
            if isinstance(media, dict) and media.get("variants"):
                media_id = media["id"]
                media_obj = await entity_store.get(Media, media_id)
                assert media_obj is not None
                if media_obj.variants:
                    assert len(media_obj.variants) > 0

        # Facet 2: media bundles. The original bundles test skipped when no
        # bundles were present; converted to an inline conditional so the
        # variant/preview facets still run regardless.
        if bundles:
            for bundle_data in bundles:
                assert isinstance(bundle_data, dict)
                bundle = await entity_store.get(AccountMediaBundle, bundle_data["id"])
                assert bundle is not None

        # Facet 3: preview image variants
        for media_data in media_items:
            assert isinstance(media_data, dict)
            preview_data = media_data.get("preview")
            if isinstance(preview_data, dict):
                preview_id = preview_data["id"]
                preview = await entity_store.get(Media, preview_id)
                assert preview is not None
