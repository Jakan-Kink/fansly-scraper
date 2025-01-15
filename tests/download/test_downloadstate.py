from pathlib import Path
from unittest import TestCase

from download.core import DownloadState
from download.types import DownloadType


class TestDownloadState(TestCase):
    def setUp(self):
        """Set up test fixtures before each test method."""
        self.state = DownloadState()
        self.state.creator_name = "test_creator"
        self.state.download_type = DownloadType.NOTSET
        self.state.base_path = Path("/test/path")

    def test_initial_state(self):
        """Test initial state values."""
        state = DownloadState()
        self.assertEqual(state.pic_count, 0)
        self.assertEqual(state.vid_count, 0)
        self.assertEqual(state.duplicate_count, 0)
        self.assertEqual(state.current_batch_duplicates, 0)
        self.assertEqual(state.download_type, DownloadType.NOTSET)
        self.assertIsNone(state.creator_name)
        self.assertEqual(len(state.walls), 0)

    def test_download_type_str(self):
        """Test download_type string representation."""
        self.assertEqual(self.state.download_type_str(), "Notset")
        self.state.download_type = DownloadType.TIMELINE
        self.assertEqual(self.state.download_type_str(), "Timeline")

    def test_start_batch(self):
        """Test batch counter reset."""
        self.state.current_batch_duplicates = 5
        self.state.start_batch()
        self.assertEqual(self.state.current_batch_duplicates, 0)

    def test_add_duplicate(self):
        """Test duplicate counter incrementation."""
        initial_duplicates = self.state.duplicate_count
        initial_batch = self.state.current_batch_duplicates

        self.state.add_duplicate()

        self.assertEqual(self.state.duplicate_count, initial_duplicates + 1)
        self.assertEqual(self.state.current_batch_duplicates, initial_batch + 1)

    def test_inheritance(self):
        """Test proper inheritance from GlobalState."""
        from download.globalstate import GlobalState

        self.assertIsInstance(self.state, GlobalState)
        self.assertTrue(hasattr(self.state, "total_timeline_items"))
        self.assertTrue(hasattr(self.state, "missing_items_count"))

    def test_sets_initialization(self):
        """Test set fields are properly initialized."""
        self.assertIsInstance(self.state.recent_audio_media_ids, set)
        self.assertIsInstance(self.state.recent_photo_media_ids, set)
        self.assertIsInstance(self.state.recent_video_media_ids, set)
        self.assertIsInstance(self.state.recent_audio_hashes, set)
        self.assertIsInstance(self.state.recent_photo_hashes, set)
        self.assertIsInstance(self.state.recent_video_hashes, set)
        self.assertIsInstance(self.state.walls, set)

    def test_path_handling(self):
        """Test path attribute handling."""
        test_path = Path("/test/download/path")
        self.state.download_path = test_path
        self.assertIsInstance(self.state.download_path, Path)
        self.assertEqual(self.state.download_path, test_path)
