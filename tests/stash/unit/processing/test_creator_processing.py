"""Unit tests for creator processing methods."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
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
    return context


@pytest.fixture
def mock_database():
    """Fixture for mock database."""
    database = MagicMock()
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


class TestCreatorProcessing:
    """Test the creator processing methods of StashProcessing."""

    @pytest.mark.asyncio
    async def test_process_creator(self, processor, mock_account, mock_performer):
        """Test process_creator method."""
        # Mock _find_account
        processor._find_account = AsyncMock(return_value=mock_account)

        # Mock _find_existing_performer
        processor._find_existing_performer = AsyncMock(return_value=mock_performer)

        # Mock _update_performer_avatar
        processor._update_performer_avatar = AsyncMock()

        # Call process_creator
        account, performer = await processor.process_creator()

        # Verify results
        assert account == mock_account
        assert performer == mock_performer

        # Verify methods were called
        processor._find_account.assert_called_once()
        processor._find_existing_performer.assert_called_once_with(mock_account)
        processor._update_performer_avatar.assert_called_once_with(
            mock_account, mock_performer
        )

        # Test with no account found
        processor._find_account.reset_mock()
        processor._find_account.return_value = None

        # Call process_creator and expect ValueError
        with pytest.raises(ValueError):
            await processor.process_creator()

        # Test with no performer found
        processor._find_account.reset_mock()
        processor._find_account.return_value = mock_account
        processor._find_existing_performer.reset_mock()
        processor._find_existing_performer.return_value = None

        # Mock Performer.from_account
        mock_new_performer = MagicMock(spec=Performer)
        with patch(
            "stash.types.Performer.from_account",
            AsyncMock(return_value=mock_new_performer),
        ) as mock_from_account:
            # Call process_creator
            account, performer = await processor.process_creator()

            # Verify results
            assert account == mock_account
            assert performer == mock_new_performer

            # Verify methods were called
            processor._find_account.assert_called_once()
            processor._find_existing_performer.assert_called_once_with(mock_account)
            mock_from_account.assert_called_once_with(mock_account)
            mock_new_performer.save.assert_called_once_with(processor.context.client)
            processor._update_performer_avatar.assert_called_once_with(
                mock_account, mock_new_performer
            )

    @pytest.mark.asyncio
    async def test_find_existing_studio(self, processor, mock_account):
        """Test _find_existing_studio method."""
        # Mock process_creator_studio
        mock_studio = MagicMock(spec=Studio)
        processor.process_creator_studio = AsyncMock(return_value=mock_studio)

        # Call _find_existing_studio
        studio = await processor._find_existing_studio(mock_account)

        # Verify studio and process_creator_studio was called
        assert studio == mock_studio
        processor.process_creator_studio.assert_called_once_with(
            account=mock_account, performer=None
        )

    @pytest.mark.asyncio
    async def test_start_creator_processing(
        self, processor, mock_account, mock_performer
    ):
        """Test start_creator_processing method."""
        # Case 1: No stash_context_conn
        processor.config.stash_context_conn = None

        # Mock print_warning
        with patch("stash.processing.print_warning") as mock_print_warning:
            # Call start_creator_processing
            await processor.start_creator_processing()

            # Verify warning was printed and no further processing
            mock_print_warning.assert_called_once()
            assert "not configured" in str(mock_print_warning.call_args)
            assert not processor.context.get_client.called
            assert not processor.scan_creator_folder.called
            assert not processor.process_creator.called

        # Case 2: With stash_context_conn
        processor.config.stash_context_conn = {
            "url": "http://test.com",
            "api_key": "test_key",
        }

        # Mock methods and asyncio
        processor.context.get_client = AsyncMock()
        processor.scan_creator_folder = AsyncMock()
        processor.process_creator = AsyncMock(
            return_value=(mock_account, mock_performer)
        )
        processor._safe_background_processing = AsyncMock()

        with patch("asyncio.get_running_loop") as mock_get_loop:
            mock_loop = MagicMock()
            mock_task = MagicMock()
            mock_loop.create_task.return_value = mock_task
            mock_get_loop.return_value = mock_loop

            # Call start_creator_processing
            await processor.start_creator_processing()

            # Verify methods were called
            processor.context.get_client.assert_called_once()
            processor.scan_creator_folder.assert_called_once()
            processor.process_creator.assert_called_once()
            mock_loop.create_task.assert_called_once()
            assert processor._background_task == mock_task
            processor.config.get_background_tasks.return_value.append.assert_called_once_with(
                mock_task
            )

    @pytest.mark.asyncio
    async def test_scan_creator_folder(self, processor):
        """Test scan_creator_folder method."""
        # Mock client and methods
        processor.context.client.metadata_scan = AsyncMock(return_value="job_123")
        processor.context.client.wait_for_job = AsyncMock(side_effect=[False, True])

        # Call scan_creator_folder
        await processor.scan_creator_folder()

        # Verify methods were called
        processor.context.client.metadata_scan.assert_called_once()
        assert (
            processor.state.base_path
            in processor.context.client.metadata_scan.call_args[1]["paths"]
        )
        assert processor.context.client.wait_for_job.call_count == 2

        # Case 2: metadata_scan raises RuntimeError
        processor.context.client.metadata_scan.reset_mock()
        processor.context.client.metadata_scan.side_effect = RuntimeError("Test error")

        # Call scan_creator_folder and expect RuntimeError
        with pytest.raises(RuntimeError) as excinfo:
            await processor.scan_creator_folder()

        # Verify error message
        assert "Failed to process metadata" in str(excinfo.value)
        assert "Test error" in str(excinfo.value)

        # Case 3: No download_path set
        processor.context.client.metadata_scan.reset_mock()
        processor.context.client.metadata_scan.side_effect = None
        processor.state.base_path = None

        with (
            patch("stash.processing.print_info") as mock_print_info,
            patch(
                "stash.processing.set_create_directory_for_download"
            ) as mock_set_path,
        ):
            # Mock successful path creation
            mock_path = MagicMock()
            mock_set_path.return_value = mock_path

            # Call scan_creator_folder
            await processor.scan_creator_folder()

            # Verify path creation and info
            mock_print_info.assert_any_call(
                "No download path set, attempting to create one..."
            )
            mock_set_path.assert_called_once_with(processor.config, processor.state)
            assert processor.state.download_path == mock_path
            processor.context.client.metadata_scan.assert_called_once()
