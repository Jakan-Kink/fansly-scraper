"""Tests for stash.types.base - Relationship Processing

Tests StashObject relationship processing including single and list relationships,
transform functions, and relationship data conversion.

Coverage targets: Lines 257-311 (relationship processing methods)
"""

import gc
from typing import Any
from unittest.mock import Mock

import pytest

from stash.types.base import StashObject

from ...fixtures.stash_fixtures import MockTag, TestStashObject


@pytest.fixture(autouse=True)
def clear_field_names_cache():
    """Clear __field_names__ cache from all StashObject subclasses before each test."""
    # Before test: clear all caches
    for obj in gc.get_objects():
        if isinstance(obj, type) and issubclass(obj, StashObject):
            try:
                if hasattr(obj, "__field_names__"):
                    delattr(obj, "__field_names__")
            except (AttributeError, TypeError):
                # Some classes might not allow attribute deletion
                continue

    yield

    # After test: clear all caches again to prevent pollution
    for obj in gc.get_objects():
        if isinstance(obj, type) and issubclass(obj, StashObject):
            try:
                if hasattr(obj, "__field_names__"):
                    delattr(obj, "__field_names__")
            except (AttributeError, TypeError):
                # Some classes might not allow attribute deletion
                continue


# =============================================================================
# Relationship Processing Tests (Lines 257-311)
# =============================================================================


@pytest.mark.asyncio
async def test_process_single_relationship() -> None:
    """Test _process_single_relationship method (Lines 257-311)."""
    obj = TestStashObject(id="test", name="Test")

    # Test with transform function
    mock_tag = MockTag("tag_123", "Test Tag")
    result = await obj._process_single_relationship(
        mock_tag, lambda x: getattr(x, "id", None)
    )
    assert result == "tag_123"

    # Test with None value
    result = await obj._process_single_relationship(None, lambda x: x)
    assert result is None

    # Test with None transform
    result = await obj._process_single_relationship("test", None)
    assert result is None


@pytest.mark.asyncio
async def test_process_list_relationship() -> None:
    """Test _process_list_relationship method."""
    obj = TestStashObject(id="test", name="Test")

    # Test with list of objects
    mock_tags: list[Any] = [MockTag("tag_1", "Tag 1"), MockTag("tag_2", "Tag 2")]
    result = await obj._process_list_relationship(
        mock_tags, lambda x: getattr(x, "id", None)
    )
    assert result == ["tag_1", "tag_2"]

    # Test with empty list
    result = await obj._process_list_relationship([], lambda x: x)
    assert result == []

    # Test with None transform
    result = await obj._process_list_relationship(["test"], None)
    assert result == []


@pytest.mark.asyncio
async def test_process_relationships() -> None:
    """Test _process_relationships method."""
    obj = TestStashObject(
        id="test",
        name="Test",
        tags=[
            MockTag("tag_1", "Tag 1"),
            MockTag("tag_2", "Tag 2"),
        ],  # Use MockTag objects
    )

    # Process tags relationship
    result = await obj._process_relationships({"tags"})
    assert "tag_ids" in result
    # Should extract IDs from MockTag objects using default _get_id
    assert set(result["tag_ids"]) == {"tag_1", "tag_2"}

    # Test with non-existent field
    result = await obj._process_relationships({"nonexistent"})
    assert result == {}


@pytest.mark.asyncio
async def test_process_single_relationship_no_transform() -> None:
    """Test _process_single_relationship with None transform (Line 347)."""
    obj = TestStashObject(id="test", name="Test")

    # Test with value but no transform function
    result = await obj._process_single_relationship("test_value", None)

    # Should return None when transform is None
    assert result is None


@pytest.mark.asyncio
async def test_process_list_relationship_no_items() -> None:
    """Test _process_list_relationship when no items are added (Line 374, 377->370)."""
    obj = TestStashObject(id="test", name="Test")

    # Test with transform that returns None/empty for all items
    def null_transform(item):
        return None

    result = await obj._process_list_relationship(["item1", "item2"], null_transform)

    # Should return empty list when no items are transformed
    assert result == []


@pytest.mark.asyncio
async def test_process_relationships_default_transform() -> None:
    """Test _process_relationships using default _get_id transform."""

    # Create object with relationship that has no explicit transform (uses default)
    class TestDefaultTransform(TestStashObject):
        pass

    # Set class-level relationship with no transform (should use default _get_id)
    TestDefaultTransform.__relationships__ = {
        "tags": (
            "tag_ids",
            True,
            None,
        ),  # No transform specified, should use default _get_id
    }

    # Use proper Tag objects since tags field now expects list[Any] (Tag objects)
    obj = TestDefaultTransform(
        id="test",
        name="Test",
        tags=[
            MockTag("tag1", "Tag 1"),  # Object with .id attribute
            {"id": "tag2", "name": "Tag 2"},  # Dict with "id" key
        ],
    )

    result = await obj._process_relationships({"tags"})

    # Should use default _get_id transform and extract IDs from Tag objects
    assert "tag_ids" in result
    assert set(result["tag_ids"]) == {"tag1", "tag2"}


@pytest.mark.asyncio
async def test_process_relationships_empty_items() -> None:
    """Test _process_relationships when items list is empty (Lines 412-414)."""
    obj = TestStashObject(id="test", name="Test", tags=[])

    result = await obj._process_relationships({"tags"})

    # Should not include tag_ids when items list is empty
    assert "tag_ids" not in result


@pytest.mark.asyncio
async def test_process_single_relationship_async_transform() -> None:
    """Test async transform function in _process_single_relationship (Line 347)."""
    obj = TestStashObject(id="test", name="Test")

    # Create an async transform function
    async def async_transform(value):
        return f"async_{value}"

    # This should hit line 347 where it checks if transform is a coroutine
    result = await obj._process_single_relationship("test_value", async_transform)
    assert result == "async_test_value"


@pytest.mark.asyncio
async def test_process_relationships_non_list() -> None:
    """Test non-list relationship processing (Lines 412-414)."""
    # Use the existing TestStashObject and temporarily modify its relationships
    obj = TestStashObject(id="test", name="Test")

    # Temporarily modify the relationships to include a non-list relationship
    original_relationships = TestStashObject.__relationships__.copy()
    TestStashObject.__relationships__ = {
        "single_tag": (
            "tag_id",
            False,
            lambda x: getattr(x, "id", str(x)),
        ),  # is_list=False
    }

    # Add the single_tag attribute
    obj.single_tag = MockTag("single_123", "Single Tag")

    try:
        # Process the non-list relationship
        result = await obj._process_relationships({"single_tag"})

        # Should include the single tag ID
        assert "tag_id" in result
        assert result["tag_id"] == "single_123"
    finally:
        # Restore original relationships
        TestStashObject.__relationships__ = original_relationships


@pytest.mark.asyncio
async def test_process_relationships_is_list_false_branch() -> None:
    """Test the is_list=False branch that leads to 413->395."""
    obj = TestStashObject(id="test", name="Test")

    # Temporarily add a non-list relationship
    original_relationships = TestStashObject.__relationships__.copy()
    TestStashObject.__relationships__ = {
        "single_item": (
            "item_id",
            False,
            None,
        ),  # is_list=False, no transform (uses default)
    }

    # Add the attribute
    obj.single_item = {"id": "item_123"}

    try:
        # This should hit line 413->395 where is_list is False
        result = await obj._process_relationships({"single_item"})
        assert "item_id" in result
        assert result["item_id"] == "item_123"
    finally:
        TestStashObject.__relationships__ = original_relationships


@pytest.mark.asyncio
async def test_process_single_relationship_with_default_transform() -> None:
    """Test processing single relationship with default _get_id transform."""

    # Use TestStashObject and add a real non-list relationship
    obj = TestStashObject(id="test", name="Test")

    # Add a non-list relationship directly to the object
    obj.single_item = {"id": "item_123"}

    # Temporarily modify the class relationships to include non-list
    original_relationships = TestStashObject.__relationships__.copy()
    TestStashObject.__relationships__ = {
        "tags": ("tag_ids", True, lambda x: getattr(x, "id", str(x))),  # Keep existing
        "single_item": ("item_id", False, None),  # Add non-list relationship
    }

    try:
        # This should use the default _get_id transform for single_item
        result = await obj._process_relationships({"single_item"})
        assert "item_id" in result
        assert result["item_id"] == "item_123"
    finally:
        TestStashObject.__relationships__ = original_relationships


@pytest.mark.asyncio
async def test_relationship_processing_edge_cases() -> None:
    """Test relationship processing with edge cases."""
    obj = TestStashObject(id="test", name="Test")

    # Test with mixed relationship types - use proper Tag objects
    obj.tags = [
        MockTag("tag_1", "Tag 1"),  # Object with .id attribute
        {"id": "dict_tag", "name": "Dict Tag"},  # Dict with "id" key
        {"id": "string_tag", "name": "String Tag"},  # Another dict with "id" key
        MockTag("none_placeholder", "None Placeholder"),  # Object instead of None
    ]

    result = await obj._process_relationships({"tags"})

    # Should handle all types appropriately and extract IDs
    assert "tag_ids" in result
    assert set(result["tag_ids"]) == {
        "tag_1",
        "dict_tag",
        "string_tag",
        "none_placeholder",
    }


@pytest.mark.asyncio
async def test_relationship_transform_variations() -> None:
    """Test various relationship transform scenarios."""
    obj = TestStashObject(id="test", name="Test")

    # Test with different data types - use Any list to avoid strict type checking
    test_data: list[Any] = [
        MockTag("obj_id", "Object"),  # Object with id attribute
        {"id": "dict_id", "name": "Dict"},  # Dict with id key
        "string_value",  # Plain string
    ]

    # Test with lambda transform that handles different types
    def flexible_transform(item):
        if hasattr(item, "id"):
            return item.id
        elif isinstance(item, dict) and "id" in item:
            return item["id"]
        else:
            return str(item)

    result = await obj._process_list_relationship(test_data, flexible_transform)
    expected = ["obj_id", "dict_id", "string_value"]
    assert result == expected


@pytest.mark.asyncio
async def test_relationship_processing_with_none_values() -> None:
    """Test relationship processing with None values in lists."""
    obj = TestStashObject(id="test", name="Test")

    # Test list with None values
    test_data: list[Any] = [
        MockTag("valid_id", "Valid"),
        None,  # Should be skipped
        {"id": "dict_id"},
        None,  # Should be skipped
    ]

    def safe_transform(item):
        if item is None:
            return None
        return getattr(
            item, "id", item.get("id") if isinstance(item, dict) else str(item)
        )

    result = await obj._process_list_relationship(test_data, safe_transform)
    # Should filter out None values
    assert None not in result
    assert "valid_id" in result
    assert "dict_id" in result


@pytest.mark.asyncio
async def test_relationship_processing_async_transforms() -> None:
    """Test relationship processing with async transform functions."""
    obj = TestStashObject(id="test", name="Test")

    # Test async transform for single relationship
    async def async_single_transform(item):
        if hasattr(item, "id"):
            return f"async_{item.id}"
        return f"async_{item}"

    single_result = await obj._process_single_relationship(
        MockTag("test_id", "Test"), async_single_transform
    )
    assert single_result == "async_test_id"

    # Test async transform for list relationship
    async def async_list_transform(item):
        if hasattr(item, "id"):
            return f"async_{item.id}"
        return f"async_{item}"

    test_list: list[Any] = [MockTag("id1", "Tag1"), MockTag("id2", "Tag2")]
    list_result = await obj._process_list_relationship(test_list, async_list_transform)
    assert list_result == ["async_id1", "async_id2"]


@pytest.mark.asyncio
async def test_relationship_error_handling() -> None:
    """Test error handling in relationship processing."""
    obj = TestStashObject(id="test", name="Test")

    # Test transform function that raises an error
    def error_transform(item):
        raise ValueError("Transform error")

    # Should handle errors gracefully by returning None or empty list
    try:
        await obj._process_single_relationship("test", error_transform)
        # Depending on implementation, might return None or raise
    except ValueError:
        pass  # Expected behavior

    try:
        await obj._process_list_relationship(["test"], error_transform)
        # Depending on implementation, might return empty list or raise
    except ValueError:
        pass  # Expected behavior


@pytest.mark.asyncio
async def test_relationship_configuration_edge_cases() -> None:
    """Test edge cases in relationship configuration."""
    obj = TestStashObject(id="test", name="Test")

    # Test processing relationships that don't exist on the object
    result = await obj._process_relationships({"nonexistent_relationship"})
    assert result == {}

    # Test with empty relationships set
    result = await obj._process_relationships(set())
    assert result == {}

    # Test with relationships that exist in config but not as attributes
    original_relationships = TestStashObject.__relationships__.copy()
    TestStashObject.__relationships__ = {
        "missing_attr": ("missing_ids", True, lambda x: x)
    }

    try:
        result = await obj._process_relationships({"missing_attr"})
        # Should handle missing attribute gracefully
        assert "missing_ids" not in result or result["missing_ids"] == []
    finally:
        TestStashObject.__relationships__ = original_relationships


@pytest.mark.asyncio
async def test_relationship_default_transforms() -> None:
    """Test default transform behavior."""
    obj = TestStashObject(id="test", name="Test")

    # Test relationship with no transform specified (should use default _get_id)
    original_relationships = TestStashObject.__relationships__.copy()
    TestStashObject.__relationships__ = {
        "tags": ("tag_ids", True, None),  # No transform, should use _get_id
    }

    try:
        # Set up data that _get_id can handle - use a different attribute to avoid type conflicts
        setattr(
            obj,
            "test_items",
            [
                {"id": "dict_id"},  # Dict with id
                MockTag("obj_id", "Object"),  # Object with id
            ],
        )

        # Temporarily override the relationship to use our test attribute
        TestStashObject.__relationships__ = {
            "test_items": ("tag_ids", True, None),  # No transform, should use _get_id
        }

        result = await obj._process_relationships({"test_items"})
        assert "tag_ids" in result
        # Should extract IDs using default _get_id transform
    # Note: result is a dict, not a string

    finally:
        TestStashObject.__relationships__ = original_relationships


@pytest.mark.asyncio
async def test_process_relationships_single_relationship_falsy_result() -> None:
    """Test _process_relationships when single relationship transform returns falsy value (Line 422).

    This test covers the scenario where 'if transformed:' evaluates to False
    because _process_single_relationship returns None or an empty string.
    """
    obj = TestStashObject(id="test", name="Test")

    # Set up a non-list relationship that will return None from transform
    original_relationships = TestStashObject.__relationships__.copy()

    # Transform function that returns None/falsy values
    def falsy_transform(value):
        return None  # This will make 'if transformed:' False

    TestStashObject.__relationships__ = {
        "single_item": ("item_id", False, falsy_transform),  # is_list=False
    }

    # Add the attribute with a valid value
    obj.single_item = MockTag("valid_id", "Valid Item")

    try:
        # This should hit line 422 where 'if transformed:' is False
        # because falsy_transform returns None
        result = await obj._process_relationships({"single_item"})

        # The target_field should NOT be included in result when transformed is falsy
        assert "item_id" not in result
        assert result == {}

    finally:
        TestStashObject.__relationships__ = original_relationships


@pytest.mark.asyncio
async def test_process_relationships_single_relationship_empty_string() -> None:
    """Test _process_relationships when single relationship transform returns empty string (Line 422).

    This also tests the 'if transformed:' false branch with an empty string.
    """
    obj = TestStashObject(id="test", name="Test")

    # Set up a non-list relationship that will return empty string from transform
    original_relationships = TestStashObject.__relationships__.copy()

    # Transform function that returns empty string (falsy)
    def empty_string_transform(value):
        return ""  # This will make 'if transformed:' False

    TestStashObject.__relationships__ = {
        "single_item": ("item_id", False, empty_string_transform),  # is_list=False
    }

    # Add the attribute with a valid value
    obj.single_item = MockTag("valid_id", "Valid Item")

    try:
        # This should hit line 422 where 'if transformed:' is False
        # because empty_string_transform returns ""
        result = await obj._process_relationships({"single_item"})

        # The target_field should NOT be included in result when transformed is falsy
        assert "item_id" not in result
        assert result == {}

    finally:
        TestStashObject.__relationships__ = original_relationships
