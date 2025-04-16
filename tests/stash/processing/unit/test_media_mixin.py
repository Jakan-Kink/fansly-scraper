"""Tests for the MediaProcessingMixin.

This module imports all the media mixin tests to ensure they are discovered by pytest.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from stash.processing.mixins.media import MediaProcessingMixin
from tests.stash.processing.unit.media_mixin.test_file_handling import TestFileHandling
from tests.stash.processing.unit.media_mixin.test_media_processing import (
    TestMediaProcessing,
)
from tests.stash.processing.unit.media_mixin.test_metadata_update import (
    TestMetadataUpdate,
)


class TestMixinClass(MediaProcessingMixin):
    """Test class that implements MediaProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.context = MagicMock()
        self.context.client = MagicMock()
        self.database = MagicMock()
        self.log = MagicMock()
        self._find_existing_performer = AsyncMock()
        self._find_existing_studio = AsyncMock()
        self._process_hashtags_to_tags = AsyncMock()
        self._generate_title_from_content = MagicMock(return_value="Test Title")
        self._add_preview_tag = AsyncMock()
        self._update_account_stash_id = AsyncMock()


@pytest.fixture
def mixin():
    """Fixture for MediaProcessingMixin instance."""
    return TestMixinClass()


# Import and run all media tests when this module is imported
__all__ = [
    "TestFileHandling",
    "TestMetadataUpdate",
    "TestMediaProcessing",
]
