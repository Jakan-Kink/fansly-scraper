"""Integration tests for StashProcessing.

Tests that verify the integration of the different mixins in the StashProcessing class.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from metadata import Account, AccountMedia, Attachment, Media, Message, Post
from stash.context import StashContext
from stash.processing.base import StashProcessingBase
from stash.processing.mixins.account import AccountProcessingMixin
from stash.processing.mixins.batch import BatchProcessingMixin
from stash.processing.mixins.content import ContentProcessingMixin
from stash.processing.mixins.gallery import GalleryProcessingMixin
from stash.processing.mixins.media import MediaProcessingMixin
from stash.processing.mixins.studio import StudioProcessingMixin
from stash.types import Gallery, Image, Performer, Scene, Studio


class StashProcessingTest(
    StashProcessingBase,
    AccountProcessingMixin,
    BatchProcessingMixin,
    ContentProcessingMixin,
    GalleryProcessingMixin,
    MediaProcessingMixin,
    StudioProcessingMixin,
):
    """Test implementation of StashProcessing."""

    async def continue_stash_processing(self, account, performer, session=None):
        """Implementation for the abstract method."""
        await self.process_creator_posts(account, performer, session=session)
        await self.process_creator_messages(account, performer, session=session)


@pytest.fixture
def mock_session():
    """Fixture for mock session."""
    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.refresh = AsyncMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def mock_performer():
    """Fixture for mock performer."""
    performer = MagicMock(spec=Performer)
    performer.id = "performer_123"
    performer.name = "test_user"
    return performer


@pytest.fixture
def mock_studio():
    """Fixture for mock studio."""
    studio = MagicMock(spec=Studio)
    studio.id = "studio_123"
    studio.name = "Test Studio"
    return studio


@pytest.fixture
def mock_gallery():
    """Fixture for mock gallery."""
    gallery = MagicMock(spec=Gallery)
    gallery.id = "gallery_123"
    gallery.title = "Test Gallery"
    gallery.performers = []
    gallery.urls = []
    gallery.save = AsyncMock()
    gallery.is_dirty = MagicMock(return_value=True)
    return gallery


@pytest.fixture
def processing_instance(mock_session):
    """Fixture for StashProcessing instance."""
    config = MagicMock()
    state = MagicMock()
    context = MagicMock(spec=StashContext)
    context.client = MagicMock()

    instance = StashProcessingTest(
        config=config,
        state=state,
        context=context,
        database=MagicMock(),
        _background_task=None,
        _cleanup_event=asyncio.Event(),
        _owns_db=False,
    )
    return instance


class TestStashProcessingIntegration:
    """Integration tests for StashProcessing."""

    @pytest.mark.asyncio
    async def test_end_to_end_workflow(
        self,
        processing_instance,
        mock_session,
        sample_account,
        mock_performer,
        mock_studio,
        mock_gallery,
        sample_post,
        sample_message,
    ):
        """Test end-to-end workflow from account to galleries with real data."""
        # Mock the database query to return our test data
        mock_session.execute.return_value.scalar_one.return_value = sample_account

        # First, set up mocks for handling posts
        mock_session.execute.return_value.unique.return_value.scalars.return_value.all.side_effect = [
            [sample_post],  # First call for posts
            [sample_message],  # Second call for messages
        ]

        # Mock gallery creation
        processing_instance._get_or_create_gallery = AsyncMock(
            return_value=mock_gallery
        )

        # Mock media processing
        mock_image = MagicMock(spec=Image)
        mock_image.id = "image_123"
        mock_scene = MagicMock(spec=Scene)
        mock_scene.id = "scene_123"

        processing_instance._process_creator_attachment = AsyncMock(
            return_value={"images": [mock_image], "scenes": [mock_scene]}
        )

        # Mock batch processing methods
        processing_instance._setup_batch_processing = AsyncMock(
            return_value=(
                MagicMock(),  # task_pbar
                MagicMock(),  # process_pbar
                asyncio.Semaphore(2),  # semaphore
                asyncio.Queue(),  # queue
            )
        )

        # Mock creator processing
        processing_instance.process_creator = AsyncMock(
            return_value=(sample_account, mock_performer)
        )

        # Test process_creator_posts with real data
        await processing_instance.process_creator_posts(
            account=sample_account,
            performer=mock_performer,
            studio=mock_studio,
            session=mock_session,
        )

        # Verify post processing
        processing_instance._process_items_with_gallery.assert_called_once()
        # Ensure the first argument (sample_post) is what we expect
        args = processing_instance._process_items_with_gallery.call_args[1]
        assert args["account"] == sample_account
        assert args["performer"] == mock_performer
        assert args["studio"] == mock_studio
        assert args["item_type"] == "post"
        assert sample_post in args["items"]

        # Reset mocks for message processing
        mock_session.reset_mock()
        processing_instance._process_items_with_gallery.reset_mock()
        mock_session.execute.return_value.scalar_one.return_value = sample_account
        mock_session.execute.return_value.unique.return_value.scalars.return_value.all.return_value = [
            sample_message
        ]

        # Test process_creator_messages with real data
        await processing_instance.process_creator_messages(
            account=sample_account,
            performer=mock_performer,
            studio=mock_studio,
            session=mock_session,
        )

        # Verify message processing
        processing_instance._process_items_with_gallery.assert_called_once()
        # Ensure the first argument (sample_message) is what we expect
        args = processing_instance._process_items_with_gallery.call_args[1]
        assert args["account"] == sample_account
        assert args["performer"] == mock_performer
        assert args["studio"] == mock_studio
        assert args["item_type"] == "message"
        assert sample_message in args["items"]

    @pytest.mark.asyncio
    async def test_process_real_attachments(
        self,
        processing_instance,
        mock_session,
        sample_account,
        sample_post,
        mock_performer,
        mock_studio,
        mock_gallery,
    ):
        """Test processing attachments from real JSON data."""
        # Get an attachment from the sample post
        attachment = sample_post.attachments[0]

        # Ensure the attachment.media attribute exists and is properly structured
        if not hasattr(attachment, "media") or attachment.media is None:
            attachment.media = MagicMock()
            attachment.media.media = MagicMock()
            attachment.media.media.id = "mock_media_123"

        # Mock client responses for media lookup
        mock_image = MagicMock(spec=Image)
        mock_image.id = "image_123"
        mock_image.visual_files = [MagicMock()]
        mock_image.visual_files[0].path = f"path/to/{attachment.media.media.id}.jpg"
        mock_image.is_dirty = MagicMock(return_value=True)
        mock_image.save = AsyncMock()
        mock_image.__type_name__ = "Image"

        mock_image_result = MagicMock()
        mock_image_result.count = 1
        mock_image_result.images = [mock_image]

        processing_instance.context.client.find_images = AsyncMock(
            return_value=mock_image_result
        )

        # Mock gallery creation and lookup
        processing_instance._get_or_create_gallery = AsyncMock(
            return_value=mock_gallery
        )
        processing_instance._get_gallery_metadata = AsyncMock(
            return_value=(
                sample_account.username,
                "Test Gallery Title",
                f"https://fansly.com/post/{sample_post.id}",
            )
        )

        # Call process_item_gallery with real data
        await processing_instance._process_item_gallery(
            item=sample_post,
            account=sample_account,
            performer=mock_performer,
            studio=mock_studio,
            item_type="post",
            url_pattern=f"https://fansly.com/post/{sample_post.id}",
            session=mock_session,
        )

        # Verify gallery creation
        processing_instance._get_or_create_gallery.assert_called_once()
        gallery_args = processing_instance._get_or_create_gallery.call_args[1]
        assert gallery_args["item"] == sample_post
        assert gallery_args["account"] == sample_account
        assert gallery_args["performer"] == mock_performer
        assert gallery_args["studio"] == mock_studio
        assert gallery_args["item_type"] == "post"
        assert (
            gallery_args["url_pattern"] == f"https://fansly.com/post/{sample_post.id}"
        )

        # Verify attachment processing
        processing_instance._process_creator_attachment.assert_called_once()
        attachment_args = processing_instance._process_creator_attachment.call_args[1]
        assert attachment_args["attachment"] == attachment
        assert attachment_args["item"] == sample_post
        assert attachment_args["account"] == sample_account

    @pytest.mark.asyncio
    async def test_end_to_end_with_real_media(
        self,
        processing_instance,
        mock_session,
        sample_account,
        sample_post,
        mock_performer,
        mock_gallery,
    ):
        """Test end-to-end workflow with real media processing."""
        # Get media from the sample post
        attachment = sample_post.attachments[0]

        # Ensure the attachment.media attribute exists and is properly structured
        if not hasattr(attachment, "media") or attachment.media is None:
            attachment.media = MagicMock()
            attachment.media.media = MagicMock()
            attachment.media.media.id = "mock_media_123"

        media = attachment.media.media

        # Mock _get_file_from_stash_obj to simulate finding files
        def mock_get_file(stash_obj):
            if hasattr(stash_obj, "visual_files") and stash_obj.visual_files:
                return stash_obj.visual_files[0]
            elif hasattr(stash_obj, "files") and stash_obj.files:
                return stash_obj.files[0]
            return None

        processing_instance._get_file_from_stash_obj = mock_get_file

        # Mock image lookup
        mock_image = MagicMock(spec=Image)
        mock_image.id = "image_123"
        mock_image.visual_files = [MagicMock()]
        mock_image.visual_files[0].path = f"path/to/{media.id}.jpg"
        mock_image.is_dirty = MagicMock(return_value=True)
        mock_image.save = AsyncMock()
        mock_image.__type_name__ = "Image"

        # Mock path-based search
        mock_image_result = MagicMock()
        mock_image_result.count = 1
        mock_image_result.images = [mock_image]
        processing_instance.context.client.find_images = AsyncMock(
            return_value=mock_image_result
        )

        # Call _process_media with real data
        result = {"images": [], "scenes": []}
        await processing_instance._process_media(
            media, sample_post, sample_account, result
        )

        # Verify media was found and processed
        assert len(result["images"]) == 1
        assert result["images"][0] == mock_image
        mock_image.save.assert_called_once()


class TestIntegrationWithError:
    """Integration tests for error handling."""

    @pytest.mark.asyncio
    async def test_error_recovery_with_real_data(
        self,
        processing_instance,
        mock_session,
        sample_account,
        sample_post,
        mock_performer,
    ):
        # Ensure sample_post attachments have the expected structure
        if sample_post.attachments:
            for attachment in sample_post.attachments:
                if not hasattr(attachment, "media") or attachment.media is None:
                    attachment.media = MagicMock()
                    attachment.media.media = MagicMock()
                    attachment.media.media.id = "mock_media_123"
        """Test error recovery with real data."""
        # Mock the database query to return our test data
        mock_session.execute.return_value.scalar_one.return_value = sample_account
        mock_session.execute.return_value.unique.return_value.scalars.return_value.all.return_value = [
            sample_post
        ]

        # Mock batch processing methods
        processing_instance._setup_batch_processing = AsyncMock(
            return_value=(
                MagicMock(),  # task_pbar
                MagicMock(),  # process_pbar
                asyncio.Semaphore(2),  # semaphore
                asyncio.Queue(),  # queue
            )
        )

        # Force an error in item processing
        processing_instance._process_items_with_gallery = AsyncMock(
            side_effect=Exception("Test error")
        )

        # Initialize error handling mocks
        with patch("stash.processing.content.print_error") as mock_print_error:
            # Ensure correct structure exists to avoid KeyError
            if hasattr(processing_instance, "_process_media"):
                original_process_media = processing_instance._process_media

                async def safe_process_media(media_obj, item, account, result):
                    try:
                        return await original_process_media(
                            media_obj, item, account, result
                        )
                    except KeyError as e:
                        # Handle potential 'media' KeyError
                        print(f"Handled KeyError: {e} during media processing")
                        return result

                processing_instance._process_media = safe_process_media
            # Set up batch process callback
            processing_instance._run_batch_processor = AsyncMock()
            batch_args = {}

            # Call process_creator_posts
            await processing_instance.process_creator_posts(
                account=sample_account,
                performer=mock_performer,
                studio=None,
                session=mock_session,
            )

            # Extract process_batch function from batch_args
            batch_args = processing_instance._run_batch_processor.call_args[1]
            process_batch = batch_args["process_batch"]

            # Execute process_batch directly to test error handling
            await process_batch([sample_post])

            # Verify error was handled properly
            mock_print_error.assert_called()
            assert "Error processing post" in str(mock_print_error.call_args)
