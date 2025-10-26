"""Tests for the _process_item_gallery method."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# Note: All fixtures are automatically imported via conftest.py
# This includes mock_item, mock_account, mock_performer, mock_studio,
# mock_gallery, mock_image, and mock_scene


class TestProcessItemGallery:
    """Test the _process_item_gallery method."""

    @pytest.mark.asyncio
    async def test_process_item_gallery(
        self,
        mixin,
        mock_item,
        mock_account,
        mock_performer,
        mock_studio,
        mock_gallery,
        mock_image,
        mock_scene,
    ):
        """Test _process_item_gallery method with a successful scenario."""
        # Setup mocks
        mock_session = MagicMock()

        # Mock database session context manager
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_session
        mixin.database.get_async_session.return_value = mock_context

        # Mock attachments
        attachment1 = MagicMock()
        attachment1.id = "att1"
        attachment1.contentType = "ACCOUNT_MEDIA"
        attachment1.contentId = "content1"

        attachment2 = MagicMock()
        attachment2.id = "att2"
        attachment2.contentType = "ACCOUNT_MEDIA"
        attachment2.contentId = "content2"

        mock_item.attachments = [attachment1, attachment2]
        mock_item.awaitable_attrs.attachments = AsyncMock(
            return_value=[attachment1, attachment2]
        )

        # Mock gallery creation
        mixin._get_or_create_gallery = AsyncMock(return_value=mock_gallery)

        # Mock hashtags
        hashtag1 = MagicMock()
        hashtag1.value = "test_tag"
        mock_item.hashtags = [hashtag1]
        mock_item.awaitable_attrs.hashtags = AsyncMock()

        # Mock tag processing
        mock_tag = MagicMock()
        mixin._process_hashtags_to_tags = AsyncMock(return_value=[mock_tag])

        # Mock attachment processing
        images = [MagicMock() for _ in range(2)]
        scenes = [MagicMock() for _ in range(1)]

        # Configure process_creator_attachment to return different results for each attachment
        mixin.process_creator_attachment.side_effect = [
            {"images": images[:1], "scenes": []},
            {"images": images[1:], "scenes": scenes},
        ]

        # Mock adding gallery images
        mixin.context.client.add_gallery_images = AsyncMock(return_value=True)

        # Call the method
        url_pattern = "https://test.com/{username}/post/{id}"
        await mixin._process_item_gallery(
            mock_item,
            mock_account,
            mock_performer,
            mock_studio,
            "post",
            url_pattern,
            mock_session,
        )

        # Verify gallery creation
        mixin._get_or_create_gallery.assert_called_once_with(
            item=mock_item,
            account=mock_account,
            performer=mock_performer,
            studio=mock_studio,
            item_type="post",
            url_pattern=url_pattern,
        )

        # Verify hashtag processing
        mixin._process_hashtags_to_tags.assert_called_once_with(mock_item.hashtags)

        # Verify gallery tags were set
        assert mock_gallery.tags == [mock_tag]

        # Verify attachment processing
        assert mixin.process_creator_attachment.call_count == 2
        mixin.process_creator_attachment.assert_has_calls(
            [
                call(
                    attachment=attachment1,
                    item=mock_item,
                    account=mock_account,
                    session=mock_session,
                ),
                call(
                    attachment=attachment2,
                    item=mock_item,
                    account=mock_account,
                    session=mock_session,
                ),
            ]
        )

        # Verify gallery images were added
        mixin.context.client.add_gallery_images.assert_called_once_with(
            gallery_id=mock_gallery.id,
            image_ids=[img.id for img in images],
        )

        # Verify gallery scenes were set
        assert mock_gallery.scenes == scenes

        # Verify gallery was saved
        mock_gallery.save.assert_called_once_with(mixin.context.client)

    @pytest.mark.asyncio
    async def test_process_item_gallery_no_attachments(
        self, mixin, mock_item, mock_account, mock_performer, mock_studio
    ):
        """Test _process_item_gallery method with no attachments."""
        # Setup mocks
        mock_session = MagicMock()

        # Mock database session context manager
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_session
        mixin.database.get_async_session.return_value = mock_context

        # Mock no attachments
        mock_item.attachments = []
        mock_item.awaitable_attrs.attachments = AsyncMock(return_value=[])

        # Call the method
        url_pattern = "https://test.com/{username}/post/{id}"
        await mixin._process_item_gallery(
            mock_item,
            mock_account,
            mock_performer,
            mock_studio,
            "post",
            url_pattern,
            mock_session,
        )

        # Verify no gallery creation was attempted
        mixin._get_or_create_gallery.assert_not_called()
        mixin.process_creator_attachment.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_item_gallery_no_gallery(
        self, mixin, mock_item, mock_account, mock_performer, mock_studio
    ):
        """Test _process_item_gallery method when _get_or_create_gallery returns None."""
        # Setup mocks
        mock_session = MagicMock()

        # Mock database session context manager
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_session
        mixin.database.get_async_session.return_value = mock_context

        # Mock attachments
        attachment = MagicMock()
        attachment.id = "att1"
        attachment.contentType = "ACCOUNT_MEDIA"
        attachment.contentId = "content1"

        mock_item.attachments = [attachment]
        mock_item.awaitable_attrs.attachments = AsyncMock(return_value=[attachment])

        # Mock _get_or_create_gallery to return None
        mixin._get_or_create_gallery = AsyncMock(return_value=None)

        # Call the method
        url_pattern = "https://test.com/{username}/post/{id}"
        await mixin._process_item_gallery(
            mock_item,
            mock_account,
            mock_performer,
            mock_studio,
            "post",
            url_pattern,
            mock_session,
        )

        # Verify no attachment processing was attempted
        mixin.process_creator_attachment.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_item_gallery_no_content(
        self, mixin, mock_item, mock_account, mock_performer, mock_studio, mock_gallery
    ):
        """Test _process_item_gallery method when no content is processed."""
        # Setup mocks
        mock_session = MagicMock()

        # Mock database session context manager
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_session
        mixin.database.get_async_session.return_value = mock_context

        # Mock attachments
        attachment = MagicMock()
        attachment.id = "att1"
        attachment.contentType = "ACCOUNT_MEDIA"
        attachment.contentId = "content1"

        mock_item.attachments = [attachment]
        mock_item.awaitable_attrs.attachments = AsyncMock(return_value=[attachment])

        # Mock gallery creation
        mock_gallery.id = "new"  # Newly created gallery
        mixin._get_or_create_gallery = AsyncMock(return_value=mock_gallery)

        # Mock empty attachment processing results
        mixin.process_creator_attachment.return_value = {"images": [], "scenes": []}

        # Call the method
        url_pattern = "https://test.com/{username}/post/{id}"
        await mixin._process_item_gallery(
            mock_item,
            mock_account,
            mock_performer,
            mock_studio,
            "post",
            url_pattern,
            mock_session,
        )

        # Verify gallery was destroyed since no content was processed
        mock_gallery.destroy.assert_called_once_with(mixin.context.client)
        mixin.context.client.add_gallery_images.assert_not_called()
        mock_gallery.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_item_gallery_attachment_error(
        self, mixin, mock_item, mock_account, mock_performer, mock_studio, mock_gallery
    ):
        """Test _process_item_gallery method with error in attachment processing."""
        # Setup mocks
        mock_session = MagicMock()

        # Mock database session context manager
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_session
        mixin.database.get_async_session.return_value = mock_context

        # Mock attachments
        attachment1 = MagicMock()
        attachment1.id = "att1"
        attachment1.contentType = "ACCOUNT_MEDIA"
        attachment1.contentId = "content1"

        attachment2 = MagicMock()
        attachment2.id = "att2"
        attachment2.contentType = "ACCOUNT_MEDIA"
        attachment2.contentId = "content2"

        mock_item.attachments = [attachment1, attachment2]
        mock_item.awaitable_attrs.attachments = AsyncMock(
            return_value=[attachment1, attachment2]
        )

        # Mock gallery creation
        mixin._get_or_create_gallery = AsyncMock(return_value=mock_gallery)

        # Mock first attachment processing with error, second succeeds with content
        mixin.process_creator_attachment.side_effect = [
            Exception("Test error"),
            {"images": [MagicMock()], "scenes": []},
        ]

        # Call the method
        url_pattern = "https://test.com/{username}/post/{id}"
        await mixin._process_item_gallery(
            mock_item,
            mock_account,
            mock_performer,
            mock_studio,
            "post",
            url_pattern,
            mock_session,
        )

        # Verify all attachments were attempted despite error
        assert mixin.process_creator_attachment.call_count == 2

        # Verify gallery content was still processed and saved
        mixin.context.client.add_gallery_images.assert_called_once()
        mock_gallery.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_item_gallery_add_images_error(
        self, mixin, mock_item, mock_account, mock_performer, mock_studio, mock_gallery
    ):
        """Test _process_item_gallery method with error in adding gallery images."""
        # Setup mocks
        mock_session = MagicMock()

        # Mock database session context manager
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_session
        mixin.database.get_async_session.return_value = mock_context

        # Mock attachments
        attachment = MagicMock()
        attachment.id = "att1"
        attachment.contentType = "ACCOUNT_MEDIA"
        attachment.contentId = "content1"

        mock_item.attachments = [attachment]
        mock_item.awaitable_attrs.attachments = AsyncMock(return_value=[attachment])

        # Mock gallery creation
        mixin._get_or_create_gallery = AsyncMock(return_value=mock_gallery)

        # Mock successful attachment processing
        mock_image = MagicMock()
        mock_image.id = "image_123"
        mixin.process_creator_attachment.return_value = {
            "images": [mock_image],
            "scenes": [],
        }

        # Mock add_gallery_images to fail first time, succeed on retry
        mixin.context.client.add_gallery_images = AsyncMock(
            side_effect=[Exception("Network error"), True]
        )

        # Call the method
        url_pattern = "https://test.com/{username}/post/{id}"

        # Use patch to control sleep in retry logic
        with patch.object(asyncio, "sleep", AsyncMock()) as mock_sleep:
            await mixin._process_item_gallery(
                mock_item,
                mock_account,
                mock_performer,
                mock_studio,
                "post",
                url_pattern,
                mock_session,
            )

        # Verify sleep was called (for retry backoff)
        mock_sleep.assert_called_once_with(1)  # First retry delay is 2^0=1 second

        # Verify multiple attempts were made
        assert mixin.context.client.add_gallery_images.call_count == 2

        # Verify gallery was still saved after retry success
        mock_gallery.save.assert_called_once()
