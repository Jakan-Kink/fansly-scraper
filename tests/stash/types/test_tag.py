"""Tests for stash.types.tag module.

Tests tag types including Tag, TagCreateInput, TagUpdateInput and related types.
"""

import pytest
from strawberry import ID

from metadata import Hashtag
from stash.types.tag import (
    BulkTagUpdateInput,
    FindTagsResultType,
    Tag,
    TagCreateInput,
    TagDestroyInput,
    TagsMergeInput,
    TagUpdateInput,
)


@pytest.mark.unit
def test_tag_create_input() -> None:
    """Test TagCreateInput input type."""
    assert hasattr(TagCreateInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in TagCreateInput.__strawberry_definition__.fields
    }
    expected_fields = ["name", "aliases", "description", "parent_ids", "child_ids"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in TagCreateInput"


@pytest.mark.unit
def test_tag_update_input() -> None:
    """Test TagUpdateInput input type."""
    assert hasattr(TagUpdateInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in TagUpdateInput.__strawberry_definition__.fields
    }
    expected_fields = [
        "id",
        "name",
        "aliases",
        "description",
        "parent_ids",
        "child_ids",
    ]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in TagUpdateInput"


@pytest.mark.unit
def test_tag() -> None:
    """Test Tag type."""
    assert hasattr(Tag, "__strawberry_definition__")

    # Test that it extends StashObject
    fields = {field.name: field for field in Tag.__strawberry_definition__.fields}
    assert "id" in fields  # From StashObject

    # Test tag-specific fields
    expected_fields = [
        "name",
        "aliases",
        "parents",
        "children",
        "description",
        "image_path",
    ]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in Tag"


@pytest.mark.unit
def test_tag_class_variables() -> None:
    """Test Tag class variables."""
    assert hasattr(Tag, "__type_name__")
    assert Tag.__type_name__ == "Tag"

    assert hasattr(Tag, "__update_input_type__")
    assert Tag.__update_input_type__ == TagUpdateInput

    assert hasattr(Tag, "__create_input_type__")
    assert Tag.__create_input_type__ == TagCreateInput

    assert hasattr(Tag, "__tracked_fields__")
    expected_tracked_fields = {"name", "aliases", "description", "parents", "children"}
    assert Tag.__tracked_fields__ == expected_tracked_fields


@pytest.mark.unit
def test_tag_field_conversions() -> None:
    """Test Tag field conversions."""
    assert hasattr(Tag, "__field_conversions__")

    expected_conversions = {
        "name": str,
        "description": str,
        "aliases": list,
    }

    for field, conversion in expected_conversions.items():
        assert field in Tag.__field_conversions__
        assert Tag.__field_conversions__[field] == conversion


@pytest.mark.unit
def test_tag_relationships() -> None:
    """Test Tag relationships."""
    assert hasattr(Tag, "__relationships__")

    expected_relationships = {
        "parents": ("parent_ids", True, None),
        "children": ("child_ids", True, None),
    }

    for field, mapping in expected_relationships.items():
        assert field in Tag.__relationships__
        assert Tag.__relationships__[field] == mapping


@pytest.mark.unit
def test_tag_destroy_input() -> None:
    """Test TagDestroyInput input type."""
    assert hasattr(TagDestroyInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in TagDestroyInput.__strawberry_definition__.fields
    }
    expected_fields = ["id"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in TagDestroyInput"


@pytest.mark.unit
def test_tags_merge_input() -> None:
    """Test TagsMergeInput input type."""
    assert hasattr(TagsMergeInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in TagsMergeInput.__strawberry_definition__.fields
    }
    expected_fields = ["source", "destination"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in TagsMergeInput"


@pytest.mark.unit
def test_bulk_tag_update_input() -> None:
    """Test BulkTagUpdateInput input type."""
    assert hasattr(BulkTagUpdateInput, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in BulkTagUpdateInput.__strawberry_definition__.fields
    }
    expected_fields = [
        "ids",
        "aliases",
        "description",
        "parent_ids",
        "child_ids",
    ]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in BulkTagUpdateInput"


@pytest.mark.unit
def test_find_tags_result_type() -> None:
    """Test FindTagsResultType result type."""
    assert hasattr(FindTagsResultType, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in FindTagsResultType.__strawberry_definition__.fields
    }
    expected_fields = ["count", "tags"]

    for field in expected_fields:
        assert field in fields, f"Field {field} not found in FindTagsResultType"


@pytest.mark.unit
def test_tag_instantiation() -> None:
    """Test Tag instantiation."""
    tag = Tag(id=ID("123"), name="test-tag")

    assert tag.id == ID("123")
    assert tag.name == "test-tag"
    assert tag.aliases == []  # default factory
    assert tag.parents == []  # default factory
    assert tag.children == []  # default factory


@pytest.mark.unit
def test_tag_create_input_instantiation() -> None:
    """Test TagCreateInput instantiation."""
    tag_input = TagCreateInput(
        name="new-tag", description="A new tag", aliases=["alias1", "alias2"]
    )

    assert tag_input.name == "new-tag"
    assert tag_input.description == "A new tag"
    assert tag_input.aliases == ["alias1", "alias2"]


@pytest.mark.unit
def test_tag_update_input_instantiation() -> None:
    """Test TagUpdateInput instantiation."""
    tag_input = TagUpdateInput(
        id=ID("123"), name="updated-tag", description="An updated tag"
    )

    assert tag_input.id == ID("123")
    assert tag_input.name == "updated-tag"
    assert tag_input.description == "An updated tag"


@pytest.mark.unit
def test_strawberry_decorations() -> None:
    """Test that all types are properly decorated with strawberry."""
    types_to_test = [
        TagCreateInput,
        TagUpdateInput,
        Tag,
        TagDestroyInput,
        TagsMergeInput,
        BulkTagUpdateInput,
        FindTagsResultType,
    ]

    for type_class in types_to_test:
        assert hasattr(type_class, "__strawberry_definition__"), (
            f"{type_class.__name__} missing strawberry definition"
        )


@pytest.mark.unit
def test_tag_inheritance() -> None:
    """Test that Tag properly inherits from StashObject."""

    # Test that Tag follows the StashObject interface pattern
    assert hasattr(Tag, "__type_name__")
    assert hasattr(Tag, "__tracked_fields__")
    assert hasattr(Tag, "__field_conversions__")
    assert hasattr(Tag, "__relationships__")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tag_from_hashtag() -> None:
    """Test Tag.from_hashtag class method."""
    # Create a hashtag with a value
    hashtag = Hashtag()
    hashtag.value = "testhashtag"

    # Call the from_hashtag method
    tag = await Tag.from_hashtag(hashtag)

    # Verify the tag was created with the correct properties
    assert tag.id == "new"  # Will be replaced on save
    assert tag.name == "testhashtag"
    assert tag.aliases == []  # Default factory
    assert tag.parents == []  # Default factory
    assert tag.children == []  # Default factory
    assert tag.description is None  # Default None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tag_from_hashtag_with_special_characters() -> None:
    """Test Tag.from_hashtag with hashtag containing special characters."""
    # Create a hashtag with special characters
    hashtag = Hashtag()
    hashtag.value = "my-awesome_tag123"

    # Call the from_hashtag method
    tag = await Tag.from_hashtag(hashtag)

    # Verify the tag was created with the correct properties
    assert tag.id == "new"
    assert tag.name == "my-awesome_tag123"
    assert tag.aliases == []
    assert tag.parents == []
    assert tag.children == []
    assert tag.description is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tag_from_hashtag_empty_value() -> None:
    """Test Tag.from_hashtag with empty hashtag value."""
    # Create a hashtag with empty value
    hashtag = Hashtag()
    hashtag.value = ""

    # Call the from_hashtag method
    tag = await Tag.from_hashtag(hashtag)

    # Verify the tag was created with empty name
    assert tag.id == "new"
    assert tag.name == ""
    assert tag.aliases == []
    assert tag.parents == []
    assert tag.children == []
    assert tag.description is None
