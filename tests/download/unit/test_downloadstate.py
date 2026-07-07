from pathlib import Path

from download.core import DownloadState
from download.globalstate import GlobalState
from download.types import DownloadType


def test_state_lifecycle_walk():
    """Walk a fresh DownloadState: initial values, inheritance, duplicate
    accounting, and batch-counter reset."""
    state = DownloadState()

    # Initial state values.
    assert state.pic_count == 0
    assert state.vid_count == 0
    assert state.duplicate_count == 0
    assert state.current_batch_duplicates == 0
    assert state.download_type == DownloadType.NOTSET
    assert state.creator_name is None
    assert len(state.walls) == 0

    # Inheritance from GlobalState wires through.
    assert isinstance(state, GlobalState)
    assert hasattr(state, "total_timeline_items")
    assert hasattr(state, "missing_items_count")

    # add_duplicate increments both counters.
    initial_duplicates = state.duplicate_count
    initial_batch = state.current_batch_duplicates
    state.add_duplicate()
    assert state.duplicate_count == initial_duplicates + 1
    assert state.current_batch_duplicates == initial_batch + 1

    # start_batch resets only the batch counter.
    state.current_batch_duplicates = 5
    state.start_batch()
    assert state.current_batch_duplicates == 0


def test_download_type_and_paths_walk(notset_download_state):
    """Walk download_type string representation and path attribute handling."""
    # download_type_str tracks the enum value.
    assert notset_download_state.download_type_str() == "Notset"
    notset_download_state.download_type = DownloadType.TIMELINE
    assert notset_download_state.download_type_str() == "Timeline"

    # Path attribute handling.
    test_path = Path("/test/download/path")
    notset_download_state.download_path = test_path
    assert isinstance(notset_download_state.download_path, Path)
    assert notset_download_state.download_path == test_path
