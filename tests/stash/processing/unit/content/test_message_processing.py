"""Tests for message processing methods in ContentProcessingMixin.

These tests mock at the HTTP boundary using respx, allowing real code execution
through the entire processing pipeline. We verify that data flows correctly from
database queries to GraphQL API calls.
"""

import json

import httpx
import pytest
import respx
from sqlalchemy import insert, select

from metadata import Account
from metadata.attachment import ContentType
from metadata.messages import group_users
from stash.processing import StashProcessing
from tests.fixtures import (
    AccountFactory,
    AttachmentFactory,
    MessageFactory,
    MetadataGroupFactory,
    PerformerFactory,
    StudioFactory,
)


class TestMessageProcessing:
    """Test message processing methods in ContentProcessingMixin."""

    @pytest.mark.asyncio
    async def test_process_creator_messages(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_messages processes messages and makes GraphQL calls."""
        # Create real account, group and messages with factories
        account = AccountFactory(id=12345, username="test_user")
        group = MetadataGroupFactory(id=40001, createdBy=12345)

        # Link account to group using direct SQL (avoid lazy loading)
        await session.execute(
            insert(group_users).values(accountId=12345, groupId=40001)
        )
        await session.flush()

        # Create 3 messages with attachments (required for query to find them)
        for i in range(3):
            MessageFactory(
                id=400 + i,
                groupId=40001,
                senderId=12345,
                content=f"Test message {i}",
            )
            # Create attachment for each message
            AttachmentFactory(
                messageId=400 + i,
                contentId=400 + i,
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
        # Use side_effect with list that returns same response - catches if call count changes
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
        # Allow multiple calls for this test (3 messages = multiple gallery lookups)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[generic_response] * 20  # Enough for 3 messages
        )

        # Call method - let it execute fully to HTTP boundary
        await respx_stash_processor.process_creator_messages(
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
    async def test_process_creator_messages_empty(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_messages with no messages makes no GraphQL calls."""
        # Create account with group but no messages
        account = AccountFactory(id=12346, username="test_user_2")
        group = MetadataGroupFactory(id=40002, createdBy=12346)

        await session.execute(
            insert(group_users).values(accountId=12346, groupId=40002)
        )
        await session.flush()

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Account).where(Account.id == 12346))
        account = result.scalar_one()

        performer = PerformerFactory.build(id="performer_124", name="test_user_2")
        studio = StudioFactory.build(id="studio_124", name="Test Studio 2")

        # Set up respx - expect NO calls for empty messages
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[]  # Empty list catches any unexpected call
        )

        # Call method with no messages
        await respx_stash_processor.process_creator_messages(
            account=account,
            performer=performer,
            studio=studio,
            session=session,
        )

        # With no messages, no GraphQL calls should occur at all
        assert len(graphql_route.calls) == 0, (
            "Should not make any GraphQL calls for empty messages"
        )

    @pytest.mark.asyncio
    async def test_database_query_structure(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test that database query correctly retrieves messages with attachments."""
        # Create account, group and message with attachment
        account = AccountFactory(id=12347, username="test_user_3")
        group = MetadataGroupFactory(id=40003, createdBy=12347)

        await session.execute(
            insert(group_users).values(accountId=12347, groupId=40003)
        )
        await session.flush()

        # Create 1 message with attachment
        MessageFactory(
            id=600,
            groupId=40003,
            senderId=12347,
            content="Test message with attachment",
        )
        AttachmentFactory(
            messageId=600,
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
        await respx_stash_processor.process_creator_messages(
            account=account,
            performer=performer,
            studio=studio,
            session=session,
        )

        # Verify calls were made (query found the message)
        assert len(graphql_route.calls) > 0, "Expected GraphQL calls for message"

    @pytest.mark.asyncio
    async def test_message_without_attachment_not_processed(
        self,
        factory_async_session,
        session,
        respx_stash_processor: StashProcessing,
    ):
        """Test that messages without attachments are not processed."""
        # Create account, group and message WITHOUT attachment
        account = AccountFactory(id=12348, username="test_user_4")
        group = MetadataGroupFactory(id=40004, createdBy=12348)

        await session.execute(
            insert(group_users).values(accountId=12348, groupId=40004)
        )
        await session.flush()

        # Create message WITHOUT attachment - should not be found by query
        MessageFactory(
            id=700,
            groupId=40004,
            senderId=12348,
            content="Test message without attachment",
        )
        # No AttachmentFactory call - message has no attachments

        factory_async_session.commit()
        await session.commit()

        result = await session.execute(select(Account).where(Account.id == 12348))
        account = result.scalar_one()

        performer = PerformerFactory.build(id="performer_126", name="test_user_4")
        studio = StudioFactory.build(id="studio_126", name="Test Studio 4")

        # Set up respx - expect NO calls for messages without attachments
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

        # Should not make any GraphQL calls for messages without attachments
        assert len(graphql_route.calls) == 0, (
            "Should not make any GraphQL calls for messages without attachments"
        )
