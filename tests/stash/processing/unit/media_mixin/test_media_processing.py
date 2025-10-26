"""Tests for media processing methods in MediaProcessingMixin."""

from unittest.mock import MagicMock

import pytest

from tests.stash.processing.unit.media_mixin.async_mock_helper import (
    AccessibleAsyncMock,
    async_return,
)


class TestMediaProcessing:
    """Test media processing methods in MediaProcessingMixin."""

    @staticmethod
    def _convert_to_accessible_mock(mock_obj):
        """Convert a regular mock to an AccessibleAsyncMock with proper attributes."""
        if mock_obj is None:
            return None

        accessible_mock = AccessibleAsyncMock()

        # Copy all non-private attributes
        for key, value in mock_obj.__dict__.items():
            if not key.startswith("_"):
                setattr(accessible_mock, key, value)

        # Ensure it's properly awaitable
        accessible_mock.__await__ = lambda: async_return(accessible_mock)().__await__()
        return accessible_mock

    @pytest.mark.asyncio
    async def test_process_media(self, mixin, mock_item, mock_account, mock_media):
        """Test _process_media method."""
        # Setup test harness to avoid awaiting AsyncMock
        found_results = []

        # Convert mock_media to AccessibleAsyncMock instead of using awaitable_attrs
        accessible_media = AccessibleAsyncMock()
        accessible_media.id = mock_media.id
        accessible_media.mimetype = mock_media.mimetype
        accessible_media.is_downloaded = mock_media.is_downloaded
        accessible_media.variants = set()
        # Copy other attributes as needed
        accessible_media.__dict__.update(
            {
                k: v
                for k, v in mock_media.__dict__.items()
                if not k.startswith("_")
                and k not in ["id", "mimetype", "is_downloaded", "variants"]
            }
        )

        # Ensure the mock media is properly awaitable when returned from a method
        accessible_media.__await__ = lambda: async_return(
            accessible_media
        )().__await__()

        # Create a mock implementation
        async def mock_find_by_stash_id(stash_files):
            # Return fake results
            mock_image = AccessibleAsyncMock()
            mock_image_file = AccessibleAsyncMock()
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
                media=accessible_media,
                item=mock_item,
                account=mock_account,
                result=result,
            )

            # Verify update_stash_metadata was called
            assert len(found_results) == 1
            assert found_results[0]["item"] == mock_item
            assert found_results[0]["account"] == mock_account
            assert found_results[0]["media_id"] == str(accessible_media.id)
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
        accessible_media = AccessibleAsyncMock()
        accessible_media.id = mock_media.id
        accessible_media.mimetype = mock_media.mimetype
        accessible_media.is_downloaded = mock_media.is_downloaded
        accessible_media.stash_id = "stash_123"
        accessible_media.variants = set()
        # Copy other attributes as needed
        accessible_media.__dict__.update(
            {
                k: v
                for k, v in mock_media.__dict__.items()
                if not k.startswith("_")
                and k not in ["id", "mimetype", "is_downloaded", "variants", "stash_id"]
            }
        )

        # Setup test harness to avoid awaiting AsyncMock
        found_results = []

        # Create a mock implementation
        async def mock_find_by_stash_id(stash_files):
            # Return fake results
            mock_image = AccessibleAsyncMock()
            mock_image_file = AccessibleAsyncMock()
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
                media=accessible_media,
                item=mock_item,
                account=mock_account,
                result=result,
            )

            # Verify update_stash_metadata was called
            assert len(found_results) == 1
            assert found_results[0]["item"] == mock_item
            assert found_results[0]["account"] == mock_account
            assert found_results[0]["media_id"] == str(accessible_media.id)
        finally:
            # Restore original methods
            mixin._find_stash_files_by_id = original_find
            mixin._update_stash_metadata = original_update

    @pytest.mark.asyncio
    async def test_process_media_with_variants(
        self, mixin, mock_item, mock_account, mock_media
    ):
        """Test _process_media method with variants."""
        # Setup media with variants
        variant1 = AccessibleAsyncMock()
        variant1.id = "variant_1"
        variant1.mimetype = "image/jpeg"
        variant2 = AccessibleAsyncMock()
        variant2.id = "variant_2"
        variant2.mimetype = "video/mp4"
        variants = [variant1, variant2]

        accessible_media = AccessibleAsyncMock()
        accessible_media.id = mock_media.id
        accessible_media.mimetype = mock_media.mimetype
        accessible_media.is_downloaded = mock_media.is_downloaded
        accessible_media.variants = variants
        # Copy other attributes as needed
        accessible_media.__dict__.update(
            {
                k: v
                for k, v in mock_media.__dict__.items()
                if not k.startswith("_")
                and k not in ["id", "mimetype", "is_downloaded", "variants"]
            }
        )

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
                media=accessible_media,
                item=mock_item,
                account=mock_account,
                result=result,
            )

            # Verify update_stash_metadata was called
            assert len(found_results) == 1
            assert found_results[0]["item"] == mock_item
            assert found_results[0]["account"] == mock_account
            assert found_results[0]["media_id"] == str(accessible_media.id)
        finally:
            # Restore original methods
            mixin._find_stash_files_by_path = original_find
            mixin._update_stash_metadata = original_update

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
        # Create accessible versions of our mocks
        accessible_media = AccessibleAsyncMock()
        accessible_media.__dict__.update(
            {k: v for k, v in mock_media.__dict__.items() if not k.startswith("_")}
        )

        accessible_account_media = AccessibleAsyncMock()
        accessible_account_media.media = accessible_media
        accessible_account_media.__dict__.update(
            {
                k: v
                for k, v in mock_account_media.__dict__.items()
                if not k.startswith("_") and k != "media"
            }
        )

        accessible_media_bundle = AccessibleAsyncMock()
        accessible_media_bundle.accountMedia = [accessible_account_media]
        accessible_media_bundle.__dict__.update(
            {
                k: v
                for k, v in mock_media_bundle.__dict__.items()
                if not k.startswith("_") and k != "accountMedia"
            }
        )

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
                bundle=accessible_media_bundle,
                item=mock_item,
                account=mock_account,
                result=result,
            )

            # Verify process_media was called with correct media
            assert len(processed_media) == 1
            assert processed_media[0]["media"] == accessible_media
            assert processed_media[0]["item"] == mock_item
            assert processed_media[0]["account"] == mock_account
            assert processed_media[0]["result"] == result
        finally:
            # Restore original method
            mixin._process_media = original_process

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_direct_media(
        self, mixin, mock_item, mock_account, mock_attachment, mock_media
    ):
        """Test process_creator_attachment method with direct media."""
        # Create accessible versions of our mocks
        accessible_media = AccessibleAsyncMock()
        accessible_media.__dict__.update(
            {k: v for k, v in mock_media.__dict__.items() if not k.startswith("_")}
        )

        media_container = AccessibleAsyncMock()
        media_container.media = accessible_media

        accessible_attachment = AccessibleAsyncMock()
        accessible_attachment.media = media_container
        accessible_attachment.__dict__.update(
            {
                k: v
                for k, v in mock_attachment.__dict__.items()
                if not k.startswith("_") and k != "media"
            }
        )

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
                attachment=accessible_attachment,
                item=mock_item,
                account=mock_account,
            )

            # Verify process_media was called with correct media
            assert len(processed_media) == 1
            assert processed_media[0]["media"] == accessible_media
            assert processed_media[0]["item"] == mock_item
            assert processed_media[0]["account"] == mock_account

            # Verify empty result was returned
            assert result["images"] == []
            assert result["scenes"] == []
        finally:
            # Restore original method
            mixin._process_media = original_process

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_bundle(
        self, mixin, mock_item, mock_account, mock_attachment, mock_media_bundle
    ):
        """Test process_creator_attachment method with media bundle."""
        # Create accessible versions of our mocks
        accessible_media_bundle = AccessibleAsyncMock()
        accessible_media_bundle.__dict__.update(
            {
                k: v
                for k, v in mock_media_bundle.__dict__.items()
                if not k.startswith("_")
            }
        )

        accessible_attachment = AccessibleAsyncMock()
        accessible_attachment.bundle = accessible_media_bundle
        accessible_attachment.__dict__.update(
            {
                k: v
                for k, v in mock_attachment.__dict__.items()
                if not k.startswith("_") and k != "bundle"
            }
        )

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
                attachment=accessible_attachment,
                item=mock_item,
                account=mock_account,
            )

            # Verify process_bundle_media was called with correct bundle
            assert len(processed_bundles) == 1
            assert processed_bundles[0]["bundle"] == accessible_media_bundle
            assert processed_bundles[0]["item"] == mock_item
            assert processed_bundles[0]["account"] == mock_account

            # Verify empty result was returned
            assert result["images"] == []
            assert result["scenes"] == []
        finally:
            # Restore original method
            mixin._process_bundle_media = original_process

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_aggregated_post(
        self, mixin, mock_item, mock_account, mock_attachment
    ):
        """Test process_creator_attachment method with aggregated post."""
        # Create an accessible attachment with aggregated post
        accessible_attachment = AccessibleAsyncMock()
        accessible_attachment.is_aggregated_post = True

        # Create aggregated post with attachments
        agg_attachment = AccessibleAsyncMock()

        agg_post = AccessibleAsyncMock()
        agg_post.id = "agg_post_123"
        agg_post.attachments = [agg_attachment]

        accessible_attachment.aggregated_post = agg_post
        accessible_attachment.__dict__.update(
            {
                k: v
                for k, v in mock_attachment.__dict__.items()
                if not k.startswith("_")
                and k not in ["is_aggregated_post", "aggregated_post"]
            }
        )

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
                attachment=accessible_attachment,
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
