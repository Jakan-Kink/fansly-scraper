"""General daemon test fixtures."""

from unittest.mock import AsyncMock

import pytest

from helpers.rich_progress import ProgressManager


def async_noop_spy() -> AsyncMock:
    """Build a records-calls-but-no-op async spy for dispatch-only sites.

    Returns an ``AsyncMock`` whose awaitable body does nothing but records
    every call, so ``.await_count`` / ``.call_args`` / ``.await_args_list``
    read correctly. Use to replace a heavy download ENTRYPOINT
    (``download_timeline`` / ``download_messages_for_group`` /
    ``download_stories`` and friends) at the daemon dispatch layer when a
    test only asserts "this was dispatched" — the entrypoint's own real
    coverage lives in its dedicated respx+DB tests, so re-running it here
    would only duplicate that work.

    Prefer ``AsyncMock(wraps=real_fn)`` instead when the test wants the real
    function to actually run while still recording the call. Never use a
    behaviour-replacing ``AsyncMock(return_value=...)`` on an internal
    function whose behaviour the test is meant to exercise.

    Returns:
        AsyncMock with ``return_value=None`` and no side effects.
    """
    return AsyncMock(return_value=None)


@pytest.fixture
def progress_manager():
    """Fresh ProgressManager per test to avoid bleed-over of task state.

    The module-level _progress_manager singleton accumulates tasks across
    tests and sessions, which would make "no tasks leaked" assertions
    unreliable. A fresh instance per test gives each case its own slate.
    """
    return ProgressManager()


__all__ = [
    "async_noop_spy",
    "progress_manager",
]
