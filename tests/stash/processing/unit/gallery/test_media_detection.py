"""Tests for media detection methods in GalleryProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from metadata import ContentType, Post
from tests.fixtures.metadata.metadata_factories import AccountFactory, AttachmentFactory, PostFactory


class TestMediaDetection:
    """Test media detection methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_check_aggregated_posts(
        self, factory_async_session, session, gallery_mixin
    ):
        """Test _check_aggregated_posts orchestration method."""
        # Create real Account (FK requirement)
        account = AccountFactory(id=12345, username="test_user")

        # Create real Post objects with different attachment scenarios
        # Post 1: No attachments
        post1 = PostFactory(id=77890, accountId=12345)

        # Post 2: Has TIP_GOALS attachment (not media)
        post2 = PostFactory(id=77891, accountId=12345)
        attachment_no_media = AttachmentFactory(
            id=9001,
            postId=77891,
            contentId=9101,
            contentType=ContentType.TIP_GOALS,
            pos=0,
        )

        # Post 3: Has ACCOUNT_MEDIA attachment (is media)
        post3 = PostFactory(id=77892, accountId=12345)
        attachment_media = AttachmentFactory(
            id=9002,
            postId=77892,
            contentId=9102,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        factory_async_session.commit()

        # Query fresh from async session
        result = await session.execute(select(Post).where(Post.id == 77890))
        post_obj1 = result.unique().scalar_one()

        result = await session.execute(select(Post).where(Post.id == 77891))
        post_obj2 = result.unique().scalar_one()

        result = await session.execute(select(Post).where(Post.id == 77892))
        post_obj3 = result.unique().scalar_one()

        # Test when no posts have media - patch delegate method
        with patch.object(
            gallery_mixin, "_has_media_content", AsyncMock(return_value=False)
        ) as mock_has_media:
            result = await gallery_mixin._check_aggregated_posts([post_obj1, post_obj2])

            # Verify orchestration - both posts checked
            assert result is False
            assert mock_has_media.call_count == 2

        # Test when first post has media (should return early)
        with patch.object(
            gallery_mixin, "_has_media_content", AsyncMock(side_effect=[True, False])
        ) as mock_has_media:
            result = await gallery_mixin._check_aggregated_posts([post_obj3, post_obj1])

            # Verify orchestration - early return after first True
            assert result is True
            assert mock_has_media.call_count == 1

        # Test when second post has media
        with patch.object(
            gallery_mixin, "_has_media_content", AsyncMock(side_effect=[False, True])
        ) as mock_has_media:
            result = await gallery_mixin._check_aggregated_posts([post_obj1, post_obj3])

            # Verify orchestration - checks both posts
            assert result is True
            assert mock_has_media.call_count == 2

        # Test with empty list
        with patch.object(
            gallery_mixin, "_has_media_content", AsyncMock(return_value=False)
        ) as mock_has_media:
            result = await gallery_mixin._check_aggregated_posts([])

            # Verify orchestration - no calls for empty list
            assert result is False
            mock_has_media.assert_not_called()

    @pytest.mark.asyncio
    async def test_has_media_content(
        self, factory_async_session, session, gallery_mixin
    ):
        """Test _has_media_content method."""
        # Create real Post object with Attachments using real ContentType enum
        account = AccountFactory(id=12345, username="test_user")
        post = PostFactory(id=67890, accountId=12345)

        # Create real Attachments with proper ContentType enum
        attachment1 = AttachmentFactory(
            id=1001,
            postId=67890,
            contentId=2001,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        attachment2 = AttachmentFactory(
            id=1002,
            postId=67890,
            contentId=2002,
            contentType=ContentType.TIP_GOALS,  # Not a media type
            pos=1,
        )

        factory_async_session.commit()

        # Query fresh from async session
        result = await session.execute(select(Post).where(Post.id == 67890))
        post_obj = result.unique().scalar_one()

        # Test with direct media content (has ACCOUNT_MEDIA)
        result = await gallery_mixin._has_media_content(post_obj)

        # Verify
        assert result is True

        # Test with no media content (only TIP_GOALS attachment)
        # Create new post with only TIP_GOALS attachment (not a media type)
        post2 = PostFactory(id=67891, accountId=12345)
        attachment_text_only = AttachmentFactory(
            id=1003,
            postId=67891,
            contentId=2003,
            contentType=ContentType.TIP_GOALS,
            pos=0,
        )
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 67891))
        post_obj2 = result.unique().scalar_one()

        result = await gallery_mixin._has_media_content(post_obj2)

        # Verify
        assert result is False

        # Test with different media content type (ACCOUNT_MEDIA_BUNDLE)
        post3 = PostFactory(id=67892, accountId=12345)
        attachment_bundle = AttachmentFactory(
            id=1004,
            postId=67892,
            contentId=2004,
            contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
            pos=0,
        )
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 67892))
        post_obj3 = result.unique().scalar_one()

        result = await gallery_mixin._has_media_content(post_obj3)

        # Verify
        assert result is True

        # Test with aggregated posts
        post4 = PostFactory(id=67893, accountId=12345)
        aggregated_attachment = AttachmentFactory(
            id=1005,
            postId=67893,
            contentId=2005,
            contentType=ContentType.AGGREGATED_POSTS,
            pos=0,
        )
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 67893))
        post_obj4 = result.unique().scalar_one()

        # Get the actual attachment from the queried post
        actual_attachment = post_obj4.attachments[0]

        # Mock resolve_content on the actual attachment instance
        mock_aggregated_post = MagicMock()
        actual_attachment.resolve_content = AsyncMock(return_value=mock_aggregated_post)

        # Mock _check_aggregated_posts to return True
        with patch.object(
            gallery_mixin, "_check_aggregated_posts", AsyncMock(return_value=True)
        ):
            result = await gallery_mixin._has_media_content(post_obj4)

            # Verify
            assert result is True
            gallery_mixin._check_aggregated_posts.assert_called_once_with(
                [mock_aggregated_post]
            )

        # Test with aggregated posts but no media
        with patch.object(
            gallery_mixin, "_check_aggregated_posts", AsyncMock(return_value=False)
        ):
            result = await gallery_mixin._has_media_content(post_obj4)

            # Verify
            assert result is False
            gallery_mixin._check_aggregated_posts.assert_called_once_with(
                [mock_aggregated_post]
            )

        # Test with no attachments
        post5 = PostFactory(id=67894, accountId=12345)
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 67894))
        post_obj5 = result.unique().scalar_one()

        result = await gallery_mixin._has_media_content(post_obj5)

        # Verify
        assert result is False
