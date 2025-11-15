"""Tests for the _process_item_gallery method."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from metadata import Account, ContentType, Post
from tests.fixtures import (
    AccountFactory,
    AccountMediaFactory,
    AttachmentFactory,
    HashtagFactory,
    MediaFactory,
    PostFactory,
)
from tests.fixtures.stash.stash_type_factories import (
    ImageFactory,
    SceneFactory,
    TagFactory,
)


class TestProcessItemGallery:
    """Test the _process_item_gallery orchestration method."""

    @pytest.mark.asyncio
    async def test_process_item_gallery_no_attachments(
        self,
        factory_async_session,
        session,
        gallery_mixin,
        gallery_mock_performer,
        gallery_mock_studio,
    ):
        """Test _process_item_gallery returns early when no attachments."""
        # Create real Account and Post with no media
        account = AccountFactory(id=12345, username="test_user")
        post = PostFactory(id=67890, accountId=12345, content="Test post")
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account_obj = result.scalar_one()

        result = await session.execute(select(Post).where(Post.id == 67890))
        post_obj = result.unique().scalar_one()

        # Post has no attachments - method should return early
        assert post_obj.attachments == []

        # Call method
        url_pattern = "https://test.com/{username}/post/{id}"
        await gallery_mixin.context.get_client()
        await gallery_mixin._process_item_gallery(
            post_obj,
            account_obj,
            gallery_mock_performer,
            gallery_mock_studio,
            "post",
            url_pattern,
            session,
        )

        # Method returns early, no API calls made - test passes if no errors

    @pytest.mark.asyncio
    async def test_process_item_gallery_orchestration(
        self,
        factory_async_session,
        session,
        gallery_mixin,
        gallery_mock_performer,
        gallery_mock_studio,
        mock_gallery,
    ):
        """Test _process_item_gallery orchestration - verifies delegate methods get called."""
        # Create REAL Account and Post with proper attachments (factories auto-persist)
        account = AccountFactory(id=12345, username="test_user")
        post = PostFactory(id=67890, accountId=12345, content="Test post #test")

        # Create REAL Media
        media1 = MediaFactory(id=1001, accountId=12345, mimetype="image/jpeg")
        media2 = MediaFactory(id=1002, accountId=12345, mimetype="video/mp4")

        # Create REAL AccountMedia
        account_media1 = AccountMediaFactory(id=2001, accountId=12345, mediaId=1001)
        account_media2 = AccountMediaFactory(id=2002, accountId=12345, mediaId=1002)

        # Create REAL Attachments linking to AccountMedia
        attachment1 = AttachmentFactory(
            id=3001,
            postId=67890,
            contentId=2001,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        attachment2 = AttachmentFactory(
            id=3002,
            postId=67890,
            contentId=2002,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=1,
        )

        # Create hashtag and associate with post
        hashtag = HashtagFactory(id=4001, value="test")
        post.hashtags = [hashtag]

        factory_async_session.commit()

        # Query fresh from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account_obj = result.scalar_one()

        result = await session.execute(select(Post).where(Post.id == 67890))
        post_obj = result.unique().scalar_one()

        # Verify post has attachments and hashtags
        assert len(post_obj.attachments) == 2
        await post_obj.awaitable_attrs.hashtags
        assert len(post_obj.hashtags) == 1

        mock_tag = TagFactory(id="123", name="test")
        mock_image = ImageFactory(id="img_1")
        mock_scene = SceneFactory(id="scene_1")

        # Mock gallery.save method (real Gallery objects have real async save methods)
        mock_gallery.save = AsyncMock()
        await gallery_mixin.context.get_client()

        # Patch delegate methods at module level, verify they're called, control return
        with (
            patch.object(
                gallery_mixin, "_collect_media_from_attachments"
            ) as mock_collect,
            patch.object(gallery_mixin, "_get_or_create_gallery") as mock_get_gallery,
            patch.object(
                gallery_mixin, "_process_hashtags_to_tags"
            ) as mock_process_tags,
            patch.object(
                gallery_mixin, "_process_media_batch_by_mimetype"
            ) as mock_process_batch,
        ):
            # Control what delegate methods return
            mock_collect.return_value = [media1, media2]
            mock_get_gallery.return_value = mock_gallery
            mock_process_tags.return_value = [mock_tag]
            mock_process_batch.side_effect = [
                {"images": [mock_image], "scenes": []},  # Image batch
                {"images": [], "scenes": [mock_scene]},  # Video batch
            ]

            # Mock ONLY external API call
            gallery_mixin.context.client.add_gallery_images = AsyncMock(
                return_value=True
            )

            # Call method with REAL Account and Post objects
            url_pattern = "https://test.com/{username}/post/{id}"
            await gallery_mixin._process_item_gallery(
                post_obj,
                account_obj,
                gallery_mock_performer,
                gallery_mock_studio,
                "post",
                url_pattern,
                session,
            )

            # Verify orchestration - delegate methods called with REAL objects
            mock_collect.assert_called_once_with(post_obj.attachments)
            mock_get_gallery.assert_called_once_with(
                item=post_obj,  # Real Post object
                account=account_obj,  # Real Account object
                performer=gallery_mock_performer,
                studio=gallery_mock_studio,
                item_type="post",
                url_pattern=url_pattern,
            )
            mock_process_tags.assert_called_once_with(post_obj.hashtags)

            # Verify external API called
            gallery_mixin.context.client.add_gallery_images.assert_called_once_with(
                gallery_id=mock_gallery.id,
                image_ids=["img_1"],
            )

            # Verify final state
            assert mock_gallery.tags == [mock_tag]
            assert mock_gallery.scenes == [mock_scene]
            mock_gallery.save.assert_called_once_with(gallery_mixin.context.client)
