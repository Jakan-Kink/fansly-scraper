"""Integration tests for the full StashProcessing workflow."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestFullWorkflowIntegration:
    """Integration tests for the full StashProcessing workflow."""

    @pytest.mark.slow
    @pytest.mark.full_workflow
    @pytest.mark.asyncio
    async def test_full_workflow(
        self,
        real_stash_processor,
        session,
        test_account,
        mock_performer,
        mock_studio,
        mock_gallery,
        mock_image,
    ):
        """Test the full workflow using continue_stash_processing."""
        # Update account to have a stash_id that matches the performer
        test_account.stash_id = mock_performer.id

        # Mock the Stash API methods that will be called during processing
        real_stash_processor.context.client.find_studio.return_value = None
        real_stash_processor.context.client.create_studio.return_value = (
            mock_studio
        )

        # Mock process_creator_studio to return the studio
        real_stash_processor.process_creator_studio = AsyncMock(
            return_value=mock_studio
        )

        # Set up find_gallery to return gallery
        real_stash_processor.context.client.find_gallery.return_value = mock_gallery

        # Set up find_images to return image
        mock_image_result = MagicMock()
        mock_image_result.count = 1
        mock_image_result.images = [mock_image]
        real_stash_processor.context.client.find_images.return_value = mock_image_result

        # Mock internal methods to verify they're called
        real_stash_processor._find_account = AsyncMock(return_value=test_account)
        real_stash_processor._find_existing_performer = AsyncMock(
            return_value=mock_performer
        )
        real_stash_processor._find_existing_studio = AsyncMock(
            return_value=mock_studio
        )
        real_stash_processor._process_items_with_gallery = AsyncMock()

        # Mock process_creator_posts and process_creator_messages
        real_stash_processor.process_creator_posts = AsyncMock()
        real_stash_processor.process_creator_messages = AsyncMock()

        # Call the new API
        await real_stash_processor.continue_stash_processing(
            account=test_account,
            performer=mock_performer,
            session=session,
        )

        # Verify that all processing methods were called
        real_stash_processor.process_creator_studio.assert_called_once()
        real_stash_processor.process_creator_posts.assert_called_once()
        real_stash_processor.process_creator_messages.assert_called_once()

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_full_post_processing_flow(
        self,
        real_stash_processor,
        session,
        test_account,
        mock_performer,
        mock_studio,
        test_posts,
        mock_gallery,
        mock_image,
    ):
        """Test the full post processing flow with gallery creation and media processing."""
        # Setup gallery creation
        real_stash_processor._get_or_create_gallery = AsyncMock(return_value=mock_gallery)

        # Setup process_creator_attachment to return some images and scenes
        mock_result = {"images": [mock_image], "scenes": []}
        real_stash_processor.process_creator_attachment = AsyncMock(return_value=mock_result)

        # Setup gallery image addition
        real_stash_processor.context.client.add_gallery_images = AsyncMock(return_value=True)

        # Directly call the item processing function to test the flow
        await real_stash_processor._process_item_gallery(
            item=test_posts[0],
            account=test_account,
            performer=mock_performer,
            studio=mock_studio,
            item_type="post",
            url_pattern="https://fansly.com/post/test",
            session=session,
        )

        # Verify gallery creation was called
        real_stash_processor._get_or_create_gallery.assert_called_once()

        # Verify attachment processing was called
        real_stash_processor.process_creator_attachment.assert_called_once()

        # Verify gallery image addition was called
        real_stash_processor.context.client.add_gallery_images.assert_called_once_with(
            gallery_id=mock_gallery.id,
            image_ids=[mock_image.id],
        )

        # Verify gallery was saved
        mock_gallery.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_handling_full_workflow(
        self,
        real_stash_processor,
        session,
        test_account,
        mock_performer,
        mock_studio,
    ):
        """Test error handling in the full workflow."""
        # Mock process_creator_studio to raise exception
        real_stash_processor.process_creator_studio = AsyncMock(
            side_effect=Exception("Test error in studio processing")
        )

        # Mock error printing to avoid console output
        with (
            patch("textio.textio.print_error"),
            patch("config.logging.logger.exception"),
        ):
            # Try to call continue_stash_processing which should trigger the error
            # The exception should be caught and re-raised
            with pytest.raises(Exception, match="Test error in studio processing"):
                await real_stash_processor.continue_stash_processing(
                    account=test_account,
                    performer=mock_performer,
                    session=session,
                )
            # Verify process_creator_studio was called
            real_stash_processor.process_creator_studio.assert_called_once()

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_integration_with_real_batch_processing(
        self,
        real_stash_processor,
        session,
        test_account,
        mock_performer,
        mock_studio,
        test_posts,
    ):
        """Test integration with real worker pool batch processing (not mocked)."""
        # The real_stash_processor fixture already mocks _setup_worker_pool to avoid progress bars
        # We just need to test that the worker pool actually processes items

        # Mock the process_item_gallery to track calls
        real_stash_processor._process_item_gallery = AsyncMock()

        # Call method - this will use the real _run_worker_pool implementation
        with patch(
            "asyncio.sleep", new_callable=AsyncMock
        ):  # Mock sleep to speed up test
            await real_stash_processor.process_creator_posts(
                account=test_account,
                performer=mock_performer,
                studio=mock_studio,
                session=session,
            )

        # Verify process_item_gallery was called for each post
        assert real_stash_processor._process_item_gallery.call_count == len(test_posts)
