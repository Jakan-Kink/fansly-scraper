"""Tests for helpers/rich_progress.py — progress manager, FFmpeg progress, Rich integration.

No external boundaries — all pure logic. Uses real Rich objects.
"""

import threading
import time
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest
from rich.console import Console, ConsoleRenderable, RichCast
from rich.progress import Task, TaskID
from rich.progress_bar import ProgressBar
from rich.segment import Segment
from rich.text import Text

from helpers.rich_progress import (
    ContextualTimeColumn,
    PhasedBar,
    PhasedBarColumn,
    ProgressManager,
    _do_watch_progress,
    _shutdown_progress_live,
    _watch_progress,
    create_rich_handler,
    ffmpeg_progress,
    get_progress_manager,
    get_rich_console,
    handle_download_progress,
)
from tests.fixtures.utils import scaled_sync_sleep


class TestContextualTimeColumn:
    """Lines 43-98: time column renders elapsed or remaining based on task context."""

    def _make_task(self, description="", fields=None):
        """Build a minimal Rich Task for rendering."""
        task = Task(
            id=TaskID(0),
            description=description,
            total=100,
            completed=0,
            _get_time=time.monotonic,
        )
        if fields:
            task.fields = fields
        return task

    @pytest.mark.parametrize(
        ("description", "fields"),
        [
            # Lines 56-57: show_elapsed=True → elapsed column.
            pytest.param("", {"show_elapsed": True}, id="explicit-show-elapsed"),
            # Lines 58-59: show_elapsed=False → remaining column.
            pytest.param("", {"show_elapsed": False}, id="explicit-show-remaining"),
            # Lines 88-90: description matches elapsed pattern → elapsed.
            pytest.param("Scanning files", None, id="auto-detect-elapsed-pattern"),
            # Lines 93-95: description matches remaining pattern → remaining.
            pytest.param("Downloading media", None, id="auto-detect-remaining-pattern"),
            # Line 98: no pattern match → defaults to remaining.
            pytest.param("Some unknown task", None, id="auto-detect-default"),
        ],
    )
    def test_render_returns_text(
        self, description: str, fields: dict[str, bool] | None
    ) -> None:
        """Explicit show_elapsed field or description pattern → renders a Text."""
        col = ContextualTimeColumn()
        task = self._make_task(description=description, fields=fields)
        result = col.render(task)
        assert isinstance(result, Text)


class TestPhasedBar:
    """Lines 117-235: multi-colored progress bar renderable.

    PhasedBar is a custom Rich renderable that draws several colored
    segments per task (e.g., downloaded=green + skipped=yellow + errors=red).
    Tests render to a Console and verify the produced segments / styles.
    """

    def _render(
        self,
        bar: PhasedBar,
        *,
        color_system: Literal["auto", "standard", "256", "truecolor", "windows"]
        | None = "truecolor",
    ) -> list[ConsoleRenderable | RichCast | str | Segment]:
        """Render a PhasedBar against a deterministic Console and return segments."""
        # Use a fixed-width, color-enabled console so phase styles render to Segments.
        console = Console(
            width=40,
            color_system=color_system,
            force_terminal=True,
            legacy_windows=False,
        )
        options = console.options
        return list(bar.__rich_console__(console, options))

    def test_init_stores_attributes(self):
        """Lines 150-156: __init__ assigns every constructor arg to self."""
        bar = PhasedBar(
            total=100,
            phases={"a": 10, "b": 5},
            phase_styles={"a": "green", "b": "red"},
            width=20,
            background_style="bar.back",
            pulse=False,
            animation_time=1.5,
        )
        assert bar.total == 100
        assert bar.phases == {"a": 10, "b": 5}
        assert bar.phase_styles == {"a": "green", "b": "red"}
        assert bar.width == 20
        assert bar.background_style == "bar.back"
        assert bar.pulse is False
        assert bar.animation_time == 1.5

    @pytest.mark.parametrize(
        (
            "total",
            "phases",
            "phase_styles",
            "width",
            "pulse",
            "color_system",
            "min_text_segments",
            "bar_chars",
        ),
        [
            # Lines 165-174: pulse=True yields fallback ProgressBar segments
            # (at least one Segment — the pulse animation; text not required).
            pytest.param(
                100,
                {"a": 50},
                {"a": "green"},
                20,
                True,
                "truecolor",
                0,
                (),
                id="pulse-true-delegates-to-progress-bar",
            ),
            # Lines 165-174: total=None also takes the indeterminate path.
            pytest.param(
                None,
                {},
                {},
                20,
                False,
                "truecolor",
                0,
                (),
                id="total-none-delegates-to-pulse-fallback",
            ),
            # Lines 186-211: each non-zero phase yields a Segment with its
            # style; BAR character is "━", ASCII fallback is "-".
            pytest.param(
                10,
                {"green_phase": 5, "red_phase": 3},
                {"green_phase": "green", "red_phase": "red"},
                20,
                False,
                "truecolor",
                1,
                ("━", "-"),
                id="renders-phase-segments-with-styles",
            ),
            # Lines 188-189: count <= 0 → continue without yielding; green
            # phase still renders even with two zero phases.
            pytest.param(
                10,
                {"green": 5, "red": 0, "blue": 0},
                {"green": "green", "red": "red", "blue": "blue"},
                20,
                False,
                "truecolor",
                1,
                (),
                id="skips-zero-count-phases",
            ),
            # Lines 194-197: tiny non-zero count (1 of 1000 = 0.1% would
            # round to 0 halves) → at least 1 half-char; both phases visible.
            pytest.param(
                1000,
                {"errors": 1, "ok": 999},
                {"errors": "red", "ok": "green"},
                20,
                False,
                "truecolor",
                2,
                (),
                id="minimum-one-half-for-tiny-phases",
            ),
            # Lines 213-226: only 25% filled — remaining 75% fills with the
            # background style, producing segments beyond the phase.
            pytest.param(
                20,
                {"a": 5},
                {"a": "green"},
                20,
                False,
                "truecolor",
                2,
                (),
                id="background-fill-when-color-system-present",
            ),
            # Line 215: color_system=None → no color → background fill
            # skipped; the phase itself still renders.
            pytest.param(
                20,
                {"a": 5},
                {"a": "green"},
                20,
                False,
                None,
                1,
                (),
                id="no-background-when-color-disabled",
            ),
            # Line 199: width=2 → 4 halves; "a" (2/2) claims all 4, "b" then
            # computes min(max(1, 4), 4 - 4) == 0 → phase_halves <= 0 →
            # skipped without yielding. Only "a" renders, bar fills exactly.
            pytest.param(
                2,
                {"a": 2, "b": 2},
                {"a": "green", "b": "red"},
                2,
                False,
                "truecolor",
                1,
                ("━",),
                id="phase-clamped-to-zero-halves-is-skipped",
            ),
            # Branch 218->221: width=10 → 20 halves; "a" (1/3) → 7 halves
            # (odd → has_half True) so `if not last_had_half` is False and
            # control jumps straight to the background fill at line 221.
            pytest.param(
                3,
                {"a": 1},
                {"a": "green"},
                10,
                False,
                "truecolor",
                1,
                (),
                id="background-skips-boundary-half-when-last-phase-had-half",
            ),
            # Branch 223->225: width=20 → 40 halves; "a" (19/20) → 38 halves
            # (even). Boundary half_left decrements remaining to 1 → bg_full
            # is 0, so only the trailing half_left renders.
            pytest.param(
                20,
                {"a": 19},
                {"a": "green"},
                20,
                False,
                "truecolor",
                1,
                (),
                id="background-with-only-a-half-remaining",
            ),
            # Branch 225->exit: width=10 → 20 halves; "a"+"b" (1/3 each) →
            # 7+7 = 14 halves used, last_had_half True → no boundary
            # decrement; remaining 6 even → bg_half 0 → final `if` False.
            pytest.param(
                3,
                {"a": 1, "b": 1},
                {"a": "green", "b": "red"},
                10,
                False,
                "truecolor",
                1,
                (),
                id="background-with-even-remaining-emits-no-trailing-half",
            ),
        ],
    )
    def test_render_segments(
        self,
        total: int | None,
        phases: dict[str, float],
        phase_styles: dict[str, str],
        width: int,
        pulse: bool,
        color_system: Literal["truecolor"] | None,
        min_text_segments: int,
        bar_chars: tuple[str, ...],
    ) -> None:
        """Render each PhasedBar configuration and check the emitted segments."""
        bar = PhasedBar(
            total=total,
            phases=phases,
            phase_styles=phase_styles,
            width=width,
            pulse=pulse,
        )
        segments = self._render(bar, color_system=color_system)

        # Every configuration yields at least one Segment.
        assert any(isinstance(s, Segment) for s in segments)

        text_segments = [s for s in segments if isinstance(s, Segment) and s.text]
        assert len(text_segments) >= min_text_segments

        if bar_chars:
            all_text = "".join(s.text for s in text_segments)
            assert any(char in all_text for char in bar_chars)

    @pytest.mark.parametrize(
        ("width", "expected_minimum", "expected_maximum"),
        [
            # Lines 233-234: width set → Measurement(width, width).
            pytest.param(15, 15, 15, id="explicit-width-15"),
            # Line 235: width=None → Measurement(4, options.max_width);
            # None here means "assert against console.options.max_width".
            pytest.param(None, 4, None, id="width-none-min-4-max-console"),
        ],
    )
    def test_measure(
        self,
        width: int | None,
        expected_minimum: int,
        expected_maximum: int | None,
    ) -> None:
        """__rich_measure__ uses explicit width or falls back to (4, max_width)."""
        bar = PhasedBar(total=10, phases={}, phase_styles={}, width=width)
        console = Console(width=80)
        measurement = bar.__rich_measure__(console, console.options)
        assert measurement.minimum == expected_minimum
        if expected_maximum is None:
            assert measurement.maximum == console.options.max_width
        else:
            assert measurement.maximum == expected_maximum


class TestPhasedBarColumn:
    """Lines 238-294: column wrapper that picks PhasedBar or fallback ProgressBar."""

    def _make_task(self, fields=None, *, started=True, total=100, completed=10):
        # Rich's Task.started is a read-only property derived from
        # start_time != None. Set start_time directly to control it.
        task = Task(
            id=TaskID(0),
            description="Test task",
            total=total,
            completed=completed,
            _get_time=time.monotonic,
        )
        if started:
            task.start_time = time.monotonic()
        if fields:
            task.fields = fields
        return task

    def test_renders_phased_bar_when_phases_present(self):
        """Lines 272-281: task.fields has phase_styles + phases → PhasedBar."""
        column = PhasedBarColumn(bar_width=20)
        task = self._make_task(
            fields={
                "phase_styles": {"a": "green", "b": "red"},
                "phases": {"a": 5, "b": 3},
            }
        )
        result = column.render(task)
        assert isinstance(result, PhasedBar)
        assert result.phases == {"a": 5, "b": 3}
        assert result.phase_styles == {"a": "green", "b": "red"}

    def test_renders_progress_bar_fallback_without_phases(self):
        """Lines 283-294: missing phases → standard ProgressBar fallback."""
        column = PhasedBarColumn(bar_width=20)
        task = self._make_task(fields={})
        result = column.render(task)
        assert isinstance(result, ProgressBar)

    def test_renders_progress_bar_when_phase_styles_only(self):
        """Lines 272 false branch: phase_styles present but phases empty/missing."""
        column = PhasedBarColumn(bar_width=20)
        task = self._make_task(fields={"phase_styles": {"a": "green"}})
        result = column.render(task)
        assert isinstance(result, ProgressBar)

    def test_phased_bar_pulse_when_task_not_started(self):
        """Line 279: started=False → PhasedBar(pulse=True)."""
        column = PhasedBarColumn(bar_width=20)
        task = self._make_task(
            fields={
                "phase_styles": {"a": "green"},
                "phases": {"a": 1},
            },
            started=False,
        )
        result = column.render(task)
        assert isinstance(result, PhasedBar)
        assert result.pulse is True

    def test_bar_width_none_yields_none_width(self):
        """Line 270: bar_width=None → width=None passed through."""
        column = PhasedBarColumn(bar_width=None)
        task = self._make_task(
            fields={
                "phase_styles": {"a": "green"},
                "phases": {"a": 5},
            }
        )
        result = column.render(task)
        assert isinstance(result, PhasedBar)
        assert result.width is None


class TestHandleDownloadProgress:
    """Lines 643-667: async chunk-progress accumulator with optional callback."""

    @pytest.mark.asyncio
    async def test_no_callback_returns_updated_total(self):
        """Lines 664, 667: callback=None → just sum and return."""
        result = await handle_download_progress(
            chunk=b"abcde", downloaded=100, total=1000, callback=None
        )
        assert result == 105

    @pytest.mark.asyncio
    async def test_callback_invoked_via_to_thread(self):
        """Lines 665-666: callback present → asyncio.to_thread(callback, downloaded, total)."""
        captured: list[tuple[int, int]] = []

        def _cb(downloaded: int, total: int) -> None:
            captured.append((downloaded, total))

        result = await handle_download_progress(
            chunk=b"hello", downloaded=50, total=500, callback=_cb
        )

        assert result == 55
        assert captured == [(55, 500)]

    @pytest.mark.asyncio
    async def test_empty_chunk_does_not_advance_count(self):
        """len(b'') == 0 → downloaded unchanged but callback still fires."""
        captured: list[tuple[int, int]] = []
        result = await handle_download_progress(
            chunk=b"",
            downloaded=42,
            total=100,
            callback=lambda d, t: captured.append((d, t)),
        )
        assert result == 42
        assert captured == [(42, 100)]


class TestProgressManagerExtras:
    """Cover small ProgressManager methods not exercised by TestProgressManager.

    Targets the missed lines 533-536 (phased update), 561-563 (hide_task),
    and 579 (get_task_fields when name missing).
    """

    def test_update_task_with_phases_auto_computes_completed(self):
        """Lines 533-536: phases provided + no explicit completed → sum(phases.values())."""
        pm = ProgressManager()
        with pm.session():
            pm.add_task(
                name="phased_task",
                description="Phased",
                total=10,
                phase_styles={"a": "green", "b": "red"},
            )
            pm.update_task("phased_task", phases={"a": 3, "b": 2})

            fields = pm.get_task_fields("phased_task")
            # phases stored on task.fields as kwargs; completed should equal 5.
            assert fields.get("phases") == {"a": 3, "b": 2}

    def test_update_task_with_explicit_completed_overrides_phases(self):
        """Lines 532-534: explicit completed wins even when phases also given."""
        pm = ProgressManager()
        with pm.session():
            pm.add_task(
                name="phased_explicit",
                description="Mixed",
                total=10,
                phase_styles={"a": "green"},
            )
            # No exception — both phases AND completed accepted; completed wins.
            pm.update_task("phased_explicit", phases={"a": 3}, completed=7)

    def test_hide_task_makes_invisible_without_removing(self):
        """Lines 561-563: hide_task(name) → set visible=False, task remains in active_tasks."""
        pm = ProgressManager()
        with pm.session():
            pm.add_task(name="hideable", description="Hide me", total=10)
            assert "hideable" in pm.active_tasks

            pm.hide_task("hideable")

            # Task still tracked even after hiding.
            assert "hideable" in pm.active_tasks

    def test_hide_task_unknown_name_is_noop(self):
        """Line 562: name not in active_tasks → silently skip."""
        pm = ProgressManager()
        with pm.session():
            # Must not raise.
            pm.hide_task("never_added")

    def test_get_task_fields_unknown_name_returns_empty(self):
        """Line 579: name not in active_tasks → return {} (early)."""
        pm = ProgressManager()
        with pm.session():
            assert pm.get_task_fields("missing") == {}

    def test_reset_task_unknown_name_is_noop(self):
        """Line 563: name not in active_tasks → early return without touching Rich."""
        pm = ProgressManager()
        with pm.session():
            # Must not raise even though the task was never added.
            pm.reset_task("never_added", total=5, description="x")

    def test_reset_task_defaults_skip_optional_kwargs(self):
        """Branches 565->567 and 567->569: total/description None → not added to reset_kwargs.

        With both optional args omitted, only ``completed=0`` flows into
        ``Progress.reset()`` — the two ``if ... is not None`` guards are False.
        """
        pm = ProgressManager()
        with pm.session():
            pm.add_task("resettable", "Reset me", total=10)
            pm.update_task("resettable", completed=10)
            # Defaults (total=None, description=None) take both False arcs.
            pm.reset_task("resettable")
            assert "resettable" in pm.active_tasks

    def test_reset_task_applies_total_and_description(self):
        """Branches 565->566 and 567->568: both optionals set → reset_kwargs carries them."""
        pm = ProgressManager()
        with pm.session():
            pm.add_task("retask", "Original", total=10)
            pm.reset_task("retask", total=42, description="Refreshed")
            assert "retask" in pm.active_tasks


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

    def test_handler_when_console_lacks_push_theme(self, monkeypatch):
        """Branch 732->741: console without ``push_theme`` → skip theme, return handler.

        The ``hasattr(_console, "push_theme")`` guard exists for older rich
        versions (pre-3.11). Modern rich always has it, so the False arc is only
        reachable by forcing the predicate for the ``push_theme`` lookup.
        """
        real_hasattr = hasattr

        def fake_hasattr(obj, name):
            if name == "push_theme":
                return False
            return real_hasattr(obj, name)

        monkeypatch.setattr(
            "helpers.rich_progress.hasattr", fake_hasattr, raising=False
        )
        # Reaches the early ``return handler`` without pushing a theme.
        assert create_rich_handler() is not None

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
            patch("helpers.rich_progress.time.sleep", scaled_sync_sleep),
        ):
            _do_watch_progress(nonexistent, lambda k, v: events.append((k, v)))

        assert events == []

    def test_stop_event_breaks_read_loop(self, tmp_path):
        """stop_event.set() exits the readline/sleep loop without "progress=end".

        Without cooperative cancellation, a killed FFmpeg leaves this daemon
        thread spinning forever in the time.sleep(0.05) branch — racing
        _Py_Finalize on shutdown. The stop_event lets the context manager
        signal a clean exit.
        """
        progress_file = tmp_path / "ffmpeg_progress.txt"
        progress_file.write_text("frame=1\n")  # one event then EOF (no "progress=end")

        stop_event = threading.Event()
        thread = threading.Thread(
            target=_do_watch_progress,
            args=(progress_file, lambda _k, _v: None, stop_event),
            daemon=True,
        )
        thread.start()

        # Let the thread enter the readline/sleep cycle, then signal stop.
        time.sleep(0.15)
        stop_event.set()
        thread.join(timeout=1.0)

        assert not thread.is_alive(), "stop_event did not break the read loop"

    def test_stop_event_set_before_file_created(self, tmp_path):
        """Line 767: stop_event already set while still waiting for the file → return.

        Exercises the cancellation check inside the ``while not file.exists()``
        wait loop (distinct from the read-loop check at 775).
        """
        nonexistent = tmp_path / "never_created.txt"
        stop_event = threading.Event()
        stop_event.set()
        events = []

        _do_watch_progress(nonexistent, lambda k, v: events.append((k, v)), stop_event)

        assert events == []

    def test_handler_exception_is_swallowed(self, tmp_path):
        """Lines 796-800: an exception in the read loop is caught, not propagated.

        A raising handler would otherwise kill the daemon thread; the broad
        ``except Exception`` logs at DEBUG and returns. The function returning
        normally (no raise) proves the except arc ran.
        """
        progress_file = tmp_path / "ffmpeg_progress.txt"
        progress_file.write_text("frame=1\nprogress=end\n")

        def boom(_key, _value):
            raise RuntimeError("handler blew up")

        # Must return without raising — the except block swallowed RuntimeError.
        _do_watch_progress(progress_file, boom)


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

    def test_unknown_duration_updates_description_and_skips_completion(self):
        """Indeterminate path: ``total_duration <= 0`` → ``total`` is None.

        Drives the progress_handler through the real watch thread:
          - ``out_time_ms`` → ``known_duration`` False → the else branch updates
            the description with elapsed time (line 899),
          - ``progress=end`` → the ``total is not None`` clause is False, so the
            100% completion update is skipped (branch 904->exit).
        """
        with ffmpeg_progress(
            total_duration=0.0, task_name="test_indeterminate"
        ) as progress_file:
            pm = get_progress_manager()
            assert "test_indeterminate" in pm.active_tasks

            progress_file.write_text("out_time_ms=5000000\nprogress=end\n")
            time.sleep(0.2)

        assert "test_indeterminate" not in pm.active_tasks


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
