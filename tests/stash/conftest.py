"""Test configuration and fixtures for Stash tests."""

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from stash import StashClient, StashContext
from stash.types.scene import Scene, SceneCreateInput


@pytest_asyncio.fixture
async def stash_context() -> AsyncGenerator[StashContext, None]:
    """Create a StashContext for testing.

    In sandbox mode, raises an error since these tests require a real Stash instance.
    Tests that require a real server should be skipped with pytest.mark.skip.
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

    This ensures proper client initialization through the context's get_client() method.
    Tests that require a real server should be skipped with pytest.mark.skip.
    """
    client = await stash_context.get_client()
    yield client
    # Ensure we explicitly clean up after each test
    await client.close()


@pytest.fixture
def enable_scene_creation():
    """Enable scene creation during tests.

    This fixture temporarily sets Scene.__create_input_type__ to SceneCreateInput,
    allowing scenes to be created directly during testing. It restores the original
    value after the test completes.

    Usage:
        @pytest.mark.asyncio
        async def test_something(stash_client, enable_scene_creation):
            scene = Scene(
                title="Test Scene",
                urls=["https://example.com/scene"],
                organized=True,
            )
            scene = await stash_client.create_scene(scene)  # Now works!
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

    Usage:
        async with stash_cleanup_tracker() as cleanup:
            performer = await create_performer(...)
            cleanup.performers.append(performer.id)
            # ... create more objects ...
            # Cleanup happens automatically when exiting the context
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
    """Create a mock session for testing GraphQL execution."""
    session = MagicMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def mock_transport():
    """Create a mock transport for testing GraphQL execution."""
    transport = MagicMock()
    transport.headers = {}
    transport.close = AsyncMock()
    return transport


@pytest.fixture
def mock_client(mock_transport):
    """Create a mock client with async context manager behavior and transport setup."""
    client = MagicMock()
    client.transport = mock_transport
    client.http_transport = mock_transport
    client.ws_transport = mock_transport
    client.__aenter__ = AsyncMock()
    client.close_async = AsyncMock()
    return client


@pytest.fixture
def test_query():
    """Sample GraphQL query for testing."""
    return """
    query TestQuery($id: ID!) {
        findScene(id: $id) {
            id
            title
        }
    }
    """
