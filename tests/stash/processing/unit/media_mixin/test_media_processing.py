"""Tests for media processing methods in MediaProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestMediaProcessing:
    """Test media processing methods in MediaProcessingMixin."""

    @pytest.mark.asyncio
    async def test_process_media(self, mixin, mock_item, mock_account, mock_media):
        """Test _process_media method."""
        # Setup test harness to avoid awaiting AsyncMock
        found_results = []

        # Create a coroutine that will be resolved to an empty set
        async def mock_variants_coro():
            return set()

        # Create a coroutine that will be resolved to the mimetype
        async def mock_mimetype_coro():
            return mock_media.mimetype

        # Create a coroutine that will be resolved to is_downloaded
        async def mock_is_downloaded_coro():
            return mock_media.is_downloaded

        # Properly set up awaitable attributes
        mock_media.awaitable_attrs.variants = mock_variants_coro()
        mock_media.awaitable_attrs.mimetype = mock_mimetype_coro()
        mock_media.awaitable_attrs.is_downloaded = mock_is_downloaded_coro()

        # Create a mock implementation
        async def mock_find_by_stash_id(stash_files):
            # Return fake results
            mock_image = MagicMock()
            mock_image_file = MagicMock()
            return [(mock_image, mock_image_file)]

        # Create a mock update_stash_metadata that records calls
        async def mock_update_metadata(
            stash_obj, item, account, media_id, is_preview=False
        ):
            found_results.append(
                {
                    "stash_obj": stash_obj,
                    "item": item,
                    "account": account,
                    "media_id": media_id,
                    "is_preview": is_preview,
                }
            )

        # Mock the relevant methods
        original_find = mixin._find_stash_files_by_id
        original_update = mixin._update_stash_metadata
        mixin._find_stash_files_by_id = mock_find_by_stash_id
        mixin._update_stash_metadata = mock_update_metadata

        # Create empty result object
        result = {"images": [], "scenes": []}

        try:
            # Call the method
            await mixin._process_media(
                media=mock_media,
                item=mock_item,
                account=mock_account,
                result=result,
            )

            # Verify update_stash_metadata was called
            assert len(found_results) == 1
            assert found_results[0]["item"] == mock_item
            assert found_results[0]["account"] == mock_account
            assert found_results[0]["media_id"] == str(mock_media.id)
        finally:
            # Restore original methods
            mixin._find_stash_files_by_id = original_find
            mixin._update_stash_metadata = original_update

    @pytest.mark.asyncio
    async def test_process_media_with_stash_id(
        self, mixin, mock_item, mock_account, mock_media
    ):
        """Test _process_media method with stash_id."""
        # Setup media with stash_id
        mock_media.stash_id = "stash_123"

        # Setup test harness to avoid awaiting AsyncMock
        found_results = []

        # Create a mock implementation
        async def mock_find_by_stash_id(stash_files):
            # Return fake results
            mock_image = MagicMock()
            mock_image_file = MagicMock()
            return [(mock_image, mock_image_file)]

        # Create a mock update_stash_metadata that records calls
        async def mock_update_metadata(
            stash_obj, item, account, media_id, is_preview=False
        ):
            found_results.append(
                {
                    "stash_obj": stash_obj,
                    "item": item,
                    "account": account,
                    "media_id": media_id,
                    "is_preview": is_preview,
                }
            )

        # Mock the relevant methods
        original_find = mixin._find_stash_files_by_id
        original_update = mixin._update_stash_metadata
        mixin._find_stash_files_by_id = mock_find_by_stash_id
        mixin._update_stash_metadata = mock_update_metadata

        # Create empty result object
        result = {"images": [], "scenes": []}

        try:
            # Call the method
            await mixin._process_media(
                media=mock_media,
                item=mock_item,
                account=mock_account,
                result=result,
            )

            # Verify update_stash_metadata was called
            assert len(found_results) == 1
            assert found_results[0]["item"] == mock_item
            assert found_results[0]["account"] == mock_account
            assert found_results[0]["media_id"] == str(mock_media.id)
        finally:
            # Restore original methods
            mixin._find_stash_files_by_id = original_find
            mixin._update_stash_metadata = original_update
            # Cleanup
            mock_media.stash_id = None

    @pytest.mark.asyncio
    async def test_process_media_with_variants(
        self, mixin, mock_item, mock_account, mock_media
    ):
        """Test _process_media method with variants."""
        # Setup media with variants
        variant1 = MagicMock()
        variant1.id = "variant_1"
        variant1.mimetype = "image/jpeg"
        variant2 = MagicMock()
        variant2.id = "variant_2"
        variant2.mimetype = "video/mp4"
        mock_media.variants = [variant1, variant2]

        # Setup test harness to avoid awaiting AsyncMock
        found_results = []

        # Create a mock implementation
        async def mock_find_by_path(media_files):
            # Return fake results
            mock_image = MagicMock()
            mock_image_file = MagicMock()
            return [(mock_image, mock_image_file)]

        # Create a mock update_stash_metadata that records calls
        async def mock_update_metadata(
            stash_obj, item, account, media_id, is_preview=False
        ):
            found_results.append(
                {
                    "stash_obj": stash_obj,
                    "item": item,
                    "account": account,
                    "media_id": media_id,
                    "is_preview": is_preview,
                }
            )

        # Mock the relevant methods
        original_find = mixin._find_stash_files_by_path
        original_update = mixin._update_stash_metadata
        mixin._find_stash_files_by_path = mock_find_by_path
        mixin._update_stash_metadata = mock_update_metadata

        # Create empty result object
        result = {"images": [], "scenes": []}

        try:
            # Call the method
            await mixin._process_media(
                media=mock_media,
                item=mock_item,
                account=mock_account,
                result=result,
            )

            # Verify update_stash_metadata was called
            assert len(found_results) == 1
            assert found_results[0]["item"] == mock_item
            assert found_results[0]["account"] == mock_account
            assert found_results[0]["media_id"] == str(mock_media.id)
        finally:
            # Restore original methods
            mixin._find_stash_files_by_path = original_find
            mixin._update_stash_metadata = original_update
            # Cleanup
            mock_media.variants = []

    @pytest.mark.asyncio
    async def test_process_bundle_media(
        self,
        mixin,
        mock_item,
        mock_account,
        mock_media_bundle,
        mock_account_media,
        mock_media,
    ):
        """Test _process_bundle_media method."""
        # Setup bundle with account media
        mock_account_media.media = mock_media
        mock_media_bundle.accountMedia = [mock_account_media]

        # Setup test harness to avoid awaiting AsyncMock
        processed_media = []

        # Create a mock process_media that records calls
        async def mock_process_media(media, item, account, result):
            processed_media.append(
                {"media": media, "item": item, "account": account, "result": result}
            )

        # Mock the relevant methods
        original_process = mixin._process_media
        mixin._process_media = mock_process_media

        # Create empty result object
        result = {"images": [], "scenes": []}

        try:
            # Call the method
            await mixin._process_bundle_media(
                bundle=mock_media_bundle,
                item=mock_item,
                account=mock_account,
                result=result,
            )

            # Verify process_media was called with correct media
            assert len(processed_media) == 1
            assert processed_media[0]["media"] == mock_media
            assert processed_media[0]["item"] == mock_item
            assert processed_media[0]["account"] == mock_account
            assert processed_media[0]["result"] == result
        finally:
            # Restore original method
            mixin._process_media = original_process
            # Cleanup
            mock_account_media.media = None
            mock_media_bundle.accountMedia = []

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_direct_media(
        self, mixin, mock_item, mock_account, mock_attachment, mock_media
    ):
        """Test process_creator_attachment method with direct media."""
        # Setup attachment with direct media
        mock_attachment.media = MagicMock()
        mock_attachment.media.media = mock_media

        # Setup test harness to avoid awaiting AsyncMock
        processed_media = []

        # Create a mock process_media that records calls
        async def mock_process_media(media, item, account, result):
            processed_media.append(
                {"media": media, "item": item, "account": account, "result": result}
            )

        # Mock the relevant methods
        original_process = mixin._process_media
        mixin._process_media = mock_process_media

        try:
            # Call the method
            result = await mixin.process_creator_attachment(
                attachment=mock_attachment,
                item=mock_item,
                account=mock_account,
            )

            # Verify process_media was called with correct media
            assert len(processed_media) == 1
            assert processed_media[0]["media"] == mock_media
            assert processed_media[0]["item"] == mock_item
            assert processed_media[0]["account"] == mock_account

            # Verify empty result was returned
            assert result["images"] == []
            assert result["scenes"] == []
        finally:
            # Restore original method
            mixin._process_media = original_process
            # Cleanup
            mock_attachment.media = None

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_bundle(
        self, mixin, mock_item, mock_account, mock_attachment, mock_media_bundle
    ):
        """Test process_creator_attachment method with media bundle."""
        # Setup attachment with bundle
        mock_attachment.bundle = mock_media_bundle
        if hasattr(mock_attachment, "awaitable_attrs"):
            mock_attachment.awaitable_attrs.bundle = mock_media_bundle

        # Setup test harness to avoid awaiting AsyncMock
        processed_bundles = []

        # Create a mock process_bundle_media that records calls
        async def mock_process_bundle(bundle, item, account, result):
            processed_bundles.append(
                {"bundle": bundle, "item": item, "account": account, "result": result}
            )

        # Mock the relevant methods
        original_process = mixin._process_bundle_media
        mixin._process_bundle_media = mock_process_bundle

        try:
            # Call the method
            result = await mixin.process_creator_attachment(
                attachment=mock_attachment,
                item=mock_item,
                account=mock_account,
            )

            # Verify process_bundle_media was called with correct bundle
            assert len(processed_bundles) == 1
            assert processed_bundles[0]["bundle"] == mock_media_bundle
            assert processed_bundles[0]["item"] == mock_item
            assert processed_bundles[0]["account"] == mock_account

            # Verify empty result was returned
            assert result["images"] == []
            assert result["scenes"] == []
        finally:
            # Restore original method
            mixin._process_bundle_media = original_process
            # Cleanup
            mock_attachment.bundle = None

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_aggregated_post(
        self, mixin, mock_item, mock_account, mock_attachment
    ):
        """Test process_creator_attachment method with aggregated post."""
        # Setup attachment with aggregated post
        mock_attachment.is_aggregated_post = True
        if hasattr(mock_attachment, "awaitable_attrs"):
            mock_attachment.awaitable_attrs.is_aggregated_post = True

        # Create aggregated post with attachments
        agg_post = MagicMock()
        agg_post.id = "agg_post_123"
        agg_attachment = MagicMock()
        agg_post.attachments = [agg_attachment]
        if hasattr(agg_post, "awaitable_attrs"):
            agg_post.awaitable_attrs.attachments = agg_post.attachments

        mock_attachment.aggregated_post = agg_post
        if hasattr(mock_attachment, "awaitable_attrs"):
            mock_attachment.awaitable_attrs.aggregated_post = agg_post

        # Setup test harness to avoid awaiting AsyncMock
        processed_attachments = []

        # Create a mock recursive call that returns results
        async def mock_process_attachment(attachment, item, account, session=None):
            # Record call
            processed_attachments.append(
                {
                    "attachment": attachment,
                    "item": item,
                    "account": account,
                    "session": session,
                }
            )
            # Return a mock result
            return {"images": [MagicMock()], "scenes": [MagicMock()]}

        # Store original method
        original_method = mixin.process_creator_attachment

        try:
            # Replace with our mock for recursive call
            mixin.process_creator_attachment = mock_process_attachment

            # Call the method - use direct implementation to avoid infinite recursion
            from stash.processing.mixins.media import MediaProcessingMixin

            result = await MediaProcessingMixin.process_creator_attachment(
                mixin,
                attachment=mock_attachment,
                item=mock_item,
                account=mock_account,
            )

            # Verify attachment processing was called for aggregated post
            assert len(processed_attachments) == 1
            assert processed_attachments[0]["attachment"] == agg_attachment
            assert processed_attachments[0]["item"] == agg_post
            assert processed_attachments[0]["account"] == mock_account

            # Verify results were added to result
            assert len(result["images"]) == 1
            assert len(result["scenes"]) == 1
        finally:
            # Restore original method
            mixin.process_creator_attachment = original_method
            # Cleanup
            mock_attachment.is_aggregated_post = False
            mock_attachment.aggregated_post = None
