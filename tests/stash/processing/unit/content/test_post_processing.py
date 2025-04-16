"""Tests for post processing methods in ContentProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


class TestPostProcessing:
    """Test post processing methods in ContentProcessingMixin."""

    @pytest.mark.asyncio
    async def test_process_creator_posts(
        self, mixin, mock_session, mock_account, mock_performer, mock_studio, mock_posts
    ):
        """Test process_creator_posts method."""
        # Setup session mock to return posts
        mock_session.execute().scalar_one.return_value = mock_account
        mock_session.execute().unique().scalars().all.return_value = mock_posts

        # Setup batch processing
        task_pbar = MagicMock()
        process_pbar = MagicMock()
        semaphore = MagicMock()
        queue = MagicMock()

        mixin._setup_batch_processing.return_value = (
            task_pbar,
            process_pbar,
            semaphore,
            queue,
        )

        # Call method
        await mixin.process_creator_posts(
            account=mock_account,
            performer=mock_performer,
            studio=mock_studio,
            session=mock_session,
        )

        # Verify session was used
        mock_session.add.assert_called_with(mock_account)

        # Verify batch processing was setup
        mixin._setup_batch_processing.assert_called_once_with(mock_posts, "post")

        # Verify batch processor was run
        mixin._run_batch_processor.assert_called_once()

        # Extract process_batch function from the call
        process_batch = mixin._run_batch_processor.call_args[1]["process_batch"]
        assert callable(process_batch)

        # Test the process_batch function with a batch of posts
        test_batch = mock_posts[:2]

        # Make semaphore context manager work in test
        semaphore.__aenter__ = AsyncMock()
        semaphore.__aexit__ = AsyncMock()

        # Call the process_batch function
        await process_batch(test_batch)

        # Verify session operations
        assert mock_session.add.call_count >= 3  # Account + 2 posts
        mock_session.refresh.assert_called_with(mock_account)

        # Verify _process_items_with_gallery was called for each post
        assert mixin._process_items_with_gallery.call_count == 2

        # Verify first call arguments
        first_call = mixin._process_items_with_gallery.call_args_list[0]
        assert first_call[1]["account"] == mock_account
        assert first_call[1]["performer"] == mock_performer
        assert first_call[1]["studio"] == mock_studio
        assert first_call[1]["item_type"] == "post"
        assert first_call[1]["items"] == [test_batch[0]]
        assert callable(first_call[1]["url_pattern_func"])
        assert first_call[1]["session"] == mock_session

        # Test url_pattern_func
        url_pattern_func = first_call[1]["url_pattern_func"]
        assert (
            url_pattern_func(test_batch[0])
            == f"https://fansly.com/post/{test_batch[0].id}"
        )

        # Verify progress bar was updated
        assert process_pbar.update.call_count == 2

    @pytest.mark.asyncio
    async def test_process_creator_posts_error_handling(
        self, mixin, mock_session, mock_account, mock_performer, mock_studio, mock_posts
    ):
        """Test process_creator_posts method with error handling."""
        # Setup session mock to return posts
        mock_session.execute().scalar_one.return_value = mock_account
        mock_session.execute().unique().scalars().all.return_value = mock_posts

        # Setup batch processing
        task_pbar = MagicMock()
        process_pbar = MagicMock()
        semaphore = MagicMock()
        queue = MagicMock()

        mixin._setup_batch_processing.return_value = (
            task_pbar,
            process_pbar,
            semaphore,
            queue,
        )

        # Setup _process_items_with_gallery to raise exception for a specific post
        mixin._process_items_with_gallery.side_effect = [
            Exception("Test error"),  # First call fails
            None,  # Second call succeeds
        ]

        # Call method
        await mixin.process_creator_posts(
            account=mock_account,
            performer=mock_performer,
            studio=mock_studio,
            session=mock_session,
        )

        # Extract process_batch function from the call
        process_batch = mixin._run_batch_processor.call_args[1]["process_batch"]

        # Make semaphore context manager work in test
        semaphore.__aenter__ = AsyncMock()
        semaphore.__aexit__ = AsyncMock()

        # Test the process_batch function with a batch of posts
        test_batch = mock_posts[:2]

        # Call the process_batch function
        await process_batch(test_batch)

        # Verify error was handled and processing continued
        assert mixin._process_items_with_gallery.call_count == 2

        # Verify progress bar was still updated for both posts
        assert process_pbar.update.call_count == 2

    @pytest.mark.asyncio
    async def test_process_items_with_gallery(
        self, mixin, mock_session, mock_account, mock_performer, mock_studio, mock_post
    ):
        """Test _process_items_with_gallery method."""
        # Setup original method
        original_method = mixin._process_items_with_gallery

        # Override mock to call the actual method but still track calls
        async def _process_items_with_gallery_impl(*args, **kwargs):
            # Record the call
            mixin._process_items_with_gallery.mock_calls.append(call(*args, **kwargs))
            # Call the original implementation
            return await original_method(*args, **kwargs)

        # Since we need to test the actual implementation, patch _process_item_gallery
        with patch.object(
            mixin, "_process_item_gallery", AsyncMock()
        ) as mock_process_gallery:
            # Replace the mock with our implementation
            mixin._process_items_with_gallery = _process_items_with_gallery_impl

            # Define URL pattern function
            def url_pattern_func(item):
                return f"https://example.com/{item.id}"

            # Call the method
            await mixin._process_items_with_gallery(
                account=mock_account,
                performer=mock_performer,
                studio=mock_studio,
                item_type="post",
                items=[mock_post],
                url_pattern_func=url_pattern_func,
                session=mock_session,
            )

            # Verify session operations
            mock_session.execute.assert_called()
            mock_session.add.assert_called_with(mock_account)

            # Verify _process_item_gallery was called
            mock_process_gallery.assert_called_once_with(
                item=mock_post,
                account=mock_account,
                performer=mock_performer,
                studio=mock_studio,
                item_type="post",
                url_pattern=url_pattern_func(mock_post),
                session=mock_session,
            )

        # Restore the original mock
        mixin._process_items_with_gallery = AsyncMock()

    @pytest.mark.asyncio
    async def test_process_items_with_gallery_error_handling(
        self, mixin, mock_session, mock_account, mock_performer, mock_studio, mock_posts
    ):
        """Test _process_items_with_gallery method with error handling."""
        # Setup original method
        original_method = mixin._process_items_with_gallery

        # Override mock to call the actual method but still track calls
        async def _process_items_with_gallery_impl(*args, **kwargs):
            # Record the call
            mixin._process_items_with_gallery.mock_calls.append(call(*args, **kwargs))
            # Call the original implementation
            return await original_method(*args, **kwargs)

        # Since we need to test the actual implementation, patch _process_item_gallery
        with patch.object(
            mixin, "_process_item_gallery", AsyncMock()
        ) as mock_process_gallery:
            # Replace the mock with our implementation
            mixin._process_items_with_gallery = _process_items_with_gallery_impl

            # Setup _process_item_gallery to raise exception for a specific post
            mock_process_gallery.side_effect = [
                Exception("Test error"),  # First call fails
                None,  # Second call succeeds
            ]

            # Define URL pattern function
            def url_pattern_func(item):
                return f"https://example.com/{item.id}"

            # Call the method with multiple items
            await mixin._process_items_with_gallery(
                account=mock_account,
                performer=mock_performer,
                studio=mock_studio,
                item_type="post",
                items=mock_posts[:2],
                url_pattern_func=url_pattern_func,
                session=mock_session,
            )

            # Verify _process_item_gallery was called for both items despite the error
            assert mock_process_gallery.call_count == 2

        # Restore the original mock
        mixin._process_items_with_gallery = AsyncMock()
