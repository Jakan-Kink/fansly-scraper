"""Conftest for StashProcessing tests."""

import asyncio
import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from metadata import Account, AccountMedia, Attachment, Group, Media, Message, Post
from stash.processing import StashProcessing
from stash.processing.mixins.batch import BatchProcessingMixin
from stash.types import Image, Scene, SceneMarker, Studio, Tag

# Import fixtures from stash_api_fixtures
from tests.fixtures.stash_api_fixtures import (
    mock_account,
    mock_client,
    mock_performer,
    mock_scene,
    mock_session,
    mock_studio,
    mock_transport,
    stash_cleanup_tracker,
    stash_client,
    stash_context,
    test_query,
)
from tests.stash.processing.unit.media_mixin.async_mock_helper import (
    AccessibleAsyncMock,
    make_asyncmock_awaitable,
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


# Export imported fixtures and utility functions
__all__ = [
    # Helper functions and classes
    "sanitize_model_data",
    "safe_scene_marker_create",
    "safe_tag_create",
    "safe_studio_create",
    "safe_image_create",
    "safe_scene_create",
    "AsyncResult",
    "AsyncSessionContext",
    "MockDatabase",
    # Stash fixtures imported from stash/conftest.py
    "mock_client",
    "mock_session",
    "mock_transport",
    "mock_account",
    "mock_performer",
    "mock_studio",
    "mock_scene",
    "stash_cleanup_tracker",
    "stash_client",
    "stash_context",
    "test_query",
    # Local fixtures (renamed with processing_ prefix to avoid conflicts with factory-based fixtures)
    "mixin",
    "mock_items",
    "mock_progress_bars",
    "mock_semaphore",
    "mock_process_item",
    "mock_queue",
    "processing_mock_posts",
    "processing_mock_messages",
    "mock_item",
    "processing_mock_media",
    "processing_mock_attachment",
    "processing_mock_multiple_posts",
    "processing_mock_multiple_messages",
    "mock_gallery",
    "mock_image",
    "mock_context",
    "mock_config",
    "mock_state",
    "mock_database",
    "stash_processor",
]


class TestMixinClass(BatchProcessingMixin):
    """Test class that implements BatchProcessingMixin for testing."""

    def __init__(self):
        """Initialize test class."""
        self.log = MagicMock()
        self.context = MagicMock()
        self.context.client = MagicMock()
        self._find_existing_performer = AsyncMock()
        self._find_existing_studio = AsyncMock()


@pytest.fixture
def mixin():
    """Fixture for batch mixin test class."""
    return TestMixinClass()


@pytest.fixture
def mock_items():
    """Fixture for mock items."""
    return [MagicMock() for _ in range(10)]


@pytest.fixture
def mock_progress_bars():
    """Fixture for mock progress bars."""
    task_pbar = MagicMock()
    task_pbar.set_description = MagicMock()
    task_pbar.set_postfix = MagicMock()
    task_pbar.update = MagicMock()
    task_pbar.close = MagicMock()

    process_pbar = MagicMock()
    process_pbar.set_description = MagicMock()
    process_pbar.update = MagicMock()
    process_pbar.close = MagicMock()

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
def mock_process_item():
    """Fixture for mock process_item function."""
    return AsyncMock()


@pytest.fixture
def mock_queue():
    """Fixture for mock asyncio.Queue."""
    queue = MagicMock()
    queue.get = AsyncMock()
    queue.put = AsyncMock()
    queue.task_done = MagicMock()
    return queue


@pytest.fixture
def processing_mock_posts():
    """Fixture for mock posts (processing tests)."""
    posts = []
    for i in range(5):
        post = AccessibleAsyncMock(spec=Post)
        post.id = f"post_{i}"
        post.createdAt = datetime(2023, 1, 1, 15, 30, tzinfo=timezone.utc)
        post.content = f"Test post {i}"
        posts.append(post)
    return posts


@pytest.fixture
def processing_mock_messages():
    """Fixture for mock messages (processing tests)."""
    messages = []
    for i in range(5):
        message = AccessibleAsyncMock(spec=Message)
        message.id = f"message_{i}"
        message.createdAt = datetime(2023, 1, 1, 15, 30, tzinfo=timezone.utc)
        message.text = f"Test message {i}"
        messages.append(message)
    return messages


@pytest.fixture
def mock_item():
    """Fixture for mock item (post/message)."""
    item = AccessibleAsyncMock()
    item.id = "item_123"
    item.createdAt = datetime(2023, 1, 1, 15, 30, tzinfo=timezone.utc)
    return item


@pytest.fixture
def processing_mock_media():
    """Fixture for mock media (processing tests)."""
    media = AccessibleAsyncMock(spec=Media)
    media.id = "media_123"
    media.createdAt = datetime(2023, 1, 1, 15, 30, tzinfo=timezone.utc)
    media.variants = []
    return media


@pytest.fixture
def processing_mock_attachment():
    """Fixture for mock attachment (processing tests)."""
    attachment = AccessibleAsyncMock(spec=Attachment)
    attachment.id = "attachment_123"
    attachment.media = None
    attachment.bundle = None
    return attachment


@pytest.fixture
def processing_mock_multiple_posts(processing_mock_posts):
    """Fixture for larger set of mock posts (processing tests)."""
    return processing_mock_posts[
        :2
    ]  # Reuse processing_mock_posts fixture but limit to 2


@pytest.fixture
def processing_mock_multiple_messages(processing_mock_messages):
    """Fixture for larger set of mock messages (processing tests)."""
    return processing_mock_messages[
        :2
    ]  # Reuse processing_mock_messages fixture but limit to 2


@pytest.fixture
def mock_gallery():
    """Fixture for mock gallery."""
    gallery = MagicMock()
    gallery.id = "gallery_123"
    gallery.title = "Test Gallery"
    gallery.details = "Test gallery details"
    gallery.urls = ["http://example.com/gallery"]
    gallery.save = AsyncMock()
    return gallery


@pytest.fixture
def mock_image():
    """Fixture for mock image."""
    image = MagicMock()
    image.id = "image_123"
    image.title = "Test Image"
    image.url = "http://example.com/image.jpg"
    image.path = "/path/to/image.jpg"
    image.created_at = datetime(2023, 1, 1, 15, 30, tzinfo=timezone.utc)
    image.save = AsyncMock()
    return image


class MockContext:
    def __init__(self):
        self.client = MagicMock()
        # Make all client methods proper AsyncMocks
        self.client.find_performer = AsyncMock()
        self.client.find_studio = AsyncMock()
        self.client.find_studios = AsyncMock()
        self.client.find_gallery = AsyncMock()
        self.client.find_galleries = AsyncMock()
        self.client.find_scene = AsyncMock()
        self.client.find_scenes = AsyncMock()
        self.client.find_image = AsyncMock()
        self.client.find_images = AsyncMock()
        self.client.find_tags = AsyncMock()
        self.client.create_tag = AsyncMock()
        self.client.create_studio = AsyncMock()
        self.client.add_gallery_images = AsyncMock(return_value=True)


class MockConfig:
    def __init__(self):
        self.stash = MagicMock()
        self.stash.enabled = True
        self.stash.url = "http://localhost:9999"
        self.stash.api_key = "test_api_key"
        self.stash_context_conn = {
            "scheme": "http",
            "host": "localhost",
            "port": 9999,
            "apikey": "test_api_key",
        }
        self.metadata = MagicMock()
        self.metadata.db_path = "test.db"
        self._database = None
        self._stash = None
        self._background_tasks = []

    def get_stash_context(self):
        if self._stash is None:
            self._stash = MockContext()
        return self._stash

    def get_background_tasks(self):
        return self._background_tasks

    def get_database(self):
        return self._database

    def set_database(self, database):
        self._database = database


class MockState:
    def __init__(self):
        self.creator_id = "12345"
        self.creator_name = "test_user"
        self.messages_enabled = True
        self.verbose_logs = True


class AsyncSessionContext:
    """Context manager for AsyncSession that wraps the session.

    This allows session methods to be called directly on the context manager,
    which delegates to the underlying session object.
    """

    def __init__(self, session):
        """Initialize with the session to wrap."""
        self.session = session

    async def __aenter__(self):
        """Enter the async context, returning this context manager."""
        await self.session.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the async context, delegating to the session."""
        return await self.session.__aexit__(exc_type, exc_val, exc_tb)

    def __getattr__(self, name):
        """Delegate all method calls to the underlying session."""
        return getattr(self.session, name)


class AsyncResult:
    """Mock async result that properly handles scalar operations."""

    def __init__(self, result=None, created_at=None):
        self._result = result
        self._created_at = created_at or datetime(
            2023, 1, 1, 15, 30, tzinfo=timezone.utc
        )

    def unique(self):
        """Return self for chaining."""
        return self

    def scalars(self):
        """Return self for chaining."""
        return self

    async def scalar_one(self):
        """Return scalar result with proper datetime."""
        if isinstance(self._result, (list, tuple)):
            if self._result:
                item = self._result[0]
            else:
                return None
        else:
            item = self._result

        if item and not getattr(item, "createdAt", None):
            item.createdAt = self._created_at
        return item

    async def scalar_one_or_none(self):
        """Return scalar result or None."""
        return await self.scalar_one()

    async def all(self):
        """Return list of results."""
        if isinstance(self._result, (list, tuple)):
            results = self._result
        else:
            results = [self._result] if self._result else []

        # Ensure all items have createdAt
        for item in results:
            if item and not getattr(item, "createdAt", None):
                item.createdAt = self._created_at
        return results

    def __call__(self, *args, **kwargs):
        return self


class SyncSessionContext:
    """Sync session context that properly handles scalar operations."""

    def __init__(self, session, result):
        self.session = session
        self._result = result

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def execute(self, *args, **kwargs):
        """Return properly formatted sync result."""
        return self

    def scalar_one_or_none(self):
        """Return scalar result synchronously."""
        if self._result is None:
            return None
        if isinstance(self._result, (list, tuple)):
            return self._result[0] if self._result else None
        return self._result


class MockDatabase:
    """Mock database with proper async session handling."""

    def __init__(self):
        self.session = MagicMock()
        self._result = AsyncResult()

        # Make execute() return async result
        async def mock_execute(*args, **kwargs):
            return self._result

        self.session.execute = AsyncMock(side_effect=mock_execute)

        # Make scalar() return async result
        async def mock_scalar(*args, **kwargs):
            return self._result

        self.session.scalar = AsyncMock(side_effect=mock_scalar)

        # Mock add to set createdAt
        async def mock_add(obj):
            if not getattr(obj, "createdAt", None):
                obj.createdAt = datetime(2023, 1, 1, 15, 30, tzinfo=timezone.utc)

        self.session.add = AsyncMock(side_effect=mock_add)

        async def mock_add_all(objects):
            for obj in objects:
                await mock_add(obj)

        self.session.add_all = AsyncMock(side_effect=mock_add_all)

        self.session.commit = AsyncMock()
        self.session.refresh = AsyncMock()
        self.session.rollback = AsyncMock()

        # Properly implement async context manager
        async def mock_aenter(*args, **kwargs):
            return self.session

        async def mock_aexit(*args, **kwargs):
            pass

        self.session.__aenter__ = AsyncMock(side_effect=mock_aenter)
        self.session.__aexit__ = AsyncMock(side_effect=mock_aexit)

    def async_session_scope(self):
        """Return async context manager that can be awaited properly."""
        # This method itself is not async - it returns an object that supports __aenter__ and __aexit__
        return self.session

    def session_scope(self):
        """Sync session context manager."""
        return SyncSessionContext(self.session, self._result._result)

    def reset_mocks(self):
        """Reset all mocks to their initial state."""
        for name, value in self.session.__dict__.items():
            if hasattr(value, "reset_mock"):
                value.reset_mock()

    def set_result(self, result):
        """Set the result to be returned by execute/scalar calls."""
        if isinstance(result, (list, tuple)):
            for item in result:
                if not getattr(item, "createdAt", None):
                    item.createdAt = datetime(2023, 1, 1, 15, 30, tzinfo=timezone.utc)
        elif result and not getattr(result, "createdAt", None):
            result.createdAt = datetime(2023, 1, 1, 15, 30, tzinfo=timezone.utc)
        self._result._result = result
        return self


@pytest.fixture
def mock_context():
    return MockContext()


@pytest.fixture
def mock_config():
    return MockConfig()


@pytest.fixture
def mock_state():
    return MockState()


@pytest.fixture
def mock_database():
    return MockDatabase()


@pytest.fixture
def stash_processor(mock_config, mock_state, mock_context, mock_database):
    """Create a StashProcessing instance for testing."""
    mock_config._database = mock_database
    mock_config._stash = mock_context
    with (
        patch("stash.processing.print_info"),
        patch("stash.processing.print_warning"),
        patch("stash.processing.print_error"),
    ):
        processor = StashProcessing.from_config(mock_config, mock_state)
        processor._setup_worker_pool = AsyncMock(
            return_value=(
                MagicMock(),
                MagicMock(),
                asyncio.Semaphore(2),
                asyncio.Queue(),
            )
        )
        return processor
