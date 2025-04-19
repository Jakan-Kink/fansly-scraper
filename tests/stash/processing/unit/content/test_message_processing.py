"""Tests for message processing methods in ContentProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


class TestMessageProcessing:
    """Test message processing methods in ContentProcessingMixin."""

    @pytest.mark.asyncio
    async def test_process_creator_messages(
        self,
        mixin,
        mock_session,
        content_mock_account,
        content_mock_performer,
        content_mock_studio,
        mock_messages,
    ):
        """Test process_creator_messages method."""
        # Setup session mock to return messages
        mock_session.execute().scalar_one.return_value = content_mock_account
        mock_session.execute().unique().scalars().all.return_value = mock_messages

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
        await mixin.process_creator_messages(
            account=content_mock_account,
            performer=content_mock_performer,
            studio=content_mock_studio,
            session=mock_session,
        )

        # Verify session was used
        mock_session.add.assert_called_with(content_mock_account)

        # Verify batch processing was setup
        mixin._setup_batch_processing.assert_called_once_with(mock_messages, "message")

        # Verify batch processor was run
        mixin._run_batch_processor.assert_called_once()

        # Extract process_batch function from the call
        process_batch = mixin._run_batch_processor.call_args[1]["process_batch"]
        assert callable(process_batch)

        # Test the process_batch function with a batch of messages
        test_batch = mock_messages[:2]

        # Make semaphore context manager work in test
        semaphore.__aenter__ = AsyncMock()
        semaphore.__aexit__ = AsyncMock()

        # Call the process_batch function
        await process_batch(test_batch)

        # Verify session operations
        assert mock_session.add.call_count >= 3  # Account + 2 messages
        mock_session.refresh.assert_called_with(content_mock_account)

        # Verify _process_items_with_gallery was called for each message
        assert mixin._process_items_with_gallery.call_count == 2

        # Verify first call arguments
        first_call = mixin._process_items_with_gallery.call_args_list[0]
        assert first_call[1]["account"] == content_mock_account
        assert first_call[1]["performer"] == content_mock_performer
        assert first_call[1]["studio"] == content_mock_studio
        assert first_call[1]["item_type"] == "message"
        assert first_call[1]["items"] == [test_batch[0]]
        assert callable(first_call[1]["url_pattern_func"])
        assert first_call[1]["session"] == mock_session

        # Test url_pattern_func
        url_pattern_func = first_call[1]["url_pattern_func"]
        assert (
            url_pattern_func(test_batch[0].group)
            == f"https://fansly.com/messages/{test_batch[0].group.id}"
        )

        # Verify progress bar was updated
        assert process_pbar.update.call_count == 2

    @pytest.mark.asyncio
    async def test_process_creator_messages_error_handling(
        self,
        mixin,
        mock_session,
        content_mock_account,
        content_mock_performer,
        content_mock_studio,
        mock_messages,
    ):
        """Test process_creator_messages method with error handling."""
        # Setup session mock to return messages
        mock_session.execute().scalar_one.return_value = content_mock_account
        mock_session.execute().unique().scalars().all.return_value = mock_messages

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

        # Setup _process_items_with_gallery to raise exception for a specific message
        mixin._process_items_with_gallery.side_effect = [
            Exception("Test error"),  # First call fails
            None,  # Second call succeeds
        ]

        # Call method
        await mixin.process_creator_messages(
            account=content_mock_account,
            performer=content_mock_performer,
            studio=content_mock_studio,
            session=mock_session,
        )

        # Extract process_batch function from the call
        process_batch = mixin._run_batch_processor.call_args[1]["process_batch"]

        # Make semaphore context manager work in test
        semaphore.__aenter__ = AsyncMock()
        semaphore.__aexit__ = AsyncMock()

        # Test the process_batch function with a batch of messages
        test_batch = mock_messages[:2]

        # Call the process_batch function
        await process_batch(test_batch)

        # Verify error was handled and processing continued
        assert mixin._process_items_with_gallery.call_count == 2

        # Verify progress bar was still updated for both messages
        assert process_pbar.update.call_count == 2

    @pytest.mark.asyncio
    async def test_database_query_structure(
        self,
        mixin,
        mock_session,
        content_mock_account,
        content_mock_performer,
        content_mock_studio,
        mock_messages,
    ):
        """Test the structure of database queries used in process_creator_messages."""
        # Call method
        await mixin.process_creator_messages(
            account=content_mock_account,
            performer=content_mock_performer,
            studio=content_mock_studio,
            session=mock_session,
        )

        # Get the SQL query from the first execute call
        query_call = mock_session.execute.call_args_list[0]

        # Verify it's not None
        assert query_call is not None

        # Can't directly test query structure without ORM session, but
        # we can verify that execute was called twice:
        # 1. First to get a fresh account
        # 2. Second to get messages with attachments
        assert mock_session.execute.call_count >= 2

    @pytest.mark.asyncio
    async def test_batch_processing_setup(
        self,
        mixin,
        mock_session,
        content_mock_account,
        content_mock_performer,
        content_mock_studio,
        mock_messages,
    ):
        """Test the batch processing setup in process_creator_messages."""
        # Setup session mock to return messages
        mock_session.execute().scalar_one.return_value = content_mock_account
        mock_session.execute().unique().scalars().all.return_value = mock_messages

        # Call method
        await mixin.process_creator_messages(
            account=content_mock_account,
            performer=content_mock_performer,
            studio=content_mock_studio,
            session=mock_session,
        )

        # Verify batch processing was setup with the correct parameters
        mixin._setup_batch_processing.assert_called_once_with(mock_messages, "message")

        # Verify batch processor was run with the correct parameters
        call_args = mixin._run_batch_processor.call_args[1]
        assert call_args["items"] == mock_messages
        assert call_args["batch_size"] == 25  # Default batch size
        assert "process_batch" in call_args
        assert callable(call_args["process_batch"])

        # Extract and verify batch processing parameters
        items = call_args["items"]
        batch_size = call_args["batch_size"]
        task_pbar = call_args["task_pbar"]
        process_pbar = call_args["process_pbar"]
        semaphore = call_args["semaphore"]
        queue = call_args["queue"]

        assert items == mock_messages
        assert batch_size == 25
        assert task_pbar is not None
        assert process_pbar is not None
        assert semaphore is not None
        assert queue is not None
