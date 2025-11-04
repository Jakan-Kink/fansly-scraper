"""Integration tests for the full StashProcessing workflow."""

from unittest.mock import AsyncMock, patch

import pytest


class TestFullWorkflowIntegration:
    """Integration tests for the full StashProcessing workflow."""

    @pytest.mark.slow
    @pytest.mark.full_workflow
    @pytest.mark.asyncio
    async def test_full_workflow(
        self,
        stash_processor,
        session,
        integration_mock_account,
        integration_mock_performer,
        integration_mock_studio,
    ):
        """Test the full workflow using continue_stash_processing."""
        # Update account to have a stash_id that matches the performer
        integration_mock_account.stash_id = integration_mock_performer.id

        # Mock the Stash API methods that will be called during processing
        stash_processor.context.client.find_studio.return_value = None
        stash_processor.context.client.create_studio.return_value = integration_mock_studio

        # Mock process_creator_studio to return the studio
        stash_processor.process_creator_studio = AsyncMock(return_value=integration_mock_studio)

        # Mock process_creator_posts and process_creator_messages
        stash_processor.process_creator_posts = AsyncMock()
        stash_processor.process_creator_messages = AsyncMock()

        # Call the new API
        await stash_processor.continue_stash_processing(
            account=integration_mock_account,
            performer=integration_mock_performer,
            session=session,
        )

        # Verify that all processing methods were called
        stash_processor.process_creator_studio.assert_called_once()
        stash_processor.process_creator_posts.assert_called_once()
        stash_processor.process_creator_messages.assert_called_once()

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_full_post_processing_flow(
        self,
        stash_processor,
        session,
        integration_mock_account,
        integration_mock_performer,
        integration_mock_studio,
        mock_posts,
        mock_gallery,
        mock_image,
    ):
        """Test the full post processing flow with gallery creation and media processing."""
        # Setup gallery creation
        stash_processor._get_or_create_gallery = AsyncMock(
            return_value=mock_gallery
        )

        # Setup process_creator_attachment to return some images and scenes
        mock_result = {"images": [mock_image], "scenes": []}
        stash_processor.process_creator_attachment = AsyncMock(
            return_value=mock_result
        )

        # Setup gallery image addition
        stash_processor.context.client.add_gallery_images = AsyncMock(
            return_value=True
        )

        # Directly call the item processing function to test the flow
        await stash_processor._process_item_gallery(
            item=mock_posts[0],
            account=integration_mock_account,
            performer=integration_mock_performer,
            studio=integration_mock_studio,
            item_type="post",
            url_pattern="https://fansly.com/post/test",
            session=session,
        )

        # Verify gallery creation was called
        stash_processor._get_or_create_gallery.assert_called_once()

        # Verify attachment processing was called
        stash_processor.process_creator_attachment.assert_called_once()

        # Verify gallery image addition was called
        stash_processor.context.client.add_gallery_images.assert_called_once_with(
            gallery_id=mock_gallery.id,
            image_ids=[mock_image.id],
        )

        # Verify gallery was saved
        mock_gallery.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_handling_full_workflow(
        self,
        stash_processor,
        session,
        integration_mock_account,
        integration_mock_performer,
        integration_mock_studio,
    ):
        """Test error handling in the full workflow."""
        # Mock process_creator_studio to raise exception
        stash_processor.process_creator_studio = AsyncMock(
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
                await stash_processor.continue_stash_processing(
                    account=integration_mock_account,
                    performer=integration_mock_performer,
                    session=session,
                )
            # Verify process_creator_studio was called
            stash_processor.process_creator_studio.assert_called_once()

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_integration_with_real_batch_processing(
        self,
        stash_processor,
        session,
        integration_mock_account,
        integration_mock_performer,
        integration_mock_studio,
        mock_posts,
    ):
        """Test integration with real worker pool batch processing (not mocked)."""
        # The stash_processor fixture already mocks _setup_worker_pool to avoid progress bars
        # We just need to test that the worker pool actually processes items

        # Mock the process_item_gallery to track calls
        stash_processor._process_item_gallery = AsyncMock()

        # Call method - this will use the real _run_worker_pool implementation
        with patch(
            "asyncio.sleep", new_callable=AsyncMock
        ):  # Mock sleep to speed up test
            await stash_processor.process_creator_posts(
                account=integration_mock_account,
                performer=integration_mock_performer,
                studio=integration_mock_studio,
                session=session,
            )

        # Verify process_item_gallery was called for each post
        assert stash_processor._process_item_gallery.call_count == len(mock_posts)
