"""Integration tests for post and message processing in StashProcessing.

This module tests content processing (posts and messages) using real database
fixtures and factory-based test data instead of mocks.
"""

import pytest
from sqlalchemy import select

from metadata import Account
from stash.types import Gallery
from tests.fixtures.metadata_factories import (
    AccountFactory,
    GroupFactory,
    MessageFactory,
    PostFactory,
)
from tests.fixtures.stash_type_factories import PerformerFactory, StudioFactory


class TestContentProcessingIntegration:
    """Integration tests for content processing in StashProcessing."""

    @pytest.mark.asyncio
    async def test_process_creator_posts_integration(
        self,
        factory_session,
        stash_processor,
        test_database_sync,
        mocker,
    ):
        """Test process_creator_posts with real database integration."""
        # Create real account and posts using factories (data is committed to DB)
        account = AccountFactory(username="post_creator")
        factory_session.commit()

        posts = [
            PostFactory(accountId=account.id, content=f"Post {i}") for i in range(3)
        ]
        factory_session.commit()

        # Create real Stash API type objects (not database models)
        performer = PerformerFactory(id="performer_123", name="post_creator")
        studio = StudioFactory(id="studio_123", name="Post Studio")

        # Mock gallery creation (external Stash API call)
        mock_gallery = Gallery(id="gallery_123", title="Test Gallery", urls=[])
        stash_processor._get_or_create_gallery = mocker.AsyncMock(
            return_value=mock_gallery
        )
        stash_processor._update_stash_metadata = mocker.AsyncMock()

        # Mock _run_worker_pool since fixture only mocks _setup_worker_pool
        stash_processor._run_worker_pool = mocker.AsyncMock()

        # Use async session from the database and query account fresh
        async with test_database_sync.async_session_scope() as async_session:
            # Re-query account in async session to avoid session attachment issues
            result = await async_session.execute(
                select(Account).where(Account.id == account.id)
            )
            async_account = result.scalar_one()

            await stash_processor.process_creator_posts(
                account=async_account,
                performer=performer,
                studio=studio,
                session=async_session,
            )

        # Verify worker pool was used (from mocked methods in stash_processor fixture)
        assert stash_processor._setup_worker_pool.call_count >= 1
        assert stash_processor._run_worker_pool.call_count >= 1

    @pytest.mark.asyncio
    async def test_process_creator_messages_integration(
        self,
        factory_session,
        stash_processor,
        test_database_sync,
        mocker,
    ):
        """Test process_creator_messages with real database integration."""
        # Create real account, group, and messages using factories
        account = AccountFactory(username="message_creator")
        factory_session.commit()

        group = GroupFactory(createdBy=account.id)
        factory_session.commit()

        messages = [
            MessageFactory(
                groupId=group.id, senderId=account.id, content=f"Message {i}"
            )
            for i in range(3)
        ]
        factory_session.commit()

        # Create real Stash API type objects (not database models)
        performer = PerformerFactory(id="performer_456", name="message_creator")
        studio = StudioFactory(id="studio_456", name="Message Studio")

        # Mock gallery creation (external Stash API call)
        mock_gallery = Gallery(id="gallery_456", title="Message Gallery", urls=[])
        stash_processor._get_or_create_gallery = mocker.AsyncMock(
            return_value=mock_gallery
        )
        stash_processor._update_stash_metadata = mocker.AsyncMock()

        # Mock _run_worker_pool since fixture only mocks _setup_worker_pool
        stash_processor._run_worker_pool = mocker.AsyncMock()

        # Use async session from the database and query account fresh
        async with test_database_sync.async_session_scope() as async_session:
            # Re-query account in async session to avoid session attachment issues
            result = await async_session.execute(
                select(Account).where(Account.id == account.id)
            )
            async_account = result.scalar_one()

            await stash_processor.process_creator_messages(
                account=async_account,
                performer=performer,
                studio=studio,
                session=async_session,
            )

        # Verify worker pool was used
        assert stash_processor._setup_worker_pool.call_count >= 1
        assert stash_processor._run_worker_pool.call_count >= 1

    @pytest.mark.asyncio
    async def test_process_items_with_gallery(
        self,
        factory_session,
        stash_processor,
        test_database_sync,
        mocker,
    ):
        """Test _process_items_with_gallery integration with real posts."""
        # Create real account and posts using factories
        account = AccountFactory(username="gallery_creator")
        factory_session.commit()

        posts = [
            PostFactory(accountId=account.id, content=f"Gallery post {i}")
            for i in range(2)
        ]
        factory_session.commit()

        # Create real Stash API type objects (not database models)
        performer = PerformerFactory(id="performer_789", name="gallery_creator")
        studio = StudioFactory(id="studio_789", name="Gallery Studio")

        # Mock _process_item_gallery
        stash_processor._process_item_gallery = mocker.AsyncMock()

        # Define URL pattern function
        def url_pattern_func(item):
            return f"https://example.com/{item.id}"

        # Use async session from the database
        async with test_database_sync.async_session_scope() as async_session:
            await stash_processor._process_items_with_gallery(
                account=account,
                performer=performer,
                studio=studio,
                item_type="post",
                items=posts,
                url_pattern_func=url_pattern_func,
                session=async_session,
            )

        # Verify _process_item_gallery was called for each post
        assert stash_processor._process_item_gallery.call_count == 2

        # Verify the URLs were generated correctly
        first_call = stash_processor._process_item_gallery.call_args_list[0]
        assert first_call[1]["url_pattern"] == f"https://example.com/{posts[0].id}"

        second_call = stash_processor._process_item_gallery.call_args_list[1]
        assert second_call[1]["url_pattern"] == f"https://example.com/{posts[1].id}"

    @pytest.mark.asyncio
    async def test_process_items_with_gallery_error_handling(
        self,
        factory_session,
        stash_processor,
        test_database_sync,
        mocker,
    ):
        """Test _process_items_with_gallery with error handling."""
        # Create real account and posts using factories
        account = AccountFactory(username="error_creator")
        factory_session.commit()

        posts = [
            PostFactory(accountId=account.id, content=f"Error test post {i}")
            for i in range(2)
        ]
        factory_session.commit()

        # Create real Stash API type objects (not database models)
        performer = PerformerFactory(id="performer_999", name="error_creator")
        studio = StudioFactory(id="studio_999", name="Error Studio")

        # Setup _process_item_gallery to raise exception for first post
        stash_processor._process_item_gallery = mocker.AsyncMock(
            side_effect=[
                Exception("Test error"),  # First call fails
                None,  # Second call succeeds
            ]
        )

        # Define URL pattern function
        def url_pattern_func(item):
            return f"https://example.com/{item.id}"

        # Mock error printing to avoid console output
        mocker.patch("stash.processing.mixins.content.print_error")

        # Use async session from the database
        async with test_database_sync.async_session_scope() as async_session:
            await stash_processor._process_items_with_gallery(
                account=account,
                performer=performer,
                studio=studio,
                item_type="post",
                items=posts,
                url_pattern_func=url_pattern_func,
                session=async_session,
            )

        # Verify _process_item_gallery was called for both posts despite the error
        assert stash_processor._process_item_gallery.call_count == 2
