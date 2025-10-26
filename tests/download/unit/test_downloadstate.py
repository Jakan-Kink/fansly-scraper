from pathlib import Path

import pytest

from download.core import DownloadState
from download.globalstate import GlobalState
from download.types import DownloadType


@pytest.fixture
def download_state():
    """Create a test download state."""
    state = DownloadState()
    state.creator_name = "test_creator"
    state.download_type = DownloadType.NOTSET
    state.base_path = Path("/test/path")
    return state


def test_initial_state():
    """Test initial state values."""
    state = DownloadState()
    assert state.pic_count == 0
    assert state.vid_count == 0
    assert state.duplicate_count == 0
    assert state.current_batch_duplicates == 0
    assert state.download_type == DownloadType.NOTSET
    assert state.creator_name is None
    assert len(state.walls) == 0


def test_download_type_str(download_state):
    """Test download_type string representation."""
    assert download_state.download_type_str() == "Notset"
    download_state.download_type = DownloadType.TIMELINE
    assert download_state.download_type_str() == "Timeline"


def test_start_batch(download_state):
    """Test batch counter reset."""
    download_state.current_batch_duplicates = 5
    download_state.start_batch()
    assert download_state.current_batch_duplicates == 0


def test_add_duplicate(download_state):
    """Test duplicate counter incrementation."""
    initial_duplicates = download_state.duplicate_count
    initial_batch = download_state.current_batch_duplicates

    download_state.add_duplicate()

    assert download_state.duplicate_count == initial_duplicates + 1
    assert download_state.current_batch_duplicates == initial_batch + 1


def test_inheritance(download_state):
    """Test proper inheritance from GlobalState."""
    assert isinstance(download_state, GlobalState)
    assert hasattr(download_state, "total_timeline_items")
    assert hasattr(download_state, "missing_items_count")


def test_sets_initialization(download_state):
    """Test set fields are properly initialized."""
    assert isinstance(download_state.recent_audio_media_ids, set)
    assert isinstance(download_state.recent_photo_media_ids, set)
    assert isinstance(download_state.recent_video_media_ids, set)
    assert isinstance(download_state.recent_audio_hashes, set)
    assert isinstance(download_state.recent_photo_hashes, set)
    assert isinstance(download_state.recent_video_hashes, set)
    assert isinstance(download_state.walls, set)


def test_path_handling(download_state):
    """Test path attribute handling."""
    test_path = Path("/test/download/path")
    download_state.download_path = test_path
    assert isinstance(download_state.download_path, Path)
    assert download_state.download_path == test_path
