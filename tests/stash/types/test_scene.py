"""Tests for stash.types.scene module.

Tests scene types including Scene, SceneCreateInput, SceneUpdateInput and related types.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import PropertyMock, patch

import pytest
from strawberry import ID

from stash.types.files import StashID, VideoFile
from stash.types.scene import (
    BulkSceneUpdateInput,
    FindScenesResultType,
    ParseSceneFilenameResult,
    ParseSceneFilenamesResult,
    Scene,
    SceneDestroyInput,
    SceneFileType,
    SceneGroup,
    SceneGroupInput,
    SceneMarker,
    SceneMovieID,
    SceneParserResult,
    SceneParserResultType,
    ScenePathsType,
    ScenesDestroyInput,
    SceneStreamEndpoint,
    SceneUpdateInput,
    VideoCaption,
)


@pytest.mark.unit
def test_scene_group() -> None:
    """Test SceneGroup type."""
    assert hasattr(SceneGroup, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in SceneGroup.__strawberry_definition__.fields
    }
    expected_fields = ["group", "scene_index"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in SceneGroup"


@pytest.mark.unit
def test_video_caption() -> None:
    """Test VideoCaption type."""
    assert hasattr(VideoCaption, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in VideoCaption.__strawberry_definition__.fields
    }
    expected_fields = ["language_code", "caption_type"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in VideoCaption"


@pytest.mark.unit
def test_scene_file_type() -> None:
    """Test SceneFileType type."""
    assert hasattr(SceneFileType, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in SceneFileType.__strawberry_definition__.fields
    }
    expected_fields = [
        "size",
        "duration",
        "video_codec",
        "audio_codec",
        "width",
        "height",
        "framerate",
        "bitrate",
    ]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in SceneFileType"


@pytest.mark.unit
def test_scene_paths_type() -> None:
    """Test ScenePathsType type."""
    assert hasattr(ScenePathsType, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in ScenePathsType.__strawberry_definition__.fields
    }
    expected_fields = [
        "screenshot",
        "preview",
        "stream",
        "webp",
        "vtt",
        "sprite",
        "funscript",
        "interactive_heatmap",
        "caption",
    ]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in ScenePathsType"


@pytest.mark.unit
def test_scene_stream_endpoint() -> None:
    """Test SceneStreamEndpoint type."""
    assert hasattr(SceneStreamEndpoint, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in SceneStreamEndpoint.__strawberry_definition__.fields
    }
    expected_fields = ["url", "mime_type", "label"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in SceneStreamEndpoint"


@pytest.mark.unit
def test_scene_marker() -> None:
    """Test SceneMarker type."""
    assert hasattr(SceneMarker, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in SceneMarker.__strawberry_definition__.fields
    }
    expected_fields = [
        "id",
        "title",
        "seconds",
        "stream",
        "preview",
        "screenshot",
        "scene",
        "primary_tag",
        "tags",
    ]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in SceneMarker"


@pytest.mark.unit
def test_scene_group_input() -> None:
    """Test SceneGroupInput input type."""
    assert hasattr(SceneGroupInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in SceneGroupInput.__strawberry_definition__.fields
    }
    expected_fields = ["group_id", "scene_index"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in SceneGroupInput"


@pytest.mark.unit
def test_scene_update_input() -> None:
    """Test SceneUpdateInput input type."""
    assert hasattr(SceneUpdateInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in SceneUpdateInput.__strawberry_definition__.fields
    }
    expected_fields = [
        "id",
        "title",
        "code",
        "details",
        "director",
        "urls",
        "date",
        "rating100",
        "organized",
        "studio_id",
        "performer_ids",
        "groups",
        "tag_ids",
        "cover_image",
        "stash_ids",
    ]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in SceneUpdateInput"


@pytest.mark.unit
def test_scene() -> None:
    """Test Scene type."""
    assert hasattr(Scene, "__strawberry_definition__")

    # Test that it extends StashObject
    fields = {field.name: field for field in Scene.__strawberry_definition__.fields}
    assert "id" in fields  # From StashObject

    # Test scene-specific fields
    expected_fields = [
        "title",
        "code",
        "details",
        "director",
        "urls",
        "date",
        "rating100",
        "organized",
        "o_counter",
        "files",
        "paths",
        "scene_markers",
        "galleries",
        "groups",
        "tags",
        "performers",
        "stash_ids",
        "sceneStreams",
        "captions",
    ]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in Scene"


@pytest.mark.unit
def test_scene_class_variables() -> None:
    """Test Scene class variables."""
    assert hasattr(Scene, "__type_name__")
    assert Scene.__type_name__ == "Scene"

    assert hasattr(Scene, "__update_input_type__")
    assert Scene.__update_input_type__ == SceneUpdateInput

    # Scene intentionally does not have __create_input_type__ since scenes are
    # created by the server during scanning, not by client requests
    assert (
        not hasattr(Scene, "__create_input_type__")
        or Scene.__create_input_type__ is None
    )

    assert hasattr(Scene, "__tracked_fields__")
    # Test that key fields are tracked
    tracked_fields = Scene.__tracked_fields__
    key_tracked = ["title", "details", "studio", "performers", "tags"]
    for field in key_tracked:
        assert field in tracked_fields, f"Field {field} not in tracked fields"


@pytest.mark.unit
def test_scene_relationships() -> None:
    """Test Scene relationships."""
    assert hasattr(Scene, "__relationships__")


@pytest.mark.unit
def test_from_dict_missing_id_raises() -> None:
    """Test that Scene.from_dict raises when ID is missing."""
    with pytest.raises(ValueError) as excinfo:
        Scene.from_dict({})
    assert "ID field" in str(excinfo.value)


@pytest.mark.unit
def test_from_dict_with_minimal_data() -> None:
    """Test that Scene.from_dict works with minimal data."""
    data = {"id": "scene1"}
    scene = Scene.from_dict(data)
    assert scene.id == "scene1"
    # Default lists should be empty
    assert isinstance(scene.files, list)
    assert scene.files == []
    assert isinstance(scene.stash_ids, list)
    assert scene.stash_ids == []


@pytest.mark.unit
def test_from_dict_filters_unknown_fields() -> None:
    """Test that Scene.from_dict filters unknown fields."""
    data = {"id": "scene2", "unknown": "value"}
    scene = Scene.from_dict(data)
    assert not hasattr(scene, "unknown"), "Unknown fields should be filtered out"


@pytest.mark.unit
def test_from_dict_with_stash_ids() -> None:
    """Test that Scene.from_dict properly handles stash_ids."""
    stash_entries = [{"endpoint": "local", "stash_id": "abc123"}]
    data = {"id": "scene3", "stash_ids": stash_entries}
    scene = Scene.from_dict(data)
    assert len(scene.stash_ids) == 1
    sid = scene.stash_ids[0]
    assert isinstance(sid, StashID)
    assert sid.endpoint == "local"
    assert sid.stash_id == "abc123"


@pytest.mark.unit
def test_from_dict_with_files() -> None:
    """Test that Scene.from_dict properly handles files."""
    # Prepare minimal VideoFile data
    file_dict: dict[str, Any] = {
        "id": "file1",
        "path": "/tmp/video.mp4",  # noqa: S108
        "basename": "video.mp4",
        "parent_folder_id": "parent1",
        "zip_file_id": None,
        "mod_time": datetime(2020, 1, 1, tzinfo=UTC),
        "size": 1024,
        "fingerprints": [],
        "format": "mp4",
        "width": 1920,
        "height": 1080,
        "duration": 60.5,
        "video_codec": "h264",
        "audio_codec": "aac",
        "frame_rate": 29.97,
        "bit_rate": 500000,
    }
    data = {"id": "scene4", "files": [file_dict]}
    scene = Scene.from_dict(data)
    assert len(scene.files) == 1
    vf = scene.files[0]
    assert isinstance(vf, VideoFile)
    # Check a few attributes
    assert vf.id == "file1"
    assert vf.basename == "video.mp4"
    assert vf.path == "/tmp/video.mp4"  # noqa: S108
    assert vf.duration == 60.5

    # Test key relationships exist
    expected_relationships = ["studio", "performers", "tags", "galleries"]

    for field in expected_relationships:
        assert field in Scene.__relationships__, (
            f"Relationship {field} not found in Scene"
        )

    # Test specific relationship mappings
    performers_mapping = Scene.__relationships__["performers"]
    assert performers_mapping[0] == "performer_ids"  # target field
    assert performers_mapping[1] is True  # is_list


@pytest.mark.unit
def test_scene_movie_id() -> None:
    """Test SceneMovieID type."""
    assert hasattr(SceneMovieID, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in SceneMovieID.__strawberry_definition__.fields
    }
    expected_fields = ["movie_id", "scene_index"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in SceneMovieID"


@pytest.mark.unit
def test_parse_scene_filename_result() -> None:
    """Test ParseSceneFilenameResult type."""
    assert hasattr(ParseSceneFilenameResult, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in ParseSceneFilenameResult.__strawberry_definition__.fields
    }
    expected_fields = [
        "title",
        "code",
        "details",
        "director",
        "url",
        "date",
        "rating",
        "studio_id",
        "performer_ids",
        "tag_ids",
    ]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in ParseSceneFilenameResult"


@pytest.mark.unit
def test_parse_scene_filenames_result() -> None:
    """Test ParseSceneFilenamesResult type."""
    assert hasattr(ParseSceneFilenamesResult, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in ParseSceneFilenamesResult.__strawberry_definition__.fields
    }
    expected_fields = ["count", "results"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in ParseSceneFilenamesResult"


@pytest.mark.unit
def test_find_scenes_result_type() -> None:
    """Test FindScenesResultType result type."""
    assert hasattr(FindScenesResultType, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in FindScenesResultType.__strawberry_definition__.fields
    }
    expected_fields = ["count", "scenes"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in FindScenesResultType"


@pytest.mark.unit
def test_scene_parser_result() -> None:
    """Test SceneParserResultType type."""
    assert hasattr(SceneParserResultType, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in SceneParserResultType.__strawberry_definition__.fields
    }
    expected_fields = ["count", "results"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in SceneParserResultType"


@pytest.mark.unit
def test_scene_destroy_input() -> None:
    """Test SceneDestroyInput input type."""
    assert hasattr(SceneDestroyInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in SceneDestroyInput.__strawberry_definition__.fields
    }
    expected_fields = ["id", "delete_file", "delete_generated"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in SceneDestroyInput"


@pytest.mark.unit
def test_scenes_destroy_input() -> None:
    """Test ScenesDestroyInput input type."""
    assert hasattr(ScenesDestroyInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in ScenesDestroyInput.__strawberry_definition__.fields
    }
    expected_fields = ["ids", "delete_file", "delete_generated"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in ScenesDestroyInput"


@pytest.mark.unit
def test_bulk_scene_update_input() -> None:
    """Test BulkSceneUpdateInput input type."""
    assert hasattr(BulkSceneUpdateInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in BulkSceneUpdateInput.__strawberry_definition__.fields
    }
    expected_fields = [
        "ids",
        "director",
        "rating100",
        "organized",
        "studio_id",
        "performer_ids",
        "tag_ids",
    ]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in BulkSceneUpdateInput"


@pytest.mark.unit
def test_scene_instantiation() -> None:
    """Test Scene instantiation."""
    scene = Scene(id=ID("123"), title="Test Scene")

    assert scene.id == ID("123")
    assert scene.title == "Test Scene"
    assert scene.urls == []  # default factory
    assert scene.files == []  # default factory
    assert scene.scene_markers == []  # default factory
    assert scene.galleries == []  # default factory
    assert scene.groups == []  # default factory
    assert scene.tags == []  # default factory
    assert scene.performers == []  # default factory
    assert scene.stash_ids == []  # default factory


@pytest.mark.unit
def test_strawberry_decorations() -> None:
    """Test that all types are properly decorated with strawberry."""
    types_to_test = [
        SceneGroup,
        VideoCaption,
        SceneFileType,
        ScenePathsType,
        SceneStreamEndpoint,
        SceneMarker,
        SceneGroupInput,
        SceneUpdateInput,
        Scene,
        SceneMovieID,
        ParseSceneFilenameResult,
        ParseSceneFilenamesResult,
        FindScenesResultType,
        SceneParserResult,
        SceneDestroyInput,
        ScenesDestroyInput,
        BulkSceneUpdateInput,
    ]

    for type_class in types_to_test:
        assert hasattr(type_class, "__strawberry_definition__"), (
            f"{type_class.__name__} missing strawberry definition"
        )


@pytest.mark.unit
def test_scene_inheritance() -> None:
    """Test that Scene properly inherits from StashObject."""

    # Test that Scene follows the StashObject interface pattern
    assert hasattr(Scene, "__type_name__")
    assert hasattr(Scene, "__tracked_fields__")
    assert hasattr(Scene, "__field_conversions__")
    assert hasattr(Scene, "__relationships__")


@pytest.mark.unit
def test_from_dict_strawberry_definition_fallback() -> None:
    """Test Scene.from_dict when strawberry definition access fails."""
    # Use only valid Scene fields for the fallback test
    data = {"id": "scene1", "title": "Test Scene", "organized": True}

    # Mock the strawberry definition property to raise AttributeError
    with patch.object(
        Scene, "__strawberry_definition__", new_callable=PropertyMock
    ) as mock_def:
        mock_def.side_effect = AttributeError("Definition not available")

        # This should trigger the except AttributeError fallback
        scene = Scene.from_dict(data)

    # Should use fallback behavior - use unfiltered data (but only valid fields)
    assert scene.id == "scene1"
    assert scene.title == "Test Scene"
    assert scene.organized is True
    # Verify that the AttributeError fallback path was actually taken
    mock_def.assert_called()
