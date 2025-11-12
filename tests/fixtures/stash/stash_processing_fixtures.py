"""Conftest for StashProcessing tests.

NOTE: AwaitableAttrsMock-based fixtures have been removed as they were masking
real async relationship loading issues. Use real database fixtures from
tests/fixtures/metadata_fixtures.py instead.
"""

from unittest.mock import AsyncMock

from stash.types import Image, Scene, SceneMarker, Studio, Tag

# Import fixtures from stash_api_fixtures
from tests.fixtures.stash.stash_api_fixtures import (
    stash_cleanup_tracker,
    stash_client,
    stash_context,
    test_query,
)


# Monkey patch AsyncMock to be properly awaitable
original_asyncmock_init = AsyncMock.__init__


def patched_asyncmock_init(self, *args, **kwargs):
    """Initialize AsyncMock and make it awaitable."""
    original_asyncmock_init(self, *args, **kwargs)
    # Add __await__ method if it doesn't exist
    if not hasattr(self, "__await__"):

        async def _awaitable():
            return self.return_value

        self.__await__ = lambda: _awaitable().__await__()


# Apply the patch
AsyncMock.__init__ = patched_asyncmock_init


# Helper function to sanitize model creation
def sanitize_model_data(data_dict):
    """Remove problematic fields from dict before creating model instances.

    This prevents issues with _dirty_attrs and other internal fields
    that might cause problems with mock objects in tests.
    """
    if not isinstance(data_dict, dict):
        return data_dict

    # Remove internal attributes that could cause issues
    clean_dict = {
        k: v
        for k, v in data_dict.items()
        if not k.startswith("_") and k != "client_mutation_id"
    }
    return clean_dict


# Safe wrapper functions for model creation
def safe_scene_marker_create(**kwargs):
    """Create a SceneMarker instance with sanitized data."""
    clean_kwargs = sanitize_model_data(kwargs)
    return SceneMarker(**clean_kwargs)


def safe_tag_create(**kwargs):
    """Create a Tag instance with sanitized data."""
    clean_kwargs = sanitize_model_data(kwargs)
    return Tag(**clean_kwargs)


def safe_studio_create(**kwargs):
    """Create a Studio instance with sanitized data."""
    clean_kwargs = sanitize_model_data(kwargs)
    return Studio(**clean_kwargs)


def safe_image_create(**kwargs):
    """Create an Image instance with sanitized data."""
    clean_kwargs = sanitize_model_data(kwargs)
    return Image(**clean_kwargs)


def safe_scene_create(**kwargs):
    """Create a Scene instance with sanitized data."""
    clean_kwargs = sanitize_model_data(kwargs)
    return Scene(**clean_kwargs)


# Apply the safe wrappers to the model classes
# This will only affect the tests that import these from this module
SceneMarker.safe_create = safe_scene_marker_create
Tag.safe_create = safe_tag_create
Studio.safe_create = safe_studio_create
Image.safe_create = safe_image_create
Scene.safe_create = safe_scene_create

__all__ = [
    # Stash-specific safe wrappers
    "safe_image_create",
    "safe_scene_create",
    "safe_scene_marker_create",
    "safe_studio_create",
    "safe_tag_create",
    # Helper functions
    "sanitize_model_data",
    # Re-exported from stash_api_fixtures for convenience
    "stash_cleanup_tracker",
    "stash_client",
    "stash_context",
    "test_query",
]
