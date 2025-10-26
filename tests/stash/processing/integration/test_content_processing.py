"""Integration tests for post and message processing in StashProcessing.

This module tests content processing (posts and messages) using real database
fixtures and factory-based test data instead of mocks.
"""

import pytest

from stash.types import Gallery
from tests.fixtures.metadata_factories import (
    AccountFactory,
    GroupFactory,
    MessageFactory,
    PostFactory,
)


class TestContentProcessingIntegration:
    """Integration tests for content processing in StashProcessing."""

    @pytest.mark.asyncio
    async def test_process_creator_posts_integration(
        self,
        stash_processor,
        session_sync,
        mocker,
    ):
        """Test process_creator_posts with real database integration."""
        # Create a real account
        account = AccountFactory(username="post_creator")
        session_sync.commit()

        # Create real posts for the account
        posts = [
            PostFactory(accountId=account.id, content=f"Post {i}") for i in range(3)
        ]
        session_sync.commit()

        # Setup mock database to return the account and posts
        stash_processor.database.set_result(account)

        # Mock performer and studio
        mock_performer = mocker.MagicMock()
        mock_performer.id = "performer_123"

        mock_studio = mocker.MagicMock()
        mock_studio.id = "studio_123"

        # Mock gallery creation and processing
        mock_gallery = Gallery(id="gallery_123", title="Test Gallery", urls=[])
        stash_processor._get_or_create_gallery = mocker.AsyncMock(
            return_value=mock_gallery
        )
        stash_processor._update_stash_metadata = mocker.AsyncMock()

        # Setup mock to return posts when queried
        # The database mock will need to return posts in the query
        stash_processor.database._result._result = posts

        # Call method
        await stash_processor.process_creator_posts(
            account=account,
            performer=mock_performer,
            studio=mock_studio,
            session=stash_processor.database.session,
        )

        # Verify worker pool was used (from mocked methods in stash_processor fixture)
        assert stash_processor._setup_worker_pool.call_count >= 1
        assert stash_processor._run_worker_pool.call_count >= 1

    @pytest.mark.asyncio
    async def test_process_creator_messages_integration(
        self,
        stash_processor,
        session_sync,
        mocker,
    ):
        """Test process_creator_messages with real database integration."""
        # Create a real account
        account = AccountFactory(username="message_creator")
        session_sync.commit()

        # Create a real group
        group = GroupFactory(createdBy=account.id)
        session_sync.commit()

        # Create real messages for the account
        messages = [
            MessageFactory(
                groupId=group.id, senderId=account.id, content=f"Message {i}"
            )
            for i in range(3)
        ]
        session_sync.commit()

        # Setup mock database to return the account
        stash_processor.database.set_result(account)

        # Mock performer and studio
        mock_performer = mocker.MagicMock()
        mock_performer.id = "performer_456"

        mock_studio = mocker.MagicMock()
        mock_studio.id = "studio_456"

        # Mock gallery creation and processing
        mock_gallery = Gallery(id="gallery_456", title="Message Gallery", urls=[])
        stash_processor._get_or_create_gallery = mocker.AsyncMock(
            return_value=mock_gallery
        )
        stash_processor._update_stash_metadata = mocker.AsyncMock()

        # Setup mock to return messages when queried
        stash_processor.database._result._result = messages

        # Call method
        await stash_processor.process_creator_messages(
            account=account,
            performer=mock_performer,
            studio=mock_studio,
            session=stash_processor.database.session,
        )

        # Verify worker pool was used
        assert stash_processor._setup_worker_pool.call_count >= 1
        assert stash_processor._run_worker_pool.call_count >= 1

    @pytest.mark.asyncio
    async def test_process_items_with_gallery(
        self,
        stash_processor,
        session_sync,
        mocker,
    ):
        """Test _process_items_with_gallery integration with real posts."""
        # Create a real account
        account = AccountFactory(username="gallery_creator")
        session_sync.commit()

        # Create real posts
        posts = [
            PostFactory(accountId=account.id, content=f"Gallery post {i}")
            for i in range(2)
        ]
        session_sync.commit()

        # Mock dependencies
        mock_performer = mocker.MagicMock()
        mock_studio = mocker.MagicMock()

        # Mock _process_item_gallery
        stash_processor._process_item_gallery = mocker.AsyncMock()

        # Define URL pattern function
        def url_pattern_func(item):
            return f"https://example.com/{item.id}"

        # Call method with real posts
        await stash_processor._process_items_with_gallery(
            account=account,
            performer=mock_performer,
            studio=mock_studio,
            item_type="post",
            items=posts,
            url_pattern_func=url_pattern_func,
            session=stash_processor.database.session,
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
        stash_processor,
        session_sync,
        mocker,
    ):
        """Test _process_items_with_gallery with error handling."""
        # Create a real account
        account = AccountFactory(username="error_creator")
        session_sync.commit()

        # Create real posts
        posts = [
            PostFactory(accountId=account.id, content=f"Error test post {i}")
            for i in range(2)
        ]
        session_sync.commit()

        # Mock dependencies
        mock_performer = mocker.MagicMock()
        mock_studio = mocker.MagicMock()

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

        # Call method with real posts
        await stash_processor._process_items_with_gallery(
            account=account,
            performer=mock_performer,
            studio=mock_studio,
            item_type="post",
            items=posts,
            url_pattern_func=url_pattern_func,
            session=stash_processor.database.session,
        )

        # Verify _process_item_gallery was called for both posts despite the error
        assert stash_processor._process_item_gallery.call_count == 2
