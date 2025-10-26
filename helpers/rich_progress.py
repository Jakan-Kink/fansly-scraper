"""Rich-based progress management for fansly-downloader-ng.

This module provides a clean progress bar system using Rich that:
- Handles multiple progress bars automatically
- Allows logs to scroll properly above progress bars
- Requires no complex cursor management
- Works seamlessly with async/threading
- Integrates with loguru logging system
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from rich.console import Console
from rich.live import Live
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
from rich.text import Text

# Global console instance for coordinated output
_console = Console()


class ContextualTimeColumn(ProgressColumn):
    """Custom time column that shows elapsed or remaining based on task type."""

    def __init__(self, table_column=None):
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
                    self.progress, console=self.console, refresh_per_second=4
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
