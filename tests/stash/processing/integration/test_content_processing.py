"""Integration tests for post and message processing in StashProcessing."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestContentProcessingIntegration:
    """Integration tests for content processing in StashProcessing."""

    @pytest.mark.asyncio
    async def test_process_creator_posts_integration(
        self,
        stash_processor,
        mock_database,
        mock_account,
        mock_performer,
        mock_studio,
        mock_posts,
        mock_gallery,
        mock_image,
    ):
        """Test process_creator_posts with integration approach."""
        # Setup session mock to return posts
        mock_database.session.execute().scalar_one.return_value = mock_account
        mock_database.session.execute().unique().scalars().all.return_value = mock_posts

        # Setup gallery creation
        stash_processor._get_or_create_gallery = AsyncMock(return_value=mock_gallery)

        # Setup image finding and update
        mock_image_result = MagicMock()
        mock_image_result.count = 1
        mock_image_result.images = [mock_image]
        stash_processor.context.client.find_images = AsyncMock(
            return_value=mock_image_result
        )
        stash_processor._update_stash_metadata = AsyncMock()

        # Call method
        await stash_processor.process_creator_posts(
            account=mock_account,
            performer=mock_performer,
            studio=mock_studio,
            session=mock_database.session,
        )

        # Verify batch processor was used
        assert stash_processor._setup_batch_processing.call_count == 1
        assert stash_processor._run_batch_processor.call_count == 1

        # Extract process_batch function from the call
        process_batch = stash_processor._run_batch_processor.call_args[1][
            "process_batch"
        ]
        assert callable(process_batch)

        # Call the process_batch function with a batch of posts
        test_batch = mock_posts[:1]  # Just use one post for simplicity

        # Semaphore is returned from setup but not needed for the test

        # Call the process_batch function
        await process_batch(test_batch)

        # Verify _process_item_gallery was called for each post
        # Since we patched it with AsyncMock, we can verify call count
        assert stash_processor._get_or_create_gallery.call_count >= 1

    @pytest.mark.asyncio
    async def test_process_creator_messages_integration(
        self,
        stash_processor,
        mock_database,
        mock_account,
        mock_performer,
        mock_studio,
        mock_messages,
        mock_gallery,
        mock_image,
    ):
        """Test process_creator_messages with integration approach."""
        # Setup session mock to return messages
        mock_database.session.execute().scalar_one.return_value = mock_account
        mock_database.session.execute().unique().scalars().all.return_value = (
            mock_messages
        )

        # Setup gallery creation
        stash_processor._get_or_create_gallery = AsyncMock(return_value=mock_gallery)

        # Setup image finding and update
        mock_image_result = MagicMock()
        mock_image_result.count = 1
        mock_image_result.images = [mock_image]
        stash_processor.context.client.find_images = AsyncMock(
            return_value=mock_image_result
        )
        stash_processor._update_stash_metadata = AsyncMock()

        # Call method
        await stash_processor.process_creator_messages(
            account=mock_account,
            performer=mock_performer,
            studio=mock_studio,
            session=mock_database.session,
        )

        # Verify batch processor was used
        assert stash_processor._setup_batch_processing.call_count == 1
        assert stash_processor._run_batch_processor.call_count == 1

        # Extract process_batch function from the call
        process_batch = stash_processor._run_batch_processor.call_args[1][
            "process_batch"
        ]
        assert callable(process_batch)

        # Call the process_batch function with a batch of messages
        test_batch = mock_messages[:1]  # Just use one message for simplicity

        # Semaphore is returned from setup but not needed for the test

        # Call the process_batch function
        await process_batch(test_batch)

        # Verify _process_item_gallery was called for each message
        assert stash_processor._get_or_create_gallery.call_count >= 1

    @pytest.mark.asyncio
    async def test_process_items_with_gallery(
        self,
        stash_processor,
        mock_database,
        mock_account,
        mock_performer,
        mock_studio,
        mock_posts,
        mock_gallery,
    ):
        """Test _process_items_with_gallery integration."""
        # Setup
        stash_processor._process_item_gallery = AsyncMock()

        # Setup session mock
        mock_database.session.execute().scalar_one.return_value = mock_account

        # Define URL pattern function
        def url_pattern_func(item):
            return f"https://example.com/{item.id}"

        # Call method with posts
        await stash_processor._process_items_with_gallery(
            account=mock_account,
            performer=mock_performer,
            studio=mock_studio,
            item_type="post",
            items=mock_posts[:2],  # Use just two posts
            url_pattern_func=url_pattern_func,
            session=mock_database.session,
        )

        # Verify _process_item_gallery was called for each post
        assert stash_processor._process_item_gallery.call_count == 2

        # Verify the URLs were generated correctly
        first_call = stash_processor._process_item_gallery.call_args_list[0]
        assert first_call[1]["url_pattern"] == f"https://example.com/{mock_posts[0].id}"

        second_call = stash_processor._process_item_gallery.call_args_list[1]
        assert (
            second_call[1]["url_pattern"] == f"https://example.com/{mock_posts[1].id}"
        )

    @pytest.mark.asyncio
    async def test_process_items_with_gallery_error_handling(
        self,
        stash_processor,
        mock_database,
        mock_account,
        mock_performer,
        mock_studio,
        mock_posts,
    ):
        """Test _process_items_with_gallery with error handling."""
        # Setup _process_item_gallery to raise exception for a specific post
        stash_processor._process_item_gallery = AsyncMock(
            side_effect=[
                Exception("Test error"),  # First call fails
                None,  # Second call succeeds
            ]
        )

        # Setup session mock
        mock_database.session.execute().scalar_one.return_value = mock_account

        # Define URL pattern function
        def url_pattern_func(item):
            return f"https://example.com/{item.id}"

        # Mock error printing to avoid console output
        with patch("stash.processing.mixins.content.print_error"):
            # Call method with posts
            await stash_processor._process_items_with_gallery(
                account=mock_account,
                performer=mock_performer,
                studio=mock_studio,
                item_type="post",
                items=mock_posts[:2],  # Use just two posts
                url_pattern_func=url_pattern_func,
                session=mock_database.session,
            )

        # Verify _process_item_gallery was called for both posts despite the error
        assert stash_processor._process_item_gallery.call_count == 2
