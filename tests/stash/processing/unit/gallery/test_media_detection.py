"""Tests for media detection methods in GalleryProcessingMixin.

This test module uses real database fixtures and factories with spy pattern
to verify internal method orchestration while letting real code execute.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from metadata import ContentType, Post
from metadata.attachment import Attachment
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AttachmentFactory,
    PostFactory,
)


class TestMediaDetection:
    """Test media detection methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_check_aggregated_posts(
        self, factory_async_session, session, respx_stash_processor
    ):
        """Test _check_aggregated_posts orchestration method with real data."""
        # Create real Account (FK requirement)
        account = AccountFactory(id=12345, username="test_user")

        # Create real Post objects with different attachment scenarios
        # Post 1: No attachments - should return False
        post1 = PostFactory(id=77890, accountId=12345)

        # Post 2: Has TIP_GOALS attachment (not media) - should return False
        post2 = PostFactory(id=77891, accountId=12345)
        attachment_no_media = AttachmentFactory(
            id=9001,
            postId=77891,
            contentId=9101,
            contentType=ContentType.TIP_GOALS,
            pos=0,
        )

        # Post 3: Has ACCOUNT_MEDIA attachment (is media) - should return True
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

        # Test when no posts have media - use spy to verify call count
        original_has_media = respx_stash_processor._has_media_content
        call_count = 0

        async def spy_has_media(item):
            nonlocal call_count
            call_count += 1
            return await original_has_media(item)

        with patch.object(
            respx_stash_processor, "_has_media_content", wraps=spy_has_media
        ):
            result = await respx_stash_processor._check_aggregated_posts(
                [post_obj1, post_obj2]
            )

            # Verify orchestration - both posts checked (neither has media)
            assert result is False
            assert call_count == 2

        # Test when first post has media (should return early)
        call_count = 0
        with patch.object(
            respx_stash_processor, "_has_media_content", wraps=spy_has_media
        ):
            result = await respx_stash_processor._check_aggregated_posts(
                [post_obj3, post_obj1]
            )

            # Verify orchestration - early return after first True
            assert result is True
            assert call_count == 1

        # Test when second post has media
        call_count = 0
        with patch.object(
            respx_stash_processor, "_has_media_content", wraps=spy_has_media
        ):
            result = await respx_stash_processor._check_aggregated_posts(
                [post_obj1, post_obj3]
            )

            # Verify orchestration - checks both posts
            assert result is True
            assert call_count == 2

        # Test with empty list
        call_count = 0
        with patch.object(
            respx_stash_processor, "_has_media_content", wraps=spy_has_media
        ):
            result = await respx_stash_processor._check_aggregated_posts([])

            # Verify orchestration - no calls for empty list
            assert result is False
            assert call_count == 0

    @pytest.mark.asyncio
    async def test_has_media_content(
        self, factory_async_session, session, respx_stash_processor
    ):
        """Test _has_media_content method with real data."""
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
        result = await respx_stash_processor._has_media_content(post_obj)

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

        result = await respx_stash_processor._has_media_content(post_obj2)

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

        result = await respx_stash_processor._has_media_content(post_obj3)

        # Verify
        assert result is True

        # Test with aggregated posts that have media
        # Create a nested post with ACCOUNT_MEDIA
        nested_post_with_media = PostFactory(id=67895, accountId=12345)
        nested_attachment_media = AttachmentFactory(
            id=1006,
            postId=67895,
            contentId=2006,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        # Create main post with AGGREGATED_POSTS pointing to nested post
        post4 = PostFactory(id=67893, accountId=12345)
        aggregated_attachment = AttachmentFactory(
            id=1005,
            postId=67893,
            contentId=67895,  # Points to nested post
            contentType=ContentType.AGGREGATED_POSTS,
            pos=0,
        )
        await session.commit()

        # Eagerly load aggregated_post relationship to prevent lazy loading issues
        result = await session.execute(
            select(Post)
            .where(Post.id == 67893)
            .options(
                selectinload(Post.attachments).selectinload(Attachment.aggregated_post)
            )
        )
        post_obj4 = result.unique().scalar_one()

        # Use spy to verify _check_aggregated_posts is called
        original_check_agg = respx_stash_processor._check_aggregated_posts
        call_args_list = []

        async def spy_check_agg(posts):
            call_args_list.append(posts)
            return await original_check_agg(posts)

        with patch.object(
            respx_stash_processor, "_check_aggregated_posts", wraps=spy_check_agg
        ):
            result = await respx_stash_processor._has_media_content(post_obj4)

            # Verify - should find media in nested post
            assert result is True
            assert len(call_args_list) == 1
            assert len(call_args_list[0]) == 1
            assert call_args_list[0][0].id == 67895

        # Test with aggregated posts but no media
        # Create nested post with only TIP_GOALS (not media)
        nested_post_no_media = PostFactory(id=67896, accountId=12345)
        nested_attachment_no_media = AttachmentFactory(
            id=1007,
            postId=67896,
            contentId=2007,
            contentType=ContentType.TIP_GOALS,
            pos=0,
        )

        # Create main post with AGGREGATED_POSTS pointing to nested post
        post6 = PostFactory(id=67897, accountId=12345)
        aggregated_attachment2 = AttachmentFactory(
            id=1008,
            postId=67897,
            contentId=67896,  # Points to nested post without media
            contentType=ContentType.AGGREGATED_POSTS,
            pos=0,
        )
        await session.commit()

        # Eagerly load aggregated_post relationship to prevent lazy loading issues
        result = await session.execute(
            select(Post)
            .where(Post.id == 67897)
            .options(
                selectinload(Post.attachments).selectinload(Attachment.aggregated_post)
            )
        )
        post_obj6 = result.unique().scalar_one()

        call_args_list.clear()
        with patch.object(
            respx_stash_processor, "_check_aggregated_posts", wraps=spy_check_agg
        ):
            result = await respx_stash_processor._has_media_content(post_obj6)

            # Verify - should not find media in nested post
            assert result is False
            assert len(call_args_list) == 1
            assert len(call_args_list[0]) == 1
            assert call_args_list[0][0].id == 67896

        # Test with no attachments
        post5 = PostFactory(id=67894, accountId=12345)
        await session.commit()

        result = await session.execute(select(Post).where(Post.id == 67894))
        post_obj5 = result.unique().scalar_one()

        result = await respx_stash_processor._has_media_content(post_obj5)

        # Verify
        assert result is False
