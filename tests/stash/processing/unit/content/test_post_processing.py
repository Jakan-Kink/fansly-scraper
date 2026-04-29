"""Tests for post processing methods in ContentProcessingMixin.

These tests use entity_store for Pydantic model persistence and respx for HTTP
mocking, allowing real code execution through the entire processing pipeline.
We verify that data flows correctly from database queries to GraphQL API calls.
"""

import httpx
import pytest
import respx

from metadata import Account, ContentType
from metadata.models import get_store
from stash.processing import StashProcessing
from tests.fixtures import (
    AccountFactory,
    AttachmentFactory,
    PerformerFactory,
    PostFactory,
    StudioFactory,
)
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.utils.test_isolation import snowflake_id


class TestPostProcessing:
    """Test post processing methods in ContentProcessingMixin."""

    @pytest.mark.asyncio
    async def test_process_creator_posts(
        self,
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_posts processes posts and makes GraphQL calls."""
        store = get_store()

        acct_id = snowflake_id()
        post_ids = [snowflake_id() for _ in range(3)]
        content_ids = [snowflake_id() for _ in range(3)]

        # Create real account
        account = AccountFactory.build(id=acct_id, username="test_user")
        await store.save(account)

        # Create 3 posts with attachments (required for query to find them)
        for i in range(3):
            post = PostFactory.build(
                id=post_ids[i],
                accountId=acct_id,
                content=f"Test post {i}",
            )
            await store.save(post)

            # Create attachment for each post
            attachment = AttachmentFactory.build(
                postId=post_ids[i],
                contentId=content_ids[i],
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=0,
            )
            await store.save(attachment)

            # Link attachment to post
            post.attachments = [attachment]
            await store.save(post)

        # Refresh account from store
        account = await store.get(Account, acct_id)

        # Create real Performer and Studio using factories
        performer = PerformerFactory.build(id="5110", name="test_user")
        studio = StudioFactory.build(id="10210", name="Test Studio")

        # Set up respx to capture all GraphQL calls with generic success responses
        generic_response = httpx.Response(
            200,
            json={
                "data": {
                    # Generic empty responses - we're testing request capture
                    "findGalleries": {"galleries": [], "count": 0},
                    "galleryCreate": {"id": "20001"},
                    "findScenes": {"scenes": [], "count": 0},
                    "findImages": {"images": [], "count": 0},
                }
            },
        )
        # Allow multiple calls for this test (3 posts = multiple gallery lookups)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[generic_response] * 20  # Enough for 3 posts
        )

        # Call method - let it execute fully to HTTP boundary
        try:
            await respx_stash_processor.process_creator_posts(
                account=account,
                performer=performer,
                studio=studio,
            )
        finally:
            dump_graphql_calls(graphql_route.calls, "test_process_creator_posts")

        # Verify GraphQL calls were made. Per-operation pinning lives in the
        # integration test at tests/stash/processing/integration/test_content_processing.py
        # which uses assert_op against capture_graphql_calls; the unit-level
        # check here is intentionally loose because populate()'s filter-query
        # inlines values and omits variables, so per-call variable assertions
        # would produce brittle test text without strengthening the contract.
        assert len(graphql_route.calls) > 0, "Expected GraphQL calls to be made"

    @pytest.mark.asyncio
    async def test_process_creator_posts_empty(
        self,
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_posts with no posts makes no GraphQL calls."""
        store = get_store()

        acct_id = snowflake_id()

        # Create account but no posts
        account = AccountFactory.build(id=acct_id, username="test_user_2")
        await store.save(account)

        # Refresh account from store
        account = await store.get(Account, acct_id)

        performer = PerformerFactory.build(id="5111", name="test_user_2")
        studio = StudioFactory.build(id="10211", name="Test Studio 2")

        # Set up respx - expect NO calls for empty posts
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        # Call method with no posts
        await respx_stash_processor.process_creator_posts(
            account=account,
            performer=performer,
            studio=studio,
        )

        # With no posts, no GraphQL calls should occur at all
        assert len(graphql_route.calls) == 0, (
            "Should not make any GraphQL calls for empty posts"
        )

    @pytest.mark.asyncio
    async def test_database_query_structure(
        self,
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test that database query correctly retrieves posts with attachments."""
        store = get_store()

        acct_id = snowflake_id()
        post_id = snowflake_id()
        content_id = snowflake_id()

        # Create account and post with attachment
        account = AccountFactory.build(id=acct_id, username="test_user_3")
        await store.save(account)

        # Create 1 post with attachment
        post = PostFactory.build(
            id=post_id,
            accountId=acct_id,
            content="Test post with attachment",
        )
        await store.save(post)

        attachment = AttachmentFactory.build(
            postId=post_id,
            contentId=content_id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        await store.save(attachment)

        # Link attachment to post
        post.attachments = [attachment]
        await store.save(post)

        # Refresh account from store
        account = await store.get(Account, acct_id)

        performer = PerformerFactory.build(id="5112", name="test_user_3")
        studio = StudioFactory.build(id="10212", name="Test Studio 3")

        # Set up respx with generic responses
        generic_response = httpx.Response(
            200,
            json={
                "data": {
                    "findGalleries": {"galleries": [], "count": 0},
                    "galleryCreate": {"id": "20002"},
                }
            },
        )
        # Allow multiple calls for gallery creation workflow
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[generic_response] * 10
        )

        # Call method
        await respx_stash_processor.process_creator_posts(
            account=account,
            performer=performer,
            studio=studio,
        )

        # Verify calls were made (query found the post)
        assert len(graphql_route.calls) > 0, "Expected GraphQL calls for post"

    @pytest.mark.asyncio
    async def test_post_without_attachment_not_processed(
        self,
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test that posts without attachments are not processed."""
        store = get_store()

        acct_id = snowflake_id()
        post_id = snowflake_id()

        # Create account and post WITHOUT attachment
        account = AccountFactory.build(id=acct_id, username="test_user_4")
        await store.save(account)

        # Create post WITHOUT attachment - should not be found by query
        post = PostFactory.build(
            id=post_id,
            accountId=acct_id,
            content="Test post without attachment",
        )
        await store.save(post)
        # No attachment - post has no attachments

        # Refresh account from store
        account = await store.get(Account, acct_id)

        performer = PerformerFactory.build(id="5113", name="test_user_4")
        studio = StudioFactory.build(id="10213", name="Test Studio 4")

        # Set up respx - expect NO calls for posts without attachments
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        # Call method
        await respx_stash_processor.process_creator_posts(
            account=account,
            performer=performer,
            studio=studio,
        )

        # Should not make any GraphQL calls for posts without attachments
        assert len(graphql_route.calls) == 0, (
            "Should not make any GraphQL calls for posts without attachments"
        )
