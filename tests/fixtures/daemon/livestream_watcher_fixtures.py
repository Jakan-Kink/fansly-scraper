"""Test fixtures for ``daemon.livestream_watcher`` module-level state.

Lives under ``tests/fixtures/daemon/`` per the project's fixture-location
policy (no inline fixtures in ``test_*.py`` files; reusable test
plumbing belongs in ``tests/fixtures/<domain>/``).
"""

from __future__ import annotations

import pytest

import daemon.livestream_watcher as watcher_module


@pytest.fixture
def isolate_active_recordings():
    """Snapshot + restore ``daemon.livestream_watcher._active_recordings``.

    The watcher tracks running recording tasks in a module-level dict.
    Tests that spawn-then-leak entries would corrupt subsequent tests
    in the same process; this fixture snapshots the dict on entry,
    clears it for the test, and restores the original contents on
    exit regardless of pass/fail.

    Use as an explicit fixture parameter (not ``autouse``) so tests
    that don't touch ``_active_recordings`` aren't penalized with
    lock acquisitions they don't need.
    """
    with watcher_module._recordings_lock:
        original = dict(watcher_module._active_recordings)
        watcher_module._active_recordings.clear()
    try:
        yield
    finally:
        with watcher_module._recordings_lock:
            watcher_module._active_recordings.clear()
            watcher_module._active_recordings.update(original)
