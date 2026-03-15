"""Rate limiter visual display integrated with ProgressManager.

Shows rate limiter status (backoff countdown, token utilization) as
progress bars managed by the global ProgressManager, so they coexist
cleanly with download, dedupe, and preload progress bars.
"""

from __future__ import annotations

import inspect
import threading
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from types import TracebackType
from typing import TYPE_CHECKING, ParamSpec, TypeVar, cast, overload

from helpers.rich_progress import get_progress_manager


if TYPE_CHECKING:
    from .rate_limiter import RateLimiter

P = ParamSpec("P")
R = TypeVar("R")


class RateLimiterDisplay:
    """Visual display for rate limiter status using ProgressManager.

    Runs a background thread that polls RateLimiter.get_stats() and
    maintains two optional progress tasks:

    - **Backoff countdown** — shown only while the rate limiter is in
      backoff after a 429 response. Displays elapsed / total seconds.
    - **Token bucket** — shown only when token utilization exceeds 80%.
      Displays available / burst_size tokens.

    Both tasks are registered through the global ProgressManager so they
    share the same Rich Live context as every other progress bar in the
    application.
    """

    def __init__(
        self,
        rate_limiter: RateLimiter,
        update_interval: float = 0.1,
    ) -> None:
        self.rate_limiter = rate_limiter
        self.update_interval = max(0.05, update_interval)

        self._stop_event = threading.Event()
        self._display_thread: threading.Thread | None = None

        self._context_depth = 0
        self._context_lock = threading.Lock()

        # Track whether we currently have active progress tasks
        self._backoff_active = False
        self._tokens_active = False

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background display thread."""
        if self._display_thread and self._display_thread.is_alive():
            return

        self._stop_event.clear()
        self._display_thread = threading.Thread(
            target=self._display_loop,
            name="RateLimiterDisplay",
            daemon=True,
        )
        self._display_thread.start()

    def stop(self) -> None:
        """Stop the background display thread and clean up tasks."""
        self._stop_event.set()
        if self._display_thread:
            self._display_thread.join(timeout=1.0)
            self._display_thread = None

    # ── Background Loop ──────────────────────────────────────────

    def _display_loop(self) -> None:
        """Poll rate limiter stats and update ProgressManager tasks."""
        progress = get_progress_manager()

        with progress.session(auto_cleanup=True):
            while not self._stop_event.is_set():
                try:
                    self._update(progress)
                except Exception:
                    break  # Silently stop on display errors
                time.sleep(self.update_interval)

            # Explicit cleanup on exit
            self._cleanup(progress)

    def _update(self, progress: object) -> None:
        """Single update tick — add/update/remove tasks as needed."""
        stats = self.rate_limiter.get_stats()

        if not stats.get("enabled"):
            self._cleanup(progress)
            return

        self._update_backoff(progress, stats)
        self._update_tokens(progress, stats)

    def _update_backoff(
        self,
        progress: object,
        stats: dict[str, object],
    ) -> None:
        """Show/update/hide the backoff countdown task."""
        if stats.get("is_in_backoff"):
            total = max(float(stats.get("current_backoff_seconds", 0)), 0.1)
            remaining = max(float(stats.get("backoff_remaining", 0)), 0.0)
            elapsed = max(total - remaining, 0.0)

            if not self._backoff_active:
                progress.add_task(
                    name="rate_limit_backoff",
                    description="Rate limited — waiting",
                    total=int(total),
                    show_elapsed=True,
                )
                self._backoff_active = True

            # Use absolute completed value via Rich's update
            progress.update_task(
                "rate_limit_backoff",
                advance=0,
                completed=elapsed,
                total=total,
            )
        elif self._backoff_active:
            progress.remove_task("rate_limit_backoff")
            self._backoff_active = False

    def _update_tokens(
        self,
        progress: object,
        stats: dict[str, object],
    ) -> None:
        """Show/update/hide the token bucket task when utilization is high."""
        utilization = float(stats.get("utilization_percent", 0))
        burst_size = max(float(stats.get("burst_size", 0)), 0.0)
        available = max(float(stats.get("available_tokens", 0)), 0.0)

        if utilization >= 80 and burst_size > 0:
            if not self._tokens_active:
                progress.add_task(
                    name="rate_limit_tokens",
                    description="API tokens remaining",
                    total=int(burst_size),
                    show_elapsed=True,
                )
                self._tokens_active = True

            progress.update_task(
                "rate_limit_tokens",
                advance=0,
                completed=min(available, burst_size),
                total=burst_size,
            )
        elif self._tokens_active:
            progress.remove_task("rate_limit_tokens")
            self._tokens_active = False

    def _cleanup(self, progress: object) -> None:
        """Remove any active tasks."""
        if self._backoff_active:
            progress.remove_task("rate_limit_backoff")
            self._backoff_active = False
        if self._tokens_active:
            progress.remove_task("rate_limit_tokens")
            self._tokens_active = False

    # ── Context Manager / Decorator ──────────────────────────────

    def __enter__(self) -> RateLimiterDisplay:
        with self._context_lock:
            self._context_depth += 1
            first_entry = self._context_depth == 1
        if first_entry:
            self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
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
        self,
        func: Callable[P, Awaitable[R]] | Callable[P, R],
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
