"""Rich-based progress management for fansly-downloader-ng.

This module provides a clean progress bar system using Rich that:
- Handles multiple progress bars automatically
- Allows logs to scroll properly above progress bars
- Requires no complex cursor management
- Works seamlessly with async/threading
- Integrates with loguru logging system
"""

import os
import tempfile
import threading
import time
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    Task,
    TaskID,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Column
from rich.text import Text
from rich.theme import Theme


# Global console instance for coordinated output
_console = Console()


class ContextualTimeColumn(ProgressColumn):
    """Custom time column that shows elapsed or remaining based on task type."""

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

        # Tasks that benefit from elapsed time (exploration/discovery)
        elapsed_patterns = [
            "scanning",
            "detecting",
            "finding",
            "searching",
            "analyzing",
            "inspecting",
        ]

        # Tasks that benefit from remaining time (processing/completion)
        remaining_patterns = [
            "processing",
            "downloading",
            "extracting",
            "hashing",
            "verifying",
            "deduplicating",
            "uploading",
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


class ProgressManager:
    """Manages multiple progress bars using Rich."""

    def __init__(self) -> None:
        self.console = _console  # Use shared console instance
        self.progress = Progress(
            "[progress.description]{task.description}",
            BarColumn(bar_width=None),  # Auto-width bar
            MofNCompleteColumn(),
            "[progress.percentage]{task.percentage:>3.0f}%",
            ContextualTimeColumn(),
            console=self.console,
            expand=True,  # Use full console width
        )
        self.live: Live | None = None
        self.active_tasks: dict[str, TaskID] = {}
        self._lock = threading.Lock()
        self._session_count = 0
        self.session_stack: list[
            set[str]
        ] = []  # Stack of session task sets for auto-cleanup

    @contextmanager
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
                    self.progress, console=self.console, refresh_per_second=10
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
                        if task_name in self.active_tasks:
                            self.progress.remove_task(self.active_tasks[task_name])
                            del self.active_tasks[task_name]

                if self._session_count <= 0 and self.live is not None:
                    self.live.stop()
                    self.live = None
                    self._session_count = 0

    def add_task(
        self,
        name: str,
        description: str,
        total: int,
        parent_task: str | None = None,
        show_elapsed: bool | None = None,
    ) -> str:
        """Add a new progress task.

        Args:
            name: Unique name for the task
            description: Human-readable description
            total: Total number of items to process
            parent_task: Optional parent task name for nesting
            show_elapsed: Whether to show elapsed time (True) or remaining (False).
                         None (default) auto-detects based on patterns.

        Returns:
            The task name for use with update_task/remove_task
        """
        with self._lock:
            if name in self.active_tasks:
                # Task already exists, just update it
                task_id = self.active_tasks[name]
                self.progress.update(task_id, description=description, total=total)
            else:
                # Handle nested tasks by modifying description
                final_description = description
                if parent_task and parent_task in self.active_tasks:
                    # Add indentation for nested appearance
                    final_description = f"  ├─ {description}"

                # Create task with show_elapsed preference in fields
                task_fields = (
                    {"show_elapsed": show_elapsed} if show_elapsed is not None else {}
                )
                task_id = self.progress.add_task(
                    final_description, total=total, **task_fields
                )
                self.active_tasks[name] = task_id

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
            **kwargs: Additional update parameters for Rich
        """
        with self._lock:
            if name in self.active_tasks:
                update_kwargs = {"advance": advance, **kwargs}
                if description:
                    update_kwargs["description"] = description
                self.progress.update(self.active_tasks[name], **update_kwargs)

    def remove_task(self, name: str) -> None:
        """Remove a completed task.

        Args:
            name: Task name to remove
        """
        with self._lock:
            if name in self.active_tasks:
                self.progress.remove_task(self.active_tasks[name])
                del self.active_tasks[name]

    def get_active_count(self) -> int:
        """Get the number of active tasks.

        Returns:
            Number of currently active progress tasks
        """
        with self._lock:
            return len(self.active_tasks)


# Global progress manager instance
_progress_manager = ProgressManager()


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
    """Format datetime for RichHandler — HH:MM:SS.mmm."""
    return Text(f"[{dt.strftime('%H:%M:%S')}.{dt.microsecond // 1000:03d}]")


def create_rich_handler(
    level_styles: dict[str, str] | None = None,
) -> RichHandler:
    """Create a RichHandler bound to the shared global console.

    Using this handler as a loguru sink ensures log output is
    coordinated with the ProgressManager's Live display — Rich
    pauses the progress bar, prints the log line above it, then
    re-renders the bar.  Writing to sys.stdout directly would
    bypass this coordination and cause duplicated/striped bars.

    Args:
        level_styles: Optional mapping of level names to Rich styles.

    Returns:
        A configured RichHandler sharing the global console.
    """
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

    handler = RichHandler(
        console=_console,
        show_path=False,
        enable_link_path=False,
        markup=True,
        rich_tracebacks=True,
        log_time_format=_format_log_time,
        omit_repeated_times=False,
        show_time=True,
        keywords=[],
    )

    if hasattr(_console, "push_theme") and level_styles:
        with suppress(Exception):
            custom_theme = Theme(
                {
                    f"logging.level.{name.lower()}": style
                    for name, style in level_styles.items()
                }
            )
            _console.push_theme(custom_theme)

    return handler


# ---------------------------------------------------------------------------
# FFmpeg progress tracking (for HLS download tiers)
# ---------------------------------------------------------------------------


def _do_watch_progress(
    progress_file: Path, handler: Callable[[str, str | None], None]
) -> None:
    """Read FFmpeg progress events from a file in a background thread.

    FFmpeg writes key=value lines to its ``-progress`` output file.
    This function tails the file until ``progress=end`` is received.

    Args:
        progress_file: Path to the progress file written by FFmpeg
        handler: Callback receiving (key, value) for each event
    """
    try:
        # Wait for FFmpeg to create the file (up to 10 s)
        timeout = 10
        start_time = time.time()
        while not progress_file.exists():
            if time.time() - start_time > timeout:
                return
            time.sleep(0.1)

        with progress_file.open(encoding="utf-8") as f:
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.05)
                    continue

                line = line.strip()
                if not line:
                    continue

                parts = line.split("=", 1)
                key = parts[0] if len(parts) > 0 else ""
                value = parts[1] if len(parts) > 1 else None
                handler(key, value)

                if key == "progress" and value == "end":
                    break
    except Exception:
        return  # Daemon thread — errors are non-fatal


@contextmanager
def _watch_progress(
    handler: Callable[[str, str | None], None],
) -> Generator[Path, None, None]:
    """Context manager creating a temp file and monitor thread for FFmpeg progress.

    Yields the path of the temp file, which should be passed to FFmpeg
    via ``-progress <path>``.

    Args:
        handler: Function called for each progress event with (key, value)

    Yields:
        Path to the temp file for FFmpeg's ``-progress`` argument
    """
    fd, progress_filename = tempfile.mkstemp(suffix=".txt", prefix="ffmpeg_progress_")
    progress_file = Path(progress_filename)

    try:
        os.close(fd)  # FFmpeg will open it

        monitor_thread = threading.Thread(
            target=_do_watch_progress,
            args=(progress_file, handler),
            daemon=True,
        )
        monitor_thread.start()

        try:
            yield progress_file
        finally:
            monitor_thread.join(timeout=2.0)
    finally:
        with suppress(Exception):
            progress_file.unlink(missing_ok=True)


@contextmanager
def ffmpeg_progress(
    total_duration: float,
    task_name: str = "ffmpeg_muxing",
    description: str = "Muxing video",
    parent_task: str | None = None,
    show_elapsed: bool = False,
) -> Generator[Path, None, None]:
    """Context manager for monitoring FFmpeg progress via a Rich progress bar.

    Creates a ProgressManager task and drives it from ``out_time_ms``
    values written by FFmpeg to its ``-progress`` output file.

    Args:
        total_duration: Total video duration in seconds
        task_name: Unique task name for progress tracking
        description: Human-readable description for progress bar
        parent_task: Optional parent task name for nested display
        show_elapsed: Show elapsed time (True) or remaining (False)

    Yields:
        Path to temp file for FFmpeg ``-progress`` argument
    """
    progress_manager = get_progress_manager()

    # Duration in centiseconds for smooth updates
    total = round(total_duration * 100)

    def progress_handler(key: str, value: str | None) -> None:
        if key == "out_time_ms" and value:
            with suppress(ValueError, TypeError):
                time_us = float(value)
                current_time = round(time_us / 1_000_000.0, 2)
                completed = int(min(current_time, total_duration) * 100)
                progress_manager.update_task(task_name, completed=completed, advance=0)
        elif key == "progress" and value == "end":
            progress_manager.update_task(task_name, completed=total, advance=0)

    progress_manager.add_task(
        task_name,
        description,
        total,
        parent_task=parent_task,
        show_elapsed=show_elapsed,
    )

    try:
        with _watch_progress(progress_handler) as progress_file:
            yield progress_file
    finally:
        progress_manager.remove_task(task_name)
