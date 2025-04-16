"""Tests for the ContentProcessingMixin.

This module imports all the content mixin tests to ensure they are discovered by pytest.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from stash.processing.mixins.content import ContentProcessingMixin
from tests.stash.processing.unit.content.test_message_processing import (
    TestMessageProcessing,
)
from tests.stash.processing.unit.content.test_post_processing import TestPostProcessing


class TestMixinClass(ContentProcessingMixin):
    """Test class that implements ContentProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.context = MagicMock()
        self.context.client = MagicMock()
        self.database = MagicMock()
        self.log = MagicMock()
        self._process_item_gallery = AsyncMock()
        self._setup_batch_processing = AsyncMock()
        self._run_batch_processor = AsyncMock()


@pytest.fixture
def mixin():
    """Fixture for ContentProcessingMixin instance."""
    return TestMixinClass()


# Import and run all content tests when this module is imported
__all__ = [
    "TestPostProcessing",
    "TestMessageProcessing",
]
