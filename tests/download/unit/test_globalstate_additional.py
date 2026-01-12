"""Additional unit tests for download/globalstate.py"""

from download.globalstate import GlobalState


class TestGlobalStateAdditional:
    """Additional tests for GlobalState class."""

    def test_total_timeline_items(self):
        """Test total_timeline_items sums pictures and videos."""
        state = GlobalState(total_timeline_pictures=5, total_timeline_videos=3)
        assert state.total_timeline_items() == 8

    def test_total_downloaded_items(self):
        """Test total_downloaded_items sums pic and vid counts."""
        state = GlobalState(pic_count=10, vid_count=7)
        assert state.total_downloaded_items() == 17

    def test_missing_items_count(self):
        """Test missing_items_count calculation."""
        state = GlobalState(
            total_timeline_pictures=20,
            total_timeline_videos=10,
            total_message_items=5,
            pic_count=15,
            vid_count=8,
            duplicate_count=2,
        )
        # Formula: (20+10) + 5 - (15+8) - 2 = 35 - 23 - 2 = 10
        assert state.missing_items_count() == 10

    def test_missing_items_count_prevents_negative(self):
        """Test missing_items_count never goes negative."""
        state = GlobalState(
            total_timeline_pictures=5,
            total_timeline_videos=3,
            pic_count=10,
            vid_count=5,
        )
        # Without clamp: (5+3) + 0 - (10+5) - 0 = 8 - 15 = -7
        # With clamp: max(-7, 0) = 0
        assert state.missing_items_count() == 0
