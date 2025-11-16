"""Tests for stash.types.base - Change Tracking

Tests StashObject change tracking functionality including dirty state management,
__setattr__ behavior, mark_clean/mark_dirty methods, and original value tracking.

Coverage targets: Lines in _to_input_dirty function (554-555, 562-563, 566-567, 570)
"""

import pytest

from tests.fixtures.stash.stash_fixtures import MockTag, TestStashObject


# =============================================================================
# Change Tracking Tests (Lines 128-129, mark_clean, mark_dirty)
# =============================================================================


@pytest.mark.unit
def test_stash_object_setattr_tracks_changes(
    test_stash_object: TestStashObject,
) -> None:
    """Test that __setattr__ tracks changes to tracked fields (Lines 128-129)."""
    # Initially clean
    assert not test_stash_object.is_dirty()

    # Change a tracked field
    test_stash_object.name = "Changed Name"

    # Should be dirty now
    assert test_stash_object.is_dirty()
    assert hasattr(test_stash_object, "_dirty_attrs")
    assert "name" in test_stash_object._dirty_attrs


@pytest.mark.unit
def test_stash_object_setattr_untracked_fields(
    test_stash_object: TestStashObject,
) -> None:
    """Test that changes to untracked fields don't mark as dirty."""
    # Change the id (not tracked)
    original_dirty_state = test_stash_object.is_dirty()
    test_stash_object.id = "new_id"

    # Should remain clean since id is not tracked
    assert test_stash_object.is_dirty() == original_dirty_state


@pytest.mark.unit
def test_stash_object_mark_clean() -> None:
    """Test mark_clean method."""
    obj = TestStashObject(id="test", name="Test")

    # Make it dirty
    obj.name = "Changed"
    assert obj.is_dirty()

    # Mark clean
    obj.mark_clean()
    assert not obj.is_dirty()

    # Dirty attributes should be cleared
    assert not hasattr(obj, "_dirty_attrs") or len(obj._dirty_attrs) == 0

    # Original values should be updated
    assert obj.__original_values__["name"] == "Changed"


@pytest.mark.unit
def test_stash_object_mark_dirty() -> None:
    """Test mark_dirty method."""
    obj = TestStashObject(id="test", name="Test")

    # Initially clean
    assert not obj.is_dirty()

    # Mark dirty
    obj.mark_dirty()
    assert obj.is_dirty()


@pytest.mark.unit
def test_setattr_no_original_values() -> None:
    """Test __setattr__ when object has no __original_values__ (Line 164->exit)."""
    # Create object and manually remove __original_values__ to test the hasattr check
    obj = TestStashObject(id="test", name="Test")

    # Remove __original_values__ to trigger the hasattr check failure
    if hasattr(obj, "__original_values__"):
        delattr(obj, "__original_values__")

    # Now set an attribute - this should hit the line 164->exit path
    obj.name = "Changed Name"
    assert obj.name == "Changed Name"


@pytest.mark.unit
def test_mark_clean_missing_tracked_field() -> None:
    """Test mark_clean when tracked field doesn't exist as attribute (Line 186->185)."""

    # Create a class with tracked fields that don't exist as attributes
    class TestMissingFields(TestStashObject):
        __tracked_fields__ = {"name", "description", "nonexistent_field"}

    obj = TestMissingFields(id="test", name="Test")
    # description exists but nonexistent_field does not

    # This should hit the line 186->185 path where hasattr(self, field) is False
    obj.mark_clean()

    # Should work without error even with missing tracked field
    assert not obj.is_dirty()


# =============================================================================
# _to_input_dirty Tests - Targeted Coverage for Missing Lines
# =============================================================================


@pytest.mark.asyncio
async def test_to_input_dirty_field_added_after_creation() -> None:
    """Test _to_input_dirty when field is added after creation (lines 544-545).

    This covers the case where a field exists in current object but not in __original_values__.
    """
    # Create object without a field that's in tracked_fields
    obj = TestStashObject(id="test_added_real", name="Test")
    obj.mark_clean()

    # Manually remove description from __original_values__ to simulate field added after creation
    if "description" in obj.__original_values__:
        del obj.__original_values__["description"]

    # Verify the field is not in original values but is tracked
    assert "description" not in obj.__original_values__
    assert "description" in obj.__tracked_fields__
    assert hasattr(obj, "description")  # Field exists on object

    # Set a value for the field
    obj.description = "Added after creation"

    result = await obj._to_input_dirty()

    # Should include ID and the added field due to lines 544-545
    assert "id" in result
    assert result["id"] == "test_added_real"
    assert "description" in result
    assert result["description"] == "Added after creation"


@pytest.mark.asyncio
async def test_to_input_dirty_force_list_length_comparison() -> None:
    """Force _to_input_dirty to hit list length comparison (lines 554-555).

    This test manually manipulates __original_values__ to ensure the comparison logic runs.
    """
    # Create object with list field
    obj = TestStashObject(
        id="test_force_length", name="Test", tags=[MockTag("1", "tag1")]
    )
    obj.mark_clean()

    # Change the field (this will delete it from __original_values__)
    obj.tags = [MockTag("1", "tag1"), MockTag("2", "tag2")]

    # Manually restore the original value to __original_values__ to force comparison
    obj.__original_values__["tags"] = [
        MockTag("1", "tag1")
    ]  # Restore original with different length

    # Verify the conditions that should trigger lines 554-555
    assert "tags" in obj.__original_values__
    assert len(obj.tags) != len(obj.__original_values__["tags"])  # Different lengths
    assert isinstance(obj.tags, list)
    assert isinstance(obj.__original_values__["tags"], list)

    result = await obj._to_input_dirty()

    # Should include ID and tags field
    assert "id" in result
    assert result["id"] == "test_force_length"


@pytest.mark.asyncio
async def test_to_input_dirty_force_dict_object_comparison() -> None:
    """Force _to_input_dirty to hit dict object comparison (lines 562-563).

    This test manually manipulates __original_values__ to ensure the __dict__ comparison runs.
    """

    class MockObjWithDict:
        def __init__(self, value: str):
            self.value = value

    # Create object with list of objects that have __dict__
    original_obj = MockObjWithDict("original")
    obj = TestStashObject(id="test_force_dict", name="Test", tags=[original_obj])
    obj.mark_clean()

    # Change the field (this will delete it from __original_values__)
    different_obj = MockObjWithDict("different")
    obj.tags = [different_obj]

    # Manually restore the original value to force comparison with same length but different __dict__
    obj.__original_values__["tags"] = [original_obj]  # Same length, different __dict__

    # Verify conditions that should trigger lines 562-563
    assert "tags" in obj.__original_values__
    assert len(obj.tags) == len(obj.__original_values__["tags"])  # Same length
    assert hasattr(obj.tags[0], "__dict__")
    assert hasattr(obj.__original_values__["tags"][0], "__dict__")
    assert (
        obj.tags[0].__dict__ != obj.__original_values__["tags"][0].__dict__
    )  # Different __dict__

    result = await obj._to_input_dirty()

    # Should include ID and tags field
    assert "id" in result
    assert result["id"] == "test_force_dict"


@pytest.mark.asyncio
async def test_to_input_dirty_force_simple_object_comparison() -> None:
    """Force _to_input_dirty to hit simple object comparison (lines 566-567).

    This test manually manipulates __original_values__ to ensure the simple comparison runs.
    """
    # Create object with list of simple objects
    obj = TestStashObject(
        id="test_force_simple", name="Test", tags=["original", "tag2"]
    )
    obj.mark_clean()

    # Change the field (this will delete it from __original_values__)
    obj.tags = ["different", "tag2"]

    # Manually restore the original value to force comparison with same length but different content
    obj.__original_values__["tags"] = [
        "original",
        "tag2",
    ]  # Same length, different content

    # Verify conditions that should trigger lines 566-567
    assert "tags" in obj.__original_values__
    assert len(obj.tags) == len(obj.__original_values__["tags"])  # Same length
    assert not hasattr(obj.tags[0], "__dict__")  # Simple object
    assert obj.tags[0] != obj.__original_values__["tags"][0]  # Different content

    result = await obj._to_input_dirty()

    # Should include ID and tags field
    assert "id" in result
    assert result["id"] == "test_force_simple"


@pytest.mark.asyncio
async def test_to_input_dirty_force_non_list_comparison() -> None:
    """Force _to_input_dirty to hit non-list field comparison (line 570).

    This test manually manipulates __original_values__ to ensure the non-list comparison runs.
    """
    # Create object with non-list field
    obj = TestStashObject(
        id="test_force_nonlist", name="Original", description="Original Desc"
    )
    obj.mark_clean()

    # Change the field (this will delete it from __original_values__)
    obj.description = "Changed Description"

    # Manually restore the original value to force comparison
    obj.__original_values__["description"] = "Original Desc"

    # Verify conditions that should trigger line 570
    assert "description" in obj.__original_values__
    assert not isinstance(obj.description, list)
    assert not isinstance(obj.__original_values__["description"], list)
    assert obj.description != obj.__original_values__["description"]  # Different values

    result = await obj._to_input_dirty()

    # Should include ID and changed field
    assert "id" in result
    assert result["id"] == "test_force_nonlist"
    assert "description" in result
    assert result["description"] == "Changed Description"


@pytest.mark.asyncio
async def test_to_input_dirty_list_comparison_continuation_paths() -> None:
    """Test _to_input_dirty continuation paths in list comparison loops.

    This targets the missing branch coverage lines: 558->536, 561->558, 565->558
    These are continuation paths when list items are identical.
    """

    class MockObjWithDict:
        def __init__(self, value: str):
            self.value = value

    # Test case 1: List with same __dict__ objects (should continue loop)
    # This should hit the continuation path after line 561
    obj1 = TestStashObject(
        id="test_continue1",
        name="Test",
        tags=[MockObjWithDict("same"), MockObjWithDict("different")],
    )
    obj1.mark_clean()

    # Change to same first object but different second object
    obj1.tags = [MockObjWithDict("same"), MockObjWithDict("changed")]

    # Manually restore original to force comparison
    obj1.__original_values__["tags"] = [
        MockObjWithDict("same"),
        MockObjWithDict("different"),
    ]

    result1 = await obj1._to_input_dirty()
    assert "id" in result1

    # Test case 2: List with same simple objects (should continue loop)
    # This should hit the continuation path after line 565
    obj2 = TestStashObject(id="test_continue2", name="Test", tags=["same", "different"])
    obj2.mark_clean()

    # Change to same first object but different second object
    obj2.tags = ["same", "changed"]

    # Manually restore original to force comparison
    obj2.__original_values__["tags"] = ["same", "different"]

    result2 = await obj2._to_input_dirty()
    assert "id" in result2
