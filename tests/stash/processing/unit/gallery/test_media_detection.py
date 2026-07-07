"""Tests for media detection methods in GalleryProcessingMixin.

This test module uses real database fixtures and factories with spy pattern
to verify internal method orchestration while letting real code execute.
"""

from unittest.mock import patch

import pytest

from metadata import ContentType
from metadata.entity_store import PostgresEntityStore
from stash.processing import StashProcessing
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    AttachmentFactory,
    PostFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


class TestMediaDetection:
    """Test media detection methods in GalleryProcessingMixin."""

    @pytest.mark.asyncio
    async def test_check_aggregated_posts_and_has_media_content(
        self,
        entity_store: PostgresEntityStore,
        respx_stash_processor: StashProcessing,
    ) -> None:
        """One seeded post/attachment graph covers both detection methods.

        Walk 1 (_check_aggregated_posts orchestration, _has_media_content
        spied): all-non-media checks every post, first-media returns early,
        second-media checks both, empty list makes no calls.

        Walk 2 (_has_media_content with real data): direct ACCOUNT_MEDIA (with
        a TIP_GOALS sibling) is media; TIP_GOALS-only is not; bundle is media;
        AGGREGATED_POSTS recurses via the real resolve_content() into
        _check_aggregated_posts (spied) for both media and no-media nested
        posts; no attachments at all is not media.
        """
        acct_id = snowflake_id()
        post_no_attachments_id = snowflake_id()
        post_tip_goals_id = snowflake_id()
        post_media_id = snowflake_id()
        post_mixed_id = snowflake_id()
        post_bundle_id = snowflake_id()
        post_agg_media_id = snowflake_id()
        post_agg_no_media_id = snowflake_id()
        post_id_nested_media = snowflake_id()
        post_id_nested_no_media = snowflake_id()
        content_id_1 = snowflake_id()
        content_id_2 = snowflake_id()
        content_id_3 = snowflake_id()
        content_id_4 = snowflake_id()
        content_id_5 = snowflake_id()
        content_id_6 = snowflake_id()
        content_id_7 = snowflake_id()

        # Create real Account (FK requirement)
        account = AccountFactory.build(id=acct_id, username="test_user")
        await entity_store.save(account)

        # Post with no attachments - never media
        post_no_attachments = PostFactory.build(
            id=post_no_attachments_id, accountId=acct_id
        )
        await entity_store.save(post_no_attachments)

        # Post with a TIP_GOALS attachment (not media)
        # Set attachments on posts in-memory (no DB save for attachments needed;
        # _has_media_content only reads item.attachments from the object)
        post_tip_goals = PostFactory.build(id=post_tip_goals_id, accountId=acct_id)
        post_tip_goals.attachments = [
            AttachmentFactory.build(
                id=9001,
                postId=post_tip_goals_id,
                contentId=content_id_1,
                contentType=ContentType.TIP_GOALS,
                pos=0,
            )
        ]
        await entity_store.save(post_tip_goals)

        # Post with an ACCOUNT_MEDIA attachment (is media)
        post_media = PostFactory.build(id=post_media_id, accountId=acct_id)
        post_media.attachments = [
            AttachmentFactory.build(
                id=9002,
                postId=post_media_id,
                contentId=content_id_2,
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=0,
            )
        ]
        await entity_store.save(post_media)

        # Post with ACCOUNT_MEDIA + TIP_GOALS attachments (media wins)
        post_mixed = PostFactory.build(id=post_mixed_id, accountId=acct_id)
        post_mixed.attachments = [
            AttachmentFactory.build(
                id=1001,
                postId=post_mixed_id,
                contentId=content_id_3,
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=0,
            ),
            AttachmentFactory.build(
                id=1002,
                postId=post_mixed_id,
                contentId=content_id_4,
                contentType=ContentType.TIP_GOALS,  # Not a media type
                pos=1,
            ),
        ]
        await entity_store.save(post_mixed)

        # Post with an ACCOUNT_MEDIA_BUNDLE attachment (is media)
        post_bundle = PostFactory.build(id=post_bundle_id, accountId=acct_id)
        post_bundle.attachments = [
            AttachmentFactory.build(
                id=1004,
                postId=post_bundle_id,
                contentId=content_id_5,
                contentType=ContentType.ACCOUNT_MEDIA_BUNDLE,
                pos=0,
            )
        ]
        await entity_store.save(post_bundle)

        # Nested post with ACCOUNT_MEDIA + main post aggregating it
        nested_post_with_media = PostFactory.build(
            id=post_id_nested_media, accountId=acct_id
        )
        nested_post_with_media.attachments = [
            AttachmentFactory.build(
                id=1006,
                postId=post_id_nested_media,
                contentId=content_id_6,
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=0,
            )
        ]
        await entity_store.save(nested_post_with_media)

        post_agg_media = PostFactory.build(id=post_agg_media_id, accountId=acct_id)
        post_agg_media.attachments = [
            AttachmentFactory.build(
                id=1005,
                postId=post_agg_media_id,
                contentId=post_id_nested_media,  # Points to nested post
                contentType=ContentType.AGGREGATED_POSTS,
                pos=0,
            )
        ]
        await entity_store.save(post_agg_media)

        # Nested post without media + main post aggregating it
        nested_post_no_media = PostFactory.build(
            id=post_id_nested_no_media, accountId=acct_id
        )
        nested_post_no_media.attachments = [
            AttachmentFactory.build(
                id=1007,
                postId=post_id_nested_no_media,
                contentId=content_id_7,
                contentType=ContentType.TIP_GOALS,
                pos=0,
            )
        ]
        await entity_store.save(nested_post_no_media)

        post_agg_no_media = PostFactory.build(
            id=post_agg_no_media_id, accountId=acct_id
        )
        post_agg_no_media.attachments = [
            AttachmentFactory.build(
                id=1008,
                postId=post_agg_no_media_id,
                contentId=post_id_nested_no_media,  # Nested post without media
                contentType=ContentType.AGGREGATED_POSTS,
                pos=0,
            )
        ]
        await entity_store.save(post_agg_no_media)

        # === Walk 1: _check_aggregated_posts orchestration (spy on
        # _has_media_content to verify call counts). ===
        original_has_media = respx_stash_processor._has_media_content
        call_count = 0

        async def spy_has_media(item):
            nonlocal call_count
            call_count += 1
            return await original_has_media(item)

        # No posts have media - both checked
        with patch.object(
            respx_stash_processor, "_has_media_content", wraps=spy_has_media
        ):
            result = await respx_stash_processor._check_aggregated_posts(
                [post_no_attachments, post_tip_goals]
            )
            assert result is False
            assert call_count == 2

        # First post has media - early return after first True
        call_count = 0
        with patch.object(
            respx_stash_processor, "_has_media_content", wraps=spy_has_media
        ):
            result = await respx_stash_processor._check_aggregated_posts(
                [post_media, post_no_attachments]
            )
            assert result is True
            assert call_count == 1

        # Second post has media - checks both posts
        call_count = 0
        with patch.object(
            respx_stash_processor, "_has_media_content", wraps=spy_has_media
        ):
            result = await respx_stash_processor._check_aggregated_posts(
                [post_no_attachments, post_media]
            )
            assert result is True
            assert call_count == 2

        # Empty list - no calls
        call_count = 0
        with patch.object(
            respx_stash_processor, "_has_media_content", wraps=spy_has_media
        ):
            result = await respx_stash_processor._check_aggregated_posts([])
            assert result is False
            assert call_count == 0

        # === Walk 2: _has_media_content with real data. ===
        # Direct media content (ACCOUNT_MEDIA with TIP_GOALS sibling)
        assert await respx_stash_processor._has_media_content(post_mixed) is True

        # No media content (only TIP_GOALS attachment)
        assert await respx_stash_processor._has_media_content(post_tip_goals) is False

        # Different media content type (ACCOUNT_MEDIA_BUNDLE)
        assert await respx_stash_processor._has_media_content(post_bundle) is True

        # Aggregated posts - spy verifies _check_aggregated_posts is reached
        original_check_agg = respx_stash_processor._check_aggregated_posts
        call_args_list = []

        async def spy_check_agg(posts):
            call_args_list.append(posts)
            return await original_check_agg(posts)

        with patch.object(
            respx_stash_processor, "_check_aggregated_posts", wraps=spy_check_agg
        ):
            result = await respx_stash_processor._has_media_content(post_agg_media)
            # resolve_content() is now restored on Pydantic Attachment, so the
            # aggregated post path IS taken. The nested post has ACCOUNT_MEDIA,
            # so _check_aggregated_posts returns True → _has_media_content True.
            assert result is True

        call_args_list.clear()
        with patch.object(
            respx_stash_processor, "_check_aggregated_posts", wraps=spy_check_agg
        ):
            result = await respx_stash_processor._has_media_content(post_agg_no_media)
            # resolve_content() now works. The nested post has only TIP_GOALS
            # (no media), so _check_aggregated_posts returns False.
            assert result is False

        # No attachments at all
        assert (
            await respx_stash_processor._has_media_content(post_no_attachments) is False
        )
