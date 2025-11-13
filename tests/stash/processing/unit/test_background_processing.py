"""Unit tests for background processing methods.

Uses real database and factory objects, mocks only Stash API calls.
"""

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy import select

from config.fanslyconfig import FanslyConfig
from download.downloadstate import DownloadState
from metadata import Account
from stash.context import StashContext
from stash.processing import StashProcessing
from tests.fixtures.metadata.metadata_factories import AccountFactory
from tests.fixtures.stash.stash_type_factories import PerformerFactory, StudioFactory


@pytest.fixture
async def processor(
    config: FanslyConfig,
    download_state: DownloadState,
) -> AsyncGenerator[StashProcessing, None]:
    """Create StashProcessing with REAL database, mock Stash API only."""
    # Configure with real database
    context = StashContext()
    context._client = AsyncMock()  # Mock Stash API client
    config.get_stash_context = Mock(return_value=context)

    with (
        patch("stash.processing.base.print_info"),
        patch("stash.processing.base.print_warning"),
    ):
        processor = StashProcessing.from_config(config, download_state, True)
        processor.context = context
        # Ensure database is set from config (needed for _update_account_stash_id)
        processor.database = config._database
        yield processor
        await processor.cleanup()


class TestBackgroundProcessing:
    """Test the background processing methods of StashProcessing."""

    @pytest.mark.asyncio
    async def test_safe_background_processing(
        self, processor, mock_account, mock_performer, session
    ):
        processor.continue_stash_processing = AsyncMock()
        """Test _safe_background_processing method."""
        # Case 1: Successful processing
        await processor._safe_background_processing(mock_account, mock_performer)

        # Verify methods were called
        processor.continue_stash_processing.assert_called_once_with(
            mock_account, mock_performer
        )
        assert processor._cleanup_event.is_set()

        # Case 2: Cancelled
        processor.continue_stash_processing.reset_mock()
        processor._cleanup_event.clear()
        processor.continue_stash_processing.side_effect = asyncio.CancelledError()

        # Call _safe_background_processing and expect CancelledError
        with (
            pytest.raises(asyncio.CancelledError),
            patch("stash.processing.base.logger.debug") as mock_logger_debug,
            patch("stash.processing.base.debug_print") as mock_debug_print,
        ):
            await processor._safe_background_processing(mock_account, mock_performer)

        # Verify logging
        mock_logger_debug.assert_called_once()
        assert "cancelled" in str(mock_logger_debug.call_args)
        mock_debug_print.assert_called_once()
        assert "background_task_cancelled" in str(mock_debug_print.call_args)
        assert processor._cleanup_event.is_set()

        # Case 3: Other exception
        processor.continue_stash_processing.reset_mock()
        processor._cleanup_event.clear()
        processor.continue_stash_processing.side_effect = Exception("Test error")

        # Call _safe_background_processing and expect Exception
        with (
            pytest.raises(Exception),  # noqa: PT011, B017
            patch("stash.processing.base.logger.exception") as mock_logger_exception,
            patch("stash.processing.base.debug_print") as mock_debug_print,
        ):
            await processor._safe_background_processing(mock_account, mock_performer)

        # Verify logging
        mock_logger_exception.assert_called_once()
        assert "Background task failed" in str(mock_logger_exception.call_args)
        mock_debug_print.assert_called_once()
        assert "background_task_failed" in str(mock_debug_print.call_args)
        assert processor._cleanup_event.is_set()

    @pytest.mark.asyncio
    async def test_continue_stash_processing(
        self, factory_async_session, processor, mock_performer, session
    ):
        """Test continue_stash_processing orchestration with real DB, mock Stash API."""
        # Create real account using factory and persist to database
        # Set stash_id to match mock_performer.id (must be int, not str)
        account = AccountFactory(
            id=12345,
            username="test_user",
            displayName="Test User",
            stash_id=123,  # Must be int, not str
        )
        factory_async_session.commit()

        # Query fresh account from async session
        result = await session.execute(select(Account).where(Account.id == 12345))
        account = result.scalar_one()

        # Explicitly set mock_performer.id to match account.stash_id to avoid update
        mock_performer.id = account.stash_id

        # Mock only external Stash API calls
        from stash.types import FindStudiosResultType

        processor.context.client.find_studios = AsyncMock(
            return_value=FindStudiosResultType(count=0, studios=[])
        )
        processor.context.client.create_studio = AsyncMock(
            return_value=StudioFactory(id="123", name="Test Studio")
        )
        processor.context.client.find_galleries = AsyncMock(
            return_value={"count": 0, "galleries": []}
        )

        # Mock the processing methods to test orchestration
        processor.process_creator_studio = AsyncMock(
            return_value=StudioFactory(id="studio_123", name="Test Studio")
        )
        processor.process_creator_posts = AsyncMock()
        processor.process_creator_messages = AsyncMock()

        # Case 1: Successful processing
        await processor.continue_stash_processing(
            account, mock_performer, session=session
        )

        # Verify orchestration methods were called
        processor.process_creator_studio.assert_called_once_with(
            account=account,
            performer=mock_performer,
            session=session,
        )
        processor.process_creator_posts.assert_called_once()
        processor.process_creator_messages.assert_called_once()

        # Case 2: Different stash_ids - should call _update_account_stash_id
        processor.process_creator_studio.reset_mock()
        processor.process_creator_posts.reset_mock()
        processor.process_creator_messages.reset_mock()

        account.stash_id = None
        mock_performer.id = "123"

        # Mock _update_account_stash_id to verify it's called
        with patch.object(processor, "_update_account_stash_id", AsyncMock()):
            await processor.continue_stash_processing(
                account, mock_performer, session=session
            )

            # Verify _update_account_stash_id was called
            processor._update_account_stash_id.assert_called_once()

        # Case 3: No account or performer
        # Note: raises AttributeError in finally block when performer is None
        with pytest.raises(AttributeError):
            await processor.continue_stash_processing(None, None, session=session)

        # Case 4: performer as dict
        processor.process_creator_studio.reset_mock()
        processor.process_creator_posts.reset_mock()
        processor.process_creator_messages.reset_mock()

        # Reset account.stash_id to match performer (was set to None in Case 2)
        account.stash_id = 123

        performer_dict = {"id": 123, "name": "test_user"}
        performer_from_dict = PerformerFactory(id=123, name="test_user")

        with patch(
            "stash.types.Performer.from_dict", return_value=performer_from_dict
        ) as mock_from_dict:
            await processor.continue_stash_processing(
                account, performer_dict, session=session
            )

            # Verify Performer.from_dict was called
            mock_from_dict.assert_called_once_with(performer_dict)
            # Verify processing continued with converted performer
            processor.process_creator_studio.assert_called_once()

        # Case 5: Invalid performer type
        # Note: raises TypeError but then AttributeError in finally block
        with pytest.raises(AttributeError):
            await processor.continue_stash_processing(
                account, "invalid", session=session
            )

        # Case 6: Invalid account type
        # Note: raises AttributeError when accessing account.id
        with pytest.raises(AttributeError):
            # Pass a string instead of Account object
            await processor.continue_stash_processing(
                "invalid", mock_performer, session=session
            )
