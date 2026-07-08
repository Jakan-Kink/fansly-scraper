"""Tests for download/statistics.py — content stats, file stats, and formatted output."""

import logging
from datetime import UTC, datetime

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

    def test_download_stats_zero_initialized_by_default(self):
        """download_stats is a dataclass field — present and zeroed on fresh states."""
        gs = GlobalState()
        ds = DownloadState()

        # Field default_factory guarantees presence; no lazy init needed.
        for state in (gs, ds):
            assert state.download_stats["total_count"] == 0
            assert state.download_stats["skipped_count"] == 0
            assert state.download_stats["failed_count"] == 0
            assert state.download_stats["total_size"] == 0
            assert state.download_stats["total_size_str"] == "0 B"

        update_global_statistics(gs, ds)

        assert gs.download_stats["total_count"] == 0
        assert gs.download_stats["skipped_count"] == 0

    def test_accumulates_download_stats(self):
        """Lines 65-70: accumulates file stats from download_state."""
        gs = GlobalState()
        gs.download_stats = {
            "start_time": datetime.now(UTC),
            "total_count": 10,
            "skipped_count": 2,
            "failed_count": 1,
            "total_size": 1000,
            "total_size_str": "1 KB",
        }

        ds = DownloadState()
        ds.download_stats = {
            "start_time": datetime.now(UTC),
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

    def test_filtered_count_renders_line(self, caplog):
        """Line 63-64: the filtered-by-filters.media line only appears when nonzero."""
        caplog.set_level(logging.INFO)
        state = GlobalState()
        state.filtered_count = 3
        print_statistics_helper(state, "Header")
        output = "\n".join(r.getMessage() for r in caplog.records)
        assert "Filtered by filters.media: 3" in output


_PER_CREATOR_FOOTERS = (
    "Follow the creator to be able to scrape media!",
    "Subscribe to the creator if you would like to get the entire content.",
    "Try setting download_media_previews to True in the config.ini file.",
)


class TestPrintStatistics:
    """Lines 106-124: per-creator statistics with follow/subscribe/preview warnings."""

    @pytest.mark.parametrize(
        (
            "download_media_previews",
            "following",
            "subscribed",
            "state_fields",
            "expected_fragment",
        ),
        [
            pytest.param(
                None,
                False,
                False,
                {},
                "Follow the creator to be able to scrape media!",
                id="not-following-footer",
            ),
            pytest.param(
                None,
                True,
                False,
                {},
                "Subscribe to the creator if you would like to get the entire content.",
                id="following-not-subscribed-footer",
            ),
            pytest.param(
                False,
                True,
                True,
                {
                    "total_timeline_pictures": 50,
                    "total_timeline_videos": 20,
                    # pic_count + vid_count < total → missing > 0
                    "pic_count": 10,
                    "vid_count": 5,
                },
                "Try setting download_media_previews to True in the config.ini file.",
                id="subscribed-missing-items-preview-hint",
            ),
            pytest.param(
                True,
                True,
                True,
                {},
                None,
                id="subscribed-no-missing-no-footer",
            ),
        ],
    )
    def test_footer_variants(
        self,
        mock_config,
        caplog,
        download_media_previews,
        following,
        subscribed,
        state_fields,
        expected_fragment,
    ):
        """Footer message tracks follow/subscribe/preview state.

        ``download_media_previews=None`` leaves the fixture default untouched
        (the follow/subscribe rows never set it). The no-footer row asserts
        that NONE of the per-creator footer fragments appear.
        """
        caplog.set_level(logging.INFO)
        if download_media_previews is not None:
            mock_config.download_media_previews = download_media_previews
        state = DownloadState()
        state.creator_name = "testcreator"
        state.following = following
        state.subscribed = subscribed
        for field, value in state_fields.items():
            setattr(state, field, value)

        print_statistics(mock_config, state)

        output = "\n".join(r.getMessage() for r in caplog.records)
        assert (
            f"Finished {mock_config.download_mode_str()} type download "
            "for @testcreator" in output
        )
        if expected_fragment is not None:
            assert expected_fragment in output
        else:
            for fragment in _PER_CREATOR_FOOTERS:
                assert fragment not in output


class TestPrintGlobalStatistics:
    """Lines 134-147: global stats for all creators."""

    @pytest.mark.parametrize(
        ("user_names", "state_fields", "expected_fragment", "expected_error"),
        [
            pytest.param(
                ["creator1", "creator2"],
                {},
                None,
                None,
                id="two-creators-no-missing",
            ),
            pytest.param(
                ["creator1"],
                {"total_timeline_pictures": 100, "pic_count": 50},
                "Make sure you are following and subscribed to all creators.",
                None,
                id="missing-items-footer-warning",
            ),
            pytest.param(
                None,
                {},
                None,
                RuntimeError,
                id="no-user-names-raises-runtimeerror",
            ),
        ],
    )
    def test_global_summary_variants(
        self,
        mock_config,
        caplog,
        user_names,
        state_fields,
        expected_fragment,
        expected_error,
    ):
        """Global summary: creator count header, missing-items footer, None guard.

        ``user_names=None`` is a distinct failure mode (line 134-135) — kept
        as its own row with an ``expected_error`` column.
        """
        caplog.set_level(logging.INFO)
        mock_config.user_names = user_names
        state = GlobalState()
        for field, value in state_fields.items():
            setattr(state, field, value)

        if expected_error is not None:
            with pytest.raises(expected_error, match="user names undefined"):
                print_global_statistics(mock_config, state)
            return

        print_global_statistics(mock_config, state)

        output = "\n".join(r.getMessage() for r in caplog.records)
        assert f"Finished downloading media for {len(user_names)} creators!" in output
        missing_footer = "Make sure you are following and subscribed to all creators."
        if expected_fragment is not None:
            assert expected_fragment in output
        else:
            assert missing_footer not in output
