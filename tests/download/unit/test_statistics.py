"""Tests for download/statistics.py — content stats, file stats, and formatted output."""

import pytest

from download.core import DownloadState, GlobalState
from download.statistics import (
    print_global_statistics,
    print_statistics,
    print_statistics_helper,
    print_timing_statistics,
    update_global_statistics,
)
from helpers.timer import Timer


class TestPrintTimingStatistics:
    """Line 21-22."""

    def test_prints_without_error(self):
        timer = Timer("test_timer")
        timer.start()
        timer.stop()
        print_timing_statistics(timer)


class TestUpdateGlobalStatistics:
    """Lines 38-70: update content + file download stats."""

    def test_updates_all_content_fields(self):
        gs = GlobalState()
        ds = DownloadState()
        ds.duplicate_count = 5
        ds.pic_count = 10
        ds.vid_count = 3
        ds.total_message_items = 7
        ds.total_timeline_pictures = 20
        ds.total_timeline_videos = 8

        update_global_statistics(gs, ds)

        assert gs.duplicate_count == 5
        assert gs.pic_count == 10
        assert gs.vid_count == 3
        assert gs.total_message_items == 7
        assert gs.total_timeline_pictures == 20
        assert gs.total_timeline_videos == 8

    def test_initializes_missing_download_stats(self):
        """Lines 46-63: both download_state and global_state lack download_stats attr."""
        gs = GlobalState()
        ds = DownloadState()

        # Ensure download_stats doesn't exist
        if hasattr(ds, "download_stats"):
            delattr(ds, "download_stats")
        if hasattr(gs, "download_stats"):
            delattr(gs, "download_stats")

        update_global_statistics(gs, ds)

        assert gs.download_stats["total_count"] == 0
        assert gs.download_stats["skipped_count"] == 0

    def test_accumulates_download_stats(self):
        """Lines 65-70: accumulates file stats from download_state."""
        gs = GlobalState()
        gs.download_stats = {
            "start_time": None,
            "total_count": 10,
            "skipped_count": 2,
            "failed_count": 1,
            "total_size": 1000,
            "total_size_str": "1 KB",
        }

        ds = DownloadState()
        ds.download_stats = {
            "total_count": 5,
            "skipped_count": 3,
            "failed_count": 0,
            "total_size": 500,
            "total_size_str": "500 B",
        }

        update_global_statistics(gs, ds)

        assert gs.download_stats["total_count"] == 15
        assert gs.download_stats["skipped_count"] == 5
        assert gs.download_stats["total_size"] == 1500


class TestPrintStatisticsHelper:
    """Lines 81-86: formatted output with optional missing items warning."""

    def test_no_missing_items(self):
        state = GlobalState()
        print_statistics_helper(state, "Test Header")

    def test_with_missing_items(self):
        """Line 84: missing_items_count > 0 adds warning."""
        state = GlobalState()
        state.pic_count = 5
        state.vid_count = 3
        state.total_timeline_pictures = 20
        state.total_timeline_videos = 10
        # missing = total - downloaded = 22 > 0
        print_statistics_helper(state, "Header", "Footer")

    def test_with_footer(self):
        state = GlobalState()
        print_statistics_helper(state, "Header", "\n  Custom footer text")


class TestPrintStatistics:
    """Lines 106-124: per-creator statistics with follow/subscribe/preview warnings."""

    def test_not_following(self, mock_config):
        """Line 111: not following → footer message."""
        state = DownloadState()
        state.creator_name = "testcreator"
        state.following = False
        state.subscribed = False
        print_statistics(mock_config, state)

    def test_following_not_subscribed(self, mock_config):
        """Lines 113-116: following but not subscribed."""
        state = DownloadState()
        state.creator_name = "testcreator"
        state.following = True
        state.subscribed = False
        print_statistics(mock_config, state)

    def test_subscribed_with_missing_items(self, mock_config):
        """Lines 118-122: subscribed, has missing items, previews disabled."""
        mock_config.download_media_previews = False
        state = DownloadState()
        state.creator_name = "testcreator"
        state.following = True
        state.subscribed = True
        state.total_timeline_pictures = 50
        state.total_timeline_videos = 20
        # pic_count + vid_count < total → missing > 0
        state.pic_count = 10
        state.vid_count = 5
        print_statistics(mock_config, state)

    def test_subscribed_no_missing(self, mock_config):
        """All content downloaded, no footer additions."""
        mock_config.download_media_previews = True
        state = DownloadState()
        state.creator_name = "testcreator"
        state.following = True
        state.subscribed = True
        print_statistics(mock_config, state)


class TestPrintGlobalStatistics:
    """Lines 134-147: global stats for all creators."""

    def test_with_user_names(self, mock_config):
        mock_config.user_names = ["creator1", "creator2"]
        state = GlobalState()
        print_global_statistics(mock_config, state)

    def test_with_missing_items(self, mock_config):
        """Line 144-145: missing items → footer warning."""
        mock_config.user_names = ["creator1"]
        state = GlobalState()
        state.total_timeline_pictures = 100
        state.pic_count = 50
        print_global_statistics(mock_config, state)

    def test_no_user_names_raises(self, mock_config):
        """Line 134-135: user_names is None → RuntimeError."""
        mock_config.user_names = None
        state = GlobalState()
        with pytest.raises(RuntimeError, match="user names undefined"):
            print_global_statistics(mock_config, state)
