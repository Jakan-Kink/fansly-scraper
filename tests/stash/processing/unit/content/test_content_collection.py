"""Tests for collecting media from attachments and processing items with gallery.

These tests mock at the HTTP boundary using respx, allowing real code execution
through the entire processing pipeline. We verify that data flows correctly from
database queries to GraphQL API calls.
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from sqlalchemy import insert, select
from sqlalchemy.orm import selectinload

from metadata import Account, AccountMedia
from metadata.attachment import Attachment, ContentType
from metadata.messages import group_users
from metadata.post import Post
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


class TestCollectMediaFromAttachments:
    """Test _collect_media_from_attachments method.

    These tests verify pure database/object manipulation without HTTP calls.
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
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _collect_media_from_attachments with attachments that have no media."""
        # Create attachments with contentType but contentId pointing to non-existent media
        AttachmentFactory(
            id=60001,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=99999,  # Non-existent AccountMedia
            pos=0,
        )
        AttachmentFactory(
            id=60002,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=99998,  # Non-existent AccountMedia
            pos=1,
        )

        factory_async_session.commit()
        await session.commit()

        # Query fresh from async session
        result = await session.execute(
            select(Attachment).where(Attachment.id.in_([60001, 60002]))
        )
        attachments = list(result.scalars().all())

        result = await respx_stash_processor._collect_media_from_attachments(
            attachments
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_attachments_with_media(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _collect_media_from_attachments with attachments that have media."""
        # Create account with factory
        AccountFactory(
            id=12345,
            username="test_user",
            displayName="Test User",
        )

        # Create media objects using factory
        MediaFactory(
            id=123,
            accountId=12345,
            mimetype="image/jpeg",
            location="https://example.com/media_123.jpg",
            width=800,
            height=600,
        )
        MediaFactory(
            id=456,
            accountId=12345,
            mimetype="video/mp4",
            location="https://example.com/media_456.mp4",
            width=1280,
            height=720,
        )

        # Create AccountMedia to link attachments to media using factory
        AccountMediaFactory(id=123, accountId=12345, mediaId=123)
        AccountMediaFactory(id=456, accountId=12345, mediaId=456)

        # Create attachments with media - use contentId not accountMediaId
        AttachmentFactory(
            id=60003,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=123,  # Points to AccountMedia id
            pos=0,
        )
        AttachmentFactory(
            id=60004,
            contentType=ContentType.ACCOUNT_MEDIA,
            contentId=456,  # Points to AccountMedia id
            pos=1,
        )

        factory_async_session.commit()
        await session.commit()

        # Query fresh attachments from async session with eager loading
        result = await session.execute(
            select(Attachment)
            .where(Attachment.id.in_([60003, 60004]))
            .options(selectinload(Attachment.media).selectinload(AccountMedia.media))
        )
        attachments = list(result.scalars().all())

        result = await respx_stash_processor._collect_media_from_attachments(
            attachments
        )

        # Verify we got media objects back
        assert len(result) == 2
        media_ids = [m.id for m in result]
        assert 123 in media_ids
        assert 456 in media_ids


class TestProcessItemsWithGallery:
    """Test _process_items_with_gallery orchestration using respx.

    These tests mock at HTTP boundary to verify the full processing pipeline.
    """

    @pytest.mark.asyncio
    async def test_empty_items(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _process_items_with_gallery with empty items list."""
        # Create real account using factory
        AccountFactory(
            id=12345,
            username="test_user",
            displayName="Test User",
        )

        factory_async_session.commit()
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account = result.scalar_one()

        # Create real performer and studio
        performer = PerformerFactory.build(id="performer_123", name="test_user")
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        # Set up respx - expect NO calls with empty items
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        # Call method with empty items
        await respx_stash_processor._process_items_with_gallery(
            account=account,
            performer=performer,
            studio=studio,
            item_type="post",
            items=[],
            url_pattern_func=lambda x: f"https://example.com/post/{x.id}",
            session=session,
        )

        # With no items, no GraphQL calls should be made
        assert len(graphql_route.calls) == 0

    @pytest.mark.asyncio
    async def test_item_no_attachments(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _process_items_with_gallery with item that has no attachments."""
        # Create account and post without attachments
        AccountFactory(
            id=12345,
            username="test_user",
            displayName="Test User",
        )

        PostFactory(
            id=123,
            accountId=12345,
            content="Test content",
            createdAt=datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC),
        )

        factory_async_session.commit()
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account = result.scalar_one()

        result = await session.execute(
            select(Post).where(Post.id == 123).options(selectinload(Post.attachments))
        )
        post = result.unique().scalar_one()

        # Create real performer and studio
        performer = PerformerFactory.build(id="performer_123", name="test_user")
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        # Set up respx - expect NO calls for items without attachments
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        # Call method
        await respx_stash_processor._process_items_with_gallery(
            account=account,
            performer=performer,
            studio=studio,
            item_type="post",
            items=[post],
            url_pattern_func=lambda x: f"https://example.com/post/{x.id}",
            session=session,
        )

        # Items without attachments don't trigger any GraphQL calls
        assert len(graphql_route.calls) == 0

    @pytest.mark.asyncio
    async def test_multiple_items(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test _process_items_with_gallery with multiple items."""
        # Create account and multiple posts with attachments
        AccountFactory(
            id=12345,
            username="test_user",
            displayName="Test User",
        )

        PostFactory(
            id=123,
            accountId=12345,
            content="Test post 1",
            createdAt=datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC),
        )
        AttachmentFactory(
            postId=123,
            contentId=123,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        PostFactory(
            id=456,
            accountId=12345,
            content="Test post 2",
            createdAt=datetime(2024, 5, 2, 12, 0, 0, tzinfo=UTC),
        )
        AttachmentFactory(
            postId=456,
            contentId=456,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        factory_async_session.commit()
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account = result.scalar_one()

        result = await session.execute(
            select(Post)
            .where(Post.id.in_([123, 456]))
            .options(selectinload(Post.attachments))
            .order_by(Post.id)
        )
        posts = list(result.unique().scalars().all())

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

        # Call method
        await respx_stash_processor._process_items_with_gallery(
            account=account,
            performer=performer,
            studio=studio,
            item_type="post",
            items=posts,
            url_pattern_func=lambda x: f"https://example.com/post/{x.id}",
            session=session,
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
                    assert input_data["code"] in ["123", "456"]
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
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_posts with no posts."""
        # Create account without posts
        AccountFactory(
            id=12345,
            username="test_user",
            displayName="Test User",
        )

        factory_async_session.commit()
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account = result.scalar_one()

        # Create real performer and studio
        performer = PerformerFactory.build(id="performer_123", name="test_user")
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        # Set up respx - expect NO calls for account without posts
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

        # With no posts, no GraphQL calls should be made
        assert len(graphql_route.calls) == 0

    @pytest.mark.asyncio
    async def test_process_messages_no_messages(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_messages with no messages."""
        # Create account without messages
        AccountFactory(
            id=12345,
            username="test_user",
            displayName="Test User",
        )

        factory_async_session.commit()
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account = result.scalar_one()

        # Create real performer and studio
        performer = PerformerFactory.build(id="performer_123", name="test_user")
        studio = StudioFactory.build(id="studio_123", name="Test Studio")

        # Set up respx - expect NO calls for account without messages
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        # Call method
        await respx_stash_processor.process_creator_messages(
            account=account,
            performer=performer,
            studio=studio,
            session=session,
        )

        # With no messages, no GraphQL calls should be made
        assert len(graphql_route.calls) == 0

    @pytest.mark.asyncio
    async def test_process_posts_exception_handling(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_posts handles exceptions during processing (lines 249-257)."""
        # Create account and post with attachments
        AccountFactory(
            id=12345,
            username="test_user",
            displayName="Test User",
        )

        PostFactory(
            id=123,
            accountId=12345,
            content="Test post",
            createdAt=datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC),
        )
        AttachmentFactory(
            postId=123,
            contentId=123,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        factory_async_session.commit()
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account = result.scalar_one()

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
            # Call method - should catch exception and continue
            await respx_stash_processor.process_creator_posts(
                account=account,
                performer=performer,
                studio=studio,
                session=session,
            )
            # Test passes if no exception propagates (exception was caught and logged)

    @pytest.mark.asyncio
    async def test_process_messages_exception_handling(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_messages handles exceptions during processing (lines 129-137)."""
        # Create account
        account_obj = AccountFactory(
            id=12345,
            username="test_user",
            displayName="Test User",
        )

        # Create group with proper foreign key
        group_obj = GroupFactory(id=999, createdBy=12345)

        # Create message with proper foreign keys and attachment
        message_obj = MessageFactory(
            id=123,
            groupId=999,
            senderId=12345,
            content="Test message",
            createdAt=datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC),
        )
        AttachmentFactory(
            messageId=123,
            contentId=123,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        # Commit to sync session first
        factory_async_session.commit()

        # Set up the many-to-many relationship - Group.users is viewonly so use insert()
        factory_async_session.sync_session.execute(
            insert(group_users).values(groupId=999, accountId=12345)
        )
        factory_async_session.commit()

        # Commit to async session
        await session.commit()

        # Query fresh from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account = result.scalar_one()

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
            # Call method - should catch exception and continue
            await respx_stash_processor.process_creator_messages(
                account=account,
                performer=performer,
                studio=studio,
                session=session,
            )
            # Test passes if no exception propagates (exception was caught and logged)
