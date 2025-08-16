"""Tests for stash.types.filters module."""

import pytest
import strawberry
from strawberry import ID

from stash.types.enums import (
    CircumisedEnum,
    CriterionModifier,
    FilterMode,
    GenderEnum,
    OrientationEnum,
    ResolutionEnum,
    SortDirectionEnum,
)
from stash.types.filters import (  # Core filter types; Criterion input types; Entity filter types
    CircumcisionCriterionInput,
    CustomFieldCriterionInput,
    DateCriterionInput,
    DestroyFilterInput,
    FindFilterType,
    FloatCriterionInput,
    GalleryFilterType,
    GenderCriterionInput,
    GroupFilterType,
    HierarchicalMultiCriterionInput,
    ImageFilterType,
    IntCriterionInput,
    MultiCriterionInput,
    OrientationCriterionInput,
    PerformerFilterType,
    PhashDistanceCriterionInput,
    PHashDuplicationCriterionInput,
    ResolutionCriterionInput,
    SavedFilter,
    SavedFindFilterType,
    SaveFilterInput,
    SceneFilterType,
    SceneMarkerFilterType,
    SetDefaultFilterInput,
    StashIDCriterionInput,
    StringCriterionInput,
    StudioFilterType,
    TagFilterType,
    TimestampCriterionInput,
)


@pytest.mark.unit
class TestFindFilterType:
    """Test FindFilterType input class."""

    def test_strawberry_input_decoration(self):
        """Test that FindFilterType is decorated as strawberry input."""
        assert hasattr(FindFilterType, "__strawberry_definition__")
        assert FindFilterType.__strawberry_definition__.is_input

    def test_field_types(self):
        """Test field type annotations."""
        annotations = FindFilterType.__annotations__
        assert annotations["q"] == str | None
        assert annotations["page"] == int | None
        assert annotations["per_page"] == int | None
        assert annotations["sort"] == str | None
        assert annotations["direction"] == SortDirectionEnum | None

    def test_instantiation(self):
        """Test FindFilterType can be instantiated."""
        filter_type = FindFilterType(
            q="test", page=1, per_page=25, sort="name", direction=SortDirectionEnum.ASC
        )
        assert filter_type.q == "test"
        assert filter_type.page == 1
        assert filter_type.per_page == 25
        assert filter_type.sort == "name"
        assert filter_type.direction == SortDirectionEnum.ASC


@pytest.mark.unit
class TestSavedFindFilterType:
    """Test SavedFindFilterType class."""

    def test_strawberry_type_decoration(self):
        """Test that SavedFindFilterType is decorated as strawberry type."""
        assert hasattr(SavedFindFilterType, "__strawberry_definition__")
        assert not SavedFindFilterType.__strawberry_definition__.is_input

    def test_field_types(self):
        """Test field type annotations."""
        annotations = SavedFindFilterType.__annotations__
        assert annotations["q"] == str | None
        assert annotations["page"] == int | None
        assert annotations["per_page"] == int | None
        assert annotations["sort"] == str | None
        assert annotations["direction"] == SortDirectionEnum | None


@pytest.mark.unit
class TestCriterionInputTypes:
    """Test various criterion input types."""

    def test_string_criterion_input(self):
        """Test StringCriterionInput."""
        assert hasattr(StringCriterionInput, "__strawberry_definition__")
        assert StringCriterionInput.__strawberry_definition__.is_input

        criterion = StringCriterionInput(
            value="test", modifier=CriterionModifier.EQUALS
        )
        assert criterion.value == "test"
        assert criterion.modifier == CriterionModifier.EQUALS

    def test_int_criterion_input(self):
        """Test IntCriterionInput."""
        assert hasattr(IntCriterionInput, "__strawberry_definition__")
        assert IntCriterionInput.__strawberry_definition__.is_input

        criterion = IntCriterionInput(
            value=10, value2=20, modifier=CriterionModifier.BETWEEN
        )
        assert criterion.value == 10
        assert criterion.value2 == 20
        assert criterion.modifier == CriterionModifier.BETWEEN

    def test_float_criterion_input(self):
        """Test FloatCriterionInput."""
        assert hasattr(FloatCriterionInput, "__strawberry_definition__")
        assert FloatCriterionInput.__strawberry_definition__.is_input

        criterion = FloatCriterionInput(
            value=1.5, modifier=CriterionModifier.GREATER_THAN
        )
        assert criterion.value == 1.5
        assert criterion.modifier == CriterionModifier.GREATER_THAN

    def test_multi_criterion_input(self):
        """Test MultiCriterionInput."""
        assert hasattr(MultiCriterionInput, "__strawberry_definition__")
        assert MultiCriterionInput.__strawberry_definition__.is_input

        criterion = MultiCriterionInput(
            value=[ID("1"), ID("2")],
            modifier=CriterionModifier.INCLUDES,
            excludes=[ID("3")],
        )
        assert criterion.value is not None
        assert len(criterion.value) == 2
        assert criterion.modifier == CriterionModifier.INCLUDES
        assert criterion.excludes is not None
        assert len(criterion.excludes) == 1

    def test_gender_criterion_input(self):
        """Test GenderCriterionInput."""
        assert hasattr(GenderCriterionInput, "__strawberry_definition__")
        assert GenderCriterionInput.__strawberry_definition__.is_input

        criterion = GenderCriterionInput(
            value=GenderEnum.FEMALE,
            value_list=[GenderEnum.FEMALE, GenderEnum.MALE],
            modifier=CriterionModifier.EQUALS,
        )
        assert criterion.value == GenderEnum.FEMALE
        assert criterion.value_list is not None
        assert len(criterion.value_list) == 2

    def test_resolution_criterion_input(self):
        """Test ResolutionCriterionInput."""
        assert hasattr(ResolutionCriterionInput, "__strawberry_definition__")
        assert ResolutionCriterionInput.__strawberry_definition__.is_input

        criterion = ResolutionCriterionInput(
            value=ResolutionEnum.HUGE, modifier=CriterionModifier.EQUALS
        )
        assert criterion.value == ResolutionEnum.HUGE
        assert criterion.modifier == CriterionModifier.EQUALS

    def test_orientation_criterion_input(self):
        """Test OrientationCriterionInput."""
        assert hasattr(OrientationCriterionInput, "__strawberry_definition__")
        assert OrientationCriterionInput.__strawberry_definition__.is_input

        criterion = OrientationCriterionInput(
            value=[OrientationEnum.PORTRAIT, OrientationEnum.LANDSCAPE]
        )
        assert criterion.value is not None
        assert len(criterion.value) == 2

    def test_stash_id_criterion_input(self):
        """Test StashIDCriterionInput."""
        assert hasattr(StashIDCriterionInput, "__strawberry_definition__")
        assert StashIDCriterionInput.__strawberry_definition__.is_input

        criterion = StashIDCriterionInput(
            endpoint="stashdb", stash_id="123", modifier=CriterionModifier.EQUALS
        )
        assert criterion.endpoint == "stashdb"
        assert criterion.stash_id == "123"
        assert criterion.modifier == CriterionModifier.EQUALS

    def test_custom_field_criterion_input(self):
        """Test CustomFieldCriterionInput."""
        assert hasattr(CustomFieldCriterionInput, "__strawberry_definition__")
        assert CustomFieldCriterionInput.__strawberry_definition__.is_input

        criterion = CustomFieldCriterionInput(
            field="custom_field",
            value=["value1", "value2"],
            modifier=CriterionModifier.INCLUDES,
        )
        assert criterion.field == "custom_field"
        assert criterion.value is not None
        assert len(criterion.value) == 2
        assert criterion.modifier == CriterionModifier.INCLUDES


@pytest.mark.unit
class TestSavedFilter:
    """Test SavedFilter type."""

    def test_strawberry_type_decoration(self):
        """Test that SavedFilter is decorated as strawberry type."""
        assert hasattr(SavedFilter, "__strawberry_definition__")
        assert not SavedFilter.__strawberry_definition__.is_input

    def test_field_types(self):
        """Test field type annotations."""
        annotations = SavedFilter.__annotations__
        assert annotations["id"] == ID
        assert annotations["mode"] == FilterMode
        assert annotations["name"] == str
        assert annotations["find_filter"] == SavedFindFilterType | None
        # dict[str, any] vs dict[str, Any] can vary by Python version, so check flexibly
        assert "dict" in str(annotations["object_filter"])
        assert "dict" in str(annotations["ui_options"])


@pytest.mark.unit
class TestFilterInputTypes:
    """Test filter input types."""

    def test_save_filter_input(self):
        """Test SaveFilterInput."""
        assert hasattr(SaveFilterInput, "__strawberry_definition__")
        assert SaveFilterInput.__strawberry_definition__.is_input

        filter_input = SaveFilterInput(mode=FilterMode.SCENES, name="Test Filter")
        assert filter_input.mode == FilterMode.SCENES
        assert filter_input.name == "Test Filter"

    def test_destroy_filter_input(self):
        """Test DestroyFilterInput."""
        assert hasattr(DestroyFilterInput, "__strawberry_definition__")
        assert DestroyFilterInput.__strawberry_definition__.is_input

        destroy_input = DestroyFilterInput(id=ID("1"))
        assert destroy_input.id == ID("1")

    def test_set_default_filter_input(self):
        """Test SetDefaultFilterInput."""
        assert hasattr(SetDefaultFilterInput, "__strawberry_definition__")
        assert SetDefaultFilterInput.__strawberry_definition__.is_input

        default_input = SetDefaultFilterInput(mode=FilterMode.PERFORMERS)
        assert default_input.mode == FilterMode.PERFORMERS


@pytest.mark.unit
class TestPerformerFilterType:
    """Test PerformerFilterType input."""

    def test_strawberry_input_decoration(self):
        """Test that PerformerFilterType is decorated as strawberry input."""
        assert hasattr(PerformerFilterType, "__strawberry_definition__")
        assert PerformerFilterType.__strawberry_definition__.is_input

    def test_logical_operators(self):
        """Test logical operator fields."""
        annotations = PerformerFilterType.__annotations__
        assert "AND" in annotations
        assert "OR" in annotations
        assert "NOT" in annotations

    def test_field_types(self):
        """Test some key field type annotations."""
        annotations = PerformerFilterType.__annotations__
        assert annotations["name"] == StringCriterionInput | None
        assert annotations["birth_year"] == IntCriterionInput | None
        assert annotations["gender"] == GenderCriterionInput | None
        assert annotations["tags"] == HierarchicalMultiCriterionInput | None
        assert annotations["rating100"] == IntCriterionInput | None

    def test_instantiation(self):
        """Test PerformerFilterType can be instantiated."""
        filter_type = PerformerFilterType(
            name=StringCriterionInput(value="John", modifier=CriterionModifier.EQUALS),
            filter_favorites=True,
        )
        assert filter_type.name is not None
        assert filter_type.name.value == "John"
        assert filter_type.filter_favorites is True


@pytest.mark.unit
class TestSceneFilterType:
    """Test SceneFilterType input."""

    def test_strawberry_input_decoration(self):
        """Test that SceneFilterType is decorated as strawberry input."""
        assert hasattr(SceneFilterType, "__strawberry_definition__")
        assert SceneFilterType.__strawberry_definition__.is_input

    def test_logical_operators(self):
        """Test logical operator fields."""
        annotations = SceneFilterType.__annotations__
        assert "AND" in annotations
        assert "OR" in annotations
        assert "NOT" in annotations

    def test_field_types(self):
        """Test some key field type annotations."""
        annotations = SceneFilterType.__annotations__
        assert annotations["title"] == StringCriterionInput | None
        assert annotations["id"] == IntCriterionInput | None
        assert annotations["duration"] == IntCriterionInput | None
        assert annotations["resolution"] == ResolutionCriterionInput | None
        assert annotations["orientation"] == OrientationCriterionInput | None

    def test_instantiation(self):
        """Test SceneFilterType can be instantiated."""
        filter_type = SceneFilterType(
            title=StringCriterionInput(
                value="Test Scene", modifier=CriterionModifier.EQUALS
            ),
            organized=True,
        )
        assert filter_type.title is not None
        assert filter_type.title.value == "Test Scene"
        assert filter_type.organized is True


@pytest.mark.unit
class TestGalleryFilterType:
    """Test GalleryFilterType input."""

    def test_strawberry_input_decoration(self):
        """Test that GalleryFilterType is decorated as strawberry input."""
        assert hasattr(GalleryFilterType, "__strawberry_definition__")
        assert GalleryFilterType.__strawberry_definition__.is_input

    def test_logical_operators(self):
        """Test logical operator fields."""
        annotations = GalleryFilterType.__annotations__
        assert "AND" in annotations
        assert "OR" in annotations
        assert "NOT" in annotations

    def test_field_types(self):
        """Test some key field type annotations."""
        annotations = GalleryFilterType.__annotations__
        assert annotations["title"] == StringCriterionInput | None
        assert annotations["id"] == IntCriterionInput | None
        assert annotations["file_count"] == IntCriterionInput | None
        assert annotations["is_zip"] == bool | None
        assert annotations["average_resolution"] == ResolutionCriterionInput | None


@pytest.mark.unit
class TestOtherFilterTypes:
    """Test other entity filter types."""

    def test_studio_filter_type(self):
        """Test StudioFilterType."""
        assert hasattr(StudioFilterType, "__strawberry_definition__")
        assert StudioFilterType.__strawberry_definition__.is_input

        annotations = StudioFilterType.__annotations__
        assert annotations["name"] == StringCriterionInput | None
        assert annotations["favorite"] == bool | None
        assert annotations["scene_count"] == IntCriterionInput | None

    def test_tag_filter_type(self):
        """Test TagFilterType."""
        assert hasattr(TagFilterType, "__strawberry_definition__")
        assert TagFilterType.__strawberry_definition__.is_input

        annotations = TagFilterType.__annotations__
        assert annotations["name"] == StringCriterionInput | None
        assert annotations["favorite"] == bool | None
        assert annotations["scene_count"] == IntCriterionInput | None

    def test_image_filter_type(self):
        """Test ImageFilterType."""
        assert hasattr(ImageFilterType, "__strawberry_definition__")
        assert ImageFilterType.__strawberry_definition__.is_input

        annotations = ImageFilterType.__annotations__
        assert annotations["title"] == StringCriterionInput | None
        assert annotations["resolution"] == ResolutionCriterionInput | None
        assert annotations["orientation"] == OrientationCriterionInput | None

    def test_group_filter_type(self):
        """Test GroupFilterType."""
        assert hasattr(GroupFilterType, "__strawberry_definition__")
        assert GroupFilterType.__strawberry_definition__.is_input

        annotations = GroupFilterType.__annotations__
        assert annotations["name"] == StringCriterionInput | None
        assert annotations["duration"] == IntCriterionInput | None
        assert annotations["rating100"] == IntCriterionInput | None

    def test_scene_marker_filter_type(self):
        """Test SceneMarkerFilterType."""
        assert hasattr(SceneMarkerFilterType, "__strawberry_definition__")
        assert SceneMarkerFilterType.__strawberry_definition__.is_input

        annotations = SceneMarkerFilterType.__annotations__
        assert annotations["tags"] == HierarchicalMultiCriterionInput | None
        assert annotations["performers"] == MultiCriterionInput | None
        assert annotations["duration"] == FloatCriterionInput | None


@pytest.mark.unit
class TestComplexCriterionTypes:
    """Test complex criterion input types."""

    def test_hierarchical_multi_criterion_input(self):
        """Test HierarchicalMultiCriterionInput."""
        assert hasattr(HierarchicalMultiCriterionInput, "__strawberry_definition__")
        assert HierarchicalMultiCriterionInput.__strawberry_definition__.is_input

        criterion = HierarchicalMultiCriterionInput(
            value=[ID("1"), ID("2")],
            modifier=CriterionModifier.INCLUDES,
            depth=2,
            excludes=[ID("3")],
        )
        assert criterion.value is not None
        assert len(criterion.value) == 2
        assert criterion.depth == 2
        assert criterion.excludes is not None
        assert len(criterion.excludes) == 1

    def test_date_criterion_input(self):
        """Test DateCriterionInput."""
        assert hasattr(DateCriterionInput, "__strawberry_definition__")
        assert DateCriterionInput.__strawberry_definition__.is_input

        criterion = DateCriterionInput(
            value="2023-01-01", value2="2023-12-31", modifier=CriterionModifier.BETWEEN
        )
        assert criterion.value == "2023-01-01"
        assert criterion.value2 == "2023-12-31"
        assert criterion.modifier == CriterionModifier.BETWEEN

    def test_timestamp_criterion_input(self):
        """Test TimestampCriterionInput."""
        assert hasattr(TimestampCriterionInput, "__strawberry_definition__")
        assert TimestampCriterionInput.__strawberry_definition__.is_input

        criterion = TimestampCriterionInput(
            value="2023-01-01T00:00:00Z", modifier=CriterionModifier.GREATER_THAN
        )
        assert criterion.value == "2023-01-01T00:00:00Z"
        assert criterion.modifier == CriterionModifier.GREATER_THAN

    def test_phash_distance_criterion_input(self):
        """Test PhashDistanceCriterionInput."""
        assert hasattr(PhashDistanceCriterionInput, "__strawberry_definition__")
        assert PhashDistanceCriterionInput.__strawberry_definition__.is_input

        criterion = PhashDistanceCriterionInput(
            value="abc123", modifier=CriterionModifier.EQUALS, distance=5
        )
        assert criterion.value == "abc123"
        assert criterion.distance == 5
        assert criterion.modifier == CriterionModifier.EQUALS

    def test_phash_duplication_criterion_input(self):
        """Test PHashDuplicationCriterionInput."""
        assert hasattr(PHashDuplicationCriterionInput, "__strawberry_definition__")
        assert PHashDuplicationCriterionInput.__strawberry_definition__.is_input

        criterion = PHashDuplicationCriterionInput(duplicated=True, distance=10)
        assert criterion.duplicated is True
        assert criterion.distance == 10

    def test_circumcision_criterion_input(self):
        """Test CircumcisionCriterionInput."""
        assert hasattr(CircumcisionCriterionInput, "__strawberry_definition__")
        assert CircumcisionCriterionInput.__strawberry_definition__.is_input

        criterion = CircumcisionCriterionInput(
            value=[CircumisedEnum.CUT, CircumisedEnum.UNCUT],
            modifier=CriterionModifier.INCLUDES,
        )
        assert criterion.value is not None
        assert len(criterion.value) == 2
        assert CircumisedEnum.CUT in criterion.value
        assert criterion.modifier == CriterionModifier.INCLUDES
