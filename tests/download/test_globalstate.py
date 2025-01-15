from unittest import TestCase

from download.globalstate import GlobalState


class TestGlobalState(TestCase):
    def setUp(self):
        """Set up test fixtures before each test method."""
        self.state = GlobalState()

    def test_initial_state(self):
        """Test initial counter values."""
        self.assertEqual(self.state.duplicate_count, 0)
        self.assertEqual(self.state.pic_count, 0)
        self.assertEqual(self.state.vid_count, 0)
        self.assertEqual(self.state.total_message_items, 0)
        self.assertEqual(self.state.total_timeline_pictures, 0)
        self.assertEqual(self.state.total_timeline_videos, 0)

    def test_total_timeline_items(self):
        """Test timeline items calculation."""
        self.state.total_timeline_pictures = 5
        self.state.total_timeline_videos = 3
        self.assertEqual(self.state.total_timeline_items(), 8)

    def test_total_downloaded_items(self):
        """Test downloaded items calculation."""
        self.state.pic_count = 10
        self.state.vid_count = 7
        self.assertEqual(self.state.total_downloaded_items(), 17)

    def test_missing_items_count_normal(self):
        """Test missing items calculation in normal case."""
        self.state.total_timeline_pictures = 10
        self.state.total_timeline_videos = 5
        self.state.total_message_items = 3
        self.state.pic_count = 8
        self.state.vid_count = 4
        self.state.duplicate_count = 2
        self.assertEqual(self.state.missing_items_count(), 4)

    def test_missing_items_count_zero(self):
        """Test missing items calculation when everything is downloaded."""
        self.state.total_timeline_pictures = 5
        self.state.total_timeline_videos = 5
        self.state.pic_count = 5
        self.state.vid_count = 5
        self.assertEqual(self.state.missing_items_count(), 0)

    def test_missing_items_count_negative_protection(self):
        """Test missing items stays at 0 when calculation would be negative."""
        self.state.pic_count = 10
        self.state.vid_count = 5
        self.assertEqual(self.state.missing_items_count(), 0)
