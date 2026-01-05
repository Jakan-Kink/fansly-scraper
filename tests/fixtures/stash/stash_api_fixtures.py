"""Test configuration and fixtures for Stash tests."""

import contextlib
import logging
import os
import warnings
from collections.abc import AsyncGenerator, AsyncIterator
from unittest.mock import patch

# Removed: from unittest.mock import AsyncMock, MagicMock
# No longer using MagicMock for GraphQL client mocking - use respx instead
import httpx
import pytest
import pytest_asyncio
import respx
from stash_graphql_client import StashClient, StashContext
from stash_graphql_client.types import Scene, SceneCreateInput

from errors import StashCleanupWarning


# Export all fixtures for wildcard import
__all__ = [
    "enable_scene_creation",
    "respx_stash_client",
    "stash_cleanup_tracker",
    "stash_client",
    "stash_context",
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


@pytest_asyncio.fixture
async def respx_stash_client(stash_context) -> StashClient:
    """Get a StashClient with respx HTTP mocking enabled.

    This is for unit tests that want to mock HTTP responses to Stash GraphQL API.
    The fixture sets up respx mocking and provides the client within that context.

    Tests using this fixture should set up their own respx routes for specific
    GraphQL responses. The fixture provides a default empty response for any
    unmatched requests.

    Args:
        stash_context: The StashContext fixture

    Yields:
        StashClient: A client with respx mocking enabled

    Example:
        ```python
        @pytest.mark.asyncio
        async def test_find_studio(respx_stash_client):
            # Set up mock response
            respx.post("http://localhost:9999/graphql").mock(
                return_value=httpx.Response(200, json={
                    "data": {"findStudio": {"id": "123", "name": "Test"}}
                })
            )

            # Now the client will use your mocked response
            studio = await respx_stash_client.find_studio("123")
            assert studio.id == "123"
        ```
    """
    with respx.mock:
        # Default response for any unmatched GraphQL requests
        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )

        # Initialize the client (will use mocked HTTP)
        client = await stash_context.get_client()

        yield client

        # Cleanup: Clear LRU caches to prevent pollution between tests
        # All find_* methods may use @async_lru_cache which persists between tests
        # In stash-graphql-client v0.5.0+, safely check for cache_clear() before calling
        cache_methods = [
            "find_studio",
            "find_studios",
            "find_performer",
            "find_performers",
            "find_scene",
            "find_scenes",
            "find_tag",
            "find_tags",
            "find_gallery",
            "find_galleries",
            "find_image",
            "find_images",
            "find_marker",
            "find_markers",
        ]
        for method_name in cache_methods:
            if hasattr(client, method_name):
                method = getattr(client, method_name)
                if hasattr(method, "cache_clear"):
                    method.cache_clear()

        # Reset respx to prevent route pollution
        respx.reset()

        # Close the client
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
        auto_capture: bool = True,
    ) -> AsyncIterator[dict[str, list[str]]]:
        """Context manager for tracking and cleaning up Stash objects.

        Args:
            client: StashClient instance to track
            auto_capture: If True, automatically capture IDs from create mutations.
                         If False, require manual tracking via cleanup[type].append(id).
                         Default True for convenience, set False for performance.
        """
        created_objects = {
            "scenes": [],
            "performers": [],
            "studios": [],
            "tags": [],
            "galleries": [],
        }
        capture_mode = "with auto-capture" if auto_capture else "manual tracking"
        print(f"\n{'=' * 60}")
        print(f"CLEANUP TRACKER: Context entered ({capture_mode})")
        print(f"{'=' * 60}")
        if auto_capture:
            original_execute = client._session.execute

            async def execute_with_capture(document, *args, **kwargs):
                """Execute GraphQL and auto-capture created object IDs."""
                result = await original_execute(document, *args, **kwargs)

                # Quick check - only process if result is a dict and has data
                # This avoids overhead for queries and failed mutations
                if not (result and isinstance(result, dict)):
                    return result

                # Fast string check: only inspect if result contains "Create" mutations
                # Check first key for pattern - create mutations start with lowercase + "Create"
                result_keys = result.keys()
                has_create = any("Create" in key for key in result_keys)
                if not has_create:
                    return result

                # Now check specific create mutations
                if "sceneCreate" in result:
                    if (
                        (obj_data := result["sceneCreate"])
                        and (scene_id := obj_data.get("id"))
                        and scene_id not in created_objects["scenes"]
                    ):
                        created_objects["scenes"].append(scene_id)
                elif "performerCreate" in result:
                    if (
                        (obj_data := result["performerCreate"])
                        and (performer_id := obj_data.get("id"))
                        and performer_id not in created_objects["performers"]
                    ):
                        created_objects["performers"].append(performer_id)
                elif "studioCreate" in result:
                    if (
                        (obj_data := result["studioCreate"])
                        and (studio_id := obj_data.get("id"))
                        and studio_id not in created_objects["studios"]
                    ):
                        created_objects["studios"].append(studio_id)
                elif "tagCreate" in result:
                    if (
                        (obj_data := result["tagCreate"])
                        and (tag_id := obj_data.get("id"))
                        and tag_id not in created_objects["tags"]
                    ):
                        created_objects["tags"].append(tag_id)
                elif "galleryCreate" in result and (
                    (obj_data := result["galleryCreate"])
                    and (gallery_id := obj_data.get("id"))
                    and gallery_id not in created_objects["galleries"]
                ):
                    created_objects["galleries"].append(gallery_id)

                return result

        # Use patch.object for safer patching with automatic cleanup
        try:
            if auto_capture:
                with patch.object(client._session, "execute", execute_with_capture):
                    yield created_objects
            else:
                # No patching - manual tracking only
                yield created_objects
        finally:
            print(f"\n{'=' * 60}")
            print("CLEANUP TRACKER: Finally block entered")
            print("CLEANUP TRACKER: Objects to clean up:")
            for obj_type, ids in created_objects.items():
                if ids:
                    print(f"  - {obj_type}: {ids}")
            print(f"{'=' * 60}\n")

            # Warn about auto-captured objects (visible in pytest warnings even when test passes)
            if auto_capture and any(created_objects.values()):
                tracked_items = []
                for obj_type, ids in created_objects.items():
                    if ids:
                        tracked_items.append(f"  - {obj_type}: {ids}")

                if tracked_items:
                    warning_msg = "Auto-captured objects:\n" + "\n".join(tracked_items)
                    warnings.warn(
                        warning_msg,
                        StashCleanupWarning,
                        stacklevel=3,
                    )

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
                    error_msg = f"Cleanup had {len(errors)} error(s):\n" + "\n".join(
                        f"  - {error}" for error in errors
                    )
                    print(f"Warning: {error_msg}")

                    warnings.warn(
                        f"stash_cleanup_tracker: {error_msg}",
                        StashCleanupWarning,
                        stacklevel=3,
                    )
                else:
                    print("CLEANUP TRACKER: All objects deleted successfully")
            except Exception as e:
                error_msg = f"Cleanup failed catastrophically: {e}"
                print(f"Warning: {error_msg}")
                warnings.warn(
                    f"stash_cleanup_tracker: {error_msg}",
                    StashCleanupWarning,
                    stacklevel=3,
                )

            print(f"\n{'=' * 60}")
            print("CLEANUP TRACKER: Finally block completed")
            print(f"{'=' * 60}\n")

    return cleanup_context


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
