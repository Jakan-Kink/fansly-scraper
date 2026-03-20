"""Tests for collecting media from attachments and processing items with gallery.

These tests use entity_store for database persistence and respx for HTTP mocking,
following the Pydantic EntityStore migration patterns.
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from metadata import ContentType
from stash.processing import StashProcessing
from tests.fixtures import (
    AccountFactory,
    AccountMediaFactory,
    AttachmentFactory,
    GroupFactory,
    MediaFactory,
    MessageFactory,
    PerformerFactory,
    PostFactory,
    StudioFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


class TestCollectMediaFromAttachments:
    """Test _collect_media_from_attachments method.

    These tests verify pure object manipulation without HTTP calls.
    """

    @pytest.mark.asyncio
    async def test_empty_attachments(
        self,
        respx_stash_processor: StashProcessing,
    ):
        """Test _collect_media_from_attachments with empty attachments."""
        attachments = []
        result = await respx_stash_processor._collect_media_from_attachments(
            attachments
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_attachments_no_media(
        self,
        respx_stash_processor: StashProcessing,
    ):
        """Test _collect_media_from_attachments with attachments that have no media."""
        content_id_1 = snowflake_id()
        content_id_2 = snowflake_id()

        # Create attachments with contentType but no media relationship set
        att1 = AttachmentFactory.build(
            id=60001,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=content_id_1,  # Non-existent AccountMedia
            pos=0,
        )
        att2 = AttachmentFactory.build(
            id=60002,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=content_id_2,  # Non-existent AccountMedia
            pos=1,
        )

        result = await respx_stash_processor._collect_media_from_attachments(
            [att1, att2]
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_attachments_with_media(
        self,
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test _collect_media_from_attachments with attachments that have media."""
        acct_id = snowflake_id()
        media_id_1 = snowflake_id()
        media_id_2 = snowflake_id()

        # Create account first (FK parent for media)
        account = AccountFactory.build(id=acct_id, username="test_user")
        await entity_store.save(account)

        # Create media objects and save to entity_store (populates identity map)
        media1 = MediaFactory.build(
            id=media_id_1,
            accountId=acct_id,
            mimetype="image/jpeg",
            location="https://example.com/media_123.jpg",
            width=800,
            height=600,
        )
        media2 = MediaFactory.build(
            id=media_id_2,
            accountId=acct_id,
            mimetype="video/mp4",
            location="https://example.com/media_456.mp4",
            width=1280,
            height=720,
        )
        await entity_store.save(media1)
        await entity_store.save(media2)

        # Create AccountMedia and save (identity map resolves .media property)
        acct_media1 = AccountMediaFactory.build(
            id=media_id_1, accountId=acct_id, mediaId=media_id_1
        )
        acct_media2 = AccountMediaFactory.build(
            id=media_id_2, accountId=acct_id, mediaId=media_id_2
        )
        await entity_store.save(acct_media1)
        await entity_store.save(acct_media2)

        # Create attachments with media - contentId points to AccountMedia IDs
        # Attachment.media is a read-only property that resolves via identity map
        att1 = AttachmentFactory.build(
            id=60003,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=media_id_1,  # Points to AccountMedia.id
            pos=0,
        )

        att2 = AttachmentFactory.build(
            id=60004,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=media_id_2,  # Points to AccountMedia.id
            pos=1,
        )

        result = await respx_stash_processor._collect_media_from_attachments(
            [att1, att2]
        )

        # Verify we got media objects back
        assert len(result) == 2
        media_ids = [m.id for m in result]
        assert media_id_1 in media_ids
        assert media_id_2 in media_ids


class TestProcessItemsWithGallery:
    """Test _process_items_with_gallery orchestration using respx.

    These tests mock at HTTP boundary to verify the full processing pipeline.
    """

    @pytest.mark.asyncio
    async def test_empty_items(
        self,
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test _process_items_with_gallery with empty items list."""
        acct_id = snowflake_id()

        # Create real account using factory and save to entity_store
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            displayName="Test User",
        )
        await entity_store.save(account)

        # Create real performer and studio
        performer = PerformerFactory.build(id="performer_123", name="test_user")
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        # Set up respx - expect NO calls with empty items
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        # Call method with empty items (no session= parameter)
        await respx_stash_processor._process_items_with_gallery(
            account=account,
            performer=performer,
            studio=studio,
            item_type="post",
            items=[],
            url_pattern_func=lambda x: f"https://example.com/post/{x.id}",
        )

        # With no items, no GraphQL calls should be made
        assert len(graphql_route.calls) == 0

    @pytest.mark.asyncio
    async def test_item_no_attachments(
        self,
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test _process_items_with_gallery with item that has no attachments."""
        acct_id = snowflake_id()
        post_id = snowflake_id()

        # Create account and post without attachments
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            displayName="Test User",
        )
        await entity_store.save(account)

        post = PostFactory.build(
            id=post_id,
            accountId=acct_id,
            content="Test content",
            createdAt=datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC),
        )
        # Post has no attachments by default
        await entity_store.save(post)

        # Create real performer and studio
        performer = PerformerFactory.build(id="performer_123", name="test_user")
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        # Set up respx - expect NO calls for items without attachments
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        # Call method (no session= parameter)
        await respx_stash_processor._process_items_with_gallery(
            account=account,
            performer=performer,
            studio=studio,
            item_type="post",
            items=[post],
            url_pattern_func=lambda x: f"https://example.com/post/{x.id}",
        )

        # Items without attachments don't trigger any GraphQL calls
        assert len(graphql_route.calls) == 0

    @pytest.mark.asyncio
    async def test_multiple_items(
        self,
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test _process_items_with_gallery with multiple items."""
        acct_id = snowflake_id()
        post_id_1 = snowflake_id()
        post_id_2 = snowflake_id()
        content_id_1 = snowflake_id()
        content_id_2 = snowflake_id()

        # Create account
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            displayName="Test User",
        )
        await entity_store.save(account)

        # Create posts with attachments set via Pydantic relationships
        post1 = PostFactory.build(
            id=post_id_1,
            accountId=acct_id,
            content="Test post 1",
            createdAt=datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC),
        )
        att1 = AttachmentFactory.build(
            postId=post_id_1,
            contentId=content_id_1,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        # Use _add_to_relationship to avoid validate_assignment re-running
        # _prepare_post_data which filters non-dict attachments to [].
        await post1._add_to_relationship("attachments", att1)

        post2 = PostFactory.build(
            id=post_id_2,
            accountId=acct_id,
            content="Test post 2",
            createdAt=datetime(2024, 5, 2, 12, 0, 0, tzinfo=UTC),
        )
        att2 = AttachmentFactory.build(
            postId=post_id_2,
            contentId=content_id_2,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        await post2._add_to_relationship("attachments", att2)

        # Create real performer and studio
        performer = PerformerFactory.build(id="performer_123", name="test_user")
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        # Set up respx with multiple responses for gallery lookups/creates
        generic_response = httpx.Response(
            200,
            json={
                "data": {
                    "findGalleries": {"galleries": [], "count": 0},
                    "galleryCreate": {"id": "new_gallery"},
                }
            },
        )
        # Allow multiple calls for 2 posts with attachments
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[generic_response] * 20  # Enough for multiple gallery lookups
        )

        # Call method (no session= parameter)
        await respx_stash_processor._process_items_with_gallery(
            account=account,
            performer=performer,
            studio=studio,
            item_type="post",
            items=[post1, post2],
            url_pattern_func=lambda x: f"https://example.com/post/{x.id}",
        )

        # Verify GraphQL calls were made (at least one per item with attachments)
        assert len(graphql_route.calls) > 0

        # Inspect the requests to verify correct data was sent
        for call in graphql_route.calls:
            req = json.loads(call.request.content)
            assert "query" in req
            assert "variables" in req

            # If this is a galleryCreate call, verify the input data
            if "galleryCreate" in req["query"]:
                input_data = req["variables"].get("input", {})
                # Gallery should have title from post content
                assert "title" in input_data
                # Gallery should have code matching post ID
                if "code" in input_data:
                    assert input_data["code"] in [str(post_id_1), str(post_id_2)]
                # Gallery should have date
                if "date" in input_data:
                    assert input_data["date"].startswith("2024-05")


class TestProcessCreatorContent:
    """Test process_creator_posts and process_creator_messages using respx.

    These tests verify the full processing workflow at HTTP boundary.
    """

    @pytest.mark.asyncio
    async def test_process_posts_no_posts(
        self,
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_posts with no posts."""
        acct_id = snowflake_id()

        # Create account without posts in entity_store
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            displayName="Test User",
        )
        await entity_store.save(account)

        # Create real performer and studio
        performer = PerformerFactory.build(id="performer_123", name="test_user")
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        # Set up respx - expect NO calls for account without posts
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        # Call method (no session= parameter)
        await respx_stash_processor.process_creator_posts(
            account=account,
            performer=performer,
            studio=studio,
        )

        # With no posts, no GraphQL calls should be made
        assert len(graphql_route.calls) == 0

    @pytest.mark.asyncio
    async def test_process_messages_no_messages(
        self,
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_messages with no messages."""
        acct_id = snowflake_id()

        # Create account without messages in entity_store
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            displayName="Test User",
        )
        await entity_store.save(account)

        # Create real performer and studio
        performer = PerformerFactory.build(id="performer_123", name="test_user")
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        # Set up respx - expect NO calls for account without messages
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        # Call method (no session= parameter)
        await respx_stash_processor.process_creator_messages(
            account=account,
            performer=performer,
            studio=studio,
        )

        # With no messages, no GraphQL calls should be made
        assert len(graphql_route.calls) == 0

    @pytest.mark.asyncio
    async def test_process_posts_exception_handling(
        self,
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_posts handles exceptions during processing (lines 249-257)."""
        acct_id = snowflake_id()
        post_id = snowflake_id()
        content_id = snowflake_id()

        # Create account and post with attachments in entity_store
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            displayName="Test User",
        )
        await entity_store.save(account)

        post = PostFactory.build(
            id=post_id,
            accountId=acct_id,
            content="Test post",
            createdAt=datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC),
        )
        att = AttachmentFactory.build(
            postId=post_id,
            contentId=content_id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        await post._add_to_relationship("attachments", att)
        await entity_store.save(post)

        # Create real performer and studio
        performer = PerformerFactory.build(id="performer_123", name="test_user")
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        # Mock _process_items_with_gallery to raise an exception
        with patch.object(
            respx_stash_processor,
            "_process_items_with_gallery",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Simulated processing error"),
        ):
            # Call method - should catch exception and continue (no session= parameter)
            await respx_stash_processor.process_creator_posts(
                account=account,
                performer=performer,
                studio=studio,
            )
            # Test passes if no exception propagates (exception was caught and logged)

    @pytest.mark.asyncio
    async def test_process_messages_exception_handling(
        self,
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_messages handles exceptions during processing (lines 129-137)."""
        acct_id = snowflake_id()
        group_id = snowflake_id()
        msg_id = snowflake_id()
        content_id = snowflake_id()

        # Create account
        account = AccountFactory.build(
            id=acct_id,
            username="test_user",
            displayName="Test User",
        )
        await entity_store.save(account)

        # Create group with proper foreign key
        group = GroupFactory.build(id=group_id, createdBy=acct_id)
        # Add user via _add_to_relationship to avoid validate_assignment issues
        await group._add_to_relationship("users", account)
        await entity_store.save(group)

        # Create message with proper foreign keys and attachment
        message = MessageFactory.build(
            id=msg_id,
            groupId=group_id,
            senderId=acct_id,
            content="Test message",
            createdAt=datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC),
        )
        att = AttachmentFactory.build(
            messageId=msg_id,
            contentId=content_id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        await message._add_to_relationship("attachments", att)
        await entity_store.save(message)

        # Create real performer and studio
        performer = PerformerFactory.build(id="performer_123", name="test_user")
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        # Mock _process_items_with_gallery to raise an exception
        with patch.object(
            respx_stash_processor,
            "_process_items_with_gallery",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Simulated processing error"),
        ):
            # Call method - should catch exception and continue (no session= parameter)
            await respx_stash_processor.process_creator_messages(
                account=account,
                performer=performer,
                studio=studio,
            )
            # Test passes if no exception propagates (exception was caught and logged)
