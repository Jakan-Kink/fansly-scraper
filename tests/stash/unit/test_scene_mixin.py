"""Unit tests for SceneClientMixin."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from stash import StashClient
from stash.types import (
    FindScenesResultType,
    Scene,
    SceneCreateInput,
    SceneUpdateInput,
    VideoFile,
)


@pytest.fixture
def mock_scene() -> Scene:
    """Create a mock scene for testing."""
    return Scene(
        id="123",  # Required field
        title="Test Scene",
        details="Test scene details",
        date="2024-01-01",
        urls=["https://example.com/scene"],
        organized=True,
        files=[
            VideoFile(
                id="456",
                path="/path/to/video.mp4",
                basename="video.mp4",
                size=1024,
                format="mp4",
                width=1920,
                height=1080,
                duration=60.0,
                video_codec="h264",
                audio_codec="aac",
                frame_rate=30.0,
                bit_rate=5000000,
                parent_folder_id="789",
                mod_time=datetime.now(),
                fingerprints=[],
            )
        ],
        # Required fields with empty defaults
        scene_markers=[],
        galleries=[],
        groups=[],
        tags=[],
        performers=[],
        stash_ids=[],
        sceneStreams=[],
        captions=[],
    )


@pytest.mark.asyncio
async def test_find_scene(stash_client: StashClient, mock_scene: Scene) -> None:
    """Test finding a scene by ID."""
    with patch.object(
        stash_client,
        "find_scene",
        new_callable=AsyncMock,
        return_value=mock_scene,
    ):
        scene = await stash_client.find_scene("123")
        assert scene is not None
        assert scene.id == mock_scene.id
        assert scene.title == mock_scene.title
        assert scene.details == mock_scene.details
        assert scene.date == mock_scene.date
        assert scene.urls == mock_scene.urls
        assert scene.organized == mock_scene.organized
        assert len(scene.files) == 1
        assert scene.files[0].path == mock_scene.files[0].path


@pytest.mark.asyncio
async def test_find_scenes(stash_client: StashClient, mock_scene: Scene) -> None:
    """Test finding scenes with filters."""
    mock_result = FindScenesResultType(
        count=1,
        duration=60.0,
        filesize=1024,
        scenes=[mock_scene],
    )

    with patch.object(
        stash_client,
        "find_scenes",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        # Test with scene filter
        result = await stash_client.find_scenes(
            scene_filter={
                "path": {"modifier": "INCLUDES", "value": "test"},
                "organized": True,
            }
        )
        assert result.count == 1
        assert result.duration == 60.0
        assert result.filesize == 1024
        assert len(result.scenes) == 1
        assert result.scenes[0].id == mock_scene.id

        # Test with general filter
        result = await stash_client.find_scenes(
            filter_={
                "page": 1,
                "per_page": 10,
                "sort": "title",
                "direction": "ASC",
            }
        )
        assert result.count == 1
        assert len(result.scenes) == 1


@pytest.mark.asyncio
async def test_create_scene(stash_client: StashClient, mock_scene: Scene) -> None:
    """Test creating a scene."""
    with patch.object(
        stash_client,
        "create_scene",
        new_callable=AsyncMock,
        return_value=mock_scene,
    ):
        # Create with minimum fields
        scene = Scene(
            id="new",  # Required field for initialization
            title="New Scene",
            urls=["https://example.com/new"],
            organized=False,
            # Required fields with empty defaults
            files=[],
            scene_markers=[],
            galleries=[],
            groups=[],
            tags=[],
            performers=[],
            stash_ids=[],
            sceneStreams=[],
            captions=[],
        )
        created = await stash_client.create_scene(scene)
        assert created.id == mock_scene.id
        assert created.title == mock_scene.title

        # Create with all fields
        scene = mock_scene
        scene.id = "new"  # Force create
        created = await stash_client.create_scene(scene)
        assert created.id == mock_scene.id
        assert created.title == mock_scene.title
        assert created.details == mock_scene.details
        assert created.date == mock_scene.date
        assert created.urls == mock_scene.urls
        assert created.organized == mock_scene.organized


@pytest.mark.asyncio
async def test_update_scene(stash_client: StashClient, mock_scene: Scene) -> None:
    """Test updating a scene."""
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"sceneUpdate": mock_scene.__dict__},
    ):
        # Update single field
        scene = mock_scene
        scene.title = "Updated Title"
        updated = await stash_client.update_scene(scene)
        assert updated.id == mock_scene.id
        assert updated.title == mock_scene.title

        # Update multiple fields
        scene.details = "Updated details"
        scene.organized = False
        updated = await stash_client.update_scene(scene)
        assert updated.id == mock_scene.id
        assert updated.details == mock_scene.details
        assert updated.organized == mock_scene.organized


@pytest.mark.asyncio
async def test_scene_generate_screenshot(
    stash_client: StashClient, mock_scene: Scene
) -> None:
    """Test generating a scene screenshot."""
    mock_path = "/path/to/screenshot.jpg"
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={"sceneGenerateScreenshot": mock_path},
    ):
        # Generate at specific time
        path = await stash_client.scene_generate_screenshot(mock_scene.id, at=30.0)
        assert path == mock_path

        # Generate default screenshot
        path = await stash_client.scene_generate_screenshot(mock_scene.id)
        assert path == mock_path


@pytest.mark.asyncio
async def test_find_duplicate_scenes(
    stash_client: StashClient, mock_scene: Scene
) -> None:
    """Test finding duplicate scenes."""
    mock_result = [[mock_scene, mock_scene]]
    with patch.object(
        stash_client,
        "execute",
        new_callable=AsyncMock,
        return_value={
            "findDuplicateScenes": [
                [s.__dict__ for s in group] for group in mock_result
            ]
        },
    ):
        # Find with default settings
        duplicates = await stash_client.find_duplicate_scenes()
        assert len(duplicates) == 1
        assert len(duplicates[0]) == 2
        assert duplicates[0][0].id == mock_scene.id
        assert duplicates[0][1].id == mock_scene.id

        # Find with custom settings
        duplicates = await stash_client.find_duplicate_scenes(
            distance=100,
            duration_diff=1.0,
        )
        assert len(duplicates) == 1
        assert len(duplicates[0]) == 2


@pytest.mark.asyncio
async def test_parse_scene_filenames(
    stash_client: StashClient, mock_scene: Scene
) -> None:
    """Test parsing scene filenames."""
    # Create a mock result that matches the expected return type (dictionary)
    mock_result = {
        "count": 1,
        "results": [
            {
                "scene": mock_scene.__dict__,
                "title": "Parsed Title",
                "code": "ABC123",
                "details": "Parsed details",
                "director": "Director Name",
                "url": "https://example.com/parsed",
                "date": "2024-02-01",
                "rating100": 85,
            }
        ],
    }

    with patch.object(
        stash_client,
        "parse_scene_filenames",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        # Parse with default settings
        result = await stash_client.parse_scene_filenames()
        assert result["count"] == 1
        assert len(result["results"]) == 1
        assert result["results"][0]["scene"]["id"] == mock_scene.id
        assert result["results"][0]["title"] == "Parsed Title"
        assert result["results"][0]["code"] == "ABC123"

        # Parse with custom settings
        result = await stash_client.parse_scene_filenames(
            filter_={"q": "test"},
            config={
                "whitespace": True,
                "field": "title",
            },
        )
        assert result["count"] == 1
        assert len(result["results"]) == 1
