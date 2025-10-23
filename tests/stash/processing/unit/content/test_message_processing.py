"""Tests for message processing methods in ContentProcessingMixin."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import insert, select
from sqlalchemy.orm import selectinload

from metadata import Account, Group, Message
from metadata.attachment import ContentType
from metadata.messages import group_users
from stash.types import Performer, Studio
from tests.fixtures import (
    AccountFactory,
    AttachmentFactory,
    GroupFactory,
    MessageFactory,
)


class TestMessageProcessing:
    """Test message processing methods in ContentProcessingMixin."""

    @pytest.mark.asyncio
    async def test_process_creator_messages(
        self,
        factory_async_session,
        session,
        content_mixin,
    ):
        """Test process_creator_messages method."""
        # Create real account, group and messages with factories
        account = AccountFactory(id=12345, username="test_user")
        group = GroupFactory(id=40001, createdBy=12345)

        # Link account to group using direct SQL (avoid lazy loading)
        await session.execute(
            insert(group_users).values(accountId=12345, groupId=40001)
        )
        await session.flush()

        # Create 3 messages with attachments (required for query to find them)
        for i in range(3):
            message = MessageFactory(
                id=400 + i,
                groupId=40001,
                senderId=12345,
                content=f"Test message {i}",
            )
            # Create attachment for each message (process_creator_messages queries messages WITH attachments)
            # messageId links attachment to message, contentId points to the media
            AttachmentFactory(
                messageId=400 + i,  # Link to message
                contentId=400 + i,  # Just a dummy value
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=0,
            )

        # Query fresh account and messages from async session (factory_async_session handles sync)
        result = await session.execute(select(Account).where(Account.id == 12345))
        account = result.scalar_one()

        result = await session.execute(
            select(Message)
            .where(Message.senderId == 12345)
            .options(selectinload(Message.attachments), selectinload(Message.group))
        )
        messages = list(result.unique().scalars().all())

        # Ensure messages were created
        assert len(messages) == 3, f"Expected 3 messages, got {len(messages)}"

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

        content_mixin._setup_worker_pool = AsyncMock(
            return_value=(
                task_name,
                process_name,
                semaphore,
                queue,
            )
        )

        # Ensure _process_items_with_gallery is properly mocked
        if not isinstance(content_mixin._process_items_with_gallery, AsyncMock):
            content_mixin._process_items_with_gallery = AsyncMock()

        # Call method
        await content_mixin.process_creator_messages(
            account=account,
            performer=mock_performer,
            studio=mock_studio,
            session=session,
        )

        # Verify worker pool was setup with correct messages
        content_mixin._setup_worker_pool.assert_called_once()
        call_args = content_mixin._setup_worker_pool.call_args
        assert len(call_args[0][0]) == 3  # 3 messages
        assert call_args[0][1] == "message"  # item_type

        # Verify batch processor was run
        content_mixin._run_worker_pool.assert_called_once()
        run_args = content_mixin._run_worker_pool.call_args[1]
        assert run_args["batch_size"] == 25  # Default batch size

        # Verify process_item is callable
        assert callable(run_args["process_item"])

    @pytest.mark.asyncio
    async def test_process_creator_messages_error_handling(
        self,
        factory_async_session,
        session,
        content_mixin,
    ):
        """Test process_creator_messages method with error handling."""
        # Create real account, group and messages with factories
        account = AccountFactory(id=12346, username="test_user_2")
        group = GroupFactory(id=40002, createdBy=12346)

        # Link account to group using direct SQL
        await session.execute(
            insert(group_users).values(accountId=12346, groupId=40002)
        )
        await session.flush()

        # Create 2 messages with attachments
        for i in range(2):
            message = MessageFactory(
                id=500 + i,
                groupId=40002,
                senderId=12346,
                content=f"Test message {i}",
            )
            AttachmentFactory(
                messageId=500 + i,
                contentId=500 + i,
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=0,
            )

        # Query fresh account and messages
        result = await session.execute(select(Account).where(Account.id == 12346))
        account = result.scalar_one()

        # Create mock Performer and Studio
        mock_performer = MagicMock(spec=Performer)
        mock_performer.id = "performer_124"
        mock_studio = MagicMock(spec=Studio)
        mock_studio.id = "studio_124"

        # Setup worker pool
        content_mixin._setup_worker_pool = AsyncMock(
            return_value=(
                "task_name",
                "process_name",
                MagicMock(),
                MagicMock(),
            )
        )

        # Setup _process_items_with_gallery to raise exception on first call
        content_mixin._process_items_with_gallery = AsyncMock(
            side_effect=[
                Exception("Test error"),  # First call fails
                None,  # Second call succeeds
            ]
        )

        # Call method - should not raise exception despite error
        await content_mixin.process_creator_messages(
            account=account,
            performer=mock_performer,
            studio=mock_studio,
            session=session,
        )

        # Verify _run_worker_pool was called (error handling happens inside worker)
        content_mixin._run_worker_pool.assert_called_once()

    @pytest.mark.asyncio
    async def test_database_query_structure(
        self,
        factory_async_session,
        session,
        content_mixin,
    ):
        """Test the database query structure in process_creator_messages."""
        # Create real account, group and messages
        account = AccountFactory(id=12347, username="test_user_3")
        group = GroupFactory(id=40003, createdBy=12347)

        await session.execute(
            insert(group_users).values(accountId=12347, groupId=40003)
        )
        await session.flush()

        # Create 1 message with attachment
        message = MessageFactory(
            id=600,
            groupId=40003,
            senderId=12347,
            content="Test message",
        )
        AttachmentFactory(
            messageId=600,
            contentId=600,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )

        # Query fresh account
        result = await session.execute(select(Account).where(Account.id == 12347))
        account = result.scalar_one()

        # Mock Performer and Studio
        mock_performer = MagicMock(spec=Performer)
        mock_performer.id = "performer_125"
        mock_studio = MagicMock(spec=Studio)
        mock_studio.id = "studio_125"

        # Setup worker pool
        content_mixin._setup_worker_pool = AsyncMock(
            return_value=(
                "task_name",
                "process_name",
                MagicMock(),
                MagicMock(),
            )
        )
        content_mixin._process_items_with_gallery = AsyncMock()

        # Call method
        await content_mixin.process_creator_messages(
            account=account,
            performer=mock_performer,
            studio=mock_studio,
            session=session,
        )

        # Verify batch processor was called (validates query executed successfully)
        content_mixin._run_worker_pool.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_processing_setup(
        self,
        factory_async_session,
        session,
        content_mixin,
    ):
        """Test the batch processing setup in process_creator_messages."""
        # Create real account, group and messages
        account = AccountFactory(id=12348, username="test_user_4")
        group = GroupFactory(id=40004, createdBy=12348)

        await session.execute(
            insert(group_users).values(accountId=12348, groupId=40004)
        )
        await session.flush()

        # Create 2 messages
        for i in range(2):
            message = MessageFactory(
                id=700 + i,
                groupId=40004,
                senderId=12348,
                content=f"Test message {i}",
            )
            AttachmentFactory(
                messageId=700 + i,
                contentId=700 + i,
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=0,
            )

        # Query fresh account
        result = await session.execute(select(Account).where(Account.id == 12348))
        account = result.scalar_one()

        # Mock Performer and Studio
        mock_performer = MagicMock(spec=Performer)
        mock_performer.id = "performer_126"
        mock_studio = MagicMock(spec=Studio)
        mock_studio.id = "studio_126"

        # Setup worker pool
        content_mixin._setup_worker_pool = AsyncMock(
            return_value=(
                "task_name",
                "process_name",
                MagicMock(),
                MagicMock(),
            )
        )
        content_mixin._process_items_with_gallery = AsyncMock()

        # Call method
        await content_mixin.process_creator_messages(
            account=account,
            performer=mock_performer,
            studio=mock_studio,
            session=session,
        )

        # Verify batch processing was setup correctly
        content_mixin._setup_worker_pool.assert_called_once()
        call_args = content_mixin._setup_worker_pool.call_args
        assert call_args[0][1] == "message"  # item_type

        # Verify batch processor was run with correct parameters
        content_mixin._run_worker_pool.assert_called_once()
        run_args = content_mixin._run_worker_pool.call_args[1]
        assert run_args["batch_size"] == 25
        assert callable(run_args["process_item"])
