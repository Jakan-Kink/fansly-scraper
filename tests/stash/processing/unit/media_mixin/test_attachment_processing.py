"""Tests for batch processing of creator attachments.

This module tests the process_creator_attachment method which collects media
from attachments into batches for efficient processing.

User authorized mocking only because of over-testing of deeper methods.
"""

from unittest.mock import AsyncMock, patch

import pytest

from metadata.attachment import ContentType
from tests.fixtures.metadata.metadata_factories import (
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
)
from tests.fixtures.stash import ImageFactory, SceneFactory


class TestAttachmentProcessing:
    """Test batch processing of creator attachments."""

    @pytest.mark.asyncio
    async def test_process_attachment_with_direct_media(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Test process_creator_attachment collects media from direct attachment.

        User authorized mocking only because of over-testing of deeper methods.
        """
        # Create real Media object using factory
        media = MediaFactory.build(
            id=20789,
            mimetype="image/jpeg",
            is_downloaded=True,
            accountId=mock_account.id,
            stash_id="stash_attachment_789",
        )

        # Create real AccountMedia object using factory
        account_media = AccountMediaFactory.build(
            id=70456,
            accountId=mock_account.id,
            mediaId=media.id,
        )
        # Set up the relationship
        account_media.media = media

        # Create real Attachment object using factory
        attachment = AttachmentFactory.build(
            id=60123,
            contentId=account_media.id,
            contentType=1,  # ContentType.ACCOUNT_MEDIA
            postId=mock_item.id,
        )
        # Set up the relationship
        attachment.media = account_media

        # Mock result to return
        mock_image = ImageFactory.build(id="image_123", title=mock_item.content)
        mock_batch_result = {"images": [mock_image], "scenes": []}

        # Mock _process_batch_internal to capture what gets passed
        mock_process_batch = AsyncMock(return_value=mock_batch_result)

        with patch.object(
            respx_stash_processor,
            "_process_batch_internal",
            mock_process_batch,
        ):
            # Call the method
            result = await respx_stash_processor.process_creator_attachment(
                attachment=attachment,
                item=mock_item,
                account=mock_account,
            )

        # Verify _process_batch_internal was called once
        assert mock_process_batch.call_count == 1

        # Verify the media list passed to batch processing (positional args)
        call_args = mock_process_batch.call_args
        media_list, item, account = call_args[0]  # Unpack positional args
        assert len(media_list) == 1, f"Expected 1 media item, got {len(media_list)}"
        assert media_list[0].id == media.id
        assert media_list[0].mimetype == "image/jpeg"
        assert media_list[0].stash_id == "stash_attachment_789"

        # Verify item and account were passed correctly
        assert item == mock_item
        assert account == mock_account

        # Verify results contain the mock image
        assert "images" in result
        assert len(result["images"]) == 1
        assert result["images"][0] == mock_image

    @pytest.mark.asyncio
    async def test_process_attachment_with_bundle(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Test process_creator_attachment collects media from bundle.

        User authorized mocking only because of over-testing of deeper methods.
        """
        # Create real Media objects using factory
        media1 = MediaFactory.build(
            id=20456,
            mimetype="image/jpeg",
            is_downloaded=True,
            accountId=mock_account.id,
            stash_id="stash_bundle_456",
        )
        media2 = MediaFactory.build(
            id=20457,
            mimetype="video/mp4",
            is_downloaded=True,
            accountId=mock_account.id,
            stash_id="stash_bundle_457",
        )

        # Create real AccountMedia objects
        account_media1 = AccountMediaFactory.build(
            id=70123,
            accountId=mock_account.id,
            mediaId=media1.id,
        )
        account_media1.media = media1

        account_media2 = AccountMediaFactory.build(
            id=70124,
            accountId=mock_account.id,
            mediaId=media2.id,
        )
        account_media2.media = media2

        # Create real AccountMediaBundle object using factory
        bundle = AccountMediaBundleFactory.build(
            id=80789,
            accountId=mock_account.id,
        )
        # Set up the relationship (accountMedia is a set)
        bundle.accountMedia = {account_media1, account_media2}

        # Create real Attachment object using factory
        attachment = AttachmentFactory.build(
            id=60456,
            contentId=bundle.id,
            contentType=2,  # ContentType.ACCOUNT_MEDIA_BUNDLE
            postId=mock_item.id,
        )
        # Set up the relationship
        attachment.bundle = bundle

        # Mock results to return
        mock_image = ImageFactory.build(id="image_456", title=mock_item.content)
        mock_scene = SceneFactory.build(id="scene_457", title=mock_item.content)
        mock_batch_result = {"images": [mock_image], "scenes": [mock_scene]}

        # Mock _process_batch_internal to capture what gets passed
        mock_process_batch = AsyncMock(return_value=mock_batch_result)

        with patch.object(
            respx_stash_processor,
            "_process_batch_internal",
            mock_process_batch,
        ):
            # Call the method
            result = await respx_stash_processor.process_creator_attachment(
                attachment=attachment,
                item=mock_item,
                account=mock_account,
            )

        # Verify _process_batch_internal was called once
        assert mock_process_batch.call_count == 1

        # Verify the media list passed contains both media items (positional args)
        call_args = mock_process_batch.call_args
        media_list, item, account = call_args[0]  # Unpack positional args
        assert len(media_list) == 2, f"Expected 2 media items, got {len(media_list)}"

        # Verify both media items are in the list (order may vary due to set)
        media_ids = {m.id for m in media_list}
        assert media1.id in media_ids
        assert media2.id in media_ids

        # Verify item and account were passed correctly
        assert item == mock_item
        assert account == mock_account

        # Verify results contain both image and scene
        assert "images" in result
        assert len(result["images"]) == 1
        assert result["images"][0] == mock_image
        assert "scenes" in result
        assert len(result["scenes"]) == 1
        assert result["scenes"][0] == mock_scene

    @pytest.mark.asyncio
    async def test_process_attachment_with_aggregated_post(
        self, respx_stash_processor, mock_item, mock_account
    ):
        """Test process_creator_attachment recursively processes aggregated posts.

        User authorized mocking only because of over-testing of deeper methods.
        """
        # Create an aggregated post using factory
        agg_post = PostFactory.build(
            id=30789,
            accountId=mock_account.id,
            content="Aggregated post content",
        )

        # Create media for the aggregated post's attachment
        agg_media = MediaFactory.build(
            id=20999,
            mimetype="image/jpeg",
            is_downloaded=True,
            accountId=mock_account.id,
            stash_id="stash_agg_999",
        )

        agg_account_media = AccountMediaFactory.build(
            id=71000,
            accountId=mock_account.id,
            mediaId=agg_media.id,
        )
        agg_account_media.media = agg_media

        # Create an attachment that belongs to the aggregated post
        agg_attachment = AttachmentFactory.build(
            id=61000,
            contentId=agg_account_media.id,
            contentType=1,  # ContentType.ACCOUNT_MEDIA
            postId=agg_post.id,
        )
        agg_attachment.media = agg_account_media

        # Set up post's attachments list
        agg_post.attachments = [agg_attachment]

        # Create main attachment with aggregated post
        attachment = AttachmentFactory.build(
            id=60789,
            contentId=agg_post.id,
            contentType=ContentType.AGGREGATED_POSTS,
            postId=mock_item.id,
        )
        # Set up the relationship (is_aggregated_post property auto-computed from contentType)
        attachment.aggregated_post = agg_post

        # Mock result to return
        mock_image = ImageFactory.build(id="image_999", title=agg_post.content)
        mock_batch_result = {"images": [mock_image], "scenes": []}

        # Mock _process_batch_internal to capture what gets passed
        mock_process_batch = AsyncMock(return_value=mock_batch_result)

        with patch.object(
            respx_stash_processor,
            "_process_batch_internal",
            mock_process_batch,
        ):
            # Call the method
            result = await respx_stash_processor.process_creator_attachment(
                attachment=attachment,
                item=mock_item,
                account=mock_account,
            )

        # Verify _process_batch_internal was called once (from recursive call)
        assert mock_process_batch.call_count == 1

        # Verify the media from aggregated post's attachment was collected (positional args)
        call_args = mock_process_batch.call_args
        media_list, item, account = call_args[0]  # Unpack positional args
        assert len(media_list) == 1, f"Expected 1 media item, got {len(media_list)}"
        assert media_list[0].id == agg_media.id

        # Verify the item passed is the aggregated post, not the main item
        assert item == agg_post
        assert account == mock_account

        # Verify results contain image from aggregated post
        assert "images" in result
        assert len(result["images"]) == 1
        assert result["images"][0] == mock_image
