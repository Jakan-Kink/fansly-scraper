"""Rich-based visual display for rate limiter status."""

from __future__ import annotations

import inspect
import threading
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TYPE_CHECKING, ParamSpec, TypeVar, cast, overload

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TaskID, TextColumn, TimeRemainingColumn
from rich.table import Table
from rich.text import Text

from helpers.rich_progress import get_rich_console

if TYPE_CHECKING:
    from .rate_limiter import RateLimiter

P = ParamSpec("P")
R = TypeVar("R")


class RateLimiterDisplay:
    """Visual display for rate limiter status using Rich progress panels."""

    def __init__(self, rate_limiter: RateLimiter, update_interval: float = 0.2) -> None:
        self.rate_limiter = rate_limiter
        self.update_interval = max(0.05, update_interval)
        self.console: Console = get_rich_console()

        self._stop_event = threading.Event()
        self._display_thread: threading.Thread | None = None
        self._live: Live | None = None

        self._token_progress = Progress(
            TextColumn("[bold blue]Tokens"),
            BarColumn(bar_width=40),
            TextColumn(" {task.completed:.1f}/{task.total:.0f}"),
            TextColumn(" ({task.percentage:>3.0f}%)"),
            console=self.console,
            transient=False,
        )
        self._backoff_progress = Progress(
            TextColumn("[bold red]Backoff"),
            BarColumn(bar_width=40),
            TimeRemainingColumn(),
            TextColumn(" {task.completed:.1f}s elapsed"),
            console=self.console,
            transient=False,
        )

        self._token_task: TaskID | None = None
        self._backoff_task: TaskID | None = None

        self._context_depth = 0
        self._context_lock = threading.Lock()

    def start(self) -> None:
        """Start the rate limiter display."""
        if self._display_thread and self._display_thread.is_alive():
            return

        self._stop_event.clear()
        self._display_thread = threading.Thread(
            target=self._display_loop, name="RateLimiterDisplay", daemon=True
        )
        self._display_thread.start()

    def stop(self) -> None:
        """Stop the rate limiter display."""
        self._stop_event.set()
        if self._display_thread:
            self._display_thread.join(timeout=1.0)
            self._display_thread = None
        if self._live:
            self._live.stop()
            self._live = None

    def _render_status_panel(self, stats: dict[str, object]) -> Panel:
        """Construct the status panel renderable."""
        table = Table.grid(padding=(0, 2))
        table.add_column("Label", style="bold cyan")
        table.add_column("Value")

        status_text = Text("DISABLED", style="red")
        if bool(stats.get("enabled")):
            status_text = Text("ACTIVE", style="green")
            if bool(stats.get("is_in_backoff")):
                status_text = Text("RATE LIMITED", style="yellow")
            elif float(stats.get("utilization_percent", 0.0)) >= 80.0:
                status_text = Text("HIGH USAGE", style="yellow")

        table.add_row("Status", status_text)
        table.add_row(
            "Configured Rate",
            f"{int(stats.get('configured_rate', 0))} req/min",
        )
        table.add_row(
            "Current Rate",
            f"{int(stats.get('current_rate_per_minute', 0))} req/min",
        )
        table.add_row(
            "Utilization",
            f"{float(stats.get('utilization_percent', 0.0)):.1f}%",
        )
        table.add_row(
            "Available Tokens",
            f"{float(stats.get('available_tokens', 0.0)):.1f}",
        )
        table.add_row("Burst Size", f"{int(stats.get('burst_size', 0))}")
        table.add_row("Total Requests", f"{int(stats.get('total_requests', 0))}")
        table.add_row("Blocked Requests", f"{int(stats.get('blocked_requests', 0))}")
        table.add_row(
            "Rate Violations", f"{int(stats.get('rate_limit_violations', 0))}"
        )
        table.add_row(
            "Adaptive Adjustments", f"{int(stats.get('adaptive_adjustments', 0))}"
        )
        table.add_row(
            "Consecutive Violations",
            f"{int(stats.get('consecutive_violations', 0))}",
        )
        table.add_row(
            "Consecutive Successes",
            f"{int(stats.get('consecutive_successes', 0))}",
        )
        backoff_seconds = float(stats.get("current_backoff_seconds", 0.0))
        table.add_row("Current Backoff", f"{backoff_seconds:.1f}s")
        table.add_row(
            "Backoff Remaining", f"{float(stats.get('backoff_remaining', 0.0)):.1f}s"
        )

        return Panel(table, title="[bold]Rate Limiter Status", border_style="blue")

    def _update_progress_bars(self, stats: dict[str, object]) -> list[Panel]:
        """Update progress bars from latest stats and return active panels."""
        panels: list[Panel] = []
        if not bool(stats.get("enabled")):
            if self._token_task is not None:
                self._token_progress.remove_task(self._token_task)
                self._token_task = None
            if self._backoff_task is not None:
                self._backoff_progress.remove_task(self._backoff_task)
                self._backoff_task = None
            return panels

        burst_size = max(float(stats.get("burst_size", 0.0)), 0.0)
        available_tokens = max(float(stats.get("available_tokens", 0.0)), 0.0)

        # Only show token bucket when configured
        if burst_size > 0:
            if self._token_task is None:
                self._token_task = self._token_progress.add_task(
                    "tokens", total=burst_size or 1.0, completed=available_tokens
                )
            else:
                self._token_progress.update(
                    self._token_task,
                    total=burst_size or 1.0,
                    completed=min(available_tokens, burst_size or 1.0),
                )

            panels.append(
                Panel(
                    self._token_progress,
                    title="[bold]Token Bucket",
                    border_style="green",
                )
            )

        if bool(stats.get("is_in_backoff")):
            current_backoff = max(float(stats.get("current_backoff_seconds", 0.0)), 0.0)
            remaining = max(float(stats.get("backoff_remaining", 0.0)), 0.0)
            elapsed = max(current_backoff - remaining, 0.0)

            if self._backoff_task is None:
                self._backoff_task = self._backoff_progress.add_task(
                    "backoff", total=current_backoff or 1.0, completed=elapsed
                )
            else:
                self._backoff_progress.update(
                    self._backoff_task,
                    total=current_backoff or 1.0,
                    completed=min(elapsed, current_backoff or 1.0),
                )

            panels.append(
                Panel(
                    self._backoff_progress,
                    title="[bold]Rate Limited - Waiting",
                    border_style="red",
                )
            )
        elif self._backoff_task is not None:
            self._backoff_progress.remove_task(self._backoff_task)
            self._backoff_task = None

        return panels

    def _compose_layout(self) -> Table:
        """Create the layout table for the live display."""
        stats = self.rate_limiter.get_stats()
        layout = Table.grid(padding=1)
        layout.add_column(justify="left")

        layout.add_row(self._render_status_panel(stats))

        for panel in self._update_progress_bars(stats):
            layout.add_row(panel)

        return layout

    def _display_loop(self) -> None:
        """Background loop for updating the live display."""
        with Live(
            self._compose_layout(),
            console=self.console,
            refresh_per_second=10,
            vertical_overflow="visible",
        ) as live:
            self._live = live
            while not self._stop_event.is_set():
                try:
                    live.update(self._compose_layout())
                    time.sleep(self.update_interval)
                except Exception as exc:  # pragma: no cover - defensive for display
                    self.console.print(
                        f"[red]Rate limiter display error: {exc!r}[/red]"
                    )
                    break
            self._live = None

    def __enter__(self) -> RateLimiterDisplay:
        with self._context_lock:
            self._context_depth += 1
            first_entry = self._context_depth == 1
        if first_entry:
            self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        should_stop = False
        with self._context_lock:
            if self._context_depth > 0:
                self._context_depth -= 1
                should_stop = self._context_depth == 0
        if should_stop:
            self.stop()

    @overload
    def __call__(
        self, func: Callable[P, Awaitable[R]]
    ) -> Callable[P, Awaitable[R]]: ...

    @overload
    def __call__(self, func: Callable[P, R]) -> Callable[P, R]: ...

    def __call__(
        self, func: Callable[P, Awaitable[R]] | Callable[P, R]
    ) -> Callable[P, Awaitable[R]] | Callable[P, R]:
        """Allow the display context to be used as a decorator."""
        if inspect.iscoroutinefunction(func):
            async_func = cast(Callable[P, Awaitable[R]], func)

            @wraps(async_func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                with self:
                    return await async_func(*args, **kwargs)

            return cast(Callable[P, Awaitable[R]], async_wrapper)

        sync_func = cast(Callable[P, R], func)

        @wraps(sync_func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with self:
                return sync_func(*args, **kwargs)

        return sync_wrapper
