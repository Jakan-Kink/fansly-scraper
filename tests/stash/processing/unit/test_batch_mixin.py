"""Tests for the BatchProcessingMixin.

This module imports all the batch mixin tests to ensure they are discovered by pytest.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from stash.processing.mixins.batch import BatchProcessingMixin
from tests.stash.processing.unit.batch.test_batch_processing import TestBatchProcessing


class TestMixinClass(BatchProcessingMixin):
    """Test class that implements BatchProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        pass


@pytest.fixture
def mixin():
    """Fixture for BatchProcessingMixin instance."""
    return TestMixinClass()


@pytest.fixture
def mock_items():
    """Fixture for mock items."""
    return [MagicMock() for _ in range(10)]


@pytest.fixture
def mock_progress_bars():
    """Fixture for mock progress bars."""
    task_pbar = MagicMock()
    process_pbar = MagicMock()
    return task_pbar, process_pbar


@pytest.fixture
def mock_semaphore():
    """Fixture for mock asyncio.Semaphore."""
    semaphore = MagicMock()
    semaphore._value = 4  # Max concurrency
    semaphore.__aenter__ = AsyncMock()
    semaphore.__aexit__ = AsyncMock()
    return semaphore


@pytest.fixture
def mock_process_batch():
    """Fixture for mock process_batch function."""
    return AsyncMock()


# Import and run all batch tests when this module is imported
__all__ = [
    "TestBatchProcessing",
]
