"""Test configuration and fixtures for Stash types tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

# Import and re-export fixtures from parent conftest.py
from ..conftest import (
    mock_client,
    mock_session,
    mock_transport,
    stash_client,
    stash_context,
    test_query,
)

__all__ = [
    "mock_client",
    "mock_session",
    "mock_transport",
    "stash_client",
    "stash_context",
    "test_query",
]

# Add types-specific fixtures below


@pytest.fixture
def mock_stash_object():
    """Create a mock StashObject for testing Stash type functionality.

    This fixture provides a mock implementation of a StashObject with common
    attributes and methods that are fundamental to all Stash object types
    (like Performer, Scene, Gallery, etc.). This allows testing type functionality
    without needing to create actual instances.

    The mock includes:
    - id: A test identifier
    - Dirty state tracking methods (is_dirty, mark_dirty, mark_clean)
    - Object conversion methods (to_input)
    - Persistence methods (save)

    This is particularly useful for testing features that apply to all Stash types,
    such as dirty state tracking, object conversion, or persistence behavior.

    Returns:
        MagicMock: A mock StashObject with essential attributes and methods configured
    """
    obj = MagicMock()
    obj.id = "test_id"
    obj.is_dirty = MagicMock(return_value=False)
    obj.mark_dirty = MagicMock()
    obj.mark_clean = MagicMock()
    obj.to_input = AsyncMock()
    obj.save = AsyncMock()
    return obj
