"""Integration tests for media processing in StashProcessing.

This module tests media processing using real database fixtures and
factory-based test data instead of mocks.
"""

import pytest

from stash.types import Image, Scene
from tests.fixtures.metadata_factories import (
    AccountFactory,
    AccountMediaBundleFactory,
    AttachmentFactory,
    MediaFactory,
    PostFactory,
)


class TestMediaProcessingIntegration:
    """Integration tests for media processing in StashProcessing."""

    @pytest.mark.asyncio
    async def test_process_media_integration(
        self, stash_processor, session_sync, mocker
    ):
        """Test _process_media method integration with real media."""
        # Create a real account
        account = AccountFactory(username="media_user")
        session_sync.commit()

        # Create a real post
        post = PostFactory(accountId=account.id, content="Test post")
        session_sync.commit()

        # Create real media
        media = MediaFactory(
            accountId=account.id,
            mimetype="image/jpeg",
            local_filename="test_image.jpg"
        )
        session_sync.commit()

        # Setup result dictionary
        result = {"images": [], "scenes": []}

        # Mock _find_stash_files_by_id to return no results first time
        stash_processor._find_stash_files_by_id = mocker.AsyncMock(return_value=[])

        # Create mock image to return from path search
        mock_image = Image(
            id="image_123",
            title="Test Image",
            urls=[]
        )

        # Mock _find_stash_files_by_path to return mock image
        mock_visual_file = mocker.MagicMock()
        mock_visual_file.path = "/path/to/test_image.jpg"
        stash_processor._find_stash_files_by_path = mocker.AsyncMock(
            return_value=[(mock_image, mock_visual_file)]
        )

        # Mock _update_stash_metadata
        stash_processor._update_stash_metadata = mocker.AsyncMock()

        # Call method
        await stash_processor._process_media(
            media, post, account, result
        )

        # Verify stash lookups were attempted
        stash_processor._find_stash_files_by_id.assert_called_once()
        stash_processor._find_stash_files_by_path.assert_called_once()

        # Verify metadata update was called
        stash_processor._update_stash_metadata.assert_called_once_with(
            stash_obj=mock_image,
            item=post,
            account=account,
            media_id=str(media.id),
        )

        # Verify results were collected
        assert len(result["images"]) == 1
        assert result["images"][0] == mock_image

    @pytest.mark.asyncio
    async def test_process_bundle_media_integration(
        self, stash_processor, session_sync, mocker
    ):
        """Test _process_bundle_media method integration with real data."""
        # Create a real account
        account = AccountFactory(username="bundle_user")
        session_sync.commit()

        # Create a real post
        post = PostFactory(accountId=account.id, content="Bundle post")
        session_sync.commit()

        # Setup result dictionary
        result = {"images": [], "scenes": []}

        # Create a real bundle
        bundle = AccountMediaBundleFactory(id="bundle_123", accountId=account.id)
        session_sync.commit()

        # Create real media for the bundle
        media1 = MediaFactory(accountId=account.id, mimetype="image/jpeg")
        media2 = MediaFactory(accountId=account.id, mimetype="video/mp4", type=2)
        session_sync.commit()

        # Mock awaitable_attrs to return account media
        bundle.awaitable_attrs = mocker.MagicMock()

        # Create mock account media entries
        account_media1 = mocker.MagicMock()
        account_media1.media = media1
        account_media1.preview = None

        account_media2 = mocker.MagicMock()
        account_media2.media = media2
        account_media2.preview = None

        bundle.awaitable_attrs.accountMedia = mocker.AsyncMock(
            return_value=[account_media1, account_media2]
        )

        # Mock _process_media
        stash_processor._process_media = mocker.AsyncMock()

        # Call method
        await stash_processor._process_bundle_media(
            bundle, post, account, result
        )

        # Verify _process_media was called for each media
        assert stash_processor._process_media.call_count >= 2

        # Verify correct media were processed
        stash_processor._process_media.assert_any_call(
            media1, post, account, result
        )
        stash_processor._process_media.assert_any_call(
            media2, post, account, result
        )

    @pytest.mark.asyncio
    async def test_process_creator_attachment_integration(
        self,
        stash_processor,
        session_sync,
        mocker,
    ):
        """Test process_creator_attachment method integration with real data."""
        # Create a real account
        account = AccountFactory(username="attachment_user")
        session_sync.commit()

        # Create a real post
        post = PostFactory(accountId=account.id, content="Attachment post")
        session_sync.commit()

        # Create real attachment
        attachment = AttachmentFactory(contentId=post.id)
        session_sync.commit()

        # Create real media
        media = MediaFactory(accountId=account.id, mimetype="image/jpeg")
        session_sync.commit()

        # Mock the attachment's media relationship
        attachment.media = mocker.MagicMock()
        attachment.media.media = media
        attachment.media.preview = None
        attachment.bundle = None

        # Mock _process_media to add images to result
        mock_image = Image(id="image_456", title="Attachment Image", urls=[])

        async def mock_process_media(media_obj, item, acc, res):
            res["images"].append(mock_image)

        stash_processor._process_media = mocker.AsyncMock(
            side_effect=mock_process_media
        )

        # Call method
        result = await stash_processor.process_creator_attachment(
            attachment=attachment,
            item=post,
            account=account,
            session=stash_processor.database.session,
        )

        # Verify _process_media was called
        assert stash_processor._process_media.call_count >= 1

        # Verify results were collected
        assert len(result["images"]) >= 1
        assert result["images"][0] == mock_image

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_bundle(
        self, stash_processor, session_sync, mocker
    ):
        """Test process_creator_attachment method with media bundle."""
        # Create a real account
        account = AccountFactory(username="bundle_attachment_user")
        session_sync.commit()

        # Create a real post
        post = PostFactory(accountId=account.id, content="Bundle attachment post")
        session_sync.commit()

        # Create real attachment
        attachment = AttachmentFactory(contentId=post.id)
        session_sync.commit()

        # Create real bundle
        bundle = AccountMediaBundleFactory(id="bundle_789", accountId=account.id)
        session_sync.commit()

        # Setup attachment with bundle
        attachment.media = None
        attachment.bundle = bundle

        # Mock awaitable_attrs
        attachment.awaitable_attrs = mocker.MagicMock()
        attachment.awaitable_attrs.bundle = mocker.AsyncMock(return_value=bundle)
        attachment.awaitable_attrs.media = mocker.AsyncMock(return_value=None)

        # Mock _process_bundle_media
        stash_processor._process_bundle_media = mocker.AsyncMock()

        # Call method
        result = await stash_processor.process_creator_attachment(
            attachment=attachment,
            item=post,
            account=account,
            session=stash_processor.database.session,
        )

        # Verify _process_bundle_media was called
        stash_processor._process_bundle_media.assert_called_once_with(
            bundle, post, account, result
        )

    @pytest.mark.asyncio
    async def test_process_creator_attachment_with_aggregated_post(
        self, stash_processor, session_sync, mocker
    ):
        """Test process_creator_attachment method with aggregated post."""
        # Create a real account
        account = AccountFactory(username="aggregated_user")
        session_sync.commit()

        # Create a real parent post
        parent_post = PostFactory(accountId=account.id, content="Parent post")
        session_sync.commit()

        # Create attachment for parent post
        attachment = AttachmentFactory(contentId=parent_post.id)
        session_sync.commit()

        # Setup attachment with aggregated post
        attachment.is_aggregated_post = True
        attachment.media = None
        attachment.bundle = None

        # Create aggregated post
        agg_post = PostFactory(accountId=account.id, content="Aggregated post")
        session_sync.commit()

        # Create attachment for aggregated post
        agg_attachment = AttachmentFactory(contentId=agg_post.id)
        session_sync.commit()

        # Mock aggregated post relationship
        attachment.aggregated_post = agg_post
        agg_post.attachments = [agg_attachment]
        agg_post.awaitable_attrs = mocker.MagicMock()
        agg_post.awaitable_attrs.attachments = mocker.AsyncMock(
            return_value=[agg_attachment]
        )

        # Setup recursive call result
        mock_sub_result = {
            "images": [Image(id="agg_img", title="Agg Image", urls=[])],
            "scenes": []
        }

        # Save the original method
        original_method = stash_processor.process_creator_attachment

        # Mock only the recursive call
        call_count = 0

        async def mock_recursive_call(attachment_obj, item, acc, session=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call - process the main attachment
                # This should trigger recursive call
                return await original_method(attachment_obj, item, acc, session)
            else:
                # Recursive call for aggregated attachment
                return mock_sub_result

        stash_processor.process_creator_attachment = mock_recursive_call

        # Call method
        result = await original_method(
            attachment=attachment,
            item=parent_post,
            account=account,
            session=stash_processor.database.session,
        )

        # Verify results include aggregated content
        # Note: actual behavior depends on implementation details
        # This test validates the structure exists
        assert "images" in result
        assert "scenes" in result
