"""Unit tests for StashProcessing dataclass."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from stashapi.stashapp import StashInterface

from config import FanslyConfig
from download.core import DownloadState
from metadata import Account
from metadata.database import Database
from stash.performer import Performer
from stash.processing import StashProcessing


@pytest.fixture
def mock_stash_interface(mocker):
    """Create a mock StashInterface."""
    return cast(StashInterface, mocker.Mock(spec=StashInterface))


@pytest.fixture
def mock_database(mocker):
    """Create a mock Database."""
    return cast(Database, mocker.Mock(spec=Database))


@pytest.fixture
def mock_async_session(mocker):
    """Create a mock AsyncSession."""
    return cast(AsyncSession, mocker.Mock(spec=AsyncSession))


@pytest.fixture
def mock_config(mocker, mock_stash_interface):
    """Create a mock FanslyConfig."""
    config = cast(FanslyConfig, mocker.Mock(spec=FanslyConfig))
    config.get_stash_api.return_value = mock_stash_interface
    config.separate_metadata = False
    config.metadata_db_file = Path("/test/metadata.db")
    config._database = mock_database
    return config


@pytest.fixture
def mock_state(mocker):
    """Create a mock DownloadState."""
    state = cast(DownloadState, mocker.Mock(spec=DownloadState))
    state.creator_name = "test_creator"
    state.creator_id = "123"
    state.download_path = Path("/test/downloads")
    return state


@pytest.fixture
def mock_account():
    """Create a mock Account."""
    now = datetime.now(timezone.utc)
    return Account(
        id="123",
        username="test_creator",
        displayName="Test Creator",
        about="Test About",
        location="Test Location",
        stash_id=None,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def mock_performer():
    """Create a mock Performer."""
    now = datetime.now(timezone.utc)
    return Performer(
        id="456",
        name="Test Creator",
        urls=["https://fansly.com/test_creator/posts"],
        details="Test About",
        country="Test Location",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def processor(mock_config, mock_state, mock_database):
    """Create a StashProcessing instance."""
    return StashProcessing.from_config(mock_config, mock_state)


async def test_start_creator_processing(processor, mock_config, mock_stash_interface):
    """Test starting creator processing."""
    # Mock stash_context_conn to be None first
    mock_config.stash_context_conn = None
    await processor.start_creator_processing()
    mock_stash_interface.metadata_scan.assert_not_called()

    # Now set stash_context_conn
    mock_config.stash_context_conn = "test_conn"
    mock_stash_interface.metadata_scan.return_value = "job123"
    mock_stash_interface.wait_for_job.return_value = True

    await processor.start_creator_processing()

    mock_stash_interface.metadata_scan.assert_called_once()
    mock_stash_interface.wait_for_job.assert_called_once_with("job123")


async def test_scan_creator_folder(processor, mock_stash_interface):
    """Test scanning creator folder."""
    mock_stash_interface.metadata_scan.return_value = "job123"
    mock_stash_interface.wait_for_job.return_value = True

    await processor.scan_creator_folder()

    mock_stash_interface.metadata_scan.assert_called_once()
    mock_stash_interface.wait_for_job.assert_called_once_with("job123")


async def test_process_creator(
    processor, mock_async_session, mock_account, mock_performer, mock_stash_interface
):
    """Test processing creator."""
    # Mock database session
    processor.database.get_async_session.return_value.__aenter__.return_value = (
        mock_async_session
    )
    mock_async_session.execute.return_value.scalar_one_or_none.return_value = (
        mock_account
    )

    # Mock stash interface
    mock_stash_interface.find_performer.return_value = None
    mock_stash_interface.create_performer.return_value = mock_performer.to_dict()

    account, performer = await processor.process_creator()

    assert account == mock_account
    assert performer is not None
    assert performer.name == "Test Creator"
    assert performer.urls == ["https://fansly.com/test_creator/posts"]


async def test_continue_stash_processing(
    processor, mock_async_session, mock_account, mock_performer
):
    """Test continuing stash processing."""
    # Mock database session
    processor.database.get_async_session.return_value.__aenter__.return_value = (
        mock_async_session
    )

    # Test with account and performer
    await processor.continue_stash_processing(mock_account, mock_performer)
    mock_async_session.commit.assert_called_once()

    # Test with no account or performer
    await processor.continue_stash_processing(None, None)
    mock_async_session.commit.assert_called_once()  # Should not be called again


def test_cleanup(processor):
    """Test cleanup when processor owns database connection."""
    processor._owns_db_connection = True
    processor.database.close = mock_database.close

    try:
        processor.continue_stash_processing(None, None)
    finally:
        pass  # Cleanup should happen in finally block

    processor.database.close.assert_called_once()
