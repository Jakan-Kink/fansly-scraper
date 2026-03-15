"""Tests for message processing methods in ContentProcessingMixin.

These tests use entity_store for Pydantic model persistence and respx for HTTP
mocking, allowing real code execution through the entire processing pipeline.
We verify that data flows correctly from database queries to GraphQL API calls.
"""

import json

import httpx
import pytest
import respx

from metadata import Account, ContentType
from metadata.models import get_store
from stash.processing import StashProcessing
from tests.fixtures import (
    AccountFactory,
    AttachmentFactory,
    MessageFactory,
    PerformerFactory,
    StudioFactory,
)
from tests.fixtures.metadata.metadata_factories import GroupFactory


class TestMessageProcessing:
    """Test message processing methods in ContentProcessingMixin."""

    @pytest.mark.asyncio
    async def test_process_creator_messages(
        self,
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_messages processes messages and makes GraphQL calls."""
        store = get_store()

        # Create real account and group
        account = AccountFactory.build(id=12345, username="test_user")
        await store.save(account)

        group = GroupFactory.build(id=40001, createdBy=12345)
        await store.save(group)

        # Link account to group via the users relationship
        group.users = [account]
        await store.save(group)

        # Create 3 messages with attachments (required for query to find them)
        for i in range(3):
            message = MessageFactory.build(
                id=400 + i,
                groupId=40001,
                senderId=12345,
                content=f"Test message {i}",
            )
            await store.save(message)

            # Create attachment for each message
            attachment = AttachmentFactory.build(
                messageId=400 + i,
                contentId=400 + i,
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=0,
            )
            await store.save(attachment)

            # Link attachment to message
            message.attachments = [attachment]
            await store.save(message)

        # Refresh account from store
        account = await store.get(Account, 12345)

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
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test process_creator_messages with no messages makes no GraphQL calls."""
        store = get_store()

        # Create account with group but no messages
        account = AccountFactory.build(id=12346, username="test_user_2")
        await store.save(account)

        group = GroupFactory.build(id=40002, createdBy=12346)
        await store.save(group)

        # Link account to group via the users relationship
        group.users = [account]
        await store.save(group)

        # Refresh account from store
        account = await store.get(Account, 12346)

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
        )

        # With no messages, no GraphQL calls should occur at all
        assert len(graphql_route.calls) == 0, (
            "Should not make any GraphQL calls for empty messages"
        )

    @pytest.mark.asyncio
    async def test_database_query_structure(
        self,
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test that database query correctly retrieves messages with attachments."""
        store = get_store()

        # Create account, group and message with attachment
        account = AccountFactory.build(id=12347, username="test_user_3")
        await store.save(account)

        group = GroupFactory.build(id=40003, createdBy=12347)
        await store.save(group)

        # Link account to group via the users relationship
        group.users = [account]
        await store.save(group)

        # Create 1 message with attachment
        message = MessageFactory.build(
            id=600,
            groupId=40003,
            senderId=12347,
            content="Test message with attachment",
        )
        await store.save(message)

        attachment = AttachmentFactory.build(
            messageId=600,
            contentId=600,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        await store.save(attachment)

        # Link attachment to message
        message.attachments = [attachment]
        await store.save(message)

        # Refresh account from store
        account = await store.get(Account, 12347)

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
        )

        # Verify calls were made (query found the message)
        assert len(graphql_route.calls) > 0, "Expected GraphQL calls for message"

    @pytest.mark.asyncio
    async def test_message_without_attachment_not_processed(
        self,
        entity_store,
        respx_stash_processor: StashProcessing,
    ):
        """Test that messages without attachments are not processed."""
        store = get_store()

        # Create account, group and message WITHOUT attachment
        account = AccountFactory.build(id=12348, username="test_user_4")
        await store.save(account)

        group = GroupFactory.build(id=40004, createdBy=12348)
        await store.save(group)

        # Link account to group via the users relationship
        group.users = [account]
        await store.save(group)

        # Create message WITHOUT attachment - should not be found by query
        message = MessageFactory.build(
            id=700,
            groupId=40004,
            senderId=12348,
            content="Test message without attachment",
        )
        await store.save(message)
        # No attachment - message has no attachments

        # Refresh account from store
        account = await store.get(Account, 12348)

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
        )

        # Should not make any GraphQL calls for messages without attachments
        assert len(graphql_route.calls) == 0, (
            "Should not make any GraphQL calls for messages without attachments"
        )
