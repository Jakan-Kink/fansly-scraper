"""Tests for the BatchProcessingMixin.

This module imports all the batch mixin tests to ensure they are discovered by pytest.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from stash.processing.mixins.batch import BatchProcessingMixin

# Import TestBatchProcessing without importing the fixtures
from tests.stash.processing.unit.batch.test_batch_processing import TestBatchProcessing


class TestMixinClass(BatchProcessingMixin):
    """Test class that implements BatchProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.log = MagicMock()


@pytest.fixture
def mixin():
    """Fixture for BatchProcessingMixin instance."""
    return TestMixinClass()


# Import and run all batch tests when this module is imported
# Export TestBatchProcessing when this module is imported
__all__ = [
    "TestBatchProcessing",
]
