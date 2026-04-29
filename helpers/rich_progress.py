"""Rich-based progress management for fansly-downloader-ng.

This module provides a clean progress bar system using Rich that:
- Handles multiple progress bars automatically
- Allows logs to scroll properly above progress bars
- Requires no complex cursor management
- Works seamlessly with async/threading
- Integrates with loguru logging system

Built on the multi-group / phased-bar design from mymember-downloader.
GROUP_COLUMNS defines named progress groups (status, download, workers,
transfer, encoding) each with its own column layout — callers pass
``group=...`` to ``add_task`` to pick one. PhasedBar/PhasedBarColumn
render multi-colored segments from a ``phase_styles`` field on the task.
"""

import asyncio
import atexit
import contextlib
import os
import tempfile
import threading
import time
from collections.abc import Callable, Generator, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from loguru import logger
from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.live import Live
from rich.logging import RichHandler
from rich.measure import Measurement
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    Task,
    TaskID,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.progress_bar import ProgressBar
from rich.segment import Segment
from rich.style import Style
from rich.table import Column
from rich.text import Text
from rich.theme import Theme


# Global console instance for coordinated output
_console = Console()


class ContextualTimeColumn(ProgressColumn):
    """Custom time column that shows elapsed or remaining time based on task configuration."""

    def __init__(self, table_column: Column | None = None) -> None:
        self.elapsed_column = TimeElapsedColumn()
        self.remaining_column = TimeRemainingColumn()
        super().__init__(table_column)

    def render(self, task: Task) -> Text:
        """Render time based on task configuration."""
        # Check if task has custom fields indicating time preference
        show_elapsed = getattr(task, "fields", {}).get("show_elapsed", None)

        if show_elapsed is True:
            return self.elapsed_column.render(task)
        if show_elapsed is False:
            return self.remaining_column.render(task)
        # Auto-detect based on task name/description patterns
        task_info = (
            f"{task.description} {getattr(task, 'fields', {}).get('name', '')}".lower()
        )

        # Tasks that benefit from elapsed time (exploration/discovery tasks)
        elapsed_patterns = [
            "overall_detection",
            "scanning",
            "detecting",
            "finding",
            "searching",
            "analyzing",
        ]

        # Tasks that benefit from remaining time (processing/completion tasks)
        remaining_patterns = [
            "processing",
            "downloading",
            "extracting",
            "frames",
            "hashing",
            "verifying",
        ]

        # Check for elapsed time patterns first
        for pattern in elapsed_patterns:
            if pattern in task_info:
                return self.elapsed_column.render(task)

        # Check for remaining time patterns
        for pattern in remaining_patterns:
            if pattern in task_info:
                return self.remaining_column.render(task)

        # Default to remaining time for unknown tasks
        return self.remaining_column.render(task)


# ── Phased Progress Bar ──────────────────────────────────────────────────


class PhasedBar:
    """Multi-colored progress bar renderable.

    Renders a bar with multiple colored segments representing different phases
    of work (e.g., downloaded=green, skipped=yellow, errors=red). Uses the
    same ━╸╺ characters as Rich's native ProgressBar for visual consistency.

    Args:
        total: Total number of items (None for indeterminate/pulse).
        phases: Mapping of phase_name → count (e.g., {"downloaded": 5, "skipped": 2}).
        phase_styles: Ordered mapping of phase_name → Rich style string.
            Render order follows iteration order.
        width: Fixed bar width in characters, or None for auto.
        background_style: Style for unfilled portion.
        pulse: Whether to show pulse animation (for unstarted tasks).
        animation_time: Time value for pulse animation.
    """

    BAR = "━"
    HALF_BAR_RIGHT = "╸"
    HALF_BAR_LEFT = "╺"
    ASCII_BAR = "-"

    def __init__(
        self,
        total: float | None,
        phases: dict[str, float],
        phase_styles: dict[str, str],
        width: int | None = None,
        background_style: str = "bar.back",
        pulse: bool = False,
        animation_time: float | None = None,
    ) -> None:
        self.total = total
        self.phases = phases
        self.phase_styles = phase_styles
        self.width = width
        self.background_style = background_style
        self.pulse = pulse
        self.animation_time = animation_time

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        width = min(self.width or options.max_width, options.max_width)
        ascii_mode = options.legacy_windows or options.ascii_only

        # Indeterminate: delegate to a standard ProgressBar for pulse
        if self.pulse or self.total is None:
            fallback = ProgressBar(
                total=None,
                completed=0,
                width=width,
                pulse=True,
                animation_time=self.animation_time,
            )
            yield from fallback.__rich_console__(console, options)
            return

        bar = self.ASCII_BAR if ascii_mode else self.BAR
        half_right = " " if ascii_mode else self.HALF_BAR_RIGHT
        half_left = " " if ascii_mode else self.HALF_BAR_LEFT

        total = max(1, self.total)  # avoid division by zero
        available_halves = width * 2
        used_halves = 0
        last_had_half = False

        # Render each phase as colored segment(s)
        for phase_name, style_str in self.phase_styles.items():
            count = self.phases.get(phase_name, 0)
            if count <= 0:
                continue

            # Proportional halves for this phase, clamped to remaining space.
            # Minimum 1 half-char for any non-zero phase so small counts
            # (e.g., 1 error out of 1000) are always visible.
            phase_halves = min(
                max(1, round(count / total * width * 2)),
                available_halves - used_halves,
            )
            if phase_halves <= 0:
                continue

            full_chars = phase_halves // 2
            has_half = phase_halves % 2 == 1
            style = Style.parse(style_str)

            if full_chars > 0:
                yield Segment(bar * full_chars, style)
            if has_half:
                yield Segment(half_right, style)

            used_halves += phase_halves
            last_had_half = has_half

        # Fill remaining with background (convert halves → full characters)
        remaining_halves = available_halves - used_halves
        if remaining_halves > 0 and not console.no_color and console.color_system:
            bg_style = console.get_style(self.background_style)
            # Smooth transition character at boundary
            if not last_had_half and used_halves > 0:
                yield Segment(half_left, bg_style)
                remaining_halves -= 1
            bg_full = remaining_halves // 2
            bg_half = remaining_halves % 2
            if bg_full > 0:
                yield Segment(bar * bg_full, bg_style)
            if bg_half:
                yield Segment(half_left, bg_style)

    def __rich_measure__(
        self,
        console: Console,
        options: ConsoleOptions,
    ) -> Measurement:
        if self.width is not None:
            return Measurement(self.width, self.width)
        return Measurement(4, options.max_width)


class PhasedBarColumn(ProgressColumn):
    """Progress column that renders multi-colored phase bars.

    Drop-in replacement for BarColumn. When a task has ``phase_styles`` and
    ``phases`` in its fields, renders a PhasedBar with colored segments.
    Otherwise falls back to a standard ProgressBar (identical to BarColumn).

    Phase styles and counts are stored in ``task.fields`` and set via
    ``ProgressManager.add_task(phase_styles=...)`` and
    ``ProgressManager.update_task(phases=...)``.
    """

    def __init__(
        self,
        bar_width: int | None = 40,
        style: str = "bar.back",
        complete_style: str = "bar.complete",
        finished_style: str = "bar.finished",
        pulse_style: str = "bar.pulse",
        table_column: Column | None = None,
    ) -> None:
        self.bar_width = bar_width
        self.style = style
        self.complete_style = complete_style
        self.finished_style = finished_style
        self.pulse_style = pulse_style
        super().__init__(table_column=table_column)

    def render(self, task: Task) -> PhasedBar | ProgressBar:
        """Render a phased bar or fall back to standard bar."""
        phase_styles = task.fields.get("phase_styles")
        phases = task.fields.get("phases")
        width = None if self.bar_width is None else max(1, self.bar_width)

        if phase_styles and phases:
            return PhasedBar(
                total=max(0, task.total) if task.total is not None else None,
                phases=phases,
                phase_styles=phase_styles,
                width=width,
                background_style=self.style,
                pulse=not task.started,
                animation_time=task.get_time(),
            )

        # Standard fallback — identical to BarColumn.render()
        return ProgressBar(
            total=max(0, task.total) if task.total is not None else None,
            completed=max(0, task.completed),
            width=width,
            pulse=not task.started,
            animation_time=task.get_time(),
            style=self.style,
            complete_style=self.complete_style,
            finished_style=self.finished_style,
            pulse_style=self.pulse_style,
        )


class ProgressManager:
    """Manages multiple progress bar groups using Rich.

    Each group is a separate ``Progress`` instance with its own column layout,
    all rendered together in a single ``Live`` display via ``Group``.

    Built-in groups:
        status:   Workflow/site zone bars — description + phased bar only
        download: Content download bars — description + phased bar + MofN + time
        workers:  Worker utilization — description + phased bar only
        transfer: Byte-level downloads — description + bar + speed + remaining
        encoding: FFmpeg muxing — description + bar + percentage + elapsed
        daemon:  Monitoring-loop countdown bars — description + bar + remaining
        daemon_state: Status line for the monitoring daemon — description only
    """

    # Group name → column factory. Defined as class-level so tests can inspect.
    GROUP_COLUMNS: ClassVar[dict[str, list]] = {
        "status": [
            "[progress.description]{task.description}",
            PhasedBarColumn(bar_width=None),
        ],
        "download": [
            "[progress.description]{task.description}",
            PhasedBarColumn(bar_width=None),
            MofNCompleteColumn(),
            ContextualTimeColumn(),
        ],
        "workers": [
            "[progress.description]{task.description}",
            PhasedBarColumn(bar_width=None),
        ],
        "transfer": [
            "[progress.description]{task.description}",
            BarColumn(bar_width=None),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(compact=True),
        ],
        "encoding": [
            "[progress.description]{task.description}",
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.0f}%",
            TimeElapsedColumn(),
        ],
        "daemon": [
            "[progress.description]{task.description}",
            BarColumn(bar_width=None),
            TimeRemainingColumn(compact=True),
        ],
        "daemon_state": [
            "[progress.description]{task.description}",
        ],
    }

    # Default group when callers don't specify one
    DEFAULT_GROUP = "download"

    def __init__(self) -> None:
        self.console = _console  # Use shared console instance

        # Create a Progress instance per group.
        # expand=True fills terminal width (bars become proportional to console);
        # expand=False renders at natural column width (compact layouts like
        # daemon_state where only the description matters).
        _expanded_groups = {"status", "download", "daemon"}
        self._groups: dict[str, Progress] = {}
        for group_name, columns in self.GROUP_COLUMNS.items():
            self._groups[group_name] = Progress(
                *columns,
                console=self.console,
                expand=(group_name in _expanded_groups),
            )

        # Backward compat: self.progress points to the default group
        self.progress = self._groups[self.DEFAULT_GROUP]

        self.live: Live | None = None
        self.active_tasks: dict[str, TaskID] = {}
        self._task_groups: dict[str, str] = {}  # task_name → group_name
        self._lock = threading.Lock()
        self._session_count = 0
        self.session_stack: list[
            set[str]
        ] = []  # Stack of session task sets for auto-cleanup

    def _get_group(self, name: str) -> Progress:
        """Get the Progress instance for a task by name."""
        group_name = self._task_groups.get(name, self.DEFAULT_GROUP)
        return self._groups[group_name]

    def _build_renderable(self) -> Group:
        """Build the Group renderable for Live display.

        Includes all Progress groups unconditionally — empty Progress
        instances render as zero rows and do not disturb sibling
        positions. Swapping children in/out per frame (conditional
        filtering) destabilises Live's frame skeleton and produces
        scrollback churn in tmux. Called once at session start and the
        same Group object is handed to Live; group internals manage
        their own add/remove deltas.
        """
        return Group(*self._groups.values())

    @contextlib.contextmanager
    def session(self, auto_cleanup: bool = True) -> Iterator[None]:
        """Context manager for progress session.

        Multiple sessions can be nested - the display starts when the first
        session begins and stops when the last session ends.

        Args:
            auto_cleanup: If True, automatically remove all progress tasks
                         created within this session when the session exits.
                         Default is True for cleaner progress bar management.
        """
        with self._lock:
            self._session_count += 1
            if self.live is None:
                self.live = Live(
                    self._build_renderable(),
                    console=self.console,
                    refresh_per_second=30,
                )
                self.live.start()

            # Push a new session task set if auto_cleanup is enabled
            if auto_cleanup:
                self.session_stack.append(set())

        try:
            yield
        finally:
            with self._lock:
                self._session_count -= 1

                # Auto-cleanup: remove all tasks created in this session
                if auto_cleanup and self.session_stack:
                    session_tasks = self.session_stack.pop()
                    for task_name in session_tasks:
                        self._remove_task_unlocked(task_name)

                if self._session_count <= 0 and self.live is not None:
                    self.live.stop()
                    self.live = None
                    self._session_count = 0

    def add_task(
        self,
        name: str,
        description: str,
        total: int | None = None,
        parent_task: str | None = None,  # noqa: ARG002
        show_elapsed: bool | None = None,
        group: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Add a new progress task.

        Args:
            name: Unique name for the task
            description: Human-readable description
            total: Total number of items to process. ``None`` for indeterminate
                   (pulsing bar that counts up without a known endpoint).
            parent_task: Optional parent task name (decorative indentation only)
            show_elapsed: Whether to show elapsed time (True) or remaining time (False).
                         None (default) auto-detects based on task name/description patterns.
            group: Progress group name (status, download, workers, transfer, encoding).
                   Defaults to DEFAULT_GROUP.
            **kwargs: Additional fields stored in task.fields. Notable keys:
                phase_styles: OrderedDict mapping phase names to Rich style strings.
                    Enables multi-colored bar rendering via PhasedBarColumn.

        Returns:
            The task name for use with update_task/remove_task
        """
        group_name = group or self.DEFAULT_GROUP
        progress = self._groups.get(group_name, self.progress)

        with self._lock:
            if name in self.active_tasks:
                # Task already exists, just update it
                task_id = self.active_tasks[name]
                self._get_group(name).update(
                    task_id, description=description, total=total
                )
            else:
                final_description = description

                # Build task fields from explicit params + kwargs
                task_fields: dict[str, Any] = {}
                if show_elapsed is not None:
                    task_fields["show_elapsed"] = show_elapsed
                task_fields.update(kwargs)

                task_id = progress.add_task(
                    final_description, total=total, **task_fields
                )
                self.active_tasks[name] = task_id
                self._task_groups[name] = group_name

                # Track task in current session for auto-cleanup
                if self.session_stack:
                    self.session_stack[-1].add(name)

            return name

    def update_task(
        self, name: str, advance: int = 1, description: str | None = None, **kwargs: Any
    ) -> None:
        """Update progress for a task.

        Args:
            name: Task name
            advance: Number of items to advance (default 1)
            description: New description (optional)
            **kwargs: Additional update parameters for Rich. Notable keys:
                phases: Dict mapping phase names to counts. When provided,
                    ``completed`` is auto-computed as ``sum(phases.values())``
                    and ``advance`` is ignored — unless ``completed`` is also
                    passed explicitly, in which case it takes precedence.
                    This allows callers to include non-terminal display phases
                    (e.g. "processing", "queued") for PhasedBarColumn coloring
                    without inflating the numerical completion counter.
                completed: Explicit completed count. When passed alongside
                    ``phases``, overrides the auto-computed sum.
        """
        with self._lock:
            if name in self.active_tasks:
                update_kwargs: dict[str, Any] = {}

                # Phased progress: auto-compute completed from phase counts,
                # but allow explicit 'completed' to override (for non-terminal phases)
                phases = kwargs.get("phases")
                explicit_completed = kwargs.get("completed")
                if phases is not None:
                    if explicit_completed is not None:
                        update_kwargs["completed"] = explicit_completed
                    else:
                        update_kwargs["completed"] = sum(phases.values())
                else:
                    update_kwargs["advance"] = advance

                if description:
                    update_kwargs["description"] = description

                # Remaining kwargs (including 'phases') flow into task.fields
                update_kwargs.update(kwargs)
                self._get_group(name).update(self.active_tasks[name], **update_kwargs)

    def _remove_task_unlocked(self, name: str) -> None:
        """Remove a task without acquiring the lock. Caller must hold _lock."""
        if name in self.active_tasks:
            self._get_group(name).remove_task(self.active_tasks[name])
            del self.active_tasks[name]
            self._task_groups.pop(name, None)

    def hide_task(self, name: str) -> None:
        """Hide a task from display without removing it.

        The task remains in active_tasks so its fields can still be read.
        Use this for transient bars that should disappear on completion
        but may be referenced later.
        """
        with self._lock:
            if name in self.active_tasks:
                self._get_group(name).update(self.active_tasks[name], visible=False)

    def remove_task(self, name: str) -> None:
        """Remove a completed task.

        Args:
            name: Task name to remove
        """
        with self._lock:
            self._remove_task_unlocked(name)

    def get_task_fields(self, name: str) -> dict[str, Any]:
        """Get a task's fields dict (for reading phases, etc.)."""
        with self._lock:
            task_id = self.active_tasks.get(name)
            if task_id is None:
                return {}
            return dict(self._get_group(name)._tasks[task_id].fields)

    def get_active_count(self) -> int:
        """Get the number of active tasks.

        Returns:
            Number of currently active progress tasks
        """
        with self._lock:
            return len(self.active_tasks)


# Global progress manager instance
_progress_manager = ProgressManager()


@atexit.register
def _shutdown_progress_live() -> None:
    """Stop Rich's Live refresh thread cleanly before interpreter teardown.

    ``Live.start()`` spawns a daemon thread that periodically writes ANSI
    cursor sequences to stdout. If the process exits while a session is
    still open (KeyboardInterrupt mid-download, pytest-xdist worker
    shutdown after a test left a session open, etc.),
    ``_Py_Finalize.flush_std_files`` runs while the daemon thread is
    mid-buffered-write — triggering CPython's ``_enter_buffered_busy``
    fatal error and aborting the process with SIGABRT. ``atexit`` fires
    before ``_Py_Finalize``, so calling ``Live.stop()`` here joins the
    refresh thread cleanly first.
    """
    with contextlib.suppress(Exception), _progress_manager._lock:
        if _progress_manager.live is not None:
            _progress_manager.live.stop()
            _progress_manager.live = None
            _progress_manager._session_count = 0


def get_progress_manager() -> ProgressManager:
    """Get the global progress manager instance.

    Returns:
        The global ProgressManager instance
    """
    return _progress_manager


def get_rich_console() -> Console:
    """Get the global Rich console instance.

    This console should be used for all console output to ensure
    proper coordination with progress bars.

    Returns:
        The global Rich Console instance
    """
    return _console


def _format_log_time(dt: datetime) -> Text:
    """Format datetime to show milliseconds instead of microseconds."""
    return Text(f"[{dt.strftime('%H:%M:%S')}.{dt.microsecond // 1000:03d}]")


async def handle_download_progress(
    chunk: bytes,
    downloaded: int,
    total: int,
    callback: Callable[[int, int], None] | None,
) -> int:
    """Handle download progress updates with optional callback.

    This function is designed to work with download operations that process
    data in chunks and need to report progress through callbacks.

    Args:
        chunk: The data chunk that was just downloaded
        downloaded: Bytes downloaded so far (before this chunk)
        total: Total bytes to download
        callback: Optional callback function to report progress

    Returns:
        Updated downloaded byte count (after adding this chunk)
    """

    downloaded += len(chunk)
    if callback is not None:
        await asyncio.to_thread(callback, downloaded, total)
    return downloaded


def create_rich_handler(
    level_styles: dict[str, str] | None = None,
) -> RichHandler:
    """Create a RichHandler with custom styling.

    Args:
        level_styles: Optional dictionary mapping level names to Rich styles

    Returns:
        A configured RichHandler instance that integrates with the global console
    """
    # Default level styles based on custom levels if not provided
    if level_styles is None:
        level_styles = {
            "INFO": "bright_blue",
            "DEBUG": "bright_red",
            "WARNING": "yellow",
            "ERROR": "bold red",
            "-INFO-": "bold cyan",
            "UPDATE": "green",
            "CONFIG": "bright_magenta",
        }

    # Create Rich handler with the shared console instance
    handler = RichHandler(
        console=_console,
        show_path=True,
        enable_link_path=False,  # Disable link formatting to prevent width issues
        markup=True,
        rich_tracebacks=True,
        tracebacks_show_locals=True,
        log_time_format=_format_log_time,
        omit_repeated_times=False,
        show_time=True,
        keywords=[],  # No additional highlighting
    )

    # Update console theme if needed (from Python 3.11's rich)
    if hasattr(_console, "push_theme"):
        custom_theme = Theme(
            {
                f"logging.level.{name.lower()}": style
                for name, style in level_styles.items()
            }
        )
        _console.push_theme(custom_theme)

    return handler


def _do_watch_progress(
    progress_file: Path,
    handler: Callable[[str, str | None], None],
    stop_event: threading.Event | None = None,
) -> None:
    """Function to run in a separate thread to read progress events from a file.

    Adapted from ffmpeg-python's show_progress.py _do_watch_progress() but using
    file polling instead of socket reading.

    Args:
        progress_file: Path to the progress file written by FFmpeg
        handler: Callback function receiving (key, value) pairs
        stop_event: Optional Event; when set, the read loop exits cleanly.
            Without it, a killed FFmpeg leaves this daemon thread spinning
            indefinitely on time.sleep(0.05), which can race _Py_Finalize.
    """
    try:
        # Wait for file to be created (FFmpeg creates it on start)
        timeout = 10
        start_time = time.time()
        while not progress_file.exists():
            if stop_event is not None and stop_event.is_set():
                return
            if time.time() - start_time > timeout:
                return
            time.sleep(0.1)

        # Read progress events from file
        with progress_file.open(encoding="utf-8") as f:
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                line = f.readline()
                if not line:
                    # No more data, check if FFmpeg is still running
                    time.sleep(0.05)
                    continue

                line = line.strip()
                if not line:
                    continue

                # Parse key=value format
                parts = line.split("=", 1)
                key = parts[0] if len(parts) > 0 else ""
                value = parts[1] if len(parts) > 1 else None
                handler(key, value)

                # Stop when progress ends
                if key == "progress" and value == "end":
                    break
    except Exception as exc:
        # Daemon thread — we can't let exceptions propagate or they get
        # silently lost. Log at DEBUG so FFmpeg progress hiccups don't
        # spam normal output but remain diagnosable if needed.
        logger.debug("rich_progress: progress watcher error - {}", exc)


@contextlib.contextmanager
def _watch_progress(
    handler: Callable[[str, str | None], None],
) -> Generator[Path, None, None]:
    """Context manager for creating a temp file and monitoring FFmpeg progress.

    Adapted from ffmpeg-python's show_progress.py _watch_progress() but using
    temp files instead of Unix-domain sockets for cross-platform support.

    Args:
        handler: Function called for each progress event with (key, value)

    Yields:
        progress_file: Path to the temp file for FFmpeg to write progress to
    """
    # Create temp file for progress output
    fd, progress_filename = tempfile.mkstemp(suffix=".txt", prefix="ffmpeg_progress_")
    progress_file = Path(progress_filename)

    try:
        os.close(fd)  # Close the file descriptor, FFmpeg will open it

        # Start monitoring thread with cooperative-cancellation event so a
        # killed FFmpeg (no "progress=end" line) doesn't strand the daemon.
        stop_event = threading.Event()
        monitor_thread = threading.Thread(
            target=_do_watch_progress,
            args=(progress_file, handler, stop_event),
            daemon=True,
        )
        monitor_thread.start()

        try:
            yield progress_file
        finally:
            # Signal the watcher to exit, then wait briefly.
            stop_event.set()
            monitor_thread.join(timeout=2.0)
    finally:
        # Clean up temp file
        with contextlib.suppress(Exception):
            progress_file.unlink(missing_ok=True)


@contextlib.contextmanager
def ffmpeg_progress(
    total_duration: float,
    task_name: str = "ffmpeg_muxing",
    description: str = "Muxing video",
    parent_task: str | None = None,
    show_elapsed: bool = False,
) -> Generator[Path, None, None]:
    """Context manager for monitoring FFmpeg progress with Rich progress bar.

    Adapted from ffmpeg-python's show_progress.py but using Rich progress bars
    instead of tqdm and temp files instead of Unix sockets (cross-platform).

    Args:
        total_duration: Total video duration in seconds
        task_name: Unique task name for progress tracking
        description: Human-readable description for progress bar
        parent_task: Optional parent task name for nested progress display
        show_elapsed: Show elapsed time (True) or remaining time (False)

    Yields:
        progress_file: Path to temp file for FFmpeg -progress argument

    Example:
        >>> probe = ffmpeg.probe("input.mp4")
        >>> duration = float(probe["format"]["duration"])
        >>> stream = ffmpeg.input("input.mp4").output("output.mp4", c="copy")
        >>> with ffmpeg_progress(duration, description="Converting") as progress_file:
        ...     stream.global_args("-progress", str(progress_file)).overwrite_output().run()
    """
    progress_manager = get_progress_manager()

    # Calculate total (duration in centiseconds for smooth updates).
    # When duration is unknown (≤0), use indeterminate (pulsing) bar.
    known_duration = total_duration > 0
    total: int | None = round(total_duration * 100) if known_duration else None

    def progress_handler(key: str, value: str | None) -> None:
        """Handler called for each progress event from FFmpeg."""
        if key == "out_time_ms" and value:
            with contextlib.suppress(ValueError, TypeError):
                time_us = float(value)
                current_time = round(time_us / 1_000_000.0, 2)  # Convert to seconds

                if known_duration:
                    # Update progress bar (time in centiseconds)
                    progress = int(min(current_time, total_duration) * 100)
                    progress_manager.update_task(
                        task_name, completed=progress, advance=0
                    )
                else:
                    # Indeterminate: update description with elapsed time
                    progress_manager.update_task(
                        task_name,
                        advance=0,
                        description=f"{description} ({current_time:.0f}s)",
                    )
        elif key == "progress" and value == "end" and total is not None:
            # Ensure progress bar reaches 100%
            progress_manager.update_task(task_name, completed=total, advance=0)

    # Add progress task
    progress_manager.add_task(
        task_name,
        description,
        total,
        parent_task=parent_task,
        show_elapsed=show_elapsed if known_duration else True,
        group="encoding",
    )

    try:
        # Use nested context manager for progress watching
        with _watch_progress(progress_handler) as progress_file:
            yield progress_file
    finally:
        # Clean up progress task
        progress_manager.remove_task(task_name)
