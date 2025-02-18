"""Unit tests for base Stash types."""

from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import strawberry
from strawberry import ID

from stash.types.base import BulkUpdateIds, BulkUpdateStrings, StashObject
from stash.types.enums import BulkUpdateIdMode


def test_bulk_update_strings() -> None:
    """Test BulkUpdateStrings input type."""
    # Test creation
    bulk_update = BulkUpdateStrings(
        values=["test1", "test2"],
        mode=BulkUpdateIdMode.SET,
    )
    assert bulk_update.values == ["test1", "test2"]
    assert bulk_update.mode == BulkUpdateIdMode.SET

    # Test strawberry type
    assert isinstance(
        BulkUpdateStrings.__strawberry_definition__, strawberry.type.TypeDefinition
    )
    assert BulkUpdateStrings.__strawberry_definition__.is_input is True

    # Test field types
    fields = {
        f.name: f.type for f in BulkUpdateStrings.__strawberry_definition__.fields
    }
    assert "values" in fields
    assert "mode" in fields
    assert str(fields["values"]) == "List[str]"
    assert str(fields["mode"]) == "BulkUpdateIdMode"


def test_bulk_update_ids() -> None:
    """Test BulkUpdateIds input type."""
    # Test creation
    bulk_update = BulkUpdateIds(
        ids=[ID("1"), ID("2")],
        mode=BulkUpdateIdMode.ADD,
    )
    assert bulk_update.ids == [ID("1"), ID("2")]
    assert bulk_update.mode == BulkUpdateIdMode.ADD

    # Test strawberry type
    assert isinstance(
        BulkUpdateIds.__strawberry_definition__, strawberry.type.TypeDefinition
    )
    assert BulkUpdateIds.__strawberry_definition__.is_input is True

    # Test field types
    fields = {f.name: f.type for f in BulkUpdateIds.__strawberry_definition__.fields}
    assert "ids" in fields
    assert "mode" in fields
    assert str(fields["ids"]) == "List[ID]"
    assert str(fields["mode"]) == "BulkUpdateIdMode"


# Test implementation
@strawberry.type
class TestObject(StashObject):
    """Test implementation of StashObject."""

    __type_name__: ClassVar[str] = "TestObject"
    name: str
    description: str | None = None

    async def to_input(self) -> dict[str, Any]:
        """Convert to input type."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
        }


@pytest.mark.asyncio
async def test_stash_object_find_by_id() -> None:
    """Test StashObject.find_by_id."""
    # Mock client
    mock_client = AsyncMock()
    mock_client.execute = AsyncMock(
        return_value={
            "findTestObject": {
                "id": "1",
                "name": "Test",
                "description": "Test description",
            }
        }
    )

    # Test successful find
    obj = await TestObject.find_by_id(mock_client, "1")
    assert obj is not None
    assert obj.id == "1"
    assert obj.name == "Test"
    assert obj.description == "Test description"

    # Verify query
    call_args = mock_client.execute.call_args
    assert call_args is not None
    query, variables = call_args[0]
    assert "findTestObject" in query
    assert variables == {"id": "1"}

    # Test not found
    mock_client.execute = AsyncMock(return_value={"findTestObject": None})
    obj = await TestObject.find_by_id(mock_client, "2")
    assert obj is None

    # Test error handling
    mock_client.execute = AsyncMock(side_effect=ValueError("Test error"))
    obj = await TestObject.find_by_id(mock_client, "3")
    assert obj is None


@pytest.mark.asyncio
async def test_stash_object_save_create() -> None:
    """Test StashObject.save for creating new objects."""
    # Mock client
    mock_client = AsyncMock()
    mock_client.execute = AsyncMock(
        return_value={
            "testObjectCreate": {
                "id": "1",
            }
        }
    )

    # Test create
    obj = TestObject(id="new", name="Test", description="Test description")
    await obj.save(mock_client)

    # Verify mutation
    call_args = mock_client.execute.call_args
    assert call_args is not None
    mutation, variables = call_args[0]
    assert "testObjectCreate" in mutation
    assert variables == {
        "input": {
            "id": "new",
            "name": "Test",
            "description": "Test description",
        }
    }

    # Verify ID was updated
    assert obj.id == "1"


@pytest.mark.asyncio
async def test_stash_object_save_update() -> None:
    """Test StashObject.save for updating existing objects."""
    # Mock client
    mock_client = AsyncMock()
    mock_client.execute = AsyncMock(
        return_value={
            "testObjectUpdate": {
                "id": "1",
            }
        }
    )

    # Test update
    obj = TestObject(id="1", name="Test", description="Updated description")
    await obj.save(mock_client)

    # Verify mutation
    call_args = mock_client.execute.call_args
    assert call_args is not None
    mutation, variables = call_args[0]
    assert "testObjectUpdate" in mutation
    assert variables == {
        "input": {
            "id": "1",
            "name": "Test",
            "description": "Updated description",
        }
    }


@pytest.mark.asyncio
async def test_stash_object_save_error() -> None:
    """Test StashObject.save error handling."""
    # Mock client
    mock_client = AsyncMock()
    mock_client.execute = AsyncMock(side_effect=ValueError("Test error"))

    # Test error
    obj = TestObject(id="1", name="Test")
    with pytest.raises(ValueError, match="Failed to save TestObject: Test error"):
        await obj.save(mock_client)


def test_stash_object_hash_and_equality() -> None:
    """Test StashObject hash and equality."""
    # Create test objects
    obj1 = TestObject(id="1", name="Test 1")
    obj2 = TestObject(id="1", name="Test 1 with different name")
    obj3 = TestObject(id="2", name="Test 1")

    # Test hash
    assert hash(obj1) == hash(obj2)  # Same ID
    assert hash(obj1) != hash(obj3)  # Different ID

    # Test equality
    assert obj1 == obj2  # Same ID
    assert obj1 != obj3  # Different ID
    assert obj1 != "not an object"  # Different type


def test_stash_object_field_names() -> None:
    """Test StashObject field name handling."""
    # Test field name caching
    field_names = TestObject._get_field_names()
    assert isinstance(field_names, set)
    assert "id" in field_names
    assert "name" in field_names
    assert "description" in field_names

    # Test field name reuse (cached)
    field_names2 = TestObject._get_field_names()
    assert field_names is field_names2  # Should be same instance (cached)


@pytest.mark.asyncio
async def test_stash_object_to_input_with_coroutines() -> None:
    """Test StashObject.save with coroutine values."""

    @strawberry.type
    class TestObjectWithCoroutines(StashObject):
        """Test implementation with coroutine values."""

        __type_name__: ClassVar[str] = "TestObject"
        name: str

        async def to_input(self) -> dict[str, Any]:
            """Convert to input type with coroutine values."""

            async def get_value() -> str:
                return "async value"

            return {
                "id": self.id,
                "name": self.name,
                "async_field": get_value(),
                "list_field": [get_value(), "normal value", get_value()],
            }

    # Mock client
    mock_client = AsyncMock()
    mock_client.execute = AsyncMock(
        return_value={
            "testObjectCreate": {
                "id": "1",
            }
        }
    )

    # Test save with coroutine values
    obj = TestObjectWithCoroutines(id="new", name="Test")
    await obj.save(mock_client)

    # Verify mutation
    call_args = mock_client.execute.call_args
    assert call_args is not None
    mutation, variables = call_args[0]
    assert "testObjectCreate" in mutation
    assert variables == {
        "input": {
            "id": "new",
            "name": "Test",
            "async_field": "async value",
            "list_field": ["async value", "normal value", "async value"],
        }
    }
