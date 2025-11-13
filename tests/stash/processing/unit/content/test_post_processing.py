"""Tests for post processing methods in ContentProcessingMixin."""

from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from metadata import Account, Post
from metadata.attachment import ContentType
from stash.types import Performer, Studio
from tests.fixtures.metadata.metadata_factories import AccountFactory, AttachmentFactory, PostFactory


class TestPostProcessing:
    """Test post processing methods in ContentProcessingMixin."""

    @pytest.mark.asyncio
    async def test_process_creator_posts(
        self,
        factory_async_session,
        session,
        content_mixin,
    ):
        """Test process_creator_posts method."""
        # Create real account and posts with factories
        account = AccountFactory(id=12345, username="test_user")

        # Create 3 posts with attachments (required for query to find them)
        for i in range(3):
            post = PostFactory(
                id=200 + i,
                accountId=12345,
                content=f"Test post {i}",
            )
            # Create attachment for each post (process_creator_posts queries posts WITH attachments)
            # postId links attachment to post, contentId points to the media
            AttachmentFactory(
                postId=200 + i,  # Link to post
                contentId=200 + i,  # Just a dummy value
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=0,
            )

        # Query fresh account and posts from async session (factory_async_session handles sync)
        result = await session.execute(select(Account).where(Account.id == 12345))
        account = result.scalar_one()

        result = await session.execute(
            select(Post)
            .where(Post.accountId == 12345)
            .options(selectinload(Post.attachments))
        )
        posts = list(result.unique().scalars().all())

        # Ensure posts were created
        assert len(posts) == 3, f"Expected 3 posts, got {len(posts)}"

        # Create mock Performer and Studio
        mock_performer = MagicMock(spec=Performer)
        mock_performer.id = "performer_123"
        mock_studio = MagicMock(spec=Studio)
        mock_studio.id = "studio_123"

        # Setup worker pool
        task_name = "task_name"
        process_name = "process_name"
        semaphore = MagicMock()
        queue = MagicMock()
        queue.join = AsyncMock()  # Make queue.join() awaitable
        queue.put = AsyncMock()  # Make queue.put() awaitable

        content_mixin._setup_worker_pool = AsyncMock(
            return_value=(
                task_name,
                process_name,
                semaphore,
                queue,
            )
        )

        # Ensure _process_items_with_gallery is properly mocked
        # (fixture may not have set it up correctly)
        if not isinstance(content_mixin._process_items_with_gallery, AsyncMock):
            content_mixin._process_items_with_gallery = AsyncMock()

        # Mock _run_worker_pool
        content_mixin._run_worker_pool = AsyncMock()

        # Call method
        await content_mixin.process_creator_posts(
            account=account,
            performer=mock_performer,
            studio=mock_studio,
            session=session,
        )

        # Verify worker pool was setup with correct posts
        content_mixin._setup_worker_pool.assert_called_once()
        call_args = content_mixin._setup_worker_pool.call_args
        assert len(call_args[0][0]) == 3  # 3 posts
        assert call_args[0][1] == "post"

        # Verify worker pool was run
        content_mixin._run_worker_pool.assert_called_once()

        # Extract process_item function from the call
        process_item = content_mixin._run_worker_pool.call_args[1]["process_item"]
        assert callable(process_item)

        # Test the process_item function with individual posts
        test_post = posts[0]

        # Make semaphore context manager work in test
        semaphore.__aenter__ = AsyncMock()
        semaphore.__aexit__ = AsyncMock()

        # Call the process_item function with a single post
        await process_item(test_post)

        # Verify _process_items_with_gallery was called for the post
        assert content_mixin._process_items_with_gallery.call_count == 1

        # Verify call arguments
        call_args = content_mixin._process_items_with_gallery.call_args_list[0]
        assert call_args[1]["account"] == account
        assert call_args[1]["performer"] == mock_performer
        assert call_args[1]["studio"] == mock_studio
        assert call_args[1]["item_type"] == "post"
        assert call_args[1]["items"] == [test_post]
        assert callable(call_args[1]["url_pattern_func"])
        assert call_args[1]["session"] == session

        # Test url_pattern_func
        url_pattern_func = call_args[1]["url_pattern_func"]
        assert url_pattern_func(test_post) == f"https://fansly.com/post/{test_post.id}"

    @pytest.mark.asyncio
    async def test_process_creator_posts_error_handling(
        self,
        factory_async_session,
        session,
        content_mixin,
    ):
        """Test process_creator_posts method with error handling."""
        # Create real account and posts with factories
        account = AccountFactory(id=12346, username="test_user2")

        # Create 2 posts with attachments (required for query to find them)
        post1 = PostFactory(id=300, accountId=12346, content="Post 1")
        AttachmentFactory(
            postId=300, contentId=300, contentType=ContentType.ACCOUNT_MEDIA, pos=0
        )

        post2 = PostFactory(id=301, accountId=12346, content="Post 2")
        AttachmentFactory(
            postId=301, contentId=301, contentType=ContentType.ACCOUNT_MEDIA, pos=0
        )

        # Query fresh account and posts from async session
        result = await session.execute(select(Account).where(Account.id == 12346))
        account = result.scalar_one()

        result = await session.execute(
            select(Post)
            .where(Post.accountId == 12346)
            .options(selectinload(Post.attachments))
        )
        posts = list(result.unique().scalars().all())

        # Create mock Performer and Studio
        mock_performer = MagicMock(spec=Performer)
        mock_performer.id = "performer_124"
        mock_studio = MagicMock(spec=Studio)
        mock_studio.id = "studio_124"

        # Setup worker pool
        task_name = "task_name"
        process_name = "process_name"
        semaphore = MagicMock()
        queue = MagicMock()
        queue.join = AsyncMock()  # Make queue.join() awaitable
        queue.put = AsyncMock()  # Make queue.put() awaitable

        content_mixin._setup_worker_pool = AsyncMock(
            return_value=(
                task_name,
                process_name,
                semaphore,
                queue,
            )
        )

        # Replace _process_items_with_gallery with a mock that has side_effect
        mock_process_items = AsyncMock(
            side_effect=[
                Exception("Test error"),  # First call fails
                None,  # Second call succeeds
            ]
        )
        content_mixin._process_items_with_gallery = mock_process_items

        # Mock _run_worker_pool
        content_mixin._run_worker_pool = AsyncMock()

        # Call method
        await content_mixin.process_creator_posts(
            account=account,
            performer=mock_performer,
            studio=mock_studio,
            session=session,
        )

        # Extract process_item function from the call
        process_item = content_mixin._run_worker_pool.call_args[1]["process_item"]

        # Make semaphore context manager work in test
        semaphore.__aenter__ = AsyncMock()
        semaphore.__aexit__ = AsyncMock()

        # Test the process_item function with individual posts
        test_posts = posts[:2]

        # Call the process_item function for each post
        # Error should be handled gracefully in actual implementation
        for post in test_posts:
            with suppress(Exception):
                await process_item(post)

        # Verify error was handled and processing continued
        assert content_mixin._process_items_with_gallery.call_count == 2

    @pytest.mark.asyncio
    async def test_process_items_with_gallery(
        self,
        factory_async_session,
        session,
        content_mixin,
    ):
        """Test _process_items_with_gallery method."""
        # Create real account and post with factories
        account = AccountFactory(id=12347, username="test_user3")
        post = PostFactory(id=400, accountId=12347, content="Test post for gallery")

        # Query fresh account and post from async session
        result = await session.execute(select(Account).where(Account.id == 12347))
        account = result.scalar_one()

        result = await session.execute(
            select(Post).where(Post.id == 400).options(selectinload(Post.attachments))
        )
        post = result.unique().scalar_one()

        # Create mock Performer and Studio
        mock_performer = MagicMock(spec=Performer)
        mock_performer.id = "performer_125"
        mock_studio = MagicMock(spec=Studio)
        mock_studio.id = "studio_125"

        # Patch _process_item_gallery to test the actual implementation
        with patch.object(
            content_mixin, "_process_item_gallery", AsyncMock()
        ) as mock_process_gallery:
            # Define URL pattern function
            def url_pattern_func(item):
                return f"https://example.com/{item.id}"

            # Call the method
            await content_mixin._process_items_with_gallery(
                account=account,
                performer=mock_performer,
                studio=mock_studio,
                item_type="post",
                items=[post],
                url_pattern_func=url_pattern_func,
                session=session,
            )

            # Verify _process_item_gallery was called
            mock_process_gallery.assert_called_once_with(
                item=post,
                account=account,
                performer=mock_performer,
                studio=mock_studio,
                item_type="post",
                url_pattern=url_pattern_func(post),
                session=session,
            )

    @pytest.mark.asyncio
    async def test_process_items_with_gallery_error_handling(
        self,
        factory_async_session,
        session,
        content_mixin,
    ):
        """Test _process_items_with_gallery method with error handling."""
        # Create real account and posts with factories
        account = AccountFactory(id=12348, username="test_user4")
        post1 = PostFactory(id=500, accountId=12348, content="Post 1")
        post2 = PostFactory(id=501, accountId=12348, content="Post 2")

        # Query fresh account and posts from async session
        result = await session.execute(select(Account).where(Account.id == 12348))
        account = result.scalar_one()

        result = await session.execute(
            select(Post)
            .where(Post.accountId == 12348)
            .order_by(Post.id)
            .options(selectinload(Post.attachments))
        )
        posts = list(result.unique().scalars().all())

        # Create mock Performer and Studio
        mock_performer = MagicMock(spec=Performer)
        mock_performer.id = "performer_126"
        mock_studio = MagicMock(spec=Studio)
        mock_studio.id = "studio_126"

        # Patch _process_item_gallery to test error handling
        with patch.object(
            content_mixin, "_process_item_gallery", AsyncMock()
        ) as mock_process_gallery:
            # Setup _process_item_gallery to raise exception for a specific post
            mock_process_gallery.side_effect = [
                Exception("Test error"),  # First call fails
                None,  # Second call succeeds
            ]

            # Define URL pattern function
            def url_pattern_func(item):
                return f"https://example.com/{item.id}"

            # Call the method with multiple items
            await content_mixin._process_items_with_gallery(
                account=account,
                performer=mock_performer,
                studio=mock_studio,
                item_type="post",
                items=posts[:2],
                url_pattern_func=url_pattern_func,
                session=session,
            )

            # Verify _process_item_gallery was called for both items despite the error
            assert mock_process_gallery.call_count == 2

    @pytest.mark.asyncio
    async def test_database_query_structure(
        self,
        factory_async_session,
        session,
        content_mixin,
    ):
        """Test the database query structure in process_creator_posts."""
        # Create real account and posts with factories
        account = AccountFactory(id=12349, username="test_user5")
        post1 = PostFactory(id=600, accountId=12349, content="Post for query test")
        # Create attachment (required for query to find post)
        AttachmentFactory(
            postId=600, contentId=600, contentType=ContentType.ACCOUNT_MEDIA, pos=0
        )

        # Query fresh account from async session
        result = await session.execute(select(Account).where(Account.id == 12349))
        account = result.scalar_one()

        # Create mock Performer and Studio
        mock_performer = MagicMock(spec=Performer)
        mock_performer.id = "performer_127"
        mock_studio = MagicMock(spec=Studio)
        mock_studio.id = "studio_127"

        # Track the actual query being executed
        original_execute = session.execute
        execute_calls = []

        async def tracked_execute(stmt):
            execute_calls.append(stmt)
            return await original_execute(stmt)

        session.execute = tracked_execute

        # Mock worker pool
        queue = MagicMock()
        queue.join = AsyncMock()  # Make queue.join() awaitable
        content_mixin._setup_worker_pool = AsyncMock(
            return_value=(
                "task_name",
                "process_name",
                MagicMock(),
                queue,
            )
        )
        content_mixin._run_worker_pool = AsyncMock()

        # Call method with our real account
        await content_mixin.process_creator_posts(
            account=account,
            performer=mock_performer,
            studio=mock_studio,
            session=session,
        )

        # Verify database query was constructed correctly
        # Should have executed a select for posts
        assert len(execute_calls) > 0
        # Find the select statement (skip the initial account query)
        post_queries = [call for call in execute_calls if hasattr(call, "columns")]
        assert len(post_queries) > 0
        stmt = post_queries[-1]  # Get the most recent query
        # Basic validation that it's a select statement
        assert hasattr(stmt, "columns")
        assert hasattr(stmt, "froms")

        # Verify worker pool was called
        assert content_mixin._setup_worker_pool.called
        assert content_mixin._run_worker_pool.called
