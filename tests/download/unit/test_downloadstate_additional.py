"""Additional unit tests for download/downloadstate.py"""

from download.downloadstate import DownloadState
from download.types import DownloadType


class TestDownloadStateAdditional:
    """Additional tests for DownloadState class."""

    def test_download_type_str(self):
        """Test download_type_str returns capitalized string."""
        state = DownloadState(download_type=DownloadType.TIMELINE)
        assert state.download_type_str() == "Timeline"

    def test_start_batch(self):
        """Test start_batch resets batch duplicate counter."""
        state = DownloadState()
        state.current_batch_duplicates = 5
        state.start_batch()
        assert state.current_batch_duplicates == 0

    def test_add_duplicate(self):
        """Test add_duplicate increments both counters."""
        state = DownloadState()
        state.add_duplicate()
        assert state.duplicate_count == 1
        assert state.current_batch_duplicates == 1

        state.add_duplicate()
        assert state.duplicate_count == 2
        assert state.current_batch_duplicates == 2
