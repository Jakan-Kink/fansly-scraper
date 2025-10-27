"""Unit tests for background processing methods."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from download.core import DownloadState
from metadata import Account
from stash.context import StashContext
from stash.processing import StashProcessing
from stash.types import Performer, Studio


@pytest.fixture
def mock_config():
    """Fixture for mock configuration."""
    config = MagicMock()
    config.get_stash_context.return_value = MagicMock(spec=StashContext)
    config.stash_context_conn = {"url": "http://test.com", "api_key": "test_key"}
    config._database = MagicMock()
    config.get_background_tasks.return_value = []
    return config


@pytest.fixture
def mock_state():
    """Fixture for mock download state."""
    state = MagicMock(spec=DownloadState)
    state.creator_id = "12345"
    state.creator_name = "test_user"
    state.download_path = MagicMock()
    state.download_path.is_dir.return_value = True
    state.base_path = MagicMock()
    return state


@pytest.fixture
def mock_context():
    """Fixture for mock stash context."""
    context = MagicMock(spec=StashContext)
    context.client = MagicMock()
    return context


@pytest.fixture
def mock_database():
    """Fixture for mock database."""
    database = MagicMock()
    database.session_scope.return_value.__enter__.return_value = MagicMock(spec=Session)
    database.async_session_scope.return_value.__aenter__.return_value = AsyncMock(
        spec=AsyncSession
    )
    return database


@pytest.fixture
def mock_account():
    """Fixture for mock account."""
    account = MagicMock(spec=Account)
    account.id = 12345
    account.username = "test_user"
    account.stash_id = "stash_123"
    return account


@pytest.fixture
def mock_performer():
    """Fixture for mock performer."""
    performer = MagicMock(spec=Performer)
    performer.id = "performer_123"
    performer.name = "test_user"
    return performer


@pytest.fixture
def processor(mock_config, mock_state, mock_context, mock_database):
    """Fixture for stash processor instance."""
    processor = StashProcessing(
        config=mock_config,
        state=mock_state,
        context=mock_context,
        database=mock_database,
        _background_task=None,
        _cleanup_event=asyncio.Event(),
        _owns_db=False,
    )
    return processor


class TestBackgroundProcessing:
    """Test the background processing methods of StashProcessing."""

    @pytest.mark.asyncio
    async def test_safe_background_processing(
        self, processor, mock_account, mock_performer
    ):
        """Test _safe_background_processing method."""
        # Mock continue_stash_processing
        processor.continue_stash_processing = AsyncMock()

        # Case 1: Successful processing
        # Call _safe_background_processing
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
            patch("stash.processing.logger.debug") as mock_logger_debug,
            patch("stash.processing.debug_print") as mock_debug_print,
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
            patch("stash.processing.logger.exception") as mock_logger_exception,
            patch("stash.processing.debug_print") as mock_debug_print,
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
        self, processor, mock_account, mock_performer, mock_database
    ):
        """Test continue_stash_processing method."""
        # Mock process_creator_studio and process_creator_posts
        mock_studio = MagicMock(spec=Studio)
        processor.process_creator_studio = AsyncMock(return_value=mock_studio)
        processor.process_creator_posts = AsyncMock()
        processor.process_creator_messages = AsyncMock()

        # Mock session
        mock_session = MagicMock(spec=AsyncSession)
        mock_session.execute.return_value.scalar_one.return_value = mock_account
        mock_session.refresh = AsyncMock()

        # Mock _update_account_stash_id
        processor._update_account_stash_id = AsyncMock()

        # Call continue_stash_processing
        await processor.continue_stash_processing(
            mock_account, mock_performer, session=mock_session
        )

        # Verify methods were called
        mock_session.execute.assert_called_once()
        assert "12345" in str(mock_session.execute.call_args)
        processor.process_creator_studio.assert_called_once_with(
            account=mock_account,
            performer=mock_performer,
            session=mock_session,
        )
        processor.process_creator_posts.assert_called_once_with(
            account=mock_account,
            performer=mock_performer,
            studio=mock_studio,
            session=mock_session,
        )
        mock_session.refresh.assert_called()
        processor.process_creator_messages.assert_called_once_with(
            account=mock_account,
            performer=mock_performer,
            studio=mock_studio,
            session=mock_session,
        )

        # Case 2: Different stash_ids
        mock_session.reset_mock()
        processor.process_creator_studio.reset_mock()
        processor.process_creator_posts.reset_mock()
        processor.process_creator_messages.reset_mock()
        processor._update_account_stash_id.reset_mock()

        mock_account.stash_id = None
        mock_performer.id = "performer_123"

        # Call continue_stash_processing
        await processor.continue_stash_processing(
            mock_account, mock_performer, session=mock_session
        )

        # Verify _update_account_stash_id was called
        processor._update_account_stash_id.assert_called_once_with(
            account=mock_account,
            performer=mock_performer,
        )

        # Case 3: No account or performer
        mock_session.reset_mock()
        mock_session.execute.return_value.scalar_one.side_effect = [mock_account]
        processor.process_creator_studio.reset_mock()
        processor.process_creator_posts.reset_mock()
        processor.process_creator_messages.reset_mock()
        processor._update_account_stash_id.reset_mock()

        # Call continue_stash_processing with None values
        with pytest.raises(ValueError, match=r"Account.*performer"):
            await processor.continue_stash_processing(None, None, session=mock_session)

        # Case 4: performer as dict
        mock_session.reset_mock()
        mock_session.execute.return_value.scalar_one.side_effect = [mock_account]
        processor.process_creator_studio.reset_mock()
        processor.process_creator_posts.reset_mock()
        processor.process_creator_messages.reset_mock()
        processor._update_account_stash_id.reset_mock()

        # Create performer dict
        performer_dict = {"id": "performer_123", "name": "test_user"}

        # Mock Performer.from_dict
        mock_performer_from_dict = MagicMock(spec=Performer)
        mock_performer_from_dict.id = "performer_123"
        with patch(
            "stash.types.Performer.from_dict", return_value=mock_performer_from_dict
        ) as mock_from_dict:
            # Call continue_stash_processing with dict
            await processor.continue_stash_processing(
                mock_account, performer_dict, session=mock_session
            )

            # Verify Performer.from_dict was called
            mock_from_dict.assert_called_once_with(performer_dict)

            # Verify other methods were called with converted performer
            processor.process_creator_studio.assert_called_once()
            assert (
                processor.process_creator_studio.call_args[1]["performer"]
                == mock_performer_from_dict
            )

        # Case 5: Invalid performer type
        mock_session.reset_mock()
        mock_session.execute.return_value.scalar_one.side_effect = [mock_account]

        # Call continue_stash_processing with invalid performer type
        with pytest.raises(TypeError):
            await processor.continue_stash_processing(
                mock_account, "invalid", session=mock_session
            )

        # Case 6: Invalid account type
        mock_session.reset_mock()
        mock_session.execute.return_value.scalar_one.return_value = "invalid"

        # Call continue_stash_processing with invalid account type
        with pytest.raises(TypeError):
            await processor.continue_stash_processing(
                mock_account, mock_performer, session=mock_session
            )
