"""Tests for helpers/rich_progress.py — progress manager, FFmpeg progress, Rich integration.

No external boundaries — all pure logic. Uses real Rich objects.
"""

import time
from pathlib import Path
from unittest.mock import patch

from rich.progress import Task
from rich.text import Text

from helpers.rich_progress import (
    ContextualTimeColumn,
    ProgressManager,
    _do_watch_progress,
    _shutdown_progress_live,
    _watch_progress,
    create_rich_handler,
    ffmpeg_progress,
    get_progress_manager,
    get_rich_console,
)


class TestContextualTimeColumn:
    """Lines 43-98: time column renders elapsed or remaining based on task context."""

    def _make_task(self, description="", fields=None):
        """Build a minimal Rich Task for rendering."""
        task = Task(
            id=0,
            description=description,
            total=100,
            completed=0,
            _get_time=time.monotonic,
        )
        if fields:
            task.fields = fields
        return task

    def test_explicit_show_elapsed(self):
        """Lines 56-57: show_elapsed=True → elapsed column."""
        col = ContextualTimeColumn()
        task = self._make_task(fields={"show_elapsed": True})
        result = col.render(task)
        assert isinstance(result, Text)

    def test_explicit_show_remaining(self):
        """Lines 58-59: show_elapsed=False → remaining column."""
        col = ContextualTimeColumn()
        task = self._make_task(fields={"show_elapsed": False})
        result = col.render(task)
        assert isinstance(result, Text)

    def test_auto_detect_elapsed_pattern(self):
        """Lines 88-90: description matches elapsed pattern → elapsed."""
        col = ContextualTimeColumn()
        task = self._make_task(description="Scanning files")
        result = col.render(task)
        assert isinstance(result, Text)

    def test_auto_detect_remaining_pattern(self):
        """Lines 93-95: description matches remaining pattern → remaining."""
        col = ContextualTimeColumn()
        task = self._make_task(description="Downloading media")
        result = col.render(task)
        assert isinstance(result, Text)

    def test_auto_detect_default(self):
        """Line 98: no pattern match → defaults to remaining."""
        col = ContextualTimeColumn()
        task = self._make_task(description="Some unknown task")
        result = col.render(task)
        assert isinstance(result, Text)


class TestProgressManager:
    """Lines 101-250: session lifecycle, task CRUD, nested sessions."""

    def test_session_lifecycle_and_auto_cleanup(self):
        """Lines 123-164: session starts Live, auto-cleanup removes tasks on exit."""
        pm = ProgressManager()

        with pm.session():
            pm.add_task("test_task", "Testing", total=10)
            assert "test_task" in pm.active_tasks
            assert pm._session_count == 1

        assert "test_task" not in pm.active_tasks
        assert pm._session_count == 0
        assert pm.live is None

    def test_nested_sessions(self):
        """Lines 135-164: nested sessions share Live, outer exit stops it."""
        pm = ProgressManager()

        with pm.session():
            pm.add_task("outer", "Outer task", total=5)

            with pm.session():
                assert pm._session_count == 2
                pm.add_task("inner", "Inner task", total=3)

            assert "inner" not in pm.active_tasks
            assert pm.live is not None

        assert pm.live is None

    def test_session_no_auto_cleanup(self):
        """Lines 143-145: auto_cleanup=False → tasks persist after session."""
        pm = ProgressManager()

        with pm.session(auto_cleanup=False):
            pm.add_task("persistent", "Persists", total=10)

        assert "persistent" in pm.active_tasks
        assert pm.live is None

    def test_add_task_update_existing(self):
        """Lines 188-191: same name → updates existing task."""
        pm = ProgressManager()

        with pm.session():
            pm.add_task("dup", "First", total=5)
            pm.add_task("dup", "Updated", total=10)
            assert "dup" in pm.active_tasks

    def test_add_task_with_parent(self):
        """Lines 195-197: parent_task → indented description."""
        pm = ProgressManager()

        with pm.session():
            pm.add_task("parent", "Parent", total=10)
            pm.add_task("child", "Child", total=5, parent_task="parent")
            assert "child" in pm.active_tasks

    def test_update_task_with_description_and_nonexistent(self):
        """Lines 214-230: update with description; update unknown → no crash."""
        pm = ProgressManager()

        with pm.session():
            pm.add_task("t", "Original", total=10)
            pm.update_task("t", advance=3, description="Updated desc")

        pm.update_task("nonexistent", advance=1)  # no crash

    def test_remove_task_and_nonexistent(self):
        """Lines 232-241: explicit removal; remove unknown → no crash."""
        pm = ProgressManager()

        with pm.session(auto_cleanup=False):
            pm.add_task("removable", "Remove me", total=5)
            pm.remove_task("removable")
            assert "removable" not in pm.active_tasks

        pm.remove_task("nonexistent")  # no crash

    def test_get_active_count(self):
        """Lines 243-250."""
        pm = ProgressManager()
        assert pm.get_active_count() == 0

        with pm.session(auto_cleanup=False):
            pm.add_task("a", "A", total=1)
            pm.add_task("b", "B", total=1)
            assert pm.get_active_count() == 2


class TestGlobalInstances:
    """Lines 253-275."""

    def test_get_progress_manager_singleton(self):
        pm = get_progress_manager()
        assert isinstance(pm, ProgressManager)
        assert get_progress_manager() is pm

    def test_get_rich_console(self):
        assert get_rich_console() is not None


class TestCreateRichHandler:
    """Lines 283-333: RichHandler with custom level styles."""

    def test_default_and_custom_styles(self):
        """Default styles, custom styles, and None all produce a handler."""
        assert create_rich_handler() is not None
        assert create_rich_handler(level_styles={"CUSTOM": "bold green"}) is not None
        assert create_rich_handler(level_styles=None) is not None


class TestDoWatchProgress:
    """Lines 341-381: FFmpeg progress file tailing."""

    def test_reads_progress_events(self, tmp_path):
        """Lines 362-379: reads key=value lines, stops at progress=end."""
        progress_file = tmp_path / "ffmpeg_progress.txt"
        progress_file.write_text(
            "frame=100\n"
            "fps=30\n"
            "out_time_ms=5000000\n"
            "progress=continue\n"
            "\n"
            "frame=200\n"
            "out_time_ms=10000000\n"
            "progress=end\n"
        )

        events = []
        _do_watch_progress(progress_file, lambda k, v: events.append((k, v)))

        assert ("frame", "100") in events
        assert ("out_time_ms", "5000000") in events
        assert ("progress", "end") in events

    def test_timeout_waiting_for_file(self, tmp_path):
        """Lines 357-359: file doesn't appear within timeout → returns."""
        nonexistent = tmp_path / "never_created.txt"
        events = []

        with (
            patch("helpers.rich_progress.time.time", side_effect=[0, 0, 11]),
            patch("helpers.rich_progress.time.sleep"),
        ):
            _do_watch_progress(nonexistent, lambda k, v: events.append((k, v)))

        assert events == []


class TestWatchProgress:
    """Lines 384-418: context manager creating temp file + monitor thread."""

    def test_creates_temp_file_and_cleans_up(self):
        """Lines 399-418: yields path, cleans up after."""
        with _watch_progress(lambda _k, _v: None) as progress_file:
            assert progress_file.exists()
            progress_file.write_text("progress=end\n")
            time.sleep(0.2)

        assert not progress_file.exists()


class TestFfmpegProgress:
    """Lines 421-471: full FFmpeg progress context manager."""

    def test_creates_task_and_cleans_up(self):
        """Lines 444-471: adds task, yields path, removes on exit."""
        with ffmpeg_progress(
            total_duration=10.0, task_name="test_mux"
        ) as progress_file:
            assert isinstance(progress_file, Path)
            assert progress_file.exists()

            pm = get_progress_manager()
            assert "test_mux" in pm.active_tasks

            progress_file.write_text("out_time_ms=5000000\nprogress=end\n")
            time.sleep(0.2)

        assert "test_mux" not in pm.active_tasks


class TestAtexitShutdown:
    """Verify ``_shutdown_progress_live`` joins Rich's Live refresh thread.

    Rich's ``Live.start()`` spawns a daemon thread that writes ANSI cursor
    sequences to stdout. If a session is left open at process exit (test
    interruption, xdist worker shutdown after a test left a session
    open), ``_Py_Finalize.flush_std_files`` races the daemon thread on
    the stdout buffer lock and triggers CPython's ``_enter_buffered_busy``
    fatal error, aborting the process with SIGABRT. The atexit hook stops
    Live before finalization so the refresh thread joins cleanly first.
    """

    def test_shutdown_stops_running_live(self):
        """An open session at atexit time → Live is stopped, refresh thread joined."""
        pm = get_progress_manager()
        with pm.session():
            assert pm.live is not None
            assert pm.live.is_started, "session() must start Live"

            # Simulate atexit firing while session is still open.
            _shutdown_progress_live()

            assert pm.live is None, (
                "atexit hook must clear pm.live so the daemon refresh thread "
                "exits before _Py_Finalize"
            )
            assert pm._session_count == 0

    def test_shutdown_with_no_live_is_noop(self):
        """Calling the hook with no active Live must not raise."""
        pm = get_progress_manager()
        # Ensure clean state — no session active.
        assert pm.live is None
        # Must be a no-op, no exception.
        _shutdown_progress_live()
        assert pm.live is None

    def test_shutdown_swallows_internal_exceptions(self):
        """If Live.stop() raises, the hook must not propagate (atexit is fragile)."""
        pm = get_progress_manager()
        with (
            pm.session(),
            patch.object(pm.live, "stop", side_effect=RuntimeError("boom")),
        ):
            # Patch Live.stop to raise — atexit must still complete cleanly.
            _shutdown_progress_live()  # must not raise
            # Live reference may still be set since stop() failed — but that's OK,
            # the goal is "don't blow up at atexit time" not "guaranteed cleanup."
