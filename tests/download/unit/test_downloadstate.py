from pathlib import Path

from download.core import DownloadState
from download.globalstate import GlobalState
from download.types import DownloadType


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


def test_download_type_str(notset_download_state):
    """Test download_type string representation."""
    assert notset_download_state.download_type_str() == "Notset"
    notset_download_state.download_type = DownloadType.TIMELINE
    assert notset_download_state.download_type_str() == "Timeline"


def test_start_batch(notset_download_state):
    """Test batch counter reset."""
    notset_download_state.current_batch_duplicates = 5
    notset_download_state.start_batch()
    assert notset_download_state.current_batch_duplicates == 0


def test_add_duplicate(notset_download_state):
    """Test duplicate counter incrementation."""
    initial_duplicates = notset_download_state.duplicate_count
    initial_batch = notset_download_state.current_batch_duplicates

    notset_download_state.add_duplicate()

    assert notset_download_state.duplicate_count == initial_duplicates + 1
    assert notset_download_state.current_batch_duplicates == initial_batch + 1


def test_inheritance(notset_download_state):
    """Verify DownloadState's inheritance from GlobalState wires through."""
    assert isinstance(notset_download_state, GlobalState)
    assert hasattr(notset_download_state, "total_timeline_items")
    assert hasattr(notset_download_state, "missing_items_count")


def test_path_handling(notset_download_state):
    """Test path attribute handling."""
    test_path = Path("/test/download/path")
    notset_download_state.download_path = test_path
    assert isinstance(notset_download_state.download_path, Path)
    assert notset_download_state.download_path == test_path
