"""Code Timing Class by RealPython

This is based on https://realpython.com/python-timer/ with
minor modifiactions.
"""

import time
from collections.abc import Callable
from contextlib import ContextDecorator
from dataclasses import dataclass, field
from typing import Any, ClassVar


class TimerError(Exception):
    """A custom exception used to report errors using the Timer class."""


@dataclass
class Timer(ContextDecorator):
    """Times your code using a class, context manager, or decorator."""

    timers: ClassVar[dict[str, float]] = {}
    name: str | None = None
    text: str = "Elapsed time: {:0.4f} seconds"
    logger: Callable[[str], None] | None = None
    _start_time: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Initialization: add timer to dict of timers"""
        if self.name:
            self.timers.setdefault(self.name, 0)

    def start(self) -> None:
        """Starts a new timer."""
        if self._start_time is not None:
            raise TimerError("Timer is running. Use .stop() to stop it.")

        self._start_time = time.perf_counter()

    def stop(self) -> float:
        """Stops the timer, and report the elapsed time."""
        if self._start_time is None:
            raise TimerError("Timer is not running. Use .start() to start it.")

        # Calculate elapsed time
        elapsed_time = time.perf_counter() - self._start_time
        self._start_time = None

        # Report elapsed time
        if self.logger:
            self.logger(self.text.format(elapsed_time))

        if self.name:
            self.timers[self.name] += elapsed_time

        return elapsed_time

    def __enter__(self) -> "Timer":
        """Starts a new timer as a context manager."""
        self.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        """Stops the context manager timer."""
        self.stop()

    @staticmethod
    def _format_time(elapsed: float) -> str:
        """Format elapsed time into a human-readable string.

        Args:
            elapsed: Time in seconds

        Returns:
            Formatted string like "1h 30m 45s" or "2m 15s" or "30s"
        """
        if elapsed >= 3600:  # More than an hour
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            seconds = int(elapsed % 60)
            return f"{hours}h {minutes}m {seconds}s"
        if elapsed >= 60:  # More than a minute
            minutes = int(elapsed // 60)
            seconds = int(elapsed % 60)
            return f"{minutes}m {seconds}s"
        return f"{int(elapsed)}s"

    def get_elapsed_time_str(self) -> str:
        """Gets the elapsed time as a formatted string.

        Returns:
            A string representing the total elapsed time for this timer.
        """
        if self.name and self.name in self.timers:
            return self._format_time(self.timers[self.name])
        return "0s"

    def get_average_time_str(self) -> str:
        """Gets the average time per operation as a formatted string.

        Returns:
            A string representing the average time per operation.
            If no operations were recorded, returns "0s".
        """
        if self.name and self.name in self.timers:
            elapsed = self.timers[self.name]
            # Assuming each start/stop cycle is one operation
            # We could add a counter if needed for more accuracy
            operations = max(1, len([k for k in self.timers.keys() if k == self.name]))
            return self._format_time(elapsed / operations)
        return "0s"

    @classmethod
    def get_all_timers_str(cls) -> str:
        """Gets a formatted string of all timers with box framing.

        Returns:
            A string containing all timer information in a framed box.
        """
        message = "\n╔═\n  SESSION DURATION\n\n  Creators:\n"

        # Add individual creator times
        for timing in sorted(cls.timers.keys()):
            if timing != "Total":
                message += f"    @{timing}: {cls._format_time(cls.timers[timing])}\n"

        # Add total time if it exists
        if "Total" in cls.timers:
            message += (
                f"\n  Total execution time: {cls._format_time(cls.timers['Total'])}"
            )

        message += f"\n{74 * ' '}═╝"
        return message
