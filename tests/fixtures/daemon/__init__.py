"""Daemon-related test fakes and fixtures."""

from .daemon_fixtures import async_noop_spy, progress_manager
from .daemon_simulators import RecordingSimulator, StubSimulator
from .livestream_watcher_fixtures import isolate_active_recordings


__all__ = [
    "RecordingSimulator",
    "StubSimulator",
    "async_noop_spy",
    "isolate_active_recordings",
    "progress_manager",
]
