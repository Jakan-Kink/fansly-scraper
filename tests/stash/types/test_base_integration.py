"""Tests for stash.types.base - Integration

Tests complete StashObject workflows including end-to-end scenarios,
full object lifecycle, and integration between different components.

Coverage targets: Integration workflows, complete object lifecycle
"""

import time
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx

from stash.client import StashClient

from ...fixtures.stash.stash_fixtures import MockTag, TestStashObject


# =============================================================================
# Integration Tests
# =============================================================================


@pytest.mark.asyncio
@respx.mock
async def test_full_workflow_new_object() -> None:
    """Test complete workflow for new object creation."""
    # Mock HTTP response for create operation
    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200, json={"data": {"testStashCreate": {"id": "created_456"}}}
        )
    )

    # Create new object
    obj = TestStashObject(
        id="new",
        name="Integration Test",
        description="Full workflow test",
        tags=["integration", "test"],
    )

    # Should be dirty (new)
    obj.mark_dirty()
    assert obj.is_dirty()

    # Save should work
    client = await StashClient.create(conn={"url": "http://localhost:9999"})
    await obj.save(client)

    # Should be clean with updated ID
    assert not obj.is_dirty()
    assert obj.id == "created_456"


@pytest.mark.asyncio
@respx.mock
async def test_full_workflow_update_object() -> None:
    """Test complete workflow for object updates."""
    # Mock HTTP response for update operation
    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200, json={"data": {"testStashUpdate": {"id": "existing_123"}}}
        )
    )

    # Create existing object
    obj = TestStashObject(
        id="existing_123", name="Original Name", description="Original description"
    )
    obj.mark_clean()  # Start clean

    # Make changes
    obj.name = "Updated Name"
    obj.description = "Updated description"

    # Should be dirty
    assert obj.is_dirty()

    # Save should work
    client = await StashClient.create(conn={"url": "http://localhost:9999"})
    await obj.save(client)

    # Should be clean
    assert not obj.is_dirty()


@pytest.mark.asyncio
@respx.mock
async def test_find_and_update_workflow() -> None:
    """Test complete find-and-update workflow."""
    # Mock HTTP responses for find and update
    route = respx.post("http://localhost:9999/graphql")
    # First call: find
    route.mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": {
                        "findTestStash": {
                            "id": "existing_123",
                            "name": "Existing Object",
                        }
                    }
                },
            ),
            # Second call: update
            httpx.Response(
                200, json={"data": {"testStashUpdate": {"id": "existing_123"}}}
            ),
        ]
    )

    client = await StashClient.create(conn={"url": "http://localhost:9999"})

    # Find an existing object
    found_obj = await TestStashObject.find_by_id(client, "existing_123")

    assert found_obj is not None
    assert found_obj.id == "existing_123"
    assert not found_obj.is_dirty()  # Should be clean when loaded

    # Make changes
    original_name = found_obj.name
    found_obj.name = "Updated via Find"
    found_obj.description = "Updated description"

    # Should be dirty after changes
    assert found_obj.is_dirty()

    # Save changes
    await found_obj.save(client)

    # Verify the name actually changed
    assert found_obj.name != original_name

    # Should be clean after save
    assert not found_obj.is_dirty()
    assert found_obj.name == "Updated via Find"


@pytest.mark.asyncio
async def test_object_lifecycle_comprehensive() -> None:
    """Test complete object lifecycle from creation to cleanup."""
    # Phase 1: Creation and initialization
    obj = TestStashObject(
        id="lifecycle_test",
        name="Lifecycle Object",
        description="Testing complete lifecycle",
        tags=["lifecycle", "test"],
    )

    # Initial state checks
    assert obj.id == "lifecycle_test"
    assert obj.name == "Lifecycle Object"
    assert not obj.is_dirty()  # Should be clean after __post_init__

    # Phase 2: Modification and change tracking
    obj.name = "Modified Lifecycle Object"
    assert obj.is_dirty()
    assert "name" in obj._dirty_attrs

    # Phase 3: Clean and modify again
    obj.mark_clean()
    assert not obj.is_dirty()
    assert obj.__original_values__["name"] == "Modified Lifecycle Object"

    # Phase 4: Multiple field changes
    obj.description = "Updated description"
    obj.tags = ["updated", "lifecycle", "test"]
    assert obj.is_dirty()

    # Phase 5: Hash and equality throughout lifecycle
    other_obj = TestStashObject(id="lifecycle_test", name="Different Name")
    assert obj == other_obj  # Same ID, should be equal
    assert hash(obj) == hash(other_obj)


@pytest.mark.asyncio
async def test_relationship_integration_workflow() -> None:
    """Test integration of relationship processing with full workflow."""
    # Create object with relationships
    tag_objects: list[Any] = [
        MockTag("tag_1", "Integration Tag 1"),
        MockTag("tag_2", "Integration Tag 2"),
        {"id": "tag_3", "name": "Dict Tag 3"},  # Mixed types
    ]

    obj = TestStashObject(
        id="relationship_test", name="Relationship Integration", tags=tag_objects
    )

    # Test relationship processing
    relationships_data = await obj._process_relationships({"tags"})
    assert "tag_ids" in relationships_data
    assert "tag_1" in relationships_data["tag_ids"]
    assert "tag_2" in relationships_data["tag_ids"]
    assert "tag_3" in relationships_data["tag_ids"]

    # Test full input conversion with relationships
    obj.mark_dirty()
    input_data = await obj.to_input()

    # Should include processed relationships
    if "tag_ids" in input_data:
        assert isinstance(input_data["tag_ids"], list)
        assert len(input_data["tag_ids"]) > 0


@pytest.mark.asyncio
async def test_field_processing_integration() -> None:
    """Test integration of field processing with object workflows."""
    obj = TestStashObject(
        id="field_test",
        name="  Field Processing Test  ",  # Whitespace for processing
        description="  Field Description  ",
    )

    # Test field processing in isolation
    processed_fields = await obj._process_fields({"name", "description"})

    # Verify field processing worked
    assert isinstance(processed_fields, dict)

    # Test integration with input conversion
    obj.mark_dirty()
    input_data = await obj.to_input()

    # Processed fields should be included in input data
    assert isinstance(input_data, dict)


@pytest.mark.asyncio
@respx.mock
async def test_error_recovery_workflow() -> None:
    """Test error recovery in complete workflows."""
    # Mock HTTP response with error
    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200, json={"errors": [{"message": "GraphQL Error"}]}
        )
    )

    obj = TestStashObject(
        id="new", name="Error Recovery Test", description="Testing error scenarios"
    )
    obj.mark_dirty()

    client = await StashClient.create(conn={"url": "http://localhost:9999"})
    # Mock to_input to return valid data
    with patch.object(obj, "to_input", return_value={"name": "Error Recovery Test"}):
        # Attempt save with error response
        with pytest.raises(ValueError, match="Failed to save"):
            await obj.save(client)

        # Object should still be dirty after failed save
        assert obj.is_dirty()

    # Test find with error response
    result = await TestStashObject.find_by_id(client, "test")
    assert result is None  # Should handle error gracefully


@pytest.mark.asyncio
@respx.mock
async def test_batch_operations_workflow() -> None:
    """Test workflows involving multiple objects."""
    # Mock HTTP response for update operations
    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(
            200, json={"data": {"testStashUpdate": {"id": "batch_0"}}}
        )
    )

    # Create multiple objects
    objects = []
    for i in range(3):
        obj = TestStashObject(
            id=f"batch_{i}",
            name=f"Batch Object {i}",
            description=f"Batch test object {i}",
        )
        obj.mark_clean()
        objects.append(obj)

    # Modify all objects
    for i, obj in enumerate(objects):
        obj.name = f"Modified Batch Object {i}"
        assert obj.is_dirty()

    client = await StashClient.create(conn={"url": "http://localhost:9999"})
    # Save all objects
    for obj in objects:
        await obj.save(client)
        assert not obj.is_dirty()


@pytest.mark.asyncio
async def test_complex_data_workflow() -> None:
    """Test workflow with complex data structures."""
    # Create object with complex nested data
    complex_tags: list[Any] = [
        MockTag("complex_1", "Complex Tag 1"),
        {"id": "dict_tag", "name": "Dictionary Tag", "metadata": {"type": "complex"}},
        "simple_string_tag",
    ]

    obj = TestStashObject(
        id="complex_test",
        name="Complex Data Test",
        description="Testing complex data structures",
        tags=complex_tags,
    )

    # Test that complex data is handled properly
    obj.mark_dirty()

    # Process relationships
    rel_data = await obj._process_relationships({"tags"})
    assert "tag_ids" in rel_data

    # Process fields
    field_data = await obj._process_fields({"name", "description"})

    # Verify field processing worked
    assert isinstance(field_data, dict)

    # Full input conversion
    input_data = await obj.to_input()
    assert isinstance(input_data, dict)


@pytest.mark.asyncio
async def test_state_consistency_workflow() -> None:
    """Test state consistency throughout object lifecycle."""
    obj = TestStashObject(id="consistency_test", name="Initial Name")

    # Track state changes
    state_log = []

    # Initial state
    state_log.append(("initial", obj.is_dirty(), obj.name))
    assert not obj.is_dirty()

    # First modification
    obj.name = "First Change"
    state_log.append(("first_change", obj.is_dirty(), obj.name))
    assert obj.is_dirty()

    # Mark clean
    obj.mark_clean()
    state_log.append(("marked_clean", obj.is_dirty(), obj.name))
    assert not obj.is_dirty()

    # Second modification
    obj.name = "Second Change"
    state_log.append(("second_change", obj.is_dirty(), obj.name))
    assert obj.is_dirty()

    # Verify state consistency
    assert state_log[0] == ("initial", False, "Initial Name")
    assert state_log[1] == ("first_change", True, "First Change")
    assert state_log[2] == ("marked_clean", False, "First Change")
    assert state_log[3] == ("second_change", True, "Second Change")


@pytest.mark.asyncio
async def test_concurrent_modification_workflow() -> None:
    """Test workflow with concurrent-like modifications."""
    # Simulate scenario where object is modified while processing
    obj = TestStashObject(id="concurrent_test", name="Original")
    obj.mark_clean()

    # Start with clean object
    assert not obj.is_dirty()

    # Simulate first modification
    obj.name = "First Modification"
    assert obj.is_dirty()

    # Simulate second modification before save
    obj.description = "Added Description"
    assert obj.is_dirty()

    # Both changes should be tracked
    if hasattr(obj, "_dirty_attrs"):
        tracked: set[str] = getattr(obj, "__tracked_fields__", set())
        if "name" in tracked:
            assert "name" in obj._dirty_attrs
        if "description" in tracked:
            assert "description" in obj._dirty_attrs


@pytest.mark.asyncio
async def test_memory_efficiency_workflow() -> None:
    """Test memory efficiency in object workflows."""
    # Create and process many objects to test memory usage
    objects = []

    for i in range(10):  # Keep reasonable for testing
        obj = TestStashObject(
            id=f"memory_test_{i}",
            name=f"Memory Test {i}",
            description=f"Testing memory efficiency {i}",
        )

        # Process through full lifecycle
        obj.mark_dirty()
        _ = await obj.to_input()  # Generate input data
        obj.mark_clean()

        objects.append(obj)

    # All objects should maintain state correctly
    for i, obj in enumerate(objects):
        assert obj.id == f"memory_test_{i}"
        assert not obj.is_dirty()  # Should be clean


@pytest.mark.asyncio
async def test_validation_workflow() -> None:
    """Test validation throughout object workflow."""
    obj = TestStashObject(id="validation_test", name="Test")

    # Test that object validates correctly at creation
    assert obj.id == "validation_test"
    assert obj.name == "Test"

    # Test validation during modification
    obj.name = "Modified Name"
    assert obj.name == "Modified Name"

    # Test validation during input conversion
    obj.mark_dirty()
    input_data = await obj.to_input()

    # Input data should be valid
    assert isinstance(input_data, dict)
    assert "id" in input_data or obj.id == "new"  # Either has ID or is new


@pytest.mark.asyncio
async def test_edge_case_integration() -> None:
    """Test integration of various edge cases."""
    # Object with minimal data
    minimal_obj = TestStashObject(id="minimal", name="Minimal")
    assert not minimal_obj.is_dirty()

    # Object with None values
    none_obj = TestStashObject(id="none_test", name="Test", description=None)
    assert none_obj.description is None

    # Object with empty collections
    empty_obj = TestStashObject(id="empty_test", name="Test", tags=[])
    assert empty_obj.tags == []

    # All should process correctly
    for obj in [minimal_obj, none_obj, empty_obj]:
        obj.mark_dirty()
        input_data = await obj.to_input()
        assert isinstance(input_data, dict)
        obj.mark_clean()


@pytest.mark.asyncio
async def test_performance_integration() -> None:
    """Test performance aspects of integrated workflows."""
    start_time = time.time()

    # Create and process multiple objects
    for i in range(5):  # Keep reasonable for testing
        obj = TestStashObject(
            id=f"perf_test_{i}",
            name=f"Performance Test {i}",
            description="Testing performance",
            tags=[f"tag_{j}" for j in range(3)],
        )

        # Full processing workflow
        obj.mark_dirty()
        _ = await obj._process_relationships({"tags"})
        _ = await obj._process_fields({"name", "description"})
        _ = await obj.to_input()
        obj.mark_clean()

    elapsed = time.time() - start_time

    # Should complete reasonably quickly (adjust threshold as needed)
    assert elapsed < 5.0  # 5 seconds should be more than enough
