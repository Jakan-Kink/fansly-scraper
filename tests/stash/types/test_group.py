"""Tests for stash.types.group module."""

import pytest
from strawberry import ID

from stash.types.base import StashObject
from stash.types.enums import BulkUpdateIdMode
from stash.types.group import (  # Main group types; Group input types; Group operation types; Bulk update types; Result types
    BulkGroupUpdateInput,
    BulkUpdateGroupDescriptionsInput,
    FindGroupsResultType,
    Group,
    GroupCreateInput,
    GroupDescription,
    GroupDescriptionInput,
    GroupDestroyInput,
    GroupSubGroupAddInput,
    GroupSubGroupRemoveInput,
    GroupUpdateInput,
    ReorderSubGroupsInput,
)


@pytest.mark.unit
class TestGroupDescription:
    """Test GroupDescription class."""

    def test_strawberry_type_decoration(self):
        """Test that GroupDescription is decorated as strawberry type."""
        assert hasattr(GroupDescription, "__strawberry_definition__")
        assert not GroupDescription.__strawberry_definition__.is_input

    def test_field_types(self):
        """Test field type annotations."""
        annotations = GroupDescription.__annotations__
        assert annotations["group"] == "Group"
        assert annotations["description"] == str | None

    def test_default_values(self):
        """Test default field values."""
        # Note: Can't instantiate without group field, just test the annotation exists
        assert GroupDescription.__annotations__["description"] == str | None


@pytest.mark.unit
class TestGroup:
    """Test Group class."""

    def test_strawberry_type_decoration(self):
        """Test that Group is decorated as strawberry type."""
        assert hasattr(Group, "__strawberry_definition__")
        assert not Group.__strawberry_definition__.is_input

    def test_stash_object_inheritance(self):
        """Test that Group inherits from StashObject."""
        assert issubclass(Group, StashObject)

    def test_class_variables(self):
        """Test class variable values."""
        assert Group.__type_name__ == "Group"
        assert Group.__update_input_type__ == GroupUpdateInput
        assert Group.__create_input_type__ == GroupCreateInput

        # Test tracked fields
        expected_tracked = {
            "name",
            "urls",
            "tags",
            "containing_groups",
            "sub_groups",
            "aliases",
            "duration",
            "date",
            "studio",
            "director",
            "synopsis",
        }
        assert Group.__tracked_fields__ == expected_tracked

        # Test field conversions
        expected_conversions = {
            "name": str,
            "urls": list,
            "aliases": str,
            "duration": int,
            "date": str,
            "rating100": int,
            "director": str,
            "synopsis": str,
        }
        assert Group.__field_conversions__ == expected_conversions

        # Test relationships
        expected_relationships = {
            "studio": ("studio_id", False, None),
            "tags": ("tag_ids", True, None),
            "containing_groups": (
                "containing_groups",
                True,
                callable,
            ),  # Has transform function
            "sub_groups": ("sub_groups", True, callable),  # Has transform function
        }

        # Verify all expected relationships exist
        for field, expected_mapping in expected_relationships.items():
            assert field in Group.__relationships__, (
                f"Relationship {field} not found in Group"
            )

            actual_mapping = Group.__relationships__[field]
            # Check target field
            assert actual_mapping[0] == expected_mapping[0], (
                f"Target field mismatch for {field}"
            )
            # Check is_list flag
            assert actual_mapping[1] is expected_mapping[1], (
                f"is_list flag mismatch for {field}"
            )
            # Check if transform function exists when expected
            if expected_mapping[2] is callable:
                assert callable(actual_mapping[2]), (
                    f"Transform function missing for {field}"
                )
            else:
                assert actual_mapping[2] == expected_mapping[2], (
                    f"Transform function mismatch for {field}"
                )

    def test_field_types(self):
        """Test field type annotations."""
        annotations = Group.__annotations__
        assert annotations["name"] == str
        assert annotations["aliases"] == str | None
        assert annotations["duration"] == int | None
        assert annotations["date"] == str | None
        assert annotations["director"] == str | None
        assert annotations["synopsis"] == str | None
        assert annotations["front_image_path"] == str | None
        assert annotations["back_image_path"] == str | None

    def test_default_values(self):
        """Test default field values."""
        group = Group(id="test-group-id", name="Test Group")
        assert group.name == "Test Group"
        assert isinstance(group.urls, list)
        assert len(group.urls) == 0
        assert isinstance(group.tags, list)
        assert isinstance(group.containing_groups, list)
        assert isinstance(group.sub_groups, list)
        assert isinstance(group.scenes, list)
        assert group.aliases is None
        assert group.duration is None
        assert group.date is None
        assert group.director is None
        assert group.synopsis is None

    def test_field_conversions(self):
        """Test field conversion functions."""
        conversions = Group.__field_conversions__

        # Test string conversions
        assert conversions["name"]("Test Group") == "Test Group"
        assert conversions["aliases"]("Alias 1, Alias 2") == "Alias 1, Alias 2"
        assert conversions["director"]("John Doe") == "John Doe"
        assert conversions["synopsis"]("Test synopsis") == "Test synopsis"

        # Test int conversions
        assert conversions["duration"](120) == 120
        assert conversions["rating100"](85) == 85

        # Test list conversion
        assert conversions["urls"](["url1", "url2"]) == ["url1", "url2"]


@pytest.mark.unit
class TestGroupInputs:
    """Test group input types."""

    def test_group_create_input(self):
        """Test GroupCreateInput."""
        assert hasattr(GroupCreateInput, "__strawberry_definition__")
        assert GroupCreateInput.__strawberry_definition__.is_input

        annotations = GroupCreateInput.__annotations__
        assert annotations["name"] == str
        assert annotations["aliases"] == str | None
        assert annotations["duration"] == int | None
        assert annotations["date"] == str | None
        assert annotations["rating100"] == int | None
        assert annotations["studio_id"] == ID | None
        assert annotations["director"] == str | None
        assert annotations["synopsis"] == str | None
        assert annotations["urls"] == list[str] | None
        assert annotations["tag_ids"] == list[ID] | None
        assert annotations["front_image"] == str | None
        assert annotations["back_image"] == str | None

        # Test instantiation
        create_input = GroupCreateInput(name="Test Group")
        assert create_input.name == "Test Group"
        assert create_input.aliases is None
        assert create_input.duration is None

    def test_group_update_input(self):
        """Test GroupUpdateInput."""
        assert hasattr(GroupUpdateInput, "__strawberry_definition__")
        assert GroupUpdateInput.__strawberry_definition__.is_input

        annotations = GroupUpdateInput.__annotations__
        assert annotations["id"] == ID
        assert annotations["name"] == str | None
        assert annotations["aliases"] == str | None
        assert annotations["duration"] == int | None
        assert annotations["date"] == str | None
        assert annotations["rating100"] == int | None
        assert annotations["studio_id"] == ID | None
        assert annotations["director"] == str | None
        assert annotations["synopsis"] == str | None
        assert annotations["urls"] == list[str] | None
        assert annotations["tag_ids"] == list[ID] | None
        assert annotations["front_image"] == str | None
        assert annotations["back_image"] == str | None

        # Test instantiation
        update_input = GroupUpdateInput(id=ID("1"), name="Updated Group")
        assert update_input.id == ID("1")
        assert update_input.name == "Updated Group"
        assert update_input.duration is None

    def test_group_description_input(self):
        """Test GroupDescriptionInput."""
        assert hasattr(GroupDescriptionInput, "__strawberry_definition__")
        assert GroupDescriptionInput.__strawberry_definition__.is_input

        annotations = GroupDescriptionInput.__annotations__
        assert annotations["group_id"] == ID
        assert annotations["description"] == str | None

        # Test instantiation
        desc_input = GroupDescriptionInput(
            group_id=ID("1"), description="Test description"
        )
        assert desc_input.group_id == ID("1")
        assert desc_input.description == "Test description"


@pytest.mark.unit
class TestGroupOperationInputs:
    """Test group operation input types."""

    def test_group_destroy_input(self):
        """Test GroupDestroyInput."""
        assert hasattr(GroupDestroyInput, "__strawberry_definition__")
        assert GroupDestroyInput.__strawberry_definition__.is_input

        annotations = GroupDestroyInput.__annotations__
        assert annotations["id"] == ID

        # Test instantiation
        destroy_input = GroupDestroyInput(id=ID("1"))
        assert destroy_input.id == ID("1")

    def test_reorder_sub_groups_input(self):
        """Test ReorderSubGroupsInput."""
        assert hasattr(ReorderSubGroupsInput, "__strawberry_definition__")
        assert ReorderSubGroupsInput.__strawberry_definition__.is_input

        annotations = ReorderSubGroupsInput.__annotations__
        assert annotations["group_id"] == ID
        assert annotations["sub_group_ids"] == list[ID]
        assert annotations["insert_at_id"] == ID
        assert annotations["insert_after"] == bool

        # Test instantiation
        reorder_input = ReorderSubGroupsInput(
            group_id=ID("1"),
            sub_group_ids=[ID("2"), ID("3")],
            insert_at_id=ID("4"),
            insert_after=True,
        )
        assert reorder_input.group_id == ID("1")
        assert len(reorder_input.sub_group_ids) == 2
        assert reorder_input.insert_at_id == ID("4")
        assert reorder_input.insert_after is True

    def test_group_sub_group_add_input(self):
        """Test GroupSubGroupAddInput."""
        assert hasattr(GroupSubGroupAddInput, "__strawberry_definition__")
        assert GroupSubGroupAddInput.__strawberry_definition__.is_input

        annotations = GroupSubGroupAddInput.__annotations__
        assert annotations["containing_group_id"] == ID
        assert annotations["sub_groups"] == list[GroupDescriptionInput]
        assert annotations["insert_index"] == int | None

        # Test instantiation
        sub_groups = [
            GroupDescriptionInput(group_id=ID("2"), description="Sub 1"),
            GroupDescriptionInput(group_id=ID("3"), description="Sub 2"),
        ]
        add_input = GroupSubGroupAddInput(
            containing_group_id=ID("1"), sub_groups=sub_groups, insert_index=0
        )
        assert add_input.containing_group_id == ID("1")
        assert len(add_input.sub_groups) == 2
        assert add_input.insert_index == 0

    def test_group_sub_group_remove_input(self):
        """Test GroupSubGroupRemoveInput."""
        assert hasattr(GroupSubGroupRemoveInput, "__strawberry_definition__")
        assert GroupSubGroupRemoveInput.__strawberry_definition__.is_input

        annotations = GroupSubGroupRemoveInput.__annotations__
        assert annotations["containing_group_id"] == ID
        assert annotations["sub_group_ids"] == list[ID]

        # Test instantiation
        remove_input = GroupSubGroupRemoveInput(
            containing_group_id=ID("1"), sub_group_ids=[ID("2"), ID("3")]
        )
        assert remove_input.containing_group_id == ID("1")
        assert len(remove_input.sub_group_ids) == 2


@pytest.mark.unit
class TestBulkUpdateTypes:
    """Test bulk update input types."""

    def test_bulk_update_group_descriptions_input(self):
        """Test BulkUpdateGroupDescriptionsInput."""
        assert hasattr(BulkUpdateGroupDescriptionsInput, "__strawberry_definition__")
        assert BulkUpdateGroupDescriptionsInput.__strawberry_definition__.is_input

        annotations = BulkUpdateGroupDescriptionsInput.__annotations__
        assert annotations["groups"] == list[GroupDescriptionInput]
        assert annotations["mode"] == BulkUpdateIdMode

        # Test instantiation
        groups = [
            GroupDescriptionInput(group_id=ID("1"), description="Desc 1"),
            GroupDescriptionInput(group_id=ID("2"), description="Desc 2"),
        ]
        bulk_update = BulkUpdateGroupDescriptionsInput(
            groups=groups, mode=BulkUpdateIdMode.SET
        )
        assert len(bulk_update.groups) == 2
        assert bulk_update.mode == BulkUpdateIdMode.SET

    def test_bulk_group_update_input(self):
        """Test BulkGroupUpdateInput."""
        assert hasattr(BulkGroupUpdateInput, "__strawberry_definition__")
        assert BulkGroupUpdateInput.__strawberry_definition__.is_input

        annotations = BulkGroupUpdateInput.__annotations__
        assert annotations["client_mutation_id"] == str | None
        assert annotations["ids"] == list[ID]
        assert annotations["rating100"] == int | None
        assert annotations["studio_id"] == ID | None
        assert annotations["director"] == str | None
        assert annotations["urls"] == list[str] | None
        assert annotations["tag_ids"] == list[ID] | None
        assert (
            annotations["containing_groups"] == BulkUpdateGroupDescriptionsInput | None
        )
        assert annotations["sub_groups"] == BulkUpdateGroupDescriptionsInput | None

        # Test instantiation
        bulk_update = BulkGroupUpdateInput(
            ids=[ID("1"), ID("2")], rating100=85, director="John Doe"
        )
        assert len(bulk_update.ids) == 2
        assert bulk_update.rating100 == 85
        assert bulk_update.director == "John Doe"
        assert bulk_update.client_mutation_id is None


@pytest.mark.unit
class TestResultTypes:
    """Test result types."""

    def test_find_groups_result_type(self):
        """Test FindGroupsResultType."""
        assert hasattr(FindGroupsResultType, "__strawberry_definition__")
        assert not FindGroupsResultType.__strawberry_definition__.is_input

        annotations = FindGroupsResultType.__annotations__
        assert annotations["count"] == int
        assert annotations["groups"] == list[Group]

        # Test instantiation
        result = FindGroupsResultType(count=5, groups=[])
        assert result.count == 5
        assert isinstance(result.groups, list)
        assert len(result.groups) == 0
