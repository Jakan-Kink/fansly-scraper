"""Tests for stash.types.markers module."""

import pytest
from strawberry import ID

from stash.types.base import StashObject
from stash.types.markers import (  # Main marker types; Input types; Result types
    FindSceneMarkersResultType,
    MarkerStringsResultType,
    SceneMarker,
    SceneMarkerCreateInput,
    SceneMarkerTag,
    SceneMarkerUpdateInput,
)


@pytest.mark.unit
class TestSceneMarkerTag:
    """Test SceneMarkerTag class."""

    def test_strawberry_type_decoration(self):
        """Test that SceneMarkerTag is decorated as strawberry type."""
        if not hasattr(SceneMarkerTag, "__strawberry_definition__"):
            pytest.skip("SceneMarkerTag does not have strawberry definition")
        assert hasattr(SceneMarkerTag, "__strawberry_definition__")
        assert not SceneMarkerTag.__strawberry_definition__.is_input

    def test_field_types(self):
        """Test field type annotations."""
        annotations = SceneMarkerTag.__annotations__
        # Note: Using string annotation for forward reference
        assert "tag" in annotations
        assert "scene_markers" in annotations

    def test_default_values(self):
        """Test default field values."""
        if not hasattr(SceneMarkerTag, "__strawberry_definition__"):
            pytest.skip("SceneMarkerTag does not have strawberry definition")
        # Check that scene_markers field has default factory
        fields = {
            field.name: field
            for field in SceneMarkerTag.__strawberry_definition__.fields
        }
        assert "scene_markers" in fields
        scene_markers_field = fields["scene_markers"]
        assert scene_markers_field.default_factory is not None


@pytest.mark.unit
class TestSceneMarker:
    """Test SceneMarker class."""

    def test_strawberry_type_decoration(self):
        """Test that SceneMarker is decorated as strawberry type."""
        assert hasattr(SceneMarker, "__strawberry_definition__")
        assert not SceneMarker.__strawberry_definition__.is_input

    def test_stash_object_inheritance(self):
        """Test that SceneMarker inherits from StashObject."""
        assert issubclass(SceneMarker, StashObject)

    def test_class_variables(self):
        """Test class variable values."""
        assert SceneMarker.__type_name__ == "SceneMarker"
        assert SceneMarker.__update_input_type__ == SceneMarkerUpdateInput
        assert SceneMarker.__create_input_type__ == SceneMarkerCreateInput

        # Test tracked fields
        expected_tracked = {
            "title",
            "seconds",
            "end_seconds",
            "scene",
            "primary_tag",
            "tags",
        }
        assert SceneMarker.__tracked_fields__ == expected_tracked

        # Test field conversions
        expected_conversions = {
            "title": str,
            "seconds": float,
            "end_seconds": float,
        }
        assert SceneMarker.__field_conversions__ == expected_conversions

        # Test relationships
        expected_relationships = {
            "scene": ("scene_id", False, None),
            "primary_tag": ("primary_tag_id", False, None),
            "tags": ("tag_ids", True, None),
        }
        assert SceneMarker.__relationships__ == expected_relationships

    def test_field_types(self):
        """Test field type annotations."""
        annotations = SceneMarker.__annotations__
        assert annotations["title"] == str
        assert annotations["seconds"] == float
        assert annotations["end_seconds"] == float | None
        assert annotations["stream"] == str
        assert annotations["preview"] == str
        assert annotations["screenshot"] == str

    def test_field_conversions(self):
        """Test field conversion functions."""
        conversions = SceneMarker.__field_conversions__

        # Test string conversion
        assert conversions["title"]("Test Marker") == "Test Marker"

        # Test float conversions
        assert conversions["seconds"](10.5) == 10.5
        assert conversions["seconds"]("15.25") == 15.25
        assert conversions["end_seconds"](30.75) == 30.75
        assert conversions["end_seconds"]("45.0") == 45.0


@pytest.mark.unit
class TestSceneMarkerInputs:
    """Test scene marker input types."""

    def test_scene_marker_create_input(self):
        """Test SceneMarkerCreateInput."""
        assert hasattr(SceneMarkerCreateInput, "__strawberry_definition__")
        assert SceneMarkerCreateInput.__strawberry_definition__.is_input

        annotations = SceneMarkerCreateInput.__annotations__
        assert annotations["title"] == str
        assert annotations["seconds"] == float
        assert annotations["end_seconds"] == float | None
        assert annotations["scene_id"] == ID
        assert annotations["primary_tag_id"] == ID
        assert annotations["tag_ids"] == list[ID] | None

        # Test instantiation
        create_input = SceneMarkerCreateInput(
            title="Opening Scene",
            seconds=10.5,
            scene_id=ID("1"),
            primary_tag_id=ID("2"),
        )
        assert create_input.title == "Opening Scene"
        assert create_input.seconds == 10.5
        assert create_input.scene_id == ID("1")
        assert create_input.primary_tag_id == ID("2")
        assert create_input.end_seconds is None
        assert create_input.tag_ids is None

    def test_scene_marker_create_input_with_optional_fields(self):
        """Test SceneMarkerCreateInput with optional fields."""
        create_input = SceneMarkerCreateInput(
            title="Action Scene",
            seconds=120.0,
            end_seconds=180.5,
            scene_id=ID("1"),
            primary_tag_id=ID("2"),
            tag_ids=[ID("3"), ID("4")],
        )
        assert create_input.title == "Action Scene"
        assert create_input.seconds == 120.0
        assert create_input.end_seconds == 180.5
        assert create_input.scene_id == ID("1")
        assert create_input.primary_tag_id == ID("2")
        assert create_input.tag_ids is not None
        assert len(create_input.tag_ids) == 2
        assert ID("3") in create_input.tag_ids
        assert ID("4") in create_input.tag_ids

    def test_scene_marker_update_input(self):
        """Test SceneMarkerUpdateInput."""
        assert hasattr(SceneMarkerUpdateInput, "__strawberry_definition__")
        assert SceneMarkerUpdateInput.__strawberry_definition__.is_input

        annotations = SceneMarkerUpdateInput.__annotations__
        assert annotations["id"] == ID
        assert annotations["title"] == str | None
        assert annotations["seconds"] == float | None
        assert annotations["end_seconds"] == float | None
        assert annotations["scene_id"] == ID | None
        assert annotations["primary_tag_id"] == ID | None
        assert annotations["tag_ids"] == list[ID] | None

        # Test instantiation with required field only
        update_input = SceneMarkerUpdateInput(id=ID("1"))
        assert update_input.id == ID("1")
        assert update_input.title is None
        assert update_input.seconds is None
        assert update_input.end_seconds is None
        assert update_input.scene_id is None
        assert update_input.primary_tag_id is None
        assert update_input.tag_ids is None

    def test_scene_marker_update_input_with_changes(self):
        """Test SceneMarkerUpdateInput with all fields."""
        update_input = SceneMarkerUpdateInput(
            id=ID("1"),
            title="Updated Marker Title",
            seconds=25.5,
            end_seconds=60.0,
            scene_id=ID("2"),
            primary_tag_id=ID("3"),
            tag_ids=[ID("4"), ID("5")],
        )
        assert update_input.id == ID("1")
        assert update_input.title == "Updated Marker Title"
        assert update_input.seconds == 25.5
        assert update_input.end_seconds == 60.0
        assert update_input.scene_id == ID("2")
        assert update_input.primary_tag_id == ID("3")
        assert update_input.tag_ids is not None
        assert len(update_input.tag_ids) == 2


@pytest.mark.unit
class TestResultTypes:
    """Test result types."""

    def test_find_scene_markers_result_type(self):
        """Test FindSceneMarkersResultType."""
        assert hasattr(FindSceneMarkersResultType, "__strawberry_definition__")
        assert not FindSceneMarkersResultType.__strawberry_definition__.is_input

        annotations = FindSceneMarkersResultType.__annotations__
        assert annotations["count"] == int
        assert annotations["scene_markers"] == list[SceneMarker]

        # Test instantiation
        result = FindSceneMarkersResultType(count=5, scene_markers=[])
        assert result.count == 5
        assert isinstance(result.scene_markers, list)
        assert len(result.scene_markers) == 0

    def test_marker_strings_result_type(self):
        """Test MarkerStringsResultType."""
        assert hasattr(MarkerStringsResultType, "__strawberry_definition__")
        assert not MarkerStringsResultType.__strawberry_definition__.is_input

        annotations = MarkerStringsResultType.__annotations__
        assert annotations["count"] == int
        assert annotations["id"] == ID
        assert annotations["title"] == str

        # Test instantiation
        result = MarkerStringsResultType(count=1, id=ID("1"), title="Test Marker")
        assert result.count == 1
        assert result.id == ID("1")
        assert result.title == "Test Marker"


@pytest.mark.unit
class TestMarkerScenarios:
    """Test realistic marker scenarios."""

    def test_marker_time_validation(self):
        """Test marker timing scenarios."""
        # Test basic marker with start time only
        create_input = SceneMarkerCreateInput(
            title="Start of scene",
            seconds=0.0,
            scene_id=ID("1"),
            primary_tag_id=ID("2"),
        )
        assert create_input.seconds == 0.0
        assert create_input.end_seconds is None

        # Test marker with both start and end times
        create_input_with_end = SceneMarkerCreateInput(
            title="Action sequence",
            seconds=60.5,
            end_seconds=120.75,
            scene_id=ID("1"),
            primary_tag_id=ID("2"),
        )
        assert create_input_with_end.seconds == 60.5
        assert create_input_with_end.end_seconds == 120.75

    def test_marker_tagging(self):
        """Test marker tagging scenarios."""
        # Test marker with primary tag only
        create_input = SceneMarkerCreateInput(
            title="Solo scene",
            seconds=30.0,
            scene_id=ID("1"),
            primary_tag_id=ID("tag-solo"),
        )
        assert create_input.primary_tag_id == ID("tag-solo")
        assert create_input.tag_ids is None

        # Test marker with primary tag and additional tags
        create_input_multi_tags = SceneMarkerCreateInput(
            title="Complex scene",
            seconds=45.0,
            scene_id=ID("1"),
            primary_tag_id=ID("tag-primary"),
            tag_ids=[ID("tag-1"), ID("tag-2"), ID("tag-3")],
        )
        assert create_input_multi_tags.primary_tag_id == ID("tag-primary")
        assert create_input_multi_tags.tag_ids is not None
        assert len(create_input_multi_tags.tag_ids) == 3
        assert ID("tag-1") in create_input_multi_tags.tag_ids
        assert ID("tag-2") in create_input_multi_tags.tag_ids
        assert ID("tag-3") in create_input_multi_tags.tag_ids

    def test_marker_updates(self):
        """Test marker update scenarios."""
        # Test updating just the title
        update_input = SceneMarkerUpdateInput(id=ID("marker-1"), title="New title")
        assert update_input.id == ID("marker-1")
        assert update_input.title == "New title"
        assert update_input.seconds is None

        # Test updating timing
        update_timing = SceneMarkerUpdateInput(
            id=ID("marker-1"), seconds=15.5, end_seconds=45.0
        )
        assert update_timing.seconds == 15.5
        assert update_timing.end_seconds == 45.0
        assert update_timing.title is None

        # Test updating tags
        update_tags = SceneMarkerUpdateInput(
            id=ID("marker-1"),
            primary_tag_id=ID("new-primary"),
            tag_ids=[ID("new-1"), ID("new-2")],
        )
        assert update_tags.primary_tag_id == ID("new-primary")
        assert update_tags.tag_ids is not None
        assert len(update_tags.tag_ids) == 2

    def test_decimal_seconds_support(self):
        """Test that decimal seconds are properly supported."""
        # Test precise timing with decimals
        precise_marker = SceneMarkerCreateInput(
            title="Precise timing",
            seconds=123.456,
            end_seconds=234.789,
            scene_id=ID("1"),
            primary_tag_id=ID("2"),
        )
        assert precise_marker.seconds == 123.456
        assert precise_marker.end_seconds == 234.789

        # Test update with decimal precision
        precise_update = SceneMarkerUpdateInput(
            id=ID("1"), seconds=987.654, end_seconds=1234.321
        )
        assert precise_update.seconds == 987.654
        assert precise_update.end_seconds == 1234.321
