"""Common fixtures for batch mixin tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from tqdm import tqdm

from stash.processing.mixins.batch import BatchProcessingMixin


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
    task_pbar = MagicMock(spec=tqdm)
    process_pbar = MagicMock(spec=tqdm)
    return task_pbar, process_pbar


@pytest.fixture
def mock_queue():
    """Fixture for mock asyncio.Queue."""
    queue = MagicMock(spec=asyncio.Queue)

    # Mock queue methods
    queue.put = AsyncMock()
    queue.get = AsyncMock()
    queue.join = AsyncMock()
    queue.task_done = MagicMock()

    # Set up queue.get to return values on consecutive calls
    queue.get.side_effect = lambda: asyncio.Future()

    return queue


@pytest.fixture
def mock_semaphore():
    """Fixture for mock asyncio.Semaphore."""
    semaphore = MagicMock(spec=asyncio.Semaphore)

    # Mock the context manager methods
    semaphore.__aenter__ = AsyncMock()
    semaphore.__aexit__ = AsyncMock()

    # Set the value attribute for max concurrency
    semaphore._value = 4

    return semaphore


@pytest.fixture
def mock_process_batch():
    """Fixture for mock process_batch function."""
    return AsyncMock()
