"""Integration tests for media processing in StashProcessing."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestMediaProcessingIntegration:
    """Integration tests for media processing in StashProcessing."""

    @pytest.mark.asyncio
    async def test_process_media_integration(
        self, stash_processor, mock_account, mock_item, mock_media, mock_image
    ):
        """Test _process_media method integration."""
        # Setup result dictionary
        result = {"images": [], "scenes": []}

        # Mock _find_stash_files_by_id to return no results first time
        stash_processor._find_stash_files_by_id = AsyncMock(return_value=[])

        # Mock _find_stash_files_by_path to return mock image
        stash_processor._find_stash_files_by_path = AsyncMock(
            return_value=[(mock_image, MagicMock())]
        )

        # Mock _update_stash_metadata
        stash_processor._update_stash_metadata = AsyncMock()

        # Call method
        await stash_processor._process_media(
            mock_media, mock_item, mock_account, result
        )

        # Verify stash lookups were attempted
        stash_processor._find_stash_files_by_id.assert_called_once()
        stash_processor._find_stash_files_by_path.assert_called_once()

        # Verify metadata update was called
        stash_processor._update_stash_metadata.assert_called_once_with(
            stash_obj=mock_image,
            item=mock_item,
            account=mock_account,
            media_id=str(mock_media.id),
        )

        # Verify results were collected
        assert len(result["images"]) == 1
        assert result["images"][0] == mock_image

    @pytest.mark.asyncio
    async def test_process_bundle_media_integration(
        self, stash_processor, mock_account, mock_item
    ):
        """Test _process_bundle_media method integration."""
        # Setup result dictionary
        result = {"images": [], "scenes": []}

        # Create a mock bundle
        bundle = MagicMock()
        bundle.id = "bundle_123"

        # Create mock account media entries
        account_media1 = MagicMock()
        account_media1.media = MagicMock(id="media_1")
        account_media1.preview = MagicMock(id="preview_1")

        account_media2 = MagicMock()
        account_media2.media = MagicMock(id="media_2")
        account_media2.preview = None

        # Add the account media to the bundle
        bundle.accountMedia = [account_media1, account_media2]

        # Add a bundle preview
        bundle.preview = MagicMock(id="bundle_preview")

        # Setup awaitable_attrs
        bundle.awaitable_attrs = MagicMock()
        bundle.awaitable_attrs.accountMedia = bundle.accountMedia

        # Mock _process_media
        stash_processor._process_media = AsyncMock()

        # Call method
        await stash_processor._process_bundle_media(
            bundle, mock_item, mock_account, result
        )

        # Verify _process_media was called for each media and preview
        assert (
            stash_processor._process_media.call_count == 4
        )  # 2 media, 1 preview, 1 bundle preview

        # Verify correct media were processed
        stash_processor._process_media.assert_any_call(
            account_media1.media, mock_item, mock_account, result
        )
        stash_processor._process_media.assert_any_call(
            account_media1.preview, mock_item, mock_account, result
        )
        stash_processor._process_media.assert_any_call(
            account_media2.media, mock_item, mock_account, result
        )
        stash_processor._process_media.assert_any_call(
            bundle.preview, mock_item, mock_account, result
        )

    @pytest.mark.asyncio
    async def test_process_creator_attachment_integration(
        self,
        stash_processor,
        mock_account,
        mock_item,
        mock_attachment,
        mock_image,
        mock_scene,
    ):
        """Test process_creator_attachment method integration."""
        # Setup mock attachment with direct media
        mock_media = MagicMock()
        mock_media.media = MagicMock(id="media_123")
        mock_media.preview = MagicMock(id="preview_123")

        mock_attachment.media = mock_media
        mock_attachment.bundle = None

        # Mock _process_media to add images/scenes to result
        async def mock_process_media(media, item, account, result):
            if media.id == "media_123":
                result["images"].append(mock_image)
            elif media.id == "preview_123":
                result["scenes"].append(mock_scene)

        stash_processor._process_media = AsyncMock(side_effect=mock_process_media)

        # Mock the database session
        mock_session = MagicMock()

        # Call method
        result = await stash_processor.process_creator_attachment(
            attachment=mock_attachment,
            item=mock_item,
            account=mock_account,
            session=mock_session,
        )

        # Verify _process_media was called for media and preview
        assert stash_processor._process_media.call_count == 2
        stash_processor._process_media.assert_any_call(
            mock_media.media, mock_item, mock_account, result
        )
        stash_processor._process_media.assert_any_call(
            mock_media.preview, mock_item, mock_account, result
        )

        # Verify results were collected
        assert len(result["images"]) == 1
        assert result["images"][0] == mock_image
        assert len(result["scenes"]) == 1
        assert result["scenes"][0] == mock_scene

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_bundle(
        self, stash_processor, mock_account, mock_item, mock_attachment
    ):
        """Test process_creator_attachment method with media bundle."""
        # Setup mock attachment with bundle
        mock_bundle = MagicMock()
        mock_bundle.id = "bundle_123"

        mock_attachment.media = None
        mock_attachment.bundle = mock_bundle

        # Mock _process_bundle_media
        stash_processor._process_bundle_media = AsyncMock()

        # Mock the database session
        mock_session = MagicMock()

        # Call method
        result = await stash_processor.process_creator_attachment(
            attachment=mock_attachment,
            item=mock_item,
            account=mock_account,
            session=mock_session,
        )

        # Verify _process_bundle_media was called
        stash_processor._process_bundle_media.assert_called_once_with(
            mock_bundle, mock_item, mock_account, result
        )

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_aggregated_post(
        self, stash_processor, mock_account, mock_item, mock_attachment, mock_post
    ):
        """Test process_creator_attachment method with aggregated post."""
        # Setup attachment with aggregated post
        mock_attachment.is_aggregated_post = True
        mock_attachment.media = None
        mock_attachment.bundle = None

        # Create another attachment for the aggregated post
        agg_attachment = MagicMock()
        agg_attachment.id = "agg_attachment_1"

        # Create the aggregated post
        agg_post = MagicMock()
        agg_post.id = "post_456"
        agg_post.attachments = [agg_attachment]
        agg_post.awaitable_attrs = MagicMock()
        agg_post.awaitable_attrs.attachments = agg_post.attachments

        mock_attachment.aggregated_post = agg_post

        # Mock the database session
        mock_session = MagicMock()

        # Setup recursive call to process_creator_attachment
        mock_sub_result = {"images": [MagicMock()], "scenes": [MagicMock()]}
        stash_processor.process_creator_attachment = AsyncMock(
            return_value=mock_sub_result
        )

        # Save the original method to avoid infinite recursion
        original_method = stash_processor.process_creator_attachment

        # Define a new method that only mocks calls for the aggregated attachment
        async def mock_recursive_call(attachment, item, account, session=None):
            if attachment.id == agg_attachment.id:
                return mock_sub_result
            else:
                # Call the original for the first attachment
                return await original_method(attachment, item, account, session)

        # Replace the method
        stash_processor.process_creator_attachment = mock_recursive_call

        # Call method
        result = await original_method(
            attachment=mock_attachment,
            item=mock_item,
            account=mock_account,
            session=mock_session,
        )

        # Verify results were collected from recursive call
        assert len(result["images"]) == 1
        assert result["images"][0] == mock_sub_result["images"][0]
        assert len(result["scenes"]) == 1
        assert result["scenes"][0] == mock_sub_result["scenes"][0]

        # Restore the original method
        stash_processor.process_creator_attachment = original_method
