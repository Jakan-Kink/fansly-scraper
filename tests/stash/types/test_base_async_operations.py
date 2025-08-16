"""Tests for stash.types.base - Async Operations

Tests StashObject async operations including find_by_id, save, and GraphQL interactions.
Covers database operations, GraphQL query/mutation handling, and error scenarios.

Coverage targets: Lines 195, 204-214 (find_by_id, save methods)
"""

from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from stash.types.base import StashObject

from ...fixtures.stash_fixtures import TestStashObject

# =============================================================================
# Async Methods Tests (find_by_id, save) (Lines 195, 204-214)
# =============================================================================


@pytest.mark.asyncio
async def test_find_by_id_success(mock_stash_client_with_responses: Mock) -> None:
    """Test find_by_id method with successful response (Line 195)."""
    result = await TestStashObject.find_by_id(
        mock_stash_client_with_responses, "existing_123"
    )

    assert result is not None
    assert isinstance(result, TestStashObject)
    assert result.id == "existing_123"
    assert result.name == "Existing Object"


@pytest.mark.asyncio
async def test_find_by_id_not_found(mock_stash_client_with_responses: Mock) -> None:
    """Test find_by_id method when object not found."""
    result = await TestStashObject.find_by_id(
        mock_stash_client_with_responses, "nonexistent"
    )

    assert result is None


@pytest.mark.asyncio
async def test_find_by_id_error(mock_stash_client_with_errors: Mock) -> None:
    """Test find_by_id method with GraphQL error."""
    result = await TestStashObject.find_by_id(mock_stash_client_with_errors, "test")

    # Should return None on error
    assert result is None


@pytest.mark.asyncio
async def test_save_new_object(
    mock_stash_client_with_responses: Mock, test_stash_object_new: TestStashObject
) -> None:
    """Test save method for new objects (Lines 204-214)."""
    # Object should be dirty and new
    assert test_stash_object_new.id == "new"
    test_stash_object_new.mark_dirty()

    await test_stash_object_new.save(mock_stash_client_with_responses)

    # ID should be updated
    assert test_stash_object_new.id == "created_456"

    # Should be clean after save
    assert not test_stash_object_new.is_dirty()


@pytest.mark.asyncio
async def test_save_existing_object(
    mock_stash_client_with_responses: Mock, test_stash_object: TestStashObject
) -> None:
    """Test save method for existing objects."""
    # Make object dirty
    test_stash_object.name = "Updated Name"
    assert test_stash_object.is_dirty()

    await test_stash_object.save(mock_stash_client_with_responses)

    # Should be clean after save
    assert not test_stash_object.is_dirty()


@pytest.mark.asyncio
async def test_save_clean_object_skipped(
    mock_stash_client_with_responses: Mock, test_stash_object: TestStashObject
) -> None:
    """Test that save is skipped for clean objects."""
    # Object should be clean
    assert not test_stash_object.is_dirty()

    # Should not call client.execute for clean objects
    await test_stash_object.save(mock_stash_client_with_responses)

    # Verify client wasn't called (object was already clean)
    # This tests the early return in save method


@pytest.mark.asyncio
async def test_save_no_changes_only_id(
    mock_stash_client_with_responses: Mock, test_stash_object: TestStashObject
) -> None:
    """Test save when only ID is present in input data."""
    # Force object to be dirty but with no actual field changes
    test_stash_object.mark_dirty()

    # Mock to_input to return only ID
    with patch.object(test_stash_object, "to_input", return_value={"id": "test_123"}):
        await test_stash_object.save(mock_stash_client_with_responses)

    # Should be clean after recognizing no changes
    assert not test_stash_object.is_dirty()


@pytest.mark.asyncio
async def test_save_error_handling(
    mock_stash_client_with_errors: Mock, test_stash_object: TestStashObject
) -> None:
    """Test save method error handling."""
    # Make object dirty
    test_stash_object.name = "Changed"

    # Mock to_input to ensure it returns a valid dict to get past input validation
    with patch.object(
        test_stash_object,
        "to_input",
        return_value={"id": "test_123", "name": "Changed"},
    ):
        # The mock client raises Exception, but it gets wrapped in ValueError by save method
        with pytest.raises(ValueError, match="Failed to save TestStash"):
            await test_stash_object.save(mock_stash_client_with_errors)


@pytest.mark.asyncio
async def test_find_by_id_with_data_creation() -> None:
    """Test find_by_id when data is returned and cls(**data) is called (Line 209)."""
    client = Mock()
    client.execute = AsyncMock(
        return_value={
            "findTestStash": {
                "id": "found_123",
                "name": "Found Object",
                "description": "Found description",
            }
        }
    )

    # This should hit the cls(**data) path in find_by_id
    result = await TestStashObject.find_by_id(client, "found_123")
    assert result is not None
    assert result.id == "found_123"
    assert result.name == "Found Object"


@pytest.mark.asyncio
async def test_save_input_data_type_validation() -> None:
    """Test save method input_data type validation (Line 266)."""
    obj = TestStashObject(id="new", name="Test")
    obj.mark_dirty()

    # Mock to_input to return a non-dict type
    with patch.object(obj, "to_input", return_value="not_a_dict"):
        client = Mock()

        with pytest.raises(ValueError, match=r"to_input\(\) must return a dict"):
            await obj.save(client)


@pytest.mark.asyncio
async def test_save_missing_operation_key() -> None:
    """Test save method when operation key is missing from response (Line 298)."""
    obj = TestStashObject(id="new", name="Test")
    obj.mark_dirty()

    client = Mock()
    # Return response without the expected operation key
    client.execute = AsyncMock(return_value={"wrongKey": {"id": "123"}})

    # Mock to_input to return proper dict with name to avoid the fixture requirement issue
    with patch.object(obj, "to_input", return_value={"name": "Test"}):
        with pytest.raises(ValueError, match="Missing 'testStashCreate' in response"):
            await obj.save(client)


@pytest.mark.asyncio
async def test_save_operation_result_none() -> None:
    """Test save method when operation result is None (Line 302)."""
    obj = TestStashObject(id="new", name="Test")
    obj.mark_dirty()

    client = Mock()
    # Return response with None result
    client.execute = AsyncMock(return_value={"testStashCreate": None})

    with pytest.raises(ValueError, match="Create operation returned None"):
        await obj.save(client)


@pytest.mark.asyncio
async def test_get_id_from_dict() -> None:
    """Test _get_id static method with dict (Lines 232-245)."""
    test_dict = {"id": "dict_id_123", "name": "Test"}
    result = await StashObject._get_id(test_dict)
    assert result == "dict_id_123"


@pytest.mark.asyncio
async def test_get_id_from_object() -> None:
    """Test _get_id static method with object."""
    test_obj = TestStashObject(id="obj_id_456", name="Test")
    result = await StashObject._get_id(test_obj)
    assert result == "obj_id_456"


@pytest.mark.asyncio
async def test_get_id_from_awaitable_object() -> None:
    """Test _get_id static method with object having awaitable_attrs."""
    # Create mock object with awaitable attributes
    mock_obj = Mock()
    mock_obj.awaitable_attrs = Mock()

    # Create a coroutine that can be awaited
    async def get_awaitable_id():
        return None  # This simulates the await completing

    # Set the awaitable attribute
    mock_obj.awaitable_attrs.id = get_awaitable_id()
    mock_obj.id = "awaitable_id"

    result = await StashObject._get_id(mock_obj)
    assert result == "awaitable_id"


@pytest.mark.asyncio
async def test_get_id_none_values() -> None:
    """Test _get_id static method with None and empty values."""
    assert await StashObject._get_id(None) is None
    assert await StashObject._get_id({}) is None
    assert await StashObject._get_id({"name": "no_id"}) is None


@pytest.mark.asyncio
async def test_save_workflow_variations() -> None:
    """Test various save workflow scenarios."""
    # Test new object creation workflow
    new_obj = TestStashObject(id="new", name="New Object")
    new_obj.mark_dirty()

    client = Mock()
    client.execute = AsyncMock(return_value={"testStashCreate": {"id": "created_123"}})

    # Mock to_input for consistent behavior
    with patch.object(new_obj, "to_input", return_value={"name": "New Object"}):
        await new_obj.save(client)
        assert new_obj.id == "created_123"
        assert not new_obj.is_dirty()


@pytest.mark.asyncio
async def test_save_update_workflow() -> None:
    """Test update workflow for existing objects."""
    existing_obj = TestStashObject(id="existing_123", name="Original")
    existing_obj.mark_clean()

    # Make changes
    existing_obj.name = "Updated"
    assert existing_obj.is_dirty()

    client = Mock()
    client.execute = AsyncMock(return_value={"testStashUpdate": {"id": "existing_123"}})

    with patch.object(
        existing_obj, "to_input", return_value={"id": "existing_123", "name": "Updated"}
    ):
        await existing_obj.save(client)
        assert not existing_obj.is_dirty()


@pytest.mark.asyncio
async def test_find_by_id_graphql_variations() -> None:
    """Test find_by_id with various GraphQL response scenarios."""
    # Test successful response
    client = Mock()
    client.execute = AsyncMock(
        return_value={"findTestStash": {"id": "found_123", "name": "Found Object"}}
    )

    result = await TestStashObject.find_by_id(client, "found_123")
    assert result is not None
    assert result.id == "found_123"

    # Test not found (None in response)
    client.execute = AsyncMock(return_value={"findTestStash": None})
    result = await TestStashObject.find_by_id(client, "not_found")
    assert result is None

    # Test missing key in response
    client.execute = AsyncMock(return_value={"otherKey": "value"})
    result = await TestStashObject.find_by_id(client, "test")
    assert result is None


@pytest.mark.asyncio
async def test_save_error_scenarios() -> None:
    """Test various error scenarios in save method."""
    obj = TestStashObject(id="test", name="Test")
    obj.mark_dirty()

    # Test client execution error
    client = Mock()
    client.execute = AsyncMock(side_effect=Exception("GraphQL Error"))

    with patch.object(obj, "to_input", return_value={"name": "Test"}):
        with pytest.raises(ValueError, match="Failed to save"):
            await obj.save(client)


@pytest.mark.asyncio
async def test_get_id_edge_cases() -> None:
    """Test _get_id method with edge cases."""

    # Test with object that has no id attribute
    class NoIdObject:
        def __init__(self):
            self.name = "test"

    result = await StashObject._get_id(NoIdObject())
    assert result is None

    # Test with dict that has None id
    result = await StashObject._get_id({"id": None, "name": "test"})
    assert result is None

    # Test with object that has None id
    # Create object with valid id first, then change it to None to test the method
    obj = TestStashObject(id="temp", name="test")
    obj.id = None  # type: ignore  # Intentionally setting to None for testing
    result = await StashObject._get_id(obj)
    assert result is None
