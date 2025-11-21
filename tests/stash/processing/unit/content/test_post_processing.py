"""Tests for post processing methods in ContentProcessingMixin.

These tests mock at the HTTP boundary using respx, allowing real code execution
through the entire processing pipeline. We verify that data flows correctly from
database queries to GraphQL API calls.
"""

import json

import httpx
import pytest
import respx
from sqlalchemy import select

from metadata import Account
from metadata.attachment import ContentType
from stash.processing import StashProcessing
from tests.fixtures import (
    AccountFactory,
    AttachmentFactory,
    PerformerFactory,
    PostFactory,
    StudioFactory,
)


class TestPostProcessing:
    """Test post processing methods in ContentProcessingMixin."""

    @pytest.mark.asyncio
    async def test_process_creator_posts(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_posts processes posts and makes GraphQL calls."""
        # Create real account and posts with factories
        account = AccountFactory(id=12345, username="test_user")

        # Create 3 posts with attachments (required for query to find them)
        for i in range(3):
            PostFactory(
                id=200 + i,
                accountId=12345,
                content=f"Test post {i}",
            )
            # Create attachment for each post
            AttachmentFactory(
                postId=200 + i,
                contentId=200 + i,
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=0,
            )

        # Commit factory changes so async session can see them
        factory_async_session.commit()
        await session.commit()

        # Query fresh account from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account = result.scalar_one()

        # Create real Performer and Studio using factories
        performer = PerformerFactory.build(id="performer_123", name="test_user")
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        # Set up respx to capture all GraphQL calls with generic success responses
        generic_response = httpx.Response(
            200,
            json={
                "data": {
                    # Generic empty responses - we're testing request capture
                    "findGalleries": {"galleries": [], "count": 0},
                    "galleryCreate": {"id": "new_gallery_1"},
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
        await respx_stash_processor.process_creator_posts(
            account=account,
            performer=performer,
            studio=studio,
            session=session,
        )

        # Verify GraphQL calls were made
        assert len(graphql_route.calls) > 0, "Expected GraphQL calls to be made"

        # Verify the requests contain expected data
        for call in graphql_route.calls:
            req = json.loads(call.request.content)
            assert "query" in req or "mutation" in req.get("query", "")
            # Each call should have variables
            assert "variables" in req

    @pytest.mark.asyncio
    async def test_process_creator_posts_empty(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_posts with no posts makes no GraphQL calls."""
        # Create account but no posts
        account = AccountFactory(id=12346, username="test_user_2")

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Account).where(Account.id == 12346))
        account = result.scalar_one()

        performer = PerformerFactory.build(id="performer_124", name="test_user_2")
        studio = StudioFactory.build(id="studio_124", name="Test Studio 2")

        # Set up respx - expect NO calls for empty posts
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        # Call method with no posts
        await respx_stash_processor.process_creator_posts(
            account=account,
            performer=performer,
            studio=studio,
            session=session,
        )

        # With no posts, no GraphQL calls should occur at all
        assert len(graphql_route.calls) == 0, (
            "Should not make any GraphQL calls for empty posts"
        )

    @pytest.mark.asyncio
    async def test_database_query_structure(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test that database query correctly retrieves posts with attachments."""
        # Create account and post with attachment
        account = AccountFactory(id=12347, username="test_user_3")

        # Create 1 post with attachment
        PostFactory(
            id=600,
            accountId=12347,
            content="Test post with attachment",
        )
        AttachmentFactory(
            postId=600,
            contentId=600,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Account).where(Account.id == 12347))
        account = result.scalar_one()

        performer = PerformerFactory.build(id="performer_125", name="test_user_3")
        studio = StudioFactory.build(id="studio_125", name="Test Studio 3")

        # Set up respx with generic responses
        generic_response = httpx.Response(
            200,
            json={
                "data": {
                    "findGalleries": {"galleries": [], "count": 0},
                    "galleryCreate": {"id": "gallery_600"},
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
            session=session,
        )

        # Verify calls were made (query found the post)
        assert len(graphql_route.calls) > 0, "Expected GraphQL calls for post"

    @pytest.mark.asyncio
    async def test_post_without_attachment_not_processed(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test that posts without attachments are not processed."""
        # Create account and post WITHOUT attachment
        account = AccountFactory(id=12348, username="test_user_4")

        # Create post WITHOUT attachment - should not be found by query
        PostFactory(
            id=700,
            accountId=12348,
            content="Test post without attachment",
        )
        # No AttachmentFactory call - post has no attachments

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Account).where(Account.id == 12348))
        account = result.scalar_one()

        performer = PerformerFactory.build(id="performer_126", name="test_user_4")
        studio = StudioFactory.build(id="studio_126", name="Test Studio 4")

        # Set up respx - expect NO calls for posts without attachments
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        # Call method
        await respx_stash_processor.process_creator_posts(
            account=account,
            performer=performer,
            studio=studio,
            session=session,
        )

        # Should not make any GraphQL calls for posts without attachments
        assert len(graphql_route.calls) == 0, (
            "Should not make any GraphQL calls for posts without attachments"
        )
