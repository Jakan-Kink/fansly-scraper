"""Integration tests for the StashProcessing workflow divided into smaller components."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from metadata.attachment import ContentType


class TestAccountSetupIntegration:
    """Tests specifically for account and performer setup."""

    @pytest.mark.asyncio
    async def test_account_lookup(
        self,
        stash_processor,
        mock_database,
        integration_mock_account,
    ):
        """Test just the account lookup functionality."""
        # Setup database mock
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = AsyncMock(
            return_value=integration_mock_account
        )
        mock_database.session.execute = AsyncMock(return_value=mock_result)

        # Test account lookup
        account = await stash_processor._find_account(session=mock_database.session)

        # Verify results
        assert account == integration_mock_account
        mock_database.session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_performer_lookup(
        self,
        stash_processor,
        mock_database,
        integration_mock_account,
        integration_mock_performer,
    ):
        """Test just the performer lookup functionality."""
        # Setup client mock
        stash_processor.context.client.find_performer = AsyncMock(
            return_value=integration_mock_performer
        )

        # Test performer lookup
        performer = await stash_processor._find_existing_performer(
            integration_mock_account
        )

        # Verify results
        assert performer == integration_mock_performer
        stash_processor.context.client.find_performer.assert_called_once()

    @pytest.mark.asyncio
    async def test_studio_lookup(
        self,
        stash_processor,
        mock_database,
        integration_mock_account,
        integration_mock_studio,
    ):
        """Test just the studio lookup functionality."""
        # Setup client mock
        stash_processor.context.client.find_studio = AsyncMock(
            return_value=integration_mock_studio
        )

        # Test studio lookup
        studio = await stash_processor._find_existing_studio(integration_mock_account)

        # Verify results
        assert studio == integration_mock_studio
        stash_processor.context.client.find_studio.assert_called_once()


class TestContentProcessingComponents:
    """Tests for individual content processing components."""

    @pytest.mark.asyncio
    async def test_post_query_execution(
        self,
        stash_processor,
        mock_database,
        integration_mock_account,
        integration_mock_performer,
        integration_mock_studio,
        mock_posts,
    ):
        """Test just the post query execution part."""
        # Setup database mock for query
        mock_result = AsyncMock()
        mock_result.scalar_one = AsyncMock(return_value=integration_mock_account)
        mock_database.session.execute = AsyncMock(return_value=mock_result)

        # Set up mock for all() that returns posts
        mock_scalars_result = AsyncMock()
        mock_scalars_result.all = AsyncMock(return_value=mock_posts)
        mock_unique_result = AsyncMock()
        mock_unique_result.scalars = MagicMock(return_value=mock_scalars_result)
        mock_result.unique = MagicMock(return_value=mock_unique_result)

        # Mock the batch processing to avoid full execution
        stash_processor._setup_batch_processing = AsyncMock(
            return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        )
        stash_processor._run_batch_processor = AsyncMock()

        # Execute the test - use a patched version to avoid real execution
        with patch(
            "stash.processing.mixins.content.ContentProcessingMixin._process_items_with_gallery",
            new=AsyncMock(),
        ):
            await stash_processor.process_creator_posts(
                account=integration_mock_account,
                performer=integration_mock_performer,
                studio=integration_mock_studio,
                session=mock_database.session,
            )

        # Verify the query was executed correctly
        assert mock_database.session.execute.call_count >= 1
        # The query should include the account id
        assert str(integration_mock_account.id) in str(
            mock_database.session.execute.call_args_list[0]
        )

    @pytest.mark.asyncio
    async def test_message_query_execution(
        self,
        stash_processor,
        mock_database,
        integration_mock_account,
        integration_mock_performer,
        integration_mock_studio,
        mock_messages,
    ):
        """Test just the message query execution part."""
        # Setup database mock for query
        mock_result = AsyncMock()
        mock_result.scalar_one = AsyncMock(return_value=integration_mock_account)
        mock_database.session.execute = AsyncMock(return_value=mock_result)

        # Set up mock for all() that returns messages
        mock_scalars_result = AsyncMock()
        mock_scalars_result.all = AsyncMock(return_value=mock_messages)
        mock_unique_result = AsyncMock()
        mock_unique_result.scalars = MagicMock(return_value=mock_scalars_result)
        mock_result.unique = MagicMock(return_value=mock_unique_result)

        # Mock the batch processing to avoid full execution
        stash_processor._setup_batch_processing = AsyncMock(
            return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        )
        stash_processor._run_batch_processor = AsyncMock()

        # Execute the test - use a patched version to avoid real execution
        with patch(
            "stash.processing.mixins.content.ContentProcessingMixin._process_items_with_gallery",
            new=AsyncMock(),
        ):
            await stash_processor.process_creator_messages(
                account=integration_mock_account,
                performer=integration_mock_performer,
                studio=integration_mock_studio,
                session=mock_database.session,
            )

        # Verify the query was executed correctly
        assert mock_database.session.execute.call_count >= 1
        assert "Message" in str(mock_database.session.execute.call_args_list[0])


class TestGalleryCreationComponents:
    """Test components of gallery creation process separately."""

    @pytest.mark.asyncio
    async def test_gallery_creation(
        self,
        stash_processor,
        mock_database,
        integration_mock_account,
        integration_mock_performer,
        integration_mock_studio,
        mock_posts,
        mock_gallery,
    ):
        """Test just the gallery creation functionality."""
        # Setup mock item
        mock_item = mock_posts[0]

        # Setup mocks
        stash_processor.context.client.create_gallery = AsyncMock(
            return_value=mock_gallery
        )
        stash_processor._generate_title_from_content = MagicMock(
            return_value="Test Title"
        )

        # Call gallery creation
        gallery = await stash_processor._create_new_gallery(mock_item, "Test Title")

        # Verify results
        assert gallery is not None
        stash_processor.context.client.create_gallery.assert_called_once()

    @pytest.mark.asyncio
    async def test_gallery_lookup(
        self,
        stash_processor,
        mock_database,
        integration_mock_account,
        integration_mock_performer,
        integration_mock_studio,
        mock_posts,
        mock_gallery,
    ):
        """Test just the gallery lookup functionality."""
        # Setup mock item
        mock_item = mock_posts[0]

        # Setup mocks
        stash_processor._get_gallery_by_stash_id = AsyncMock(return_value=None)
        stash_processor._get_gallery_by_code = AsyncMock(return_value=None)
        stash_processor._get_gallery_by_title = AsyncMock(return_value=None)
        stash_processor._get_gallery_by_url = AsyncMock(return_value=mock_gallery)

        # Call get_or_create_gallery with mocked has_media_content
        with patch(
            "stash.processing.mixins.gallery.GalleryProcessingMixin._has_media_content",
            new=AsyncMock(return_value=True),
        ):
            gallery = await stash_processor._get_or_create_gallery(
                mock_item,
                integration_mock_account,
                integration_mock_performer,
                integration_mock_studio,
                "post",
                lambda i: f"https://example.com/{i.id}",
            )

        # Verify lookup methods were called in the right order
        stash_processor._get_gallery_by_stash_id.assert_called_once()
        stash_processor._get_gallery_by_code.assert_called_once()
        stash_processor._get_gallery_by_title.assert_called_once()
        stash_processor._get_gallery_by_url.assert_called_once()
        assert gallery == mock_gallery


class TestMediaProcessingComponents:
    """Test components of media processing separately."""

    @pytest.mark.asyncio
    async def test_process_media(
        self,
        stash_processor,
        mock_database,
        integration_mock_account,
        mock_posts,
        mock_media,
        mock_image,
    ):
        """Test just the media processing functionality."""
        # Setup mock item
        mock_item = mock_posts[0]

        # Setup mocks
        stash_processor._find_stash_files_by_id = AsyncMock(return_value=[])
        stash_processor._find_stash_files_by_path = AsyncMock(
            return_value=[(mock_image, MagicMock())]
        )
        stash_processor._update_stash_metadata = AsyncMock()

        # Call process_media
        result = {"images": [], "scenes": []}
        await stash_processor._process_media(
            mock_media, mock_item, integration_mock_account, result
        )

        # Verify results
        assert len(result["images"]) == 1
        assert result["images"][0] == mock_image
        stash_processor._find_stash_files_by_id.assert_called_once()
        stash_processor._find_stash_files_by_path.assert_called_once()
        stash_processor._update_stash_metadata.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_attachment(
        self,
        stash_processor,
        mock_database,
        integration_mock_account,
        mock_posts,
        mock_attachment,
        mock_media,
        mock_image,
    ):
        """Test just the attachment processing functionality."""
        # Setup mock item
        mock_item = mock_posts[0]

        # Setup attachment properties directly (not via awaitable_attrs)
        mock_attachment.bundle = None
        mock_attachment.aggregated_post = None
        mock_attachment.media = mock_media
        mock_attachment.contentType = ContentType.ACCOUNT_MEDIA

        # Mock process_media to add an image to the result
        async def mock_process_media(media, item, account, result):
            result["images"].append(mock_image)

        stash_processor._process_media = AsyncMock(side_effect=mock_process_media)

        # Call process_creator_attachment
        result = await stash_processor.process_creator_attachment(
            attachment=mock_attachment,
            item=mock_item,
            account=integration_mock_account,
        )

        # Verify results
        assert len(result["images"]) == 1
        assert result["images"][0] == mock_image
        stash_processor._process_media.assert_called()


class TestBatchProcessingComponents:
    """Test batch processing components separately."""

    @pytest.mark.asyncio
    async def test_batch_setup(
        self,
        stash_processor,
        mock_posts,
    ):
        """Test just the batch processing setup."""
        # Call setup batch
        (
            task_pbar,
            process_pbar,
            semaphore,
            queue,
        ) = await stash_processor._setup_batch_processing(mock_posts, "post")

        # Verify results
        assert task_pbar is not None
        assert process_pbar is not None
        assert isinstance(semaphore, asyncio.Semaphore)
        assert isinstance(queue, asyncio.Queue)
        assert semaphore._value > 0  # Check semaphore has positive value

    @pytest.mark.asyncio
    async def test_batch_queue_processing(
        self,
        stash_processor,
        mock_posts,
    ):
        """Test just the batch queue processing."""
        # Create a simple process function to track calls
        processed_items = []

        async def process_batch(batch):
            processed_items.extend(batch)
            return True

        # Setup minimal components for testing
        task_pbar = MagicMock()
        process_pbar = MagicMock()
        semaphore = asyncio.Semaphore(2)  # Allow 2 concurrent tasks
        queue = asyncio.Queue()

        # Use only 2 items to keep test fast
        items = mock_posts[:2]

        # Call run batch processor with mocked sleep
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await stash_processor._run_batch_processor(
                items=items,
                batch_size=1,  # Process one at a time
                process_batch=process_batch,
                task_pbar=task_pbar,
                process_pbar=process_pbar,
                semaphore=semaphore,
                queue=queue,
            )

        # Verify all items were processed
        assert len(processed_items) == 2
        assert processed_items == items
        assert process_pbar.update.call_count == 2


class TestErrorHandlingComponents:
    """Test error handling components separately."""

    @pytest.mark.asyncio
    async def test_process_item_gallery_error(
        self,
        stash_processor,
        mock_database,
        integration_mock_account,
        integration_mock_performer,
        integration_mock_studio,
        mock_posts,
    ):
        """Test error handling in processing a single item."""
        # Setup mock item
        mock_item = mock_posts[0]

        # Setup _get_or_create_gallery to raise exception
        stash_processor._get_or_create_gallery = AsyncMock(
            side_effect=Exception("Test error")
        )

        # Mock the session
        mock_result = AsyncMock()
        mock_result.scalar_one = AsyncMock(return_value=integration_mock_account)
        mock_database.session.execute = AsyncMock(return_value=mock_result)

        # Mock error logging to avoid console output
        with patch("stash.processing.mixins.gallery.print_error"):
            # Call process_item_gallery
            await stash_processor._process_item_gallery(
                item=mock_item,
                account=integration_mock_account,
                performer=integration_mock_performer,
                studio=integration_mock_studio,
                item_type="post",
                url_pattern="https://example.com/test",
                session=mock_database.session,
            )

        # Verify gallery creation was attempted
        stash_processor._get_or_create_gallery.assert_called_once()
        # Test passes if no exception is raised

    @pytest.mark.asyncio
    async def test_batch_processing_error(
        self,
        stash_processor,
        mock_posts,
    ):
        """Test error handling in batch processing."""

        # Create a process function that raises exception on first item
        async def process_batch(batch):
            if batch[0] == mock_posts[0]:
                raise Exception("Test error")  # noqa: TRY002
            return True

        # Setup minimal components for testing
        task_pbar = MagicMock()
        process_pbar = MagicMock()
        semaphore = asyncio.Semaphore(2)  # Allow 2 concurrent tasks
        queue = asyncio.Queue()

        # Use only 2 items to keep test fast
        items = mock_posts[:2]

        # Mock error logging
        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("stash.processing.mixins.batch.print_error"),
        ):
            # Call run batch processor
            await stash_processor._run_batch_processor(
                items=items,
                batch_size=1,  # Process one at a time
                process_batch=process_batch,
                task_pbar=task_pbar,
                process_pbar=process_pbar,
                semaphore=semaphore,
                queue=queue,
            )

        # Verify progress bar was still updated despite error
        assert process_pbar.update.call_count == 2
