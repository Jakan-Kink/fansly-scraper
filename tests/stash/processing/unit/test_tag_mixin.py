"""Tests for the TagProcessingMixin.

This module imports all the tag mixin tests to ensure they are discovered by pytest.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the modules instead of the classes to avoid fixture issues
from stash.processing.mixins.tag import TagProcessingMixin


class TestTagMixinClass(TagProcessingMixin):
    """Test class that implements TagProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.context = MagicMock()
        self.context.client = MagicMock()
        self.database = MagicMock()
        self.log = MagicMock()


@pytest.fixture
def tag_mixin():
    """Fixture for TagProcessingMixin instance."""
    return TestTagMixinClass()
