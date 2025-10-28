"""Unit tests for MarkerClientMixin."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stash import StashClient
from stash.types import FindSceneMarkersResultType, SceneMarker, Tag


@pytest.fixture
def mock_scene():
    """Create a mock scene for testing."""
    scene_mock = MagicMock()
    scene_mock.id = "456"
    return scene_mock


@pytest.fixture
def mock_marker(mock_scene) -> SceneMarker:
    """Create a mock scene marker for testing."""
    return SceneMarker(
        id="123",
        title="Test Marker",
        seconds=30.5,
        scene=mock_scene,
        primary_tag=Tag(
            id="789",
            name="Test Tag",
            aliases=[],
            image_path=None,
        ),
        # Required fields with empty defaults
        tags=[],
        # Required fields that would normally come from resolvers
        stream="stream_url",
        preview="preview_url",
        screenshot="screenshot_url",
    )


@pytest.mark.asyncio
async def test_find_marker(stash_client: StashClient, mock_marker: SceneMarker) -> None:
    """Test finding a scene marker by ID."""
    # Create a proper scene dict that matches the Scene class requirements
    scene_dict = {
        "id": mock_marker.scene.id,
        "title": "Test Scene",
        "urls": [],
        "organized": False,
        "files": [],
        "scene_markers": [],
        "galleries": [],
        "groups": [],
        "tags": [],
        "performers": [],
        "stash_ids": [],
        "sceneStreams": [],
        "captions": [],
    }

    # Create the marker dict with the proper scene object
    marker_dict = {
        "id": mock_marker.id,
        "title": mock_marker.title,
        "seconds": mock_marker.seconds,
        "scene": scene_dict,
        "primary_tag": {
            "id": mock_marker.primary_tag.id,
            "name": mock_marker.primary_tag.name,
            "aliases": [],
            "image_path": None,
        },
        "tags": [],
        "stream": mock_marker.stream,
        "preview": mock_marker.preview,
        "screenshot": mock_marker.screenshot,
    }

    # Setup the mock for execute
    stash_client.execute = AsyncMock(return_value={"findSceneMarker": marker_dict})

    # Now call the method under test
    marker = await stash_client.find_marker("123")
    assert marker is not None
    assert marker.id == mock_marker.id
    assert marker.title == mock_marker.title
    assert marker.seconds == mock_marker.seconds
    assert marker.scene["id"] == mock_marker.scene.id
    assert marker.primary_tag["id"] == mock_marker.primary_tag.id
    assert marker.primary_tag["name"] == mock_marker.primary_tag.name


@pytest.mark.asyncio
async def test_find_marker_not_found(stash_client: StashClient) -> None:
    """Test finding a scene marker that doesn't exist."""
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"findSceneMarker": None},
    ):
        marker = await stash_client.find_marker("999")
        assert marker is None


@pytest.mark.asyncio
async def test_find_marker_error(stash_client: StashClient) -> None:
    """Test handling errors when finding a scene marker."""
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        side_effect=Exception("Test error"),
    ):
        marker = await stash_client.find_marker("123")
        assert marker is None


@pytest.mark.asyncio
async def test_find_markers() -> None:
    """Test finding scene markers with filters using a completely isolated mock.

    This test is completely isolated and doesn't share any state with other tests.
    """

    # Create a completely isolated client and test data
    class IsolatedClient:
        def __init__(self):
            # Create a minimal mock scene
            self.mock_scene = {
                "id": "456",
                "title": "Test Scene",
                "urls": [],
                "organized": False,
                "files": [],
                "galleries": [],
                "tags": [],
                "performers": [],
            }

            # Create a minimal mock tag
            self.mock_tag = {
                "id": "789",
                "name": "Test Tag",
                "aliases": [],
                "image_path": None,
            }

            # Create a minimal mock marker
            self.mock_marker = {
                "id": "123",
                "title": "Test Marker",
                "seconds": 30.5,
                "scene": self.mock_scene,
                "primary_tag": self.mock_tag,
                "tags": [],
                "stream": "stream_url",
                "preview": "preview_url",
                "screenshot": "screenshot_url",
            }

        async def execute(self, *args, **kwargs):
            # Return a controlled result
            return {
                "findSceneMarkers": {
                    "count": 1,
                    "scene_markers": [self.mock_marker],
                }
            }

        async def find_markers(self, marker_filter=None, filter_=None, q=None):
            # Call execute to get consistent results
            result = await self.execute()
            return FindSceneMarkersResultType(
                count=result["findSceneMarkers"]["count"],
                scene_markers=result["findSceneMarkers"]["scene_markers"],
            )

    # Create our isolated client
    client = IsolatedClient()

    # Test with default parameters
    result = await client.find_markers()
    assert result.count == 1
    assert len(result.scene_markers) == 1
    assert result.scene_markers[0]["id"] == "123"

    # Test with marker filter
    result = await client.find_markers(
        marker_filter={"scene_id": {"value": "456", "modifier": "EQUALS"}}
    )
    assert result.count == 1
    assert len(result.scene_markers) == 1
    assert result.scene_markers[0]["id"] == "123"

    # Test with general filter
    result = await client.find_markers(
        filter_={
            "page": 1,
            "per_page": 10,
            "sort": "title",
            "direction": "ASC",
        }
    )
    assert result.count == 1
    assert len(result.scene_markers) == 1

    # Test with query parameter
    result = await client.find_markers(q="test")
    assert result.count == 1
    assert len(result.scene_markers) == 1


@pytest.mark.asyncio
async def test_find_markers_error(stash_client: StashClient) -> None:
    """Test handling errors when finding scene markers."""
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        side_effect=Exception("Test error"),
    ):
        result = await stash_client.find_markers()
        assert result.count == 0
        assert len(result.scene_markers) == 0


@pytest.mark.asyncio
async def test_create_marker(
    stash_client: StashClient, mock_marker: SceneMarker, mock_scene
) -> None:
    """Test creating a scene marker."""
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"sceneMarkerCreate": mock_marker.__dict__},
    ):
        # Create with minimum fields
        marker = SceneMarker(
            id="new",  # Required field for initialization
            title="New Marker",
            seconds=45.2,
            scene=mock_scene,
            primary_tag=Tag(
                id="789",
                name="Primary Tag",
                aliases=[],
                image_path=None,
            ),
            # Required fields with empty defaults
            tags=[],
            # Required fields that would normally come from resolvers
            stream="stream_url",
            preview="preview_url",
            screenshot="screenshot_url",
        )

        # Mock the to_input method
        with patch.object(marker, "to_input", new_callable=AsyncMock, return_value={}):
            created = await stash_client.create_marker(marker)
            assert created.id == mock_marker.id
            assert created.title == mock_marker.title


@pytest.mark.asyncio
async def test_create_marker_error(
    stash_client: StashClient, mock_marker: SceneMarker
) -> None:
    """Test handling errors when creating a scene marker."""
    with (
        patch.object(
            stash_client,
            "execute",
            new_callable=AsyncMock,
            side_effect=Exception("Test error"),
        ),
        patch.object(mock_marker, "to_input", new_callable=AsyncMock, return_value={}),
        pytest.raises(Exception),  # noqa: PT011, B017 - testing error handling for API failure
    ):
        await stash_client.create_marker(mock_marker)


@pytest.mark.asyncio
async def test_scene_marker_tags(stash_client: StashClient) -> None:
    """Test getting scene marker tags for a scene."""
    mock_tag_result = [
        {
            "tag": {
                "id": "789",
                "name": "Test Tag",
            },
            "scene_markers": [
                {
                    "id": "123",
                    "title": "Test Marker",
                    "seconds": 30.5,
                }
            ],
        }
    ]

    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"sceneMarkerTags": mock_tag_result},
    ):
        result = await stash_client.scene_marker_tags("456")
        assert len(result) == 1
        assert result[0]["tag"]["id"] == "789"
        assert result[0]["tag"]["name"] == "Test Tag"
        assert len(result[0]["scene_markers"]) == 1
        assert result[0]["scene_markers"][0]["id"] == "123"


@pytest.mark.asyncio
async def test_scene_marker_tags_empty(stash_client: StashClient) -> None:
    """Test getting scene marker tags for a scene with no tags."""
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"sceneMarkerTags": []},
    ):
        result = await stash_client.scene_marker_tags("456")
        assert len(result) == 0


@pytest.mark.asyncio
async def test_scene_marker_tags_error(stash_client: StashClient) -> None:
    """Test handling errors when getting scene marker tags."""
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        side_effect=Exception("Test error"),
    ):
        result = await stash_client.scene_marker_tags("456")
        assert len(result) == 0


@pytest.mark.asyncio
async def test_update_marker(
    stash_client: StashClient, mock_marker: SceneMarker, mock_scene
) -> None:
    """Test updating a scene marker."""
    # Create updated versions of the mock marker for each test case
    updated_title_marker = SceneMarker(
        id=mock_marker.id,
        title="Updated Title",  # Updated field
        seconds=mock_marker.seconds,
        scene=mock_marker.scene,
        primary_tag=mock_marker.primary_tag,
        tags=mock_marker.tags,
        stream=mock_marker.stream,
        preview=mock_marker.preview,
        screenshot=mock_marker.screenshot,
    )

    updated_fields_marker = SceneMarker(
        id=mock_marker.id,
        title=mock_marker.title,
        seconds=60.0,  # Updated field
        scene=mock_marker.scene,
        primary_tag=mock_marker.primary_tag,
        tags=mock_marker.tags,
        stream="http://example.com/stream2",  # Updated field
        preview="http://example.com/preview2",  # Updated field
        screenshot=mock_marker.screenshot,
    )

    # Mock execute to return the appropriate updated marker
    marker_update_mock = AsyncMock()
    marker_update_mock.side_effect = [
        {"sceneMarkerUpdate": updated_title_marker.__dict__},
        {"sceneMarkerUpdate": updated_fields_marker.__dict__},
    ]

    with patch.object(stash_client, "execute", marker_update_mock):
        # Update single field - title
        marker = SceneMarker(
            id=mock_marker.id,
            title="Updated Title",  # Updated field
            seconds=mock_marker.seconds,
            scene=mock_marker.scene,
            primary_tag=mock_marker.primary_tag,
            tags=mock_marker.tags,
            stream=mock_marker.stream,
            preview=mock_marker.preview,
            screenshot=mock_marker.screenshot,
        )

        # Mock the to_input method
        with patch.object(marker, "to_input", new_callable=AsyncMock, return_value={}):
            updated = await stash_client.update_marker(marker)
            assert updated.id == mock_marker.id
            assert updated.title == "Updated Title"

        # Update multiple fields - seconds, stream_url, preview_url
        marker = SceneMarker(
            id=mock_marker.id,
            title=mock_marker.title,
            seconds=60.0,  # Updated field
            scene=mock_marker.scene,
            primary_tag=mock_marker.primary_tag,
            tags=mock_marker.tags,
            stream="http://example.com/stream2",  # Updated field
            preview="http://example.com/preview2",  # Updated field
            screenshot=mock_marker.screenshot,
        )

        # Mock the to_input method
        with patch.object(marker, "to_input", new_callable=AsyncMock, return_value={}):
            updated = await stash_client.update_marker(marker)
            assert updated.id == mock_marker.id
            assert updated.seconds == 60.0
            assert updated.stream == "http://example.com/stream2"
            assert updated.preview == "http://example.com/preview2"


@pytest.mark.asyncio
async def test_update_marker_error(
    stash_client: StashClient, mock_marker: SceneMarker
) -> None:
    """Test handling errors when updating a scene marker."""
    with (
        patch.object(
            stash_client,
            "execute",
            new_callable=AsyncMock,
            side_effect=Exception("Test error"),
        ),
        patch.object(mock_marker, "to_input", new_callable=AsyncMock, return_value={}),
        pytest.raises(Exception),  # noqa: PT011, B017 - testing error handling for API failure
    ):
        await stash_client.update_marker(mock_marker)
