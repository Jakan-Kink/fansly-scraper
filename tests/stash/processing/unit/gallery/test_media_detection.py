"""Tests for media detection methods in GalleryProcessingMixin.

This test module uses real database fixtures and factories with spy pattern
to verify internal method orchestration while letting real code execute.
"""

from unittest.mock import patch

import pytest

from metadata import ContentType
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AttachmentFactory,
    PostFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


class TestMediaDetection:
    """Test media detection methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_check_aggregated_posts(self, entity_store, respx_stash_processor):
        """Test _check_aggregated_posts orchestration method with real data."""
        acct_id = snowflake_id()
        post_id_1 = snowflake_id()
        post_id_2 = snowflake_id()
        post_id_3 = snowflake_id()
        content_id_1 = snowflake_id()
        content_id_2 = snowflake_id()

        # Create real Account (FK requirement)
        account = AccountFactory.build(id=acct_id, username="test_user")
        await entity_store.save(account)

        # Create real Post objects with different attachment scenarios
        # Post 1: No attachments - should return False
        post1 = PostFactory.build(id=post_id_1, accountId=acct_id)
        await entity_store.save(post1)

        # Post 2: Has TIP_GOALS attachment (not media) - should return False
        post2 = PostFactory.build(id=post_id_2, accountId=acct_id)
        attachment_no_media = AttachmentFactory.build(
            id=9001,
            postId=post_id_2,
            contentId=content_id_1,
            contentType=ContentType.TIP_GOALS,
            pos=0,
        )
        # Set attachment on post in-memory (no DB save for attachment needed;
        # _has_media_content only reads item.attachments from the object)
        post2.attachments = [attachment_no_media]
        await entity_store.save(post2)

        # Post 3: Has ACCOUNT_MEDIA attachment (is media) - should return True
        post3 = PostFactory.build(id=post_id_3, accountId=acct_id)
        attachment_media = AttachmentFactory.build(
            id=9002,
            postId=post_id_3,
            contentId=content_id_2,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        # Set attachment on post in-memory
        post3.attachments = [attachment_media]
        await entity_store.save(post3)

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
            result = await respx_stash_processor._check_aggregated_posts([post1, post2])

            # Verify orchestration - both posts checked (neither has media)
            assert result is False
            assert call_count == 2

        # Test when first post has media (should return early)
        call_count = 0
        with patch.object(
            respx_stash_processor, "_has_media_content", wraps=spy_has_media
        ):
            result = await respx_stash_processor._check_aggregated_posts([post3, post1])

            # Verify orchestration - early return after first True
            assert result is True
            assert call_count == 1

        # Test when second post has media
        call_count = 0
        with patch.object(
            respx_stash_processor, "_has_media_content", wraps=spy_has_media
        ):
            result = await respx_stash_processor._check_aggregated_posts([post1, post3])

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
    async def test_has_media_content(self, entity_store, respx_stash_processor):
        """Test _has_media_content method with real data."""
        acct_id = snowflake_id()
        post_id_1 = snowflake_id()
        post_id_2 = snowflake_id()
        post_id_3 = snowflake_id()
        post_id_4 = snowflake_id()
        post_id_5 = snowflake_id()
        post_id_nested_media = snowflake_id()
        post_id_nested_no_media = snowflake_id()
        post_id_agg_no_media = snowflake_id()
        content_id_1 = snowflake_id()
        content_id_2 = snowflake_id()
        content_id_3 = snowflake_id()
        content_id_4 = snowflake_id()
        content_id_6 = snowflake_id()
        content_id_7 = snowflake_id()

        # Create real Post object with Attachments using real ContentType enum
        account = AccountFactory.build(id=acct_id, username="test_user")
        await entity_store.save(account)

        post = PostFactory.build(id=post_id_1, accountId=acct_id)

        # Create real Attachments with proper ContentType enum
        attachment1 = AttachmentFactory.build(
            id=1001,
            postId=post_id_1,
            contentId=content_id_1,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        attachment2 = AttachmentFactory.build(
            id=1002,
            postId=post_id_1,
            contentId=content_id_2,
            contentType=ContentType.TIP_GOALS,  # Not a media type
            pos=1,
        )

        # Set attachments on post in-memory (no DB save for attachments needed;
        # _has_media_content only reads item.attachments from the object)
        post.attachments = [attachment1, attachment2]
        await entity_store.save(post)

        # Test with direct media content (has ACCOUNT_MEDIA)
        result = await respx_stash_processor._has_media_content(post)

        # Verify
        assert result is True

        # Test with no media content (only TIP_GOALS attachment)
        post2 = PostFactory.build(id=post_id_2, accountId=acct_id)
        attachment_text_only = AttachmentFactory.build(
            id=1003,
            postId=post_id_2,
            contentId=content_id_3,
            contentType=ContentType.TIP_GOALS,
            pos=0,
        )
        post2.attachments = [attachment_text_only]
        await entity_store.save(post2)

        result = await respx_stash_processor._has_media_content(post2)

        # Verify
        assert result is False

        # Test with different media content type (ACCOUNT_MEDIA_BUNDLE)
        post3 = PostFactory.build(id=post_id_3, accountId=acct_id)
        attachment_bundle = AttachmentFactory.build(
            id=1004,
            postId=post_id_3,
            contentId=content_id_4,
            contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
            pos=0,
        )
        post3.attachments = [attachment_bundle]
        await entity_store.save(post3)

        result = await respx_stash_processor._has_media_content(post3)

        # Verify
        assert result is True

        # Test with aggregated posts that have media
        # Create a nested post with ACCOUNT_MEDIA
        nested_post_with_media = PostFactory.build(
            id=post_id_nested_media, accountId=acct_id
        )
        nested_attachment_media = AttachmentFactory.build(
            id=1006,
            postId=post_id_nested_media,
            contentId=content_id_6,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        nested_post_with_media.attachments = [nested_attachment_media]
        await entity_store.save(nested_post_with_media)

        # Create main post with AGGREGATED_POSTS pointing to nested post
        post4 = PostFactory.build(id=post_id_4, accountId=acct_id)
        aggregated_attachment = AttachmentFactory.build(
            id=1005,
            postId=post_id_4,
            contentId=post_id_nested_media,  # Points to nested post
            contentType=ContentType.AGGREGATED_POSTS,
            pos=0,
        )
        post4.attachments = [aggregated_attachment]
        await entity_store.save(post4)

        # Use spy to verify _check_aggregated_posts is called
        original_check_agg = respx_stash_processor._check_aggregated_posts
        call_args_list = []

        async def spy_check_agg(posts):
            call_args_list.append(posts)
            return await original_check_agg(posts)

        with patch.object(
            respx_stash_processor, "_check_aggregated_posts", wraps=spy_check_agg
        ):
            result = await respx_stash_processor._has_media_content(post4)

            # resolve_content() is now restored on Pydantic Attachment, so the
            # aggregated post path IS taken. The nested post has ACCOUNT_MEDIA,
            # so _check_aggregated_posts returns True → _has_media_content returns True.
            assert result is True

        # Test with aggregated posts but no media
        nested_post_no_media = PostFactory.build(
            id=post_id_nested_no_media, accountId=acct_id
        )
        nested_attachment_no_media = AttachmentFactory.build(
            id=1007,
            postId=post_id_nested_no_media,
            contentId=content_id_7,
            contentType=ContentType.TIP_GOALS,
            pos=0,
        )
        nested_post_no_media.attachments = [nested_attachment_no_media]
        await entity_store.save(nested_post_no_media)

        # Create main post with AGGREGATED_POSTS pointing to nested post
        post6 = PostFactory.build(id=post_id_agg_no_media, accountId=acct_id)
        aggregated_attachment2 = AttachmentFactory.build(
            id=1008,
            postId=post_id_agg_no_media,
            contentId=post_id_nested_no_media,  # Points to nested post without media
            contentType=ContentType.AGGREGATED_POSTS,
            pos=0,
        )
        post6.attachments = [aggregated_attachment2]
        await entity_store.save(post6)

        call_args_list.clear()
        with patch.object(
            respx_stash_processor, "_check_aggregated_posts", wraps=spy_check_agg
        ):
            result = await respx_stash_processor._has_media_content(post6)

            # resolve_content() now works. The nested post has only TIP_GOALS
            # (no media), so _check_aggregated_posts returns False.
            assert result is False

        # Test with no attachments
        post5 = PostFactory.build(id=post_id_5, accountId=acct_id)
        await entity_store.save(post5)

        result = await respx_stash_processor._has_media_content(post5)

        # Verify
        assert result is False
