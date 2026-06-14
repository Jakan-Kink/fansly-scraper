"""General daemon test fixtures."""

import pytest

from helpers.rich_progress import ProgressManager


@pytest.fixture
def progress_manager():
    """Fresh ProgressManager per test to avoid bleed-over of task state.

    The module-level _progress_manager singleton accumulates tasks across
    tests and sessions, which would make "no tasks leaked" assertions
    unreliable. A fresh instance per test gives each case its own slate.
    """
    return ProgressManager()


__all__ = [
    "progress_manager",
]
