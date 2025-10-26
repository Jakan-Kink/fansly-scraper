"""Integration tests for post and message processing in StashProcessing."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.stash.processing.unit.media_mixin.async_mock_helper import (
    AccessibleAsyncMock,
)


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
        # Use a realistic large int for metadata mock ids
        mock_account.id = 1234567890123456789
        # Setup session mock to return posts
        mock_result = AsyncMock()
        mock_result.scalar_one = AsyncMock(return_value=mock_account)
        mock_database.session.execute = AsyncMock(return_value=mock_result)

        # Set up mock for all() that returns posts
        mock_scalars_result = AsyncMock()
        mock_scalars_result.all = AsyncMock(return_value=mock_posts)
        mock_unique_result = AsyncMock()
        mock_unique_result.scalars = MagicMock(return_value=mock_scalars_result)
        mock_result.unique = MagicMock(return_value=mock_unique_result)

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

        # Create AccessibleAsyncMock for account to prevent "coroutine has no attribute" errors
        accessible_account = AccessibleAsyncMock()
        accessible_account.id = mock_account.id
        accessible_account.username = mock_account.username
        accessible_account.__dict__.update(
            {k: v for k, v in mock_account.__dict__.items() if not k.startswith("_")}
        )

        # Call method
        await stash_processor.process_creator_posts(
            account=accessible_account,
            performer=mock_performer,
            studio=mock_studio,
            session=mock_database.session,
        )

        # Verify worker pool was used
        assert stash_processor._setup_worker_pool.call_count == 1
        assert stash_processor._run_worker_pool.call_count == 1

        # Extract process_item function from the call
        process_item = stash_processor._run_worker_pool.call_args[1]["process_item"]
        assert callable(process_item)

        # Call the process_item function with individual posts
        test_post = mock_posts[0]  # Just use one post for simplicity

        # Semaphore is returned from setup but not needed for the test

        # Call the process_item function
        await process_item(test_post)

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
        # Use a realistic large int for metadata mock ids
        mock_account.id = 1234567890123456789
        # Setup session mock to return messages
        mock_result = AsyncMock()
        mock_result.scalar_one = AsyncMock(return_value=mock_account)
        mock_database.session.execute = AsyncMock(return_value=mock_result)

        # Set up mock for all() that returns messages
        mock_scalars_result = AsyncMock()
        mock_scalars_result.all = AsyncMock(return_value=mock_messages)
        mock_unique_result = AsyncMock()
        mock_unique_result.scalars = MagicMock(return_value=mock_scalars_result)
        mock_result.unique = MagicMock(return_value=mock_unique_result)

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

        # Create AccessibleAsyncMock for account to prevent "coroutine has no attribute" errors
        accessible_account = AccessibleAsyncMock()
        accessible_account.id = mock_account.id
        accessible_account.username = mock_account.username
        accessible_account.__dict__.update(
            {k: v for k, v in mock_account.__dict__.items() if not k.startswith("_")}
        )

        # Call method
        await stash_processor.process_creator_messages(
            account=accessible_account,
            performer=mock_performer,
            studio=mock_studio,
            session=mock_database.session,
        )

        # Verify worker pool was used
        assert stash_processor._setup_worker_pool.call_count == 1
        assert stash_processor._run_worker_pool.call_count == 1

        # Extract process_item function from the call
        process_item = stash_processor._run_worker_pool.call_args[1]["process_item"]
        assert callable(process_item)

        # Call the process_item function with individual messages
        test_message = mock_messages[0]  # Just use one message for simplicity

        # Semaphore is returned from setup but not needed for the test

        # Call the process_item function
        await process_item(test_message)

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
        mock_result = AsyncMock()
        mock_result.scalar_one = AsyncMock(return_value=mock_account)
        mock_database.session.execute = AsyncMock(return_value=mock_result)

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
        mock_result = AsyncMock()
        mock_result.scalar_one = AsyncMock(return_value=mock_account)
        mock_database.session.execute = AsyncMock(return_value=mock_result)

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
