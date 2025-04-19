"""Integration tests for stash processing module."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    context.get_client = AsyncMock()
    return context


@pytest.fixture
def mock_database():
    """Fixture for mock database."""
    database = MagicMock()
    mock_session = MagicMock()
    mock_account = MagicMock(spec=Account)
    mock_account.id = 12345
    mock_account.username = "test_user"
    mock_session.execute.return_value.scalar_one_or_none.return_value = mock_account
    database.session_scope.return_value.__enter__.return_value = mock_session
    database.async_session_scope.return_value.__aenter__.return_value = mock_session
    return database


@pytest.mark.asyncio
async def test_full_creator_processing_flow(
    mock_config, mock_state, mock_context, mock_database
):
    """Test the full flow of creator processing."""
    # Initialize processor
    processor = StashProcessing(
        config=mock_config,
        state=mock_state,
        context=mock_context,
        database=mock_database,
        _background_task=None,
        _cleanup_event=asyncio.Event(),
        _owns_db=False,
    )

    # Mock the account
    mock_account = MagicMock(spec=Account)
    mock_account.id = 12345
    mock_account.username = "test_user"

    # Mock the performer
    mock_performer = MagicMock(spec=Performer)
    mock_performer.id = "performer_123"
    mock_performer.name = "test_user"

    # Mock the studio
    mock_studio = MagicMock(spec=Studio)
    mock_studio.id = "studio_123"

    # Mock all the necessary methods
    processor._find_account = AsyncMock(return_value=mock_account)
    processor._find_existing_performer = AsyncMock(return_value=mock_performer)
    processor._update_performer_avatar = AsyncMock()
    processor.scan_creator_folder = AsyncMock()
    processor.process_creator_studio = AsyncMock(return_value=mock_studio)
    processor.process_creator_posts = AsyncMock()
    processor.process_creator_messages = AsyncMock()
    processor._safe_background_processing = AsyncMock()
    processor.process_creator = AsyncMock(return_value=(mock_account, mock_performer))

    # Mock database queries
    mock_database.session_scope.return_value.__enter__.return_value.execute.return_value.scalar_one_or_none.return_value = (
        mock_account
    )
    mock_database.async_session_scope.return_value.__aenter__.return_value.execute.return_value.scalar_one.return_value = (
        mock_account
    )

    # Mock process_creator as AsyncMock
    processor.process_creator = AsyncMock(return_value=(mock_account, mock_performer))

    # Execute the main method
    with patch("asyncio.get_running_loop") as mock_get_loop:
        mock_loop = MagicMock()
        mock_task = MagicMock()
        mock_loop.create_task.return_value = mock_task
        mock_get_loop.return_value = mock_loop

        # Start the processing
        await processor.start_creator_processing()

        # Verify the flow
        mock_context.get_client.assert_called_once()
        processor.scan_creator_folder.assert_called_once()
        processor.process_creator.assert_called_once()
        mock_loop.create_task.assert_called_once()
        assert processor._background_task == mock_task
        mock_config.get_background_tasks.return_value.append.assert_called_once_with(
            mock_task
        )


@pytest.mark.asyncio
async def test_process_creator_to_background(
    mock_config, mock_state, mock_context, mock_database
):
    """Test the flow from process_creator to background processing."""
    # Initialize processor
    processor = StashProcessing(
        config=mock_config,
        state=mock_state,
        context=mock_context,
        database=mock_database,
        _background_task=None,
        _cleanup_event=asyncio.Event(),
        _owns_db=False,
    )

    # Mock the account
    mock_account = MagicMock(spec=Account)
    mock_account.id = 12345
    mock_account.username = "test_user"
    mock_account.stash_id = None

    # Mock the performer
    mock_performer = MagicMock(spec=Performer)
    mock_performer.id = "performer_123"
    mock_performer.name = "test_user"

    # Mock the studio
    mock_studio = MagicMock(spec=Studio)
    mock_studio.id = "studio_123"

    # Mock account query
    mock_query_account = MagicMock(spec=Account)
    mock_query_account.id = 12345
    mock_query_account.username = "test_user"

    # Mock all the necessary methods
    processor._find_account = AsyncMock(return_value=mock_account)
    processor._find_existing_performer = AsyncMock(return_value=mock_performer)
    processor._update_performer_avatar = AsyncMock()
    processor._update_account_stash_id = AsyncMock()
    processor.process_creator_studio = AsyncMock(return_value=mock_studio)
    processor.process_creator_posts = AsyncMock()
    processor.process_creator_messages = AsyncMock()

    # Mock session scope
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute = AsyncMock()
    mock_session.execute.return_value.scalar_one = AsyncMock(
        return_value=mock_query_account
    )
    mock_session.refresh = AsyncMock()

    # Test the full process flow
    # 1. Process creator
    account, performer = await processor.process_creator()
    assert account == mock_account
    assert performer == mock_performer

    # 2. Continue stash processing
    await processor.continue_stash_processing(
        account=account,
        performer=performer,
        session=mock_session,
    )

    # Verify the methods were called as expected
    processor._update_account_stash_id.assert_called_once_with(
        account=mock_account,
        performer=mock_performer,
    )
    processor.process_creator_studio.assert_called_once_with(
        account=mock_query_account,
        performer=mock_performer,
        session=mock_session,
    )
    processor.process_creator_posts.assert_called_once_with(
        account=mock_query_account,
        performer=mock_performer,
        studio=mock_studio,
        session=mock_session,
    )
    mock_session.refresh.assert_called()
    processor.process_creator_messages.assert_called_once_with(
        account=mock_query_account,
        performer=mock_performer,
        studio=mock_studio,
        session=mock_session,
    )


@pytest.mark.asyncio
async def test_safe_background_processing_integration(
    mock_config, mock_state, mock_context, mock_database
):
    """Test the safe background processing with error handling."""
    # Initialize processor
    processor = StashProcessing(
        config=mock_config,
        state=mock_state,
        context=mock_context,
        database=mock_database,
        _background_task=None,
        _cleanup_event=asyncio.Event(),
        _owns_db=False,
    )

    # Mock the account and performer
    mock_account = MagicMock(spec=Account)
    mock_performer = MagicMock(spec=Performer)

    # Test cases for different scenarios
    test_cases = [
        # Successful processing
        {"side_effect": None, "exception": None},
        # Cancelled error
        {"side_effect": asyncio.CancelledError(), "exception": asyncio.CancelledError},
        # Other exception
        {"side_effect": ValueError("Test error"), "exception": ValueError},
    ]

    for case in test_cases:
        # Reset the cleanup event
        processor._cleanup_event.clear()

        # Mock continue_stash_processing
        processor.continue_stash_processing = AsyncMock(side_effect=case["side_effect"])

        # Patch logger directly in stash.processing namespace
        import stash.processing

        stash.processing.logger = logging.getLogger("stash.processing")

        with (
            patch("stash.processing.logger.debug") as mock_logger_debug,
            patch("stash.processing.logger.exception") as mock_logger_exception,
            patch("stash.processing.debug_print") as mock_debug_print,
        ):
            await processor._safe_background_processing(mock_account, mock_performer)

            # Verify the cleanup event was set
            assert processor._cleanup_event.is_set()

            # Verify appropriate logging happened
            if case["exception"] is None:
                # No exception expected
                await processor._safe_background_processing(
                    mock_account, mock_performer
                )
                assert processor._cleanup_event.is_set()
                processor.continue_stash_processing.assert_called_once_with(
                    mock_account, mock_performer
                )
            else:
                # Exception expected
                with (
                    pytest.raises(case["exception"]),
                    patch("stash.processing.logger.debug") as mock_logger_debug,
                    patch("stash.processing.logger.exception") as mock_logger_exception,
                    patch("stash.processing.debug_print") as mock_debug_print,
                ):
                    await processor._safe_background_processing(
                        mock_account, mock_performer
                    )

                    # Verify the cleanup event was set
                    assert processor._cleanup_event.is_set()

                    # Verify appropriate logging happened
                    if case["exception"] == asyncio.CancelledError:
                        mock_logger_debug.assert_called_once()
                        mock_debug_print.assert_called_once()
                    else:
                        mock_logger_exception.assert_called_once()
                        mock_debug_print.assert_called_once()
