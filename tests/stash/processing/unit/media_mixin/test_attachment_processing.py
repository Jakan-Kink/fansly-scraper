"""Tests for batch processing of creator attachments.

This module tests the process_creator_attachment method which collects media
from attachments into batches for efficient processing.
"""

import pytest

from metadata.attachment import ContentType
from tests.fixtures.metadata_factories import (
    AccountMediaBundleFactory,
    AccountMediaFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
)
from tests.fixtures.stash_type_factories import ImageFactory


class TestAttachmentProcessing:
    """Test batch processing of creator attachments."""

    @pytest.mark.asyncio
    async def test_process_attachment_with_direct_media(
        self, media_mixin, mock_item, mock_account
    ):
        """Test process_creator_attachment collects media from direct attachment."""
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

        # Mock the batch processing method to capture what gets passed to it
        processed_batches = []

        async def mock_process_batch(media_list, item, account):
            processed_batches.append(
                {
                    "media_list": media_list,
                    "item": item,
                    "account": account,
                }
            )
            # Return fake results using factories
            image = ImageFactory()
            return {"images": [image], "scenes": []}

        # Mock the batch processing method
        original_process = media_mixin._process_media_batch_by_mimetype
        media_mixin._process_media_batch_by_mimetype = mock_process_batch

        try:
            # Call the method
            result = await media_mixin.process_creator_attachment(
                attachment=attachment,
                item=mock_item,
                account=mock_account,
            )

            # Verify batch processing was called with collected media
            assert len(processed_batches) == 1
            assert len(processed_batches[0]["media_list"]) == 1
            assert processed_batches[0]["media_list"][0] == media
            assert processed_batches[0]["item"] == mock_item
            assert processed_batches[0]["account"] == mock_account

            # Verify results were returned
            assert len(result["images"]) == 1
            assert len(result["scenes"]) == 0
        finally:
            # Restore original method
            media_mixin._process_media_batch_by_mimetype = original_process

    @pytest.mark.asyncio
    async def test_process_attachment_with_bundle(
        self, media_mixin, mock_item, mock_account
    ):
        """Test process_creator_attachment collects media from bundle."""
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

        # Mock the batch processing method to capture what gets passed to it
        processed_batches = []

        async def mock_process_batch(media_list, item, account):
            processed_batches.append(
                {
                    "media_list": media_list,
                    "media_ids": [m.id for m in media_list],
                    "item": item,
                    "account": account,
                }
            )
            # Return fake results
            return {"images": [ImageFactory()], "scenes": []}

        # Mock the batch processing method
        original_process = media_mixin._process_media_batch_by_mimetype
        media_mixin._process_media_batch_by_mimetype = mock_process_batch

        try:
            # Call the method
            result = await media_mixin.process_creator_attachment(
                attachment=attachment,
                item=mock_item,
                account=mock_account,
            )

            # Verify batch processing was called with collected media from bundle
            assert len(processed_batches) == 1
            assert len(processed_batches[0]["media_list"]) == 2
            assert set(processed_batches[0]["media_ids"]) == {media1.id, media2.id}
            assert processed_batches[0]["item"] == mock_item
            assert processed_batches[0]["account"] == mock_account

            # Verify results were returned
            assert len(result["images"]) == 1
        finally:
            # Restore original method
            media_mixin._process_media_batch_by_mimetype = original_process

    @pytest.mark.asyncio
    async def test_process_attachment_with_aggregated_post(
        self, media_mixin, mock_item, mock_account
    ):
        """Test process_creator_attachment recursively processes aggregated posts."""
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

        # Mock the batch processing method
        processed_batches = []

        async def mock_process_batch(media_list, item, account):
            processed_batches.append(
                {
                    "media_list": media_list,
                    "item_id": item.id,
                    "account": account,
                }
            )
            return {"images": [ImageFactory()], "scenes": []}

        original_process = media_mixin._process_media_batch_by_mimetype
        media_mixin._process_media_batch_by_mimetype = mock_process_batch

        try:
            # Call the method
            result = await media_mixin.process_creator_attachment(
                attachment=attachment,
                item=mock_item,
                account=mock_account,
            )

            # Verify batch processing was called for aggregated post's attachment
            assert len(processed_batches) == 1
            assert len(processed_batches[0]["media_list"]) == 1
            assert processed_batches[0]["media_list"][0] == agg_media
            # Verify it was called with the aggregated post as the item
            assert processed_batches[0]["item_id"] == agg_post.id

            # Verify results were returned
            assert len(result["images"]) == 1
        finally:
            # Restore original method
            media_mixin._process_media_batch_by_mimetype = original_process
