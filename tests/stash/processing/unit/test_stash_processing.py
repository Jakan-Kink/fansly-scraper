"""Unit tests for stash processing module - core functionality."""

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from download.core import DownloadState
from metadata import Account
from stash.client import StashClient
from stash.context import StashContext
from stash.processing import StashProcessing
from stash.types import Image, Performer, Studio


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
    context.client = MagicMock(spec=StashClient)
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


class TestStashProcessingBasics:
    """Test the basic functionality of StashProcessing class."""

    def test_init(self, mock_config, mock_state, mock_context, mock_database):
        """Test initialization of StashProcessing."""
        # Create without background task
        processor = StashProcessing(
            config=mock_config,
            state=mock_state,
            context=mock_context,
            database=mock_database,
            _background_task=None,
            _cleanup_event=None,
            _owns_db=False,
        )

        # Verify attributes
        assert processor.config == mock_config
        assert processor.state == mock_state
        assert processor.context == mock_context
        assert processor.database == mock_database
        assert processor._background_task is None
        assert not processor._cleanup_event.is_set()
        assert not processor._owns_db
        assert isinstance(processor.log, logging.Logger)

        # Create with background task
        mock_task = MagicMock()
        processor = StashProcessing(
            config=mock_config,
            state=mock_state,
            context=mock_context,
            database=mock_database,
            _background_task=mock_task,
            _cleanup_event=None,
            _owns_db=True,
        )

        # Verify attributes
        assert processor._background_task == mock_task
        assert not processor._cleanup_event.is_set()
        assert processor._owns_db

    def test_from_config(self, mock_config, mock_state):
        """Test creating processor from config."""
        # Mock get_stash_context
        mock_context = MagicMock(spec=StashContext)
        mock_config.get_stash_context.return_value = mock_context

        # Call from_config
        processor = StashProcessing.from_config(
            config=mock_config,
            state=mock_state,
        )

        # Verify processor
        assert processor.config == mock_config
        assert processor.state is not mock_state  # Should be a copy
        assert processor.state.creator_id == mock_state.creator_id
        assert processor.state.creator_name == mock_state.creator_name
        assert processor.context == mock_context
        assert processor.database == mock_config._database
        assert processor._background_task is None
        assert not processor._cleanup_event.is_set()
        assert not processor._owns_db


class TestStashProcessingAccount:
    """Test the account-related methods of StashProcessing."""

    @pytest.mark.asyncio
    async def test_find_account(self, processor, mock_account):
        """Test _find_account method."""
        # Mock session and execute
        mock_session = MagicMock(spec=Session)
        mock_session.execute.return_value.scalar_one_or_none.return_value = mock_account

        # Call _find_account
        account = await processor._find_account(session=mock_session)

        # Verify account and session.execute was called
        assert account == mock_account
        mock_session.execute.assert_called_once()

        # Test with no account found
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        # Call _find_account
        with patch("stash.processing.print_warning") as mock_print_warning:
            account = await processor._find_account(session=mock_session)

        # Verify no account and warning was printed
        assert account is None
        mock_print_warning.assert_called_once()
        assert processor.state.creator_name in str(mock_print_warning.call_args)

    @pytest.mark.asyncio
    async def test_update_account_stash_id(
        self, processor, mock_account, mock_performer
    ):
        """Test _update_account_stash_id method."""
        # Create mock session
        mock_session = MagicMock(spec=AsyncSession)
        mock_session.execute.return_value.scalar_one.return_value = mock_account
        mock_session.flush = AsyncMock()

        # Call _update_account_stash_id
        await processor._update_account_stash_id(
            mock_account, mock_performer, session=mock_session
        )

        # Verify session operations
        mock_session.execute.assert_called_once()
        assert "12345" in str(mock_session.execute.call_args)
        assert mock_account.stash_id == mock_performer.id
        mock_session.add.assert_called_once_with(mock_account)
        mock_session.flush.assert_called_once()


class TestStashProcessingPerformer:
    """Test the performer-related methods of StashProcessing."""

    @pytest.mark.asyncio
    async def test_find_existing_performer(self, processor, mock_account):
        """Test _find_existing_performer method."""
        # Mock context.client.find_performer
        mock_performer = MagicMock(spec=Performer)
        processor.context.client.find_performer = AsyncMock(return_value=mock_performer)

        # Case 1: Account has stash_id
        mock_account.stash_id = "stash_123"

        # Call _find_existing_performer
        performer = await processor._find_existing_performer(mock_account)

        # Verify performer and find_performer was called with stash_id
        assert performer == mock_performer
        processor.context.client.find_performer.assert_called_once_with("stash_123")

        # Case 2: Account has no stash_id
        processor.context.client.find_performer.reset_mock()
        mock_account.stash_id = None

        # Call _find_existing_performer
        performer = await processor._find_existing_performer(mock_account)

        # Verify performer and find_performer was called with username
        assert performer == mock_performer
        processor.context.client.find_performer.assert_called_once_with(
            mock_account.username
        )

        # Case 3: find_performer returns None
        processor.context.client.find_performer.reset_mock()
        processor.context.client.find_performer.return_value = None

        # Call _find_existing_performer
        performer = await processor._find_existing_performer(mock_account)

        # Verify performer is None
        assert performer is None
        processor.context.client.find_performer.assert_called_once_with(
            mock_account.username
        )

        # Case 4: find_performer returns a coroutine
        processor.context.client.find_performer.reset_mock()

        # Create a coroutine that returns mock_performer
        async def mock_coroutine():
            return mock_performer

        processor.context.client.find_performer.return_value = mock_coroutine()

        # Call _find_existing_performer
        performer = await processor._find_existing_performer(mock_account)

        # Verify performer
        assert performer == mock_performer

    @pytest.mark.asyncio
    async def test_update_performer_avatar(
        self, processor, mock_account, mock_performer
    ):
        """Test _update_performer_avatar method."""
        # Mock account with no avatar
        mock_account.awaitable_attrs = MagicMock()
        mock_account.awaitable_attrs.avatar = AsyncMock(return_value=None)

        # Call _update_performer_avatar
        await processor._update_performer_avatar(mock_account, mock_performer)

        # Verify no avatar update was attempted
        assert not mock_performer.update_avatar.called

        # Mock account with avatar but no local_filename
        mock_avatar = MagicMock()
        mock_avatar.local_filename = None
        mock_account.awaitable_attrs.avatar = AsyncMock(return_value=mock_avatar)

        # Call _update_performer_avatar
        await processor._update_performer_avatar(mock_account, mock_performer)

        # Verify no avatar update was attempted
        assert not mock_performer.update_avatar.called

        # Mock account with avatar and local_filename
        mock_avatar.local_filename = "avatar.jpg"
        mock_account.awaitable_attrs.avatar = AsyncMock(return_value=mock_avatar)

        # Mock performer with default image
        mock_performer.image_path = "default=true"

        # Mock client.find_images
        mock_image = MagicMock(spec=Image)
        mock_image.visual_files = [MagicMock()]
        mock_image.visual_files[0].path = "path/to/avatar.jpg"

        mock_image_result = MagicMock()
        mock_image_result.count = 1
        mock_image_result.images = [mock_image]

        processor.context.client.find_images = AsyncMock(return_value=mock_image_result)

        # Mock performer.update_avatar
        mock_performer.update_avatar = AsyncMock()

        # Call _update_performer_avatar
        await processor._update_performer_avatar(mock_account, mock_performer)

        # Verify avatar update was attempted
        processor.context.client.find_images.assert_called_once()
        assert "avatar.jpg" in str(processor.context.client.find_images.call_args)
        mock_performer.update_avatar.assert_called_once()
        assert "path/to/avatar.jpg" in str(mock_performer.update_avatar.call_args)

        # Test with no images found
        processor.context.client.find_images.reset_mock()
        mock_image_result.count = 0
        processor.context.client.find_images.return_value = mock_image_result
        mock_performer.update_avatar.reset_mock()

        # Call _update_performer_avatar
        await processor._update_performer_avatar(mock_account, mock_performer)

        # Verify no avatar update was attempted
        processor.context.client.find_images.assert_called_once()
        assert not mock_performer.update_avatar.called

        # Test with update_avatar raising exception
        processor.context.client.find_images.reset_mock()
        mock_image_result.count = 1
        processor.context.client.find_images.return_value = mock_image_result
        mock_performer.update_avatar.reset_mock()
        mock_performer.update_avatar.side_effect = Exception("Test error")

        # Mock print_error and logger
        with (
            patch("stash.processing.print_error") as mock_print_error,
            patch("stash.processing.logger.exception") as mock_logger_exception,
            patch("stash.processing.debug_print") as mock_debug_print,
        ):
            # Call _update_performer_avatar
            await processor._update_performer_avatar(mock_account, mock_performer)

            # Verify error handling
            mock_print_error.assert_called_once()
            assert "Failed to update performer avatar" in str(
                mock_print_error.call_args
            )
            mock_logger_exception.assert_called_once()
            mock_debug_print.assert_called_once()
            assert "avatar_update_failed" in str(mock_debug_print.call_args)
