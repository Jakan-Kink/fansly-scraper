"""Test configuration and fixtures for Stash tests."""

import contextlib
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import UTC, datetime
# Removed: from unittest.mock import AsyncMock, MagicMock
# No longer using MagicMock for GraphQL client mocking - use respx instead

import pytest
import pytest_asyncio

from metadata import Account
from stash import StashClient, StashContext
from stash.types import Performer, Scene, SceneCreateInput, Studio


# Export all fixtures for wildcard import
__all__ = [
    "enable_scene_creation",
    # Removed: "mock_account", "mock_performer", "mock_studio", "mock_scene"
    # (MagicMock duplicates - use real factories from stash_type_factories)
    # Removed: "mock_client", "mock_session", "mock_transport"
    # (Mocked internal GraphQL components - use respx to mock HTTP instead)
    "stash_cleanup_tracker",
    "stash_client",
    "stash_context",
    "test_query",
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

    IMPORTANT: Any test using stash_client MUST also use stash_cleanup_tracker.
    This requirement is enforced automatically via pytest hook. Tests that use
    stash_client without stash_cleanup_tracker will fail with strict xfail.

    This fixture helps ensure test isolation by providing a context manager that
    automatically cleans up any Stash objects created during tests. It tracks objects
    by their IDs and deletes them in the correct order to handle dependencies.

    See tests/stash/CLEANUP_ENFORCEMENT_SUMMARY.md for detailed documentation.

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
        print(f"\n{'=' * 60}")
        print("CLEANUP TRACKER: Context entered")
        print(f"{'=' * 60}")
        try:
            yield created_objects
        finally:
            print(f"\n{'=' * 60}")
            print("CLEANUP TRACKER: Finally block entered")
            print("CLEANUP TRACKER: Objects to clean up:")
            for obj_type, ids in created_objects.items():
                if ids:
                    print(f"  - {obj_type}: {ids}")
            print(f"{'=' * 60}\n")

            # Clean up created objects in correct dependency order
            # Galleries reference scenes/performers/studios/tags - delete first
            # Scenes reference performers/studios/tags - delete second
            # Performers/Studios/Tags have no cross-dependencies - delete last
            errors = []

            try:
                # Delete galleries first (they can reference scenes)
                if created_objects["galleries"]:
                    for gallery_id in created_objects["galleries"]:
                        try:
                            await client.execute(
                                """
                                mutation DeleteGallery($id: ID!) {
                                    galleryDestroy(input: { ids: [$id] })
                                }
                                """,
                                {"id": gallery_id},
                            )
                        except Exception as e:
                            errors.append(f"Gallery {gallery_id}: {e}")

                # Delete scenes second (they reference performers/studios/tags)
                for scene_id in created_objects["scenes"]:
                    try:
                        await client.execute(
                            """
                            mutation DeleteScene($id: ID!) {
                                sceneDestroy(input: { id: $id })
                            }
                            """,
                            {"id": scene_id},
                        )
                    except Exception as e:
                        errors.append(f"Scene {scene_id}: {e}")

                # Delete performers
                for performer_id in created_objects["performers"]:
                    try:
                        await client.execute(
                            """
                            mutation DeletePerformer($id: ID!) {
                                performerDestroy(input: { id: $id })
                            }
                            """,
                            {"id": performer_id},
                        )
                    except Exception as e:
                        errors.append(f"Performer {performer_id}: {e}")

                # Delete studios
                for studio_id in created_objects["studios"]:
                    try:
                        await client.execute(
                            """
                            mutation DeleteStudio($id: ID!) {
                                studioDestroy(input: { id: $id })
                            }
                            """,
                            {"id": studio_id},
                        )
                    except Exception as e:
                        errors.append(f"Studio {studio_id}: {e}")

                # Delete tags
                for tag_id in created_objects["tags"]:
                    try:
                        await client.execute(
                            """
                            mutation DeleteTag($id: ID!) {
                                tagDestroy(input: { id: $id })
                            }
                            """,
                            {"id": tag_id},
                        )
                    except Exception as e:
                        errors.append(f"Tag {tag_id}: {e}")

                # Report any errors that occurred
                if errors:
                    print(f"Warning: Cleanup had {len(errors)} error(s):")
                    for error in errors:
                        print(f"  - {error}")
                else:
                    print("CLEANUP TRACKER: All objects deleted successfully")
            except Exception as e:
                print(f"Warning: Cleanup failed catastrophically: {e}")

            print(f"\n{'=' * 60}")
            print("CLEANUP TRACKER: Finally block completed")
            print(f"{'=' * 60}\n")

    return cleanup_context


# REMOVED: mock_session, mock_transport, mock_client
# These fixtures mocked internal GraphQL client components.
#
# Replacement: Use respx to mock at the true edge (HTTP layer)
# respx successfully intercepts HTTP calls underneath _session.execute()
# allowing tests through the real code path while mocking GraphQL HTTP responses.
#
# Migration example:
#   Before:
#     def test_query(mock_session, mock_client):
#         mock_session.execute.return_value = {"data": {...}}
#         mock_client.__aenter__.return_value = mock_session
#         client.client = mock_client
#
#   After:
#     @respx.mock
#     def test_query():
#         respx.post("http://localhost:9999/graphql").mock(
#             return_value=httpx.Response(200, json={"data": {...}})
#         )
#         # Test with real client, real _session.execute()
#
# This tests the same behavior through the REAL _session.execute() boundary.


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


# REMOVED: mock_account, mock_performer, mock_studio, mock_scene
# These fixtures were MagicMock-based duplicates.
#
# Replacements:
# - mock_account: Use AccountFactory from tests.fixtures.metadata
# - mock_performer: Use PerformerFactory or mock_performer from stash_type_factories
# - mock_studio: Use StudioFactory or mock_studio from stash_type_factories
# - mock_scene: Use SceneFactory or mock_scene from stash_type_factories
#
# The real factory-based fixtures are in stash_type_factories.py and return
# actual Strawberry GraphQL type instances, not MagicMock objects.
