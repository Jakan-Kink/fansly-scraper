"""Test configuration and fixtures for Stash tests."""

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator, Generator
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from metadata import Account, Attachment, Group, Media, Message, Post
from stash import StashClient, StashContext
from stash.types import Gallery, Image, Performer, Scene, SceneCreateInput, Studio, Tag

# Export all fixtures for wildcard import
__all__ = [
    "stash_context",
    "stash_client",
    "enable_scene_creation",
    "stash_cleanup_tracker",
    "mock_session",
    "mock_transport",
    "mock_client",
    "test_query",
    "mock_account",
    "mock_performer",
    "mock_studio",
    "mock_scene",
]


@pytest_asyncio.fixture
async def stash_context() -> AsyncGenerator[StashContext, None]:
    """Create a StashContext for testing.

    This is a core fixture that provides a configured StashContext for interacting with
    a Stash server. It handles connection setup and cleanup after tests are complete.

    In sandbox mode, raises an error since these tests require a real Stash instance.
    Tests that require a real server should be skipped with pytest.mark.skip.

    Yields:
        StashContext: A configured context for Stash API interactions

    Raises:
        RuntimeError: If run in sandbox mode where a real Stash instance isn't available
    """
    if os.environ.get("OPENHANDS_SANDBOX") in ("1", "true"):
        raise RuntimeError(
            "Stash integration tests cannot run in sandbox mode - they require a real Stash instance"
        )

    # Create connection config without ApiKey by default
    conn = {
        "Scheme": "http",
        "Host": "localhost",
        "Port": 9999,
        "Logger": logging.getLogger("stash.test"),
    }

    context = StashContext(
        conn=conn,
        verify_ssl=False,
    )

    yield context
    await context.close()


@pytest_asyncio.fixture
async def stash_client(stash_context) -> StashClient:
    """Get the StashClient from the StashContext.

    This fixture depends on the stash_context fixture and provides a properly initialized
    StashClient instance. It ensures that the client is created through the context's
    get_client() method and properly cleaned up after tests.

    Tests that require a real server should be skipped with pytest.mark.skip.

    Args:
        stash_context: The StashContext fixture

    Yields:
        StashClient: An initialized client for Stash API interactions
    """
    client = await stash_context.get_client()
    yield client
    # Ensure we explicitly clean up after each test
    await client.close()


@pytest.fixture
def enable_scene_creation():
    """Enable scene creation during tests.

    This fixture temporarily sets Scene.__create_input_type__ to SceneCreateInput,
    allowing scenes to be created directly during testing. It handles the setup and
    cleanup needed to modify the Scene class's behavior temporarily for testing.

    Without this fixture, Scene objects normally cannot be created directly via API
    because the __create_input_type__ attribute is not set.

    After the test completes, the original class configuration is restored.

    Usage:
        ```python
        @pytest.mark.asyncio
        async def test_something(stash_client, enable_scene_creation):
            # With this fixture, Scene objects can be created directly
            scene = Scene(
                title="Test Scene",
                urls=["https://example.com/scene"],
                organized=True,
            )
            scene = await stash_client.create_scene(scene)  # Now works!
        ```
    """
    # Store original value
    original_create_input_type = getattr(Scene, "__create_input_type__", None)

    # Enable scene creation
    Scene.__create_input_type__ = SceneCreateInput

    yield

    # Restore original value
    if original_create_input_type is None:
        delattr(Scene, "__create_input_type__")
    else:
        Scene.__create_input_type__ = original_create_input_type


@pytest_asyncio.fixture
async def stash_cleanup_tracker():
    """Fixture that provides a cleanup context manager for Stash objects.

    This fixture helps ensure test isolation by providing a context manager that
    automatically cleans up any Stash objects created during tests. It tracks objects
    by their IDs and deletes them in the correct order to handle dependencies.

    Returns:
        async_context_manager: A context manager for tracking and cleaning up Stash objects

    Usage:
        ```python
        async def test_something(stash_client, stash_cleanup_tracker):
            async with stash_cleanup_tracker(stash_client) as cleanup:
                # Create test objects
                performer = await stash_client.create_performer(...)
                cleanup['performers'].append(performer.id)

                # Create more objects that depend on performer
                scene = await stash_client.create_scene(...)
                cleanup['scenes'].append(scene.id)

                # Test logic here...

                # Cleanup happens automatically when exiting the context
        ```
    """

    @contextlib.asynccontextmanager
    async def cleanup_context(
        client: StashClient,
    ) -> AsyncIterator[dict[str, list[str]]]:
        created_objects = {
            "scenes": [],
            "performers": [],
            "studios": [],
            "tags": [],
            "galleries": [],
        }
        try:
            yield created_objects
        finally:
            # Clean up created objects in reverse order of creation
            try:
                # Delete scenes first (they depend on performers/studios/tags)
                for scene_id in created_objects["scenes"]:
                    await client.execute(
                        """
                        mutation DeleteScene($id: ID!) {
                            sceneDestroy(input: { id: $id })
                        }
                        """,
                        {"id": scene_id},
                    )

                # Delete performers
                for performer_id in created_objects["performers"]:
                    await client.execute(
                        """
                        mutation DeletePerformer($id: ID!) {
                            performerDestroy(input: { id: $id })
                        }
                        """,
                        {"id": performer_id},
                    )

                # Delete studios
                for studio_id in created_objects["studios"]:
                    await client.execute(
                        """
                        mutation DeleteStudio($id: ID!) {
                            studioDestroy(input: { id: $id })
                        }
                        """,
                        {"id": studio_id},
                    )

                # Delete tags
                for tag_id in created_objects["tags"]:
                    await client.execute(
                        """
                        mutation DeleteTag($id: ID!) {
                            tagDestroy(input: { id: $id })
                        }
                        """,
                        {"id": tag_id},
                    )

                # Delete galleries
                if created_objects["galleries"]:
                    await client.execute(
                        """
                        mutation DeleteGalleries($ids: [ID!]!) {
                            galleryDestroy(input: { ids: $ids })
                        }
                        """,
                        {"ids": created_objects["galleries"]},
                    )
            except Exception as e:
                print(f"Warning: Cleanup failed: {e}")

    return cleanup_context


@pytest.fixture
def mock_session():
    """Create a mock session for testing GraphQL execution.

    This fixture provides a mock SQLAlchemy session for testing database operations
    without needing a real database connection. The execute method is set up as an
    AsyncMock for use in async test functions.

    Returns:
        MagicMock: A mock session object with AsyncMock for the execute method
    """
    session = MagicMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def mock_transport():
    """Create a mock transport for testing GraphQL execution.

    This fixture provides a mock transport object for GraphQL client testing, with
    appropriate headers and async close method. This allows testing GraphQL client
    code without making actual network requests.

    Returns:
        MagicMock: A mock transport object configured for GraphQL client testing
    """
    transport = MagicMock()
    transport.headers = {}
    transport.close = AsyncMock()
    return transport


@pytest.fixture
def mock_client(mock_transport):
    """Create a mock client with async context manager behavior and transport setup.

    This fixture provides a mock GraphQL client that can be used for testing code
    that requires a GraphQL client without making actual network requests. It sets up
    the necessary transport attributes and async context manager behavior.

    Args:
        mock_transport: The mock transport fixture

    Returns:
        MagicMock: A mock client configured for GraphQL testing with async support
    """
    client = MagicMock()
    client.transport = mock_transport
    client.http_transport = mock_transport
    client.ws_transport = mock_transport
    client.__aenter__ = AsyncMock()
    client.close_async = AsyncMock()
    return client


@pytest.fixture
def test_query():
    """Sample GraphQL query for testing.

    This fixture provides a simple GraphQL query string that can be used in tests
    to verify GraphQL client behavior. It includes a query with variables and
    nested fields to test different aspects of GraphQL execution.

    Returns:
        str: A sample GraphQL query string for testing
    """
    return """
    query TestQuery($id: ID!) {
        findScene(id: $id) {
            id
            title
        }
    }
    """


@pytest.fixture
def mock_account():
    """Create a mock account for testing.

    This fixture provides a mock Account object that can be used for testing
    without requiring a real database connection or actual account data.

    This fixture is designed to be both injected by pytest and safe to call directly,
    which helps prevent "Fixture called directly" errors in tests that might
    accidentally use the fixture as a function.

    Returns:
        MagicMock: A mock account object with common properties that is also callable
    """
    account = MagicMock(spec=Account)
    account.id = 54321
    account.username = "test_user"
    account.displayName = "Test User"
    account.about = "Test account for unit tests"
    account.location = "Test Location"
    account.joinDate = datetime(2024, 1, 1, 12, 0, 0)
    account.lastSeen = datetime(2024, 1, 2, 12, 0, 0)

    # Make the mock account safe to call directly (returns itself when called)
    account.__call__ = MagicMock(return_value=account)

    return account


@pytest.fixture
def mock_performer():
    """Create a mock performer for testing.

    This fixture provides a mock Performer object that can be used for testing
    StashClient interactions without requiring a real Stash server connection.

    This fixture is designed to be both injected by pytest and safe to call directly,
    which helps prevent "Fixture called directly" errors in tests that might
    accidentally use the fixture as a function.

    Returns:
        MagicMock: A mock performer object with common properties that is also callable
    """
    performer = MagicMock(spec=Performer)
    performer.id = "performer_123"
    performer.name = "Test Performer"
    performer.aliases = ["Test Alias"]
    performer.gender = "FEMALE"
    performer.url = "https://example.com/performer"
    performer.twitter = "@test_performer"
    performer.instagram = "test_performer"
    performer.birthdate = "1990-01-01"
    performer.ethnicity = "CAUCASIAN"
    performer.country = "Test Country"
    performer.eye_color = "BLUE"
    performer.height = 170
    performer.measurements = "34-24-36"
    performer.fake_tits = "NO"
    performer.career_length = "2020-2024"
    performer.tattoos = "None"
    performer.piercings = "None"
    performer.tags = []

    # Make the mock performer safe to call directly (returns itself when called)
    performer.__call__ = MagicMock(return_value=performer)

    return performer


@pytest.fixture
def mock_studio():
    """Create a mock studio for testing.

    This fixture provides a mock Studio object that can be used for testing
    StashClient interactions without requiring a real Stash server connection.

    This fixture is designed to be both injected by pytest and safe to call directly,
    which helps prevent "Fixture called directly" errors in tests that might
    accidentally use the fixture as a function.

    Returns:
        MagicMock: A mock studio object with common properties that is also callable
    """
    studio = MagicMock(spec=Studio)
    studio.id = "studio_123"
    studio.name = "Test Studio"
    studio.url = "https://example.com/studio"
    studio.parent_studio = None

    # Make the mock studio safe to call directly (returns itself when called)
    studio.__call__ = MagicMock(return_value=studio)

    return studio


@pytest.fixture
def mock_scene():
    """Create a mock scene for testing.

    This fixture provides a mock Scene object that can be used for testing
    StashClient interactions without requiring a real Stash server connection.

    This fixture is designed to be both injected by pytest and safe to call directly,
    which helps prevent "Fixture called directly" errors in tests that might
    accidentally use the fixture as a function.

    Returns:
        MagicMock: A mock scene object with common properties that is also callable
    """
    scene = MagicMock(spec=Scene)
    scene.id = "scene_123"
    scene.title = "Test Scene"
    scene.details = "Test scene for testing"
    scene.date = "2024-04-01"
    scene.organized = True
    scene.url = "https://example.com/scene"
    scene.urls = ["https://example.com/scene"]
    scene.files = []
    scene.performers = []
    scene.studio = None
    scene.tags = []
    scene.__type_name__ = "Scene"

    # Make save and destroy awaitable
    scene.save = AsyncMock()
    scene.destroy = AsyncMock()
    scene.is_dirty = MagicMock(return_value=True)

    # Make the mock scene safe to call directly (returns itself when called)
    scene.__call__ = MagicMock(return_value=scene)

    return scene
