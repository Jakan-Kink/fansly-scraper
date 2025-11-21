"""TRUE integration tests for SceneClientMixin.

These tests make REAL GraphQL calls to the Docker Stash instance and verify actual API behavior.
Uses capture_graphql_calls to validate request sequences.

NOTE: Tests are being migrated incrementally from mock-based to TRUE integration.
Some tests may still use old patterns - migration in progress.
"""

import pytest

from stash import StashClient
from stash.types import Scene
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls
from tests.fixtures.stash.stash_type_factories import SceneFactory


@pytest.fixture
def mock_scene():
    """Create a test scene using SceneFactory."""
    return SceneFactory.build(
        id="123",
        title="Test Scene",
        details="Test scene details",
        date="2024-01-01",
        urls=["https://example.com/scene"],
        organized=True,
    )


@pytest.mark.asyncio
async def test_find_scene(
    stash_client: StashClient, stash_cleanup_tracker, enable_scene_creation
) -> None:
    """Test finding a scene by ID - TRUE INTEGRATION TEST.

    Creates a real scene in Stash, then verifies find_scene can retrieve it.
    Uses enable_scene_creation fixture to allow direct scene creation.
    """
    async with stash_cleanup_tracker(stash_client) as cleanup:
        # Create a real scene in Stash using SceneFactory data
        test_scene = SceneFactory.build(
            id="new",
            title="Test Find Scene",
            details="Test scene for find_scene test",
            date="2024-01-01",
            urls=["https://example.com/scene/find"],
            organized=False,
        )

        with capture_graphql_calls(stash_client) as calls:
            created_scene = await stash_client.create_scene(test_scene)
            cleanup["scenes"].append(created_scene.id)

        # Verify sceneCreate call
        assert len(calls) == 1, "Expected 1 GraphQL call for create"
        assert "sceneCreate" in calls[0]["query"]

        # Test finding by ID - makes real GraphQL call
        with capture_graphql_calls(stash_client) as calls:
            found_scene = await stash_client.find_scene(created_scene.id)

        # Verify findScene call
        assert len(calls) == 1, "Expected 1 GraphQL call for find"
        assert "findScene" in calls[0]["query"]
        assert calls[0]["variables"]["id"] == created_scene.id

        # Verify result
        assert found_scene is not None
        assert found_scene.id == created_scene.id
        assert found_scene.title == "Test Find Scene"
        assert found_scene.details == "Test scene for find_scene test"
        assert found_scene.date == "2024-01-01"
        assert "https://example.com/scene/find" in found_scene.urls
        assert found_scene.organized is False


@pytest.mark.asyncio
async def test_find_scenes(
    stash_client: StashClient,
    stash_cleanup_tracker,
    enable_scene_creation,
) -> None:
    """Test finding scenes with filters - TRUE INTEGRATION TEST.

    Creates multiple real scenes in Stash, then tests filter functionality.
    Uses enable_scene_creation fixture to allow direct scene creation.
    """
    async with stash_cleanup_tracker(stash_client) as cleanup:
        # Create multiple real scenes with different attributes
        scene1 = SceneFactory.build(
            id="new",
            title="Filter Test Alpha",
            details="First test scene",
            organized=True,
            urls=["https://example.com/scene/alpha"],
        )
        scene2 = SceneFactory.build(
            id="new",
            title="Filter Test Beta",
            details="Second test scene",
            organized=False,
            urls=["https://example.com/scene/beta"],
        )

        created1 = await stash_client.create_scene(scene1)
        created2 = await stash_client.create_scene(scene2)

        cleanup["scenes"].extend([created1.id, created2.id])

        # Test finding all scenes with name containing "Filter Test"
        with capture_graphql_calls(stash_client) as calls:
            result = await stash_client.find_scenes(
                scene_filter={"title": {"value": "Filter Test", "modifier": "INCLUDES"}}
            )

        # Verify findScenes call
        assert len(calls) == 1, "Expected 1 GraphQL call for find_scenes"
        assert "findScenes" in calls[0]["query"]
        assert calls[0]["variables"]["scene_filter"]["title"]["value"] == "Filter Test"

        # Verify results - should find both scenes
        # Note: result.scenes contains dicts, not Scene objects (Strawberry doesn't deserialize)
        assert result.count >= 2
        assert len(result.scenes) >= 2
        scene_titles = [s["title"] for s in result.scenes]
        assert "Filter Test Alpha" in scene_titles
        assert "Filter Test Beta" in scene_titles

        # Test with organized filter - should only find scene1
        with capture_graphql_calls(stash_client) as calls:
            result = await stash_client.find_scenes(scene_filter={"organized": True})

        # Verify call
        assert len(calls) == 1
        assert "findScenes" in calls[0]["query"]

        # Verify result - should include our organized scene
        # Note: result.scenes contains dicts, not Scene objects
        assert result.count >= 1
        organized_scenes = [s for s in result.scenes if s["id"] == created1.id]
        assert len(organized_scenes) == 1
        assert organized_scenes[0]["organized"] is True


@pytest.mark.asyncio
async def test_find_scenes_error(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test handling errors when finding scenes - TRUE INTEGRATION TEST.

    Tests that find_scenes handles server-side GraphQL validation errors gracefully:
    1. Invalid field types trigger GRAPHQL_VALIDATION_FAILED at gql layer
    2. Nonexistent fields trigger GRAPHQL_VALIDATION_FAILED at gql layer
    3. Both are caught by find_scenes and return empty results

    This verifies the entire error flow from gql → StashClient → find_scenes.
    """
    # Test 1: Invalid field type - rating100 expects IntCriterionInput, send string
    with capture_graphql_calls(stash_client) as calls:
        result = await stash_client.find_scenes(
            scene_filter={"rating100": "invalid_string_instead_of_int"}
        )

    # Verify the invalid parameter was sent to gql
    assert len(calls) == 1
    assert "findScenes" in calls[0]["query"]
    assert (
        calls[0]["variables"]["scene_filter"]["rating100"]
        == "invalid_string_instead_of_int"
    )

    # Verify gql raised TransportQueryError with GRAPHQL_VALIDATION_FAILED
    assert calls[0]["exception"] is not None
    assert calls[0]["exception"].__class__.__name__ == "TransportQueryError"
    assert (
        calls[0]["exception"].errors[0]["extensions"]["code"]
        == "GRAPHQL_VALIDATION_FAILED"
    )
    assert "IntCriterionInput" in calls[0]["exception"].errors[0]["message"]

    # Verify result was None from gql (error occurred)
    assert calls[0]["result"] is None

    # Verify find_scenes handled the error and returned empty results
    assert result.count == 0
    assert len(result.scenes) == 0
    assert result.duration == 0
    assert result.filesize == 0

    # Test 2: Nonexistent field - invalid structure
    with capture_graphql_calls(stash_client) as calls:
        result = await stash_client.find_scenes(
            scene_filter={"nonexistent_field": {"invalid": "structure"}}
        )

    # Verify the invalid parameter was sent to gql
    assert len(calls) == 1
    assert "findScenes" in calls[0]["query"]
    assert "nonexistent_field" in calls[0]["variables"]["scene_filter"]

    # Verify gql raised TransportQueryError with GRAPHQL_VALIDATION_FAILED
    assert calls[0]["exception"] is not None
    assert calls[0]["exception"].__class__.__name__ == "TransportQueryError"
    assert (
        calls[0]["exception"].errors[0]["extensions"]["code"]
        == "GRAPHQL_VALIDATION_FAILED"
    )
    assert "unknown field" in calls[0]["exception"].errors[0]["message"]

    # Verify result was None from gql (error occurred)
    assert calls[0]["result"] is None

    # Verify find_scenes handled the error and returned empty results
    assert result.count == 0
    assert len(result.scenes) == 0
    assert result.duration == 0
    assert result.filesize == 0


@pytest.mark.asyncio
async def test_create_scene(
    stash_client: StashClient, stash_cleanup_tracker, enable_scene_creation
) -> None:
    """Test creating a scene - TRUE INTEGRATION TEST.

    Creates real scenes with varying field sets to verify the API.
    Uses enable_scene_creation fixture to allow direct scene creation.
    """
    async with stash_cleanup_tracker(stash_client) as cleanup:
        # Test creating scene with minimal fields
        minimal_scene = SceneFactory.build(
            id="new",
            title="Minimal Scene Test",
            urls=["https://example.com/scene/minimal"],
            organized=False,
        )

        with capture_graphql_calls(stash_client) as calls:
            created1 = await stash_client.create_scene(minimal_scene)
            cleanup["scenes"].append(created1.id)

        # Verify sceneCreate call
        assert len(calls) == 1, "Expected 1 GraphQL call for minimal create"
        assert "sceneCreate" in calls[0]["query"]
        assert calls[0]["variables"]["input"]["title"] == "Minimal Scene Test"

        # Verify created scene
        assert created1 is not None
        assert created1.title == "Minimal Scene Test"
        assert "https://example.com/scene/minimal" in created1.urls
        assert created1.organized is False

        # Test creating scene with all fields populated
        full_scene = SceneFactory.build(
            id="new",
            title="Full Scene Test",
            details="Complete scene with all fields",
            date="2024-02-15",
            urls=["https://example.com/scene/full"],
            organized=True,
        )

        with capture_graphql_calls(stash_client) as calls:
            created2 = await stash_client.create_scene(full_scene)
            cleanup["scenes"].append(created2.id)

        # Verify sceneCreate call
        assert len(calls) == 1, "Expected 1 GraphQL call for full create"
        assert "sceneCreate" in calls[0]["query"]
        assert calls[0]["variables"]["input"]["title"] == "Full Scene Test"
        assert (
            calls[0]["variables"]["input"]["details"]
            == "Complete scene with all fields"
        )

        # Verify created scene
        assert created2 is not None
        assert created2.title == "Full Scene Test"
        assert created2.details == "Complete scene with all fields"
        assert created2.date == "2024-02-15"
        assert created2.organized is True


@pytest.mark.asyncio
async def test_update_scene(
    stash_client: StashClient, stash_cleanup_tracker, enable_scene_creation
) -> None:
    """Test updating a scene - TRUE INTEGRATION TEST.

    Creates a real scene, then updates it with different field values.
    Uses enable_scene_creation fixture to allow direct scene creation.
    """
    async with stash_cleanup_tracker(stash_client) as cleanup:
        # Create initial scene
        initial_scene = SceneFactory.build(
            id="new",
            title="Original Title",
            details="Original details",
            organized=False,
            urls=["https://example.com/scene/update"],
        )

        created_scene = await stash_client.create_scene(initial_scene)
        cleanup["scenes"].append(created_scene.id)

        # Update single field - title
        created_scene.title = "Updated Title"

        with capture_graphql_calls(stash_client) as calls:
            updated1 = await stash_client.update_scene(created_scene)

        # Verify sceneUpdate call
        assert len(calls) == 1, "Expected 1 GraphQL call for update"
        assert "sceneUpdate" in calls[0]["query"]
        assert calls[0]["variables"]["input"]["id"] == created_scene.id
        assert calls[0]["variables"]["input"]["title"] == "Updated Title"

        # Verify updated scene
        assert updated1.id == created_scene.id
        assert updated1.title == "Updated Title"

        # Update multiple fields
        updated1.details = "Updated details"
        updated1.organized = True

        with capture_graphql_calls(stash_client) as calls:
            updated2 = await stash_client.update_scene(updated1)

        # Verify sceneUpdate call
        assert len(calls) == 1, "Expected 1 GraphQL call for multi-field update"
        assert "sceneUpdate" in calls[0]["query"]
        assert calls[0]["variables"]["input"]["details"] == "Updated details"
        assert calls[0]["variables"]["input"]["organized"] is True

        # Verify final state
        assert updated2.details == "Updated details"
        assert updated2.organized is True


@pytest.mark.asyncio
async def test_scene_generate_screenshot(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test generating a scene screenshot - TRUE INTEGRATION TEST.

    Uses a real scene from Docker Stash if available, otherwise skips.
    Makes REAL GraphQL calls to generate screenshots from actual video files.
    Calculates safe timestamps based on the scene's actual duration.
    """
    # Check if Stash has any scenes available
    scenes_result = await stash_client.find_scenes()

    if scenes_result.count == 0:
        pytest.skip("No scenes available in Stash for screenshot testing")

    # Use the first available scene and get its duration
    real_scene = scenes_result.scenes[0]
    scene_id = real_scene["id"]

    # Get file info to check duration
    files = real_scene.get("files", [])
    if not files:
        pytest.skip("Scene has no files, cannot generate screenshots")

    duration = files[0].get("duration")
    if not duration or duration <= 0:
        pytest.skip("Scene has no valid duration, cannot generate screenshots")

    # Calculate safe timestamps (25% and 75% of duration)
    timestamp1 = duration * 0.25
    timestamp2 = duration * 0.75

    # Test generating screenshot at first timestamp
    with capture_graphql_calls(stash_client) as calls:
        path1 = await stash_client.scene_generate_screenshot(scene_id, at=timestamp1)

    # Verify GraphQL call was made correctly
    assert len(calls) == 1, "Expected 1 GraphQL call for screenshot generation"
    assert "sceneGenerateScreenshot" in calls[0]["query"]
    assert calls[0]["variables"]["id"] == scene_id
    assert calls[0]["variables"]["at"] == timestamp1
    assert calls[0]["exception"] is None, "GraphQL call should not raise exception"

    # Verify GraphQL response structure
    assert calls[0]["result"] is not None
    assert "sceneGenerateScreenshot" in calls[0]["result"]

    # Verify we got a valid path back (Stash returns "todo" for failed operations)
    assert isinstance(path1, str)
    assert path1 != "", "Should return a path or 'todo', not empty string"
    # Print for inspection
    print(f"\nScreenshot path 1: {path1!r}")

    # Test generating screenshot at second timestamp
    with capture_graphql_calls(stash_client) as calls:
        path2 = await stash_client.scene_generate_screenshot(scene_id, at=timestamp2)

    # Verify GraphQL call
    assert len(calls) == 1, "Expected 1 GraphQL call for second screenshot"
    assert "sceneGenerateScreenshot" in calls[0]["query"]
    assert calls[0]["variables"]["at"] == timestamp2

    # Verify we got a path back
    assert isinstance(path2, str)


@pytest.mark.asyncio
async def test_scene_generate_screenshot_error(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test handling errors when generating a scene screenshot - TRUE INTEGRATION TEST.

    Tests behavior with invalid scene IDs by making REAL calls.
    Stash returns "todo" for both invalid scenes and scenes where screenshot generation fails.
    """
    # Test with nonexistent scene ID
    with capture_graphql_calls(stash_client) as calls:
        result = await stash_client.scene_generate_screenshot("99999999", at=0.0)

    # Verify GraphQL request was constructed correctly
    assert len(calls) == 1, "Expected 1 GraphQL call"
    assert "sceneGenerateScreenshot" in calls[0]["query"]
    assert calls[0]["variables"]["id"] == "99999999"
    assert calls[0]["variables"]["at"] == 0.0

    # Verify no exception was raised (Stash doesn't error on invalid IDs)
    assert calls[0]["exception"] is None, "GraphQL call should succeed"

    # Verify GraphQL response structure
    assert calls[0]["result"] is not None, "Should receive a response"
    assert "sceneGenerateScreenshot" in calls[0]["result"], (
        "Response should contain sceneGenerateScreenshot field"
    )

    # Stash returns "todo" for scenes that don't exist or can't generate screenshots
    assert result == "todo", "Invalid scene ID should return 'todo'"
    assert calls[0]["result"]["sceneGenerateScreenshot"] == "todo"


@pytest.mark.asyncio
async def test_find_duplicate_scenes(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test finding duplicate scenes - TRUE INTEGRATION TEST.

    Makes REAL GraphQL calls to Stash to find duplicate scenes.
    The test verifies the API works correctly regardless of whether
    duplicates exist in the Stash instance.
    """
    # Make REAL call with default settings
    with capture_graphql_calls(stash_client) as calls:
        duplicates = await stash_client.find_duplicate_scenes()

    # Verify GraphQL request was constructed correctly
    assert len(calls) == 1, "Expected 1 GraphQL call"
    assert "findDuplicateScenes" in calls[0]["query"]
    assert calls[0]["exception"] is None, "GraphQL call should not raise exception"

    # Verify GraphQL response structure
    assert calls[0]["result"] is not None, "Should receive a response"
    assert "findDuplicateScenes" in calls[0]["result"]

    # Print verbose output showing what was returned
    print("\n=== Default Duplicate Detection ===")
    print("GraphQL query contains: findDuplicateScenes")
    print(f"GraphQL response: {calls[0]['result']['findDuplicateScenes']}")
    print(f"Number of duplicate groups found: {len(duplicates)}")
    if duplicates:
        print(f"First group has {len(duplicates[0])} scenes")
        for idx, scene in enumerate(duplicates[0][:2]):  # Show first 2
            print(f"  Scene {idx + 1}: ID={scene.id}, Title={scene.title}")
    else:
        print("No duplicates found in Stash instance")
    print("===================================\n")

    # Verify result structure (may be empty list if no duplicates exist)
    assert isinstance(duplicates, list)
    if duplicates:
        # If duplicates exist, verify structure
        assert isinstance(duplicates[0], list), "Each duplicate group should be a list"
        assert len(duplicates[0]) >= 2, "Duplicate groups should have at least 2 scenes"
        # Verify each scene in the group is a Scene object
        for scene in duplicates[0]:
            assert isinstance(scene, Scene)
            assert hasattr(scene, "id")
            assert hasattr(scene, "title")

    # Test with custom settings (distance and duration_diff)
    with capture_graphql_calls(stash_client) as calls:
        duplicates_custom = await stash_client.find_duplicate_scenes(
            distance=100,
            duration_diff=1.0,
        )

    # Verify GraphQL request included custom parameters
    assert len(calls) == 1, "Expected 1 GraphQL call"
    assert "findDuplicateScenes" in calls[0]["query"]
    assert calls[0]["variables"]["distance"] == 100
    assert calls[0]["variables"]["duration_diff"] == 1.0
    assert calls[0]["exception"] is None

    # Verify GraphQL response structure
    assert calls[0]["result"] is not None
    assert "findDuplicateScenes" in calls[0]["result"]

    # Verify result structure
    assert isinstance(duplicates_custom, list)


@pytest.mark.asyncio
async def test_find_duplicate_scenes_error(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test handling errors when finding duplicate scenes - TRUE INTEGRATION TEST.

    Tests that find_duplicate_scenes handles invalid parameters gracefully.
    Uses REAL GraphQL calls with invalid parameter types to trigger validation errors.
    """
    # Test with invalid distance parameter type (expects int, send string)
    with capture_graphql_calls(stash_client) as calls:
        result = await stash_client.find_duplicate_scenes(
            distance="invalid_string_not_int"
        )

    # Verify the invalid parameter was sent to gql
    assert len(calls) == 1, "Expected 1 GraphQL call"
    assert "findDuplicateScenes" in calls[0]["query"]
    assert calls[0]["variables"]["distance"] == "invalid_string_not_int"

    # Verify gql raised TransportQueryError with GRAPHQL_VALIDATION_FAILED
    assert calls[0]["exception"] is not None
    assert calls[0]["exception"].__class__.__name__ == "TransportQueryError"
    assert (
        calls[0]["exception"].errors[0]["extensions"]["code"]
        == "GRAPHQL_VALIDATION_FAILED"
    )
    assert "Int" in calls[0]["exception"].errors[0]["message"]

    # Verify result was None from gql (error occurred)
    assert calls[0]["result"] is None

    # Verify find_duplicate_scenes handled the error and returned empty list
    assert isinstance(result, list)
    assert len(result) == 0


@pytest.mark.asyncio
async def test_parse_scene_filenames(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test parsing scene filenames - TRUE INTEGRATION TEST.

    Makes REAL GraphQL calls to Stash's filename parser.

    Skips on Stash v0.29.3 (build hash 7716c4dd) which crashes with:
    "invalid memory address or nil pointer dereference"
    """
    # Check Stash version - skip if it's the broken version
    from gql import gql

    version_query = gql("""
        query {
            version {
                version
                hash
            }
        }
    """)

    version_result = await stash_client._session.execute(version_query)
    stash_version = version_result.get("version", {})
    build_hash = stash_version.get("hash", "")
    version_string = stash_version.get("version", "")

    # Skip if Stash v0.29.3 build 7716c4dd (has nil pointer bug)
    if build_hash == "7716c4dd":
        pytest.skip(
            f"parseSceneFilenames crashes on Stash {version_string} "
            f"(build hash {build_hash}) with nil pointer dereference"
        )

    # Make REAL call with minimal config
    with capture_graphql_calls(stash_client) as calls:
        result = await stash_client.parse_scene_filenames(
            config={"capitalizeTitle": False}
        )

    # Verify GraphQL request was constructed correctly
    assert len(calls) == 1, "Expected 1 GraphQL call"
    assert "parseSceneFilenames" in calls[0]["query"]
    assert calls[0]["exception"] is None, "GraphQL call should not raise exception"

    # Verify GraphQL response structure
    assert calls[0]["result"] is not None, "Should receive a response"
    assert "parseSceneFilenames" in calls[0]["result"]

    # Print verbose output showing what was returned
    print("\n=== Parse Scene Filenames (Default) ===")
    print(
        f"GraphQL response keys: {list(calls[0]['result']['parseSceneFilenames'].keys())}"
    )
    if result and isinstance(result, dict):
        print(f"Result keys: {list(result.keys())}")
        if "count" in result:
            print(f"Scenes parsed: {result.get('count', 0)}")
        if result.get("results"):
            print(f"First result keys: {list(result['results'][0].keys())}")
    print("======================================\n")

    # Verify result structure (may be empty if no scenes to parse)
    assert isinstance(result, dict)
    if result and "results" in result:
        assert isinstance(result["results"], list)
        if result["results"]:
            # Verify structure of first result
            assert "scene" in result["results"][0]

    # Test with custom settings
    with capture_graphql_calls(stash_client) as calls:
        result_custom = await stash_client.parse_scene_filenames(
            filter_={"q": "test"},
            config={
                "whitespace": True,
                "field": "title",
            },
        )

    # Verify GraphQL request included custom parameters
    assert len(calls) == 1, "Expected 1 GraphQL call"
    assert "parseSceneFilenames" in calls[0]["query"]
    assert calls[0]["variables"]["filter"] == {"q": "test"}
    assert calls[0]["variables"]["config"]["whitespace"] is True
    assert calls[0]["variables"]["config"]["field"] == "title"
    assert calls[0]["exception"] is None

    # Verify GraphQL response structure
    assert calls[0]["result"] is not None
    assert "parseSceneFilenames" in calls[0]["result"]

    # Verify result structure
    assert isinstance(result_custom, dict)


@pytest.mark.asyncio
async def test_parse_scene_filenames_error(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test handling errors when parsing scene filenames - TRUE INTEGRATION TEST.

    Tests that parse_scene_filenames handles invalid parameters gracefully.
    Uses REAL GraphQL calls with invalid parameter types to trigger validation errors.
    """
    # Test with invalid config parameter type (expects dict/object, send string)
    with capture_graphql_calls(stash_client) as calls:
        result = await stash_client.parse_scene_filenames(
            config="invalid_string_not_object"
        )

    # Verify the invalid parameter was sent to gql
    assert len(calls) == 1, "Expected 1 GraphQL call"
    assert "parseSceneFilenames" in calls[0]["query"]
    assert calls[0]["variables"]["config"] == "invalid_string_not_object"

    # Verify gql raised TransportQueryError with GRAPHQL_VALIDATION_FAILED
    assert calls[0]["exception"] is not None
    assert calls[0]["exception"].__class__.__name__ == "TransportQueryError"
    assert (
        calls[0]["exception"].errors[0]["extensions"]["code"]
        == "GRAPHQL_VALIDATION_FAILED"
    )

    # Verify result was None from gql (error occurred)
    assert calls[0]["result"] is None

    # Verify parse_scene_filenames handled the error and returned empty dict
    assert isinstance(result, dict)
    assert result == {}


@pytest.mark.asyncio
async def test_scenes_update(
    stash_client: StashClient, stash_cleanup_tracker, enable_scene_creation
) -> None:
    """Test updating multiple scenes - TRUE INTEGRATION TEST.

    Creates real scenes in Docker Stash, updates them via scenes_update mutation,
    and verifies the GraphQL mutation was constructed correctly.
    """
    # Create two real scenes in Stash
    scene1 = SceneFactory.build(
        id="new",
        title="First Test Scene",
        urls=["https://example.com/scene1"],
        organized=False,
    )
    scene2 = SceneFactory.build(
        id="new",
        title="Second Test Scene",
        urls=["https://example.com/scene2"],
        organized=False,
    )

    created1 = await stash_client.create_scene(scene1)
    created2 = await stash_client.create_scene(scene2)

    # Track for cleanup
    async with stash_cleanup_tracker(stash_client) as cleanup:
        cleanup["scenes"].extend([created1.id, created2.id])
        # Update both scenes with new data
        created1.title = "Updated First Scene"
        created1.organized = True
        created2.title = "Updated Second Scene"
        created2.organized = True

        scenes_to_update = [created1, created2]

        # Make REAL bulk update call with GraphQL capture
        with capture_graphql_calls(stash_client) as calls:
            updated_scenes = await stash_client.scenes_update(scenes_to_update)

        # Verify GraphQL mutation was constructed correctly
        assert len(calls) == 1, "Expected 1 GraphQL call for scenes_update"
        assert "scenesUpdate" in calls[0]["query"]
        assert calls[0]["exception"] is None, "GraphQL call should not raise exception"

        # Verify GraphQL response structure
        assert calls[0]["result"] is not None
        assert "scenesUpdate" in calls[0]["result"]

        # Verify input array structure in variables
        input_data = calls[0]["variables"]["input"]
        assert isinstance(input_data, list), "Input should be a list"
        assert len(input_data) == 2, "Should have 2 scene inputs"

        # Verify each input has required fields
        for scene_input in input_data:
            assert "id" in scene_input, "Each scene input must have an ID"
            assert "title" in scene_input, "Title should be included in update"
            assert "organized" in scene_input, "Organized should be included in update"

        # Verify both scenes were updated correctly
        assert len(updated_scenes) == 2
        assert all(isinstance(scene, Scene) for scene in updated_scenes)

        # Verify scene 1 updates
        assert updated_scenes[0].id == created1.id
        assert updated_scenes[0].title == "Updated First Scene"
        assert updated_scenes[0].organized is True

        # Verify scene 2 updates
        assert updated_scenes[1].id == created2.id
        assert updated_scenes[1].title == "Updated Second Scene"
        assert updated_scenes[1].organized is True

        # Print verbose output
        print("\n=== Bulk Scene Update ===")
        print(f"Updated {len(updated_scenes)} scenes via scenesUpdate mutation")
        print(f"Scene 1: ID={updated_scenes[0].id}, Title='{updated_scenes[0].title}'")
        print(f"Scene 2: ID={updated_scenes[1].id}, Title='{updated_scenes[1].title}'")
        print("========================\n")


@pytest.mark.asyncio
async def test_scenes_update_error(
    stash_client: StashClient, stash_cleanup_tracker
) -> None:
    """Test error handling when updating multiple scenes - TRUE INTEGRATION TEST.

    Tests various error conditions with REAL GraphQL calls:
    1. Empty list (should return empty list)
    2. Scene with invalid ID (Stash should reject)
    3. Scene missing ID (should fail during to_input())
    """
    # No scenes created in this test, but stash_cleanup_tracker is required for enforcement
    async with stash_cleanup_tracker(stash_client):
        # Test 1: Empty list should return empty list without making GraphQL call
        with capture_graphql_calls(stash_client) as calls:
            result = await stash_client.scenes_update([])

        assert result == [], "Empty list should return empty list"
        assert len(calls) == 0, "Empty list should not make GraphQL call"

        print("\n=== Test 1: Empty List ===")
        print("Empty list correctly returned without GraphQL call")
        print("==========================\n")

        # Test 2: Scene with invalid ID should raise exception from Stash
        invalid_scene = SceneFactory.build(
            id="99999999",  # ID that doesn't exist in Stash
            title="Invalid Scene",
            urls=["https://example.com/invalid"],
            organized=False,
        )

        with capture_graphql_calls(stash_client) as calls, pytest.raises(Exception):
            await stash_client.scenes_update([invalid_scene])

        # Verify GraphQL call was attempted
        assert len(calls) == 1, "Should have attempted GraphQL call"
        assert "scenesUpdate" in calls[0]["query"]

        # Verify exception was raised (could be from Stash or from processing response)
        # The exception could be at GraphQL level or during result processing
        print("\n=== Test 2: Invalid Scene ID ===")
        print(f"GraphQL call attempted for invalid scene ID: {invalid_scene.id}")
        if calls[0]["exception"]:
            print(f"GraphQL exception: {type(calls[0]['exception']).__name__}")
        else:
            print("GraphQL succeeded but processing failed (scene not found)")
        print("================================\n")

        # Test 3: Scene with None ID should fail during to_input()
        # Scene.to_input() should raise if ID is None for updates
        scene_no_id = SceneFactory.build(
            id=None,  # None ID should fail
            title="Scene Without ID",
            urls=["https://example.com/noid"],
            organized=False,
        )

        # This should fail during to_input() because update requires an ID
        with capture_graphql_calls(stash_client) as calls, pytest.raises(Exception):
            await stash_client.scenes_update([scene_no_id])

        # Should not make GraphQL call because to_input() should fail first
        # (or it might make the call and Stash rejects it - either is valid)
        print("\n=== Test 3: Scene Missing ID ===")
        print("Attempted to update scene without ID")
        print(f"GraphQL calls made: {len(calls)}")
        if calls:
            if calls[0]["exception"]:
                print(
                    f"Exception during GraphQL: {type(calls[0]['exception']).__name__}"
                )
            else:
                print("GraphQL call made but failed during processing")
        else:
            print("Failed before GraphQL call (during to_input())")
        print("================================\n")


@pytest.mark.asyncio
async def test_bulk_scene_operations(
    stash_client: StashClient, stash_cleanup_tracker, enable_scene_creation
) -> None:
    """Test bulk scene operations - TRUE INTEGRATION TEST.

    Creates real scenes, performs bulk update (same changes to all scenes),
    and verifies GraphQL mutation was constructed correctly.
    """
    # Create three real scenes in Stash
    scene1 = SceneFactory.build(
        id="new",
        title="Bulk Test Scene 1",
        urls=["https://example.com/bulk1"],
        organized=False,
        details="Original details 1",
    )
    scene2 = SceneFactory.build(
        id="new",
        title="Bulk Test Scene 2",
        urls=["https://example.com/bulk2"],
        organized=False,
        details="Original details 2",
    )
    scene3 = SceneFactory.build(
        id="new",
        title="Bulk Test Scene 3",
        urls=["https://example.com/bulk3"],
        organized=False,
        details="Original details 3",
    )

    created1 = await stash_client.create_scene(scene1)
    created2 = await stash_client.create_scene(scene2)
    created3 = await stash_client.create_scene(scene3)

    # Track all for cleanup
    async with stash_cleanup_tracker(stash_client) as cleanup:
        cleanup["scenes"].extend([created1.id, created2.id, created3.id])
        # Bulk update only scenes 1 and 2 (same changes to both)
        bulk_input = {
            "ids": [created1.id, created2.id],
            "organized": True,  # Apply this change to all specified scenes
        }

        # Make REAL bulk update call with GraphQL capture
        with capture_graphql_calls(stash_client) as calls:
            updated_scenes = await stash_client.bulk_scene_update(bulk_input)

        # Verify GraphQL mutation was constructed correctly
        assert len(calls) == 1, "Expected 1 GraphQL call for bulk_scene_update"
        assert "bulkSceneUpdate" in calls[0]["query"]
        assert calls[0]["exception"] is None, "GraphQL call should not raise exception"

        # Verify GraphQL response structure
        assert calls[0]["result"] is not None
        assert "bulkSceneUpdate" in calls[0]["result"]

        # Verify input structure
        input_data = calls[0]["variables"]["input"]
        assert "ids" in input_data
        assert len(input_data["ids"]) == 2
        assert created1.id in input_data["ids"]
        assert created2.id in input_data["ids"]
        assert input_data["organized"] is True

        # Verify both scenes were updated
        assert len(updated_scenes) == 2
        assert all(isinstance(scene, Scene) for scene in updated_scenes)
        assert all(scene.organized is True for scene in updated_scenes)

        # Print verbose output
        print("\n=== Bulk Scene Update ===")
        print(f"Updated {len(updated_scenes)} scenes with bulk operation")
        print(
            f"Scene 1: ID={updated_scenes[0].id}, Organized={updated_scenes[0].organized}"
        )
        print(
            f"Scene 2: ID={updated_scenes[1].id}, Organized={updated_scenes[1].organized}"
        )
        print("=========================\n")


@pytest.mark.asyncio
async def test_scene_cache_operations(
    stash_client: StashClient, stash_cleanup_tracker, enable_scene_creation
) -> None:
    """Test scene cache operations - TRUE INTEGRATION TEST.

    Tests LRU cache behavior with REAL GraphQL calls:
    1. Find scene (makes GraphQL call)
    2. Find same scene again (cache hit - no GraphQL call)
    3. Update scene (clears cache)
    4. Find scene again (makes GraphQL call - cache was cleared)
    """
    # Create a real scene in Stash
    scene = SceneFactory.build(
        id="new",
        title="Cache Test Scene",
        urls=["https://example.com/cache"],
        organized=False,
        details="Original details",
    )

    created = await stash_client.create_scene(scene)

    async with stash_cleanup_tracker(stash_client) as cleanup:
        cleanup["scenes"].append(created.id)

        # Test 1: Find scene (should make GraphQL call)
        with capture_graphql_calls(stash_client) as calls:
            result1 = await stash_client.find_scene(created.id)

        assert result1 is not None
        assert len(calls) == 1, "First find should make GraphQL call"
        assert "findScene" in calls[0]["query"]

        print("\n=== Cache Test 1: Initial Find ===")
        print(f"Found scene ID={result1.id}, made 1 GraphQL call (cache miss)")
        print("==================================\n")

        # Test 2: Find same scene again (should hit cache - no GraphQL call)
        with capture_graphql_calls(stash_client) as calls:
            result2 = await stash_client.find_scene(created.id)

        assert result2 is not None
        assert len(calls) == 0, "Second find should hit cache (no GraphQL call)"

        print("=== Cache Test 2: Cache Hit ===")
        print(f"Found scene ID={result2.id}, made 0 GraphQL calls (cache hit)")
        print("================================\n")

        # Test 3: Update scene (should clear cache)
        created.details = "Updated details"

        with capture_graphql_calls(stash_client) as calls:
            updated = await stash_client.update_scene(created)

        assert updated is not None
        assert updated.details == "Updated details"
        assert len(calls) == 1, "Update should make GraphQL call"
        assert "sceneUpdate" in calls[0]["query"]

        print("=== Cache Test 3: Update (Clears Cache) ===")
        print(f"Updated scene ID={updated.id}, made 1 GraphQL call")
        print("Cache was cleared by update operation")
        print("============================================\n")

        # Test 4: Find scene again (cache was cleared, should make GraphQL call)
        with capture_graphql_calls(stash_client) as calls:
            result3 = await stash_client.find_scene(created.id)

        assert result3 is not None
        assert result3.details == "Updated details", "Should get updated data"
        assert len(calls) == 1, (
            "Find after update should make GraphQL call (cache was cleared)"
        )
        assert "findScene" in calls[0]["query"]

        print("=== Cache Test 4: Find After Update ===")
        print(f"Found scene ID={result3.id}, made 1 GraphQL call (cache was cleared)")
        print(f"Details: '{result3.details}'")
        print("========================================\n")


@pytest.mark.asyncio
async def test_scene_metadata_handling(
    stash_client: StashClient, stash_cleanup_tracker, enable_scene_creation
) -> None:
    """Test handling of complex scene metadata - TRUE INTEGRATION TEST.

    Creates a scene with complex metadata (date, details, code, URLs),
    then performs partial updates to verify only changed fields are updated.
    """
    # Create a scene with complex metadata
    complex_scene = SceneFactory.build(
        id="new",
        title="Complex Metadata Scene",
        details="Original detailed description of this scene",
        code="SCENE-001",
        date="2025-04-13",
        urls=[
            "https://example.com/scene1",
            "https://example.com/scene1-alt",
        ],
        organized=True,
    )

    created = await stash_client.create_scene(complex_scene)

    async with stash_cleanup_tracker(stash_client) as cleanup:
        cleanup["scenes"].append(created.id)

        # Verify all metadata was created correctly
        assert created.title == "Complex Metadata Scene"
        assert created.details == "Original detailed description of this scene"
        assert created.code == "SCENE-001"
        assert created.date == "2025-04-13"
        assert len(created.urls) == 2
        assert created.organized is True

        print("\n=== Complex Metadata Creation ===")
        print("Created scene with:")
        print(f"  Title: '{created.title}'")
        print(f"  Code: '{created.code}'")
        print(f"  Date: '{created.date}'")
        print(f"  URLs: {len(created.urls)} URLs")
        print(f"  Organized: {created.organized}")
        print("=================================\n")

        # Test partial update - only change organized flag and details
        created.organized = False
        created.details = "Updated description"

        with capture_graphql_calls(stash_client) as calls:
            updated = await stash_client.update_scene(created)

        # Verify GraphQL mutation
        assert len(calls) == 1, "Expected 1 GraphQL call for update"
        assert "sceneUpdate" in calls[0]["query"]
        assert calls[0]["exception"] is None

        # Verify only the changed fields were updated
        assert updated.title == "Complex Metadata Scene"  # Unchanged
        assert updated.code == "SCENE-001"  # Unchanged
        assert updated.date == "2025-04-13"  # Unchanged
        assert updated.details == "Updated description"  # Changed
        assert updated.organized is False  # Changed

        print("=== Partial Metadata Update ===")
        print("Updated fields:")
        print(f"  Details: '{created.details}' -> '{updated.details}'")
        print(f"  Organized: {created.organized} -> {updated.organized}")
        print("Unchanged fields:")
        print(f"  Title: '{updated.title}'")
        print(f"  Code: '{updated.code}'")
        print(f"  Date: '{updated.date}'")
        print("================================\n")


@pytest.mark.asyncio
async def test_scene_edge_cases(
    stash_client: StashClient, stash_cleanup_tracker, enable_scene_creation
) -> None:
    """Test edge cases in scene operations - TRUE INTEGRATION TEST.

    Tests various edge cases with REAL GraphQL calls:
    1. Scene with minimal required fields only
    2. Non-existent scene ID (should return None)
    3. Empty string ID (should raise ValueError)
    4. None ID (should raise ValueError)
    """
    # Test 1: Create scene with minimal required fields
    minimal_scene = SceneFactory.build(
        id="new",
        title="Minimal Scene",  # Only title is truly required
        urls=[],  # Empty URLs
        organized=False,
    )

    created = await stash_client.create_scene(minimal_scene)

    async with stash_cleanup_tracker(stash_client) as cleanup:
        cleanup["scenes"].append(created.id)

        # Verify scene was created with minimal data
        assert created.title == "Minimal Scene"
        assert created.urls == []
        assert created.organized is False
        assert created.details is None or created.details == ""
        assert created.code is None or created.code == ""

        print("\n=== Edge Case 1: Minimal Scene ===")
        print(f"Created minimal scene with ID={created.id}")
        print(f"  Title: '{created.title}'")
        print(f"  URLs: {created.urls}")
        print(f"  Organized: {created.organized}")
        print("==================================\n")

    # Test 2: Try to find non-existent scene (should return None)
    with capture_graphql_calls(stash_client) as calls:
        result = await stash_client.find_scene("99999999")

    assert result is None, "Non-existent scene should return None"
    assert len(calls) == 1, "Should make GraphQL call for non-existent scene"
    assert "findScene" in calls[0]["query"]

    print("=== Edge Case 2: Non-Existent Scene ===")
    print("Attempted to find scene ID=99999999")
    print("Result: None (as expected)")
    print("========================================\n")

    # Test 3: Try to find with empty string ID (should raise ValueError)
    with pytest.raises(ValueError, match="Scene ID cannot be empty"):
        await stash_client.find_scene("")

    print("=== Edge Case 3: Empty String ID ===")
    print("Attempted to find scene with empty string ID")
    print("Result: ValueError raised (as expected)")
    print("=====================================\n")

    # Test 4: Try to find with None ID (should raise ValueError)
    with pytest.raises(ValueError, match="Scene ID cannot be empty"):
        await stash_client.find_scene(None)

    print("=== Edge Case 4: None ID ===")
    print("Attempted to find scene with None ID")
    print("Result: ValueError raised (as expected)")
    print("=============================\n")
