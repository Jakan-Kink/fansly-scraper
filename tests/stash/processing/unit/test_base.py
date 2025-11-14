"""Unit tests for StashProcessingBase class."""

import asyncio
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stash.context import StashContext
from stash.processing.base import StashProcessingBase
from tests.fixtures.core import FanslyConfigFactory
from tests.fixtures.download import DownloadStateFactory


@pytest.fixture
def mock_config(uuid_test_db_factory):
    """Fixture for FanslyConfig with real database and StashContext setup."""
    # Use the real config fixture with database
    config = uuid_test_db_factory

    # Configure Stash connection (won't actually connect unless used)
    config.stash_context_conn = {
        "scheme": "http",
        "host": "localhost",
        "port": 9999,
        "apikey": "test_api_key",
    }

    # _background_tasks already exists as field with default_factory=list
    # No need to mock get_background_tasks() - tests can use config._background_tasks directly

    return config


@pytest.fixture
def mock_state(tmp_path):
    """Fixture for download state using real DownloadStateFactory with real paths."""
    # Create real temporary paths
    base_path = tmp_path / "downloads"
    download_path = base_path / "test_user_fansly"
    download_path.mkdir(parents=True)

    # Use factory with real paths
    return DownloadStateFactory(
        creator_id="12345",
        creator_name="test_user",
        base_path=base_path,
        download_path=download_path,
    )


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
    return database


@pytest.fixture
def base_processor(mock_config, test_state, mock_context, mock_database):
    """Fixture for base processor instance with mocked abstract methods."""

    class TestProcessor(StashProcessingBase):
        """Test implementation of StashProcessingBase with mocked abstract methods."""

        async def continue_stash_processing(self, account, performer, session=None):
            """Mock implementation."""

        async def process_creator(self, session=None):
            """Mock implementation."""
            return MagicMock(), MagicMock()

    processor = TestProcessor(
        config=mock_config,
        state=test_state,
        context=mock_context,
        database=mock_database,
        _background_task=None,
        _cleanup_event=asyncio.Event(),
        _owns_db=False,
    )
    return processor


class TestStashProcessingBase:
    """Test the basic functionality of StashProcessingBase class."""

    def test_init(self, mock_config, test_state, mock_context, mock_database):
        """Test initialization of StashProcessingBase."""

        # Mock abstract methods for testing
        class TestProcessor(StashProcessingBase):
            """Test implementation with mocked methods."""

            async def continue_stash_processing(self, account, performer, session=None):
                pass

            async def process_creator(self, session=None):
                return None, None

        # Create without background task
        processor = TestProcessor(
            config=mock_config,
            state=test_state,
            context=mock_context,
            database=mock_database,
            _background_task=None,
            _cleanup_event=None,
            _owns_db=False,
        )

        # Verify attributes
        assert processor.config == mock_config
        assert processor.state == test_state
        assert processor.context == mock_context
        assert processor.database == mock_database
        assert processor._background_task is None
        assert not processor._cleanup_event.is_set()
        assert not processor._owns_db
        assert isinstance(processor.log, logging.Logger)

        # Create with background task
        mock_task = MagicMock()
        processor = TestProcessor(
            config=mock_config,
            state=test_state,
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

        # Mock abstract methods for testing
        class TestProcessor(StashProcessingBase):
            """Test implementation with mocked methods."""

            async def continue_stash_processing(self, account, performer, session=None):
                pass

            async def process_creator(self, session=None):
                return None, None

        # Mock get_stash_context
        mock_context = MagicMock(spec=StashContext)
        mock_config.get_stash_context.return_value = mock_context

        # Call from_config - use_batch_processing is handled in derived classes only
        processor = TestProcessor.from_config(
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

    @pytest.mark.asyncio
    async def test_scan_creator_folder(self, base_processor):
        """Test scan_creator_folder method."""
        # Mock client.metadata_scan and wait_for_job
        base_processor.context.client.metadata_scan = AsyncMock(return_value="job_123")
        base_processor.context.client.wait_for_job = AsyncMock(return_value=True)

        # Call the method
        await base_processor.scan_creator_folder()

        # Verify the calls
        base_processor.context.client.metadata_scan.assert_called_once()
        assert str(base_processor.state.base_path) in str(
            base_processor.context.client.metadata_scan.call_args
        )
        assert "scanGenerateCovers" in str(
            base_processor.context.client.metadata_scan.call_args
        )
        base_processor.context.client.wait_for_job.assert_called_once_with("job_123")

        # Test error in metadata_scan
        base_processor.context.client.metadata_scan.side_effect = RuntimeError(
            "Test error"
        )

        # Call the method and verify the error is propagated
        with pytest.raises(RuntimeError) as excinfo:
            await base_processor.scan_creator_folder()

        assert "Failed to process metadata" in str(excinfo.value)
        assert "Test error" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_start_creator_processing(self, base_processor):
        """Test start_creator_processing method."""
        # Mock methods
        base_processor.scan_creator_folder = AsyncMock()
        base_processor.process_creator = AsyncMock(
            return_value=(MagicMock(), MagicMock())
        )
        base_processor._safe_background_processing = AsyncMock()

        # Mock asyncio.get_running_loop
        mock_loop = MagicMock()
        mock_task = MagicMock()
        mock_loop.create_task.return_value = mock_task

        with patch("asyncio.get_running_loop", return_value=mock_loop):
            # Call method
            await base_processor.start_creator_processing()

            # Verify calls
            base_processor.context.get_client.assert_called_once()
            base_processor.scan_creator_folder.assert_called_once()
            base_processor.process_creator.assert_called_once()
            mock_loop.create_task.assert_called_once()
            assert base_processor._background_task == mock_task
            # Verify task was added to background tasks list
            assert mock_task in base_processor.config._background_tasks

        # Test with no StashContext configured
        base_processor.config.stash_context_conn = None
        with patch("stash.processing.base.print_warning") as mock_print_warning:
            # Call method
            await base_processor.start_creator_processing()

            # Verify warning was printed
            mock_print_warning.assert_called_once()
            assert "StashContext is not configured" in str(mock_print_warning.call_args)

    @pytest.mark.asyncio
    async def test_safe_background_processing(self, base_processor):
        """Test _safe_background_processing method."""
        # Mock methods
        base_processor.continue_stash_processing = AsyncMock()

        # Create mock account and performer
        mock_account = MagicMock()
        mock_performer = MagicMock()

        # Reset cleanup event
        base_processor._cleanup_event.clear()

        # Call the method
        await base_processor._safe_background_processing(mock_account, mock_performer)

        # Verify calls
        base_processor.continue_stash_processing.assert_called_once_with(
            mock_account, mock_performer
        )
        assert base_processor._cleanup_event.is_set()

        # Test exception handling
        base_processor._cleanup_event.clear()
        base_processor.continue_stash_processing.reset_mock()
        base_processor.continue_stash_processing.side_effect = Exception("Test error")

        with (
            patch("stash.processing.base.logger.exception") as mock_logger_exception,
            patch("stash.processing.base.debug_print") as mock_debug_print,
        ):
            with pytest.raises(Exception):  # noqa: PT011, B017
                await base_processor._safe_background_processing(
                    mock_account, mock_performer
                )

            # Verify error logging
            mock_logger_exception.assert_called_once()
            assert "Background task failed" in str(mock_logger_exception.call_args)
            mock_debug_print.assert_called_once()
            assert "background_task_failed" in str(mock_debug_print.call_args)

            # Verify cleanup event
            assert base_processor._cleanup_event.is_set()

    def test_generate_title_from_content(self, base_processor):
        """Test _generate_title_from_content method."""
        # Test case 1: Content with a short first line
        content = "This is the title\nThis is the rest of the content"
        username = "test_user"
        created_at = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)

        # Call method
        title = base_processor._generate_title_from_content(
            content, username, created_at
        )

        # Verify result
        assert title == "This is the title"

        # Test case 2: Content with a very long first line
        long_content = "X" * 200
        title = base_processor._generate_title_from_content(
            long_content, username, created_at
        )

        # Verify result is truncated with ellipsis
        assert title == ("X" * 125 + "...")
        assert len(title) == 128

        # Test case 3: No suitable content line, use fallback
        title = base_processor._generate_title_from_content(None, username, created_at)

        # Verify fallback format
        assert title == "test_user - 2023/01/01"

        # Test case 4: With position indicators
        title = base_processor._generate_title_from_content(
            content, username, created_at, 2, 5
        )

        # Verify position is appended
        assert title == "This is the title - 2/5"
