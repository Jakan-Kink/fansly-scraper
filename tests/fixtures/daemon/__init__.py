"""Daemon-related test fakes and fixtures."""

from .daemon_simulators import RecordingSimulator, StubSimulator
from .livestream_watcher_fixtures import isolate_active_recordings


__all__ = [
    "RecordingSimulator",
    "StubSimulator",
    "isolate_active_recordings",
]
