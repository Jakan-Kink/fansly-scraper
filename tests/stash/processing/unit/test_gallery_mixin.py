"""Tests for the GalleryProcessingMixin.

This module imports all the gallery mixin tests to ensure they are discovered by pytest.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from stash.processing.mixins.gallery import GalleryProcessingMixin
from tests.stash.processing.unit.gallery.test_gallery_creation import (
    TestGalleryCreation,
)
from tests.stash.processing.unit.gallery.test_gallery_lookup import TestGalleryLookup
from tests.stash.processing.unit.gallery.test_media_detection import TestMediaDetection
from tests.stash.processing.unit.gallery.test_process_item_gallery import (
    TestProcessItemGallery,
)
from tests.stash.processing.unit.gallery.test_tag_methods import TestTagMethods


class TestMixinClass(GalleryProcessingMixin):
    """Test class that implements GalleryProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.context = MagicMock()
        self.context.client = MagicMock()
        self.database = MagicMock()
        self.log = MagicMock()
        self.process_creator_attachment = AsyncMock()
        self._add_preview_tag = AsyncMock()
        self._update_account_stash_id = AsyncMock()
        self._generate_title_from_content = MagicMock(return_value="Test Title")


@pytest.fixture
def mixin():
    """Fixture for GalleryProcessingMixin instance."""
    return TestMixinClass()


@pytest.fixture
def mock_tag():
    """Fixture for mock tag."""
    tag = MagicMock()
    tag.id = "tag_123"
    tag.name = "test_tag"
    return tag


# Import and run all gallery tests when this module is imported
__all__ = [
    "TestGalleryCreation",
    "TestGalleryLookup",
    "TestMediaDetection",
    "TestProcessItemGallery",
    "TestTagMethods",
]
