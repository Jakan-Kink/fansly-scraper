"""Tests for the GalleryProcessingMixin.

This module tests the GalleryProcessingMixin functionality.
Tests use the shared gallery_mixin fixture from tests/fixtures/stash_mixin_fixtures.py.
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_tag():
    """Fixture for mock tag."""
    tag = MagicMock()
    tag.id = "tag_123"
    tag.name = "test_tag"
    return tag
