"""Tests for stash.types.base - Hash and Equality

Tests StashObject hash and equality methods including __hash__, __eq__,
and object comparison logic.

Coverage targets: Lines 580, 591-593 (__hash__, __eq__ methods)
"""

import pytest

from stash.types.base import StashObject

from ...fixtures.stash_fixtures import TestStashObject, TestStashObjectNoCreate

# =============================================================================
# Hash and Equality Tests (Lines 580, 591-593)
# =============================================================================


@pytest.mark.unit
def test_stash_object_hash(test_stash_object: TestStashObject) -> None:
    """Test StashObject __hash__ method (Line 580)."""
    hash_value = hash(test_stash_object)
    expected_hash = hash(("TestStash", "test_123"))
    assert hash_value == expected_hash


@pytest.mark.unit
def test_stash_object_equality(test_stash_object: TestStashObject) -> None:
    """Test StashObject __eq__ method (Lines 591-593)."""
    # Create another object with same type and ID
    other = TestStashObject(id="test_123", name="Different Name")
    assert test_stash_object == other

    # Different ID should not be equal
    different_id = TestStashObject(id="different_123", name="Test Object")
    assert test_stash_object != different_id

    # Different type should not be equal
    different_type = TestStashObjectNoCreate(id="test_123", name="Test")
    assert test_stash_object != different_type

    # Non-StashObject should return NotImplemented
    assert test_stash_object.__eq__("not_a_stash_object") == NotImplemented


@pytest.mark.unit
def test_stash_object_equality_not_implemented() -> None:
    """Test __eq__ method NotImplemented return (Lines 600-602)."""
    obj = TestStashObject(id="test", name="Test")

    # Test equality with non-StashObject type
    result = obj.__eq__("not_a_stash_object")

    # Should return NotImplemented, not False
    assert result is NotImplemented

    # Test with other types
    assert obj.__eq__(123) is NotImplemented
    assert obj.__eq__([]) is NotImplemented
    assert obj.__eq__({}) is NotImplemented


@pytest.mark.unit
def test_real_eq_not_implemented_600_602() -> None:
    """Test real execution of lines 600-602 (__eq__ NotImplemented)."""

    obj = TestStashObject(id="test", name="Test")

    # These should hit the real lines 600-602 in StashObject.__eq__
    assert obj.__eq__("string") is NotImplemented
    assert obj.__eq__(123) is NotImplemented
    assert obj.__eq__([]) is NotImplemented
    assert obj.__eq__({}) is NotImplemented
    assert obj.__eq__(None) is NotImplemented


@pytest.mark.unit
def test_hash_consistency() -> None:
    """Test that hash is consistent across object instances."""
    obj1 = TestStashObject(id="consistent_test", name="Test 1")
    obj2 = TestStashObject(
        id="consistent_test", name="Test 2"
    )  # Different name, same ID

    # Should have same hash since they have same type and ID
    assert hash(obj1) == hash(obj2)

    # Hash should be based on type name and ID only
    expected_hash = hash(("TestStash", "consistent_test"))
    assert hash(obj1) == expected_hash
    assert hash(obj2) == expected_hash


@pytest.mark.unit
def test_equality_edge_cases() -> None:
    """Test equality edge cases."""
    obj = TestStashObject(id="edge_test", name="Test")

    # Test equality with itself
    assert obj == obj

    # Test equality with None
    assert obj is not None
    assert obj.__eq__(None) is NotImplemented

    # Test equality with similar but different objects
    similar_obj = TestStashObject(
        id="edge_test", name="Different Name", description="Different"
    )
    assert obj == similar_obj  # Same type and ID, so should be equal

    # Test with empty/None ID
    # Create objects with valid IDs first, then set to None to test the equality method
    obj_none_id = TestStashObject(id="temp1", name="Test")
    obj_none_id.id = None  # type: ignore  # Intentionally setting to None for testing
    obj_none_id2 = TestStashObject(id="temp2", name="Test")
    obj_none_id2.id = None  # type: ignore  # Intentionally setting to None for testing

    # Objects with None ID should still follow same equality rules
    assert obj_none_id == obj_none_id2  # Same type, same (None) ID


@pytest.mark.unit
def test_hash_with_different_ids() -> None:
    """Test hash behavior with different IDs."""
    obj1 = TestStashObject(id="id_1", name="Test")
    obj2 = TestStashObject(id="id_2", name="Test")

    # Different IDs should result in different hashes
    assert hash(obj1) != hash(obj2)

    # Verify the hash calculation
    assert hash(obj1) == hash(("TestStash", "id_1"))
    assert hash(obj2) == hash(("TestStash", "id_2"))


@pytest.mark.unit
def test_hash_with_different_types() -> None:
    """Test hash behavior with different object types."""
    obj1 = TestStashObject(id="same_id", name="Test")
    obj2 = TestStashObjectNoCreate(id="same_id", name="Test")

    # Different types should result in different hashes even with same ID
    assert hash(obj1) != hash(obj2)

    # Verify the hash calculation includes type name
    assert hash(obj1) == hash(("TestStash", "same_id"))
    assert hash(obj2) == hash(("TestStashNoCreate", "same_id"))


@pytest.mark.unit
def test_equality_transitivity() -> None:
    """Test that equality is transitive."""
    obj1 = TestStashObject(id="transitive_test", name="Name 1")
    obj2 = TestStashObject(id="transitive_test", name="Name 2")
    obj3 = TestStashObject(id="transitive_test", name="Name 3")

    # If a == b and b == c, then a == c
    assert obj1 == obj2
    assert obj2 == obj3
    assert obj1 == obj3


@pytest.mark.unit
def test_equality_symmetry() -> None:
    """Test that equality is symmetric."""
    obj1 = TestStashObject(id="symmetric_test", name="Name 1")
    obj2 = TestStashObject(id="symmetric_test", name="Name 2")

    # If a == b, then b == a
    assert obj1 == obj2
    assert obj2 == obj1


@pytest.mark.unit
def test_equality_reflexivity() -> None:
    """Test that equality is reflexive."""
    obj = TestStashObject(id="reflexive_test", name="Test")

    # a == a should always be True
    assert obj == obj


@pytest.mark.unit
def test_hash_immutability() -> None:
    """Test that hash doesn't change when non-key attributes change."""
    obj = TestStashObject(id="immutable_test", name="Original Name")
    original_hash = hash(obj)

    # Change non-key attributes
    obj.name = "Changed Name"
    obj.description = "New Description"

    # Hash should remain the same since it's based on type and ID only
    assert hash(obj) == original_hash


@pytest.mark.unit
def test_hash_with_string_ids() -> None:
    """Test hash behavior with string IDs."""
    obj1 = TestStashObject(id="string_id_123", name="Test")
    obj2 = TestStashObject(id="string_id_456", name="Test")

    # Different string IDs should produce different hashes
    assert hash(obj1) != hash(obj2)

    # Should handle string IDs properly
    assert hash(obj1) == hash(("TestStash", "string_id_123"))
    assert hash(obj2) == hash(("TestStash", "string_id_456"))


@pytest.mark.unit
def test_equality_with_subclasses() -> None:
    """Test equality behavior with subclasses."""
    base_obj = TestStashObject(id="subclass_test", name="Base")

    # Create a subclass
    class TestStashSubclass(TestStashObject):
        pass

    sub_obj = TestStashSubclass(id="subclass_test", name="Sub")

    # Should be equal since they inherit the same __type_name__ and have same ID
    # The equality is based on (__type_name__, id) tuple
    assert base_obj == sub_obj
    assert sub_obj == base_obj

    # Should have same hashes since they're considered equal
    assert hash(base_obj) == hash(sub_obj)


@pytest.mark.unit
def test_equality_type_checking() -> None:
    """Test that equality properly checks types."""
    obj = TestStashObject(id="type_test", name="Test")

    # Test with objects that might have similar attributes but wrong type
    class FakeStashObject:
        def __init__(self, id, name):
            self.id = id
            self.name = name
            self.__type_name__ = "TestStash"  # Even with same type name

    fake_obj = FakeStashObject("type_test", "Test")

    # Should not be equal to fake object
    assert obj != fake_obj
    assert obj.__eq__(fake_obj) is NotImplemented


@pytest.mark.unit
def test_hash_and_equality_in_collections() -> None:
    """Test hash and equality behavior in collections."""
    obj1 = TestStashObject(id="collection_test", name="Test 1")
    obj2 = TestStashObject(
        id="collection_test", name="Test 2"
    )  # Same ID, different name
    obj3 = TestStashObject(id="different_id", name="Test 3")

    # Test in set - obj1 and obj2 should be treated as same due to equal hash/equality
    test_set = {obj1, obj2, obj3}
    assert len(test_set) == 2  # obj1 and obj2 collapse to one item

    # Test in dict as keys
    test_dict = {obj1: "value1", obj2: "value2", obj3: "value3"}
    assert len(test_dict) == 2  # obj1 and obj2 use same key

    # obj2 should overwrite obj1's value
    assert test_dict[obj1] == "value2"  # Latest value wins
    assert test_dict[obj2] == "value2"


@pytest.mark.unit
def test_error_edge_cases() -> None:
    """Test various error and edge case scenarios."""
    # Test with minimal object
    obj = TestStashObject(id="minimal", name="Minimal")

    # Test hash and equality
    assert hash(obj) == hash(("TestStash", "minimal"))

    # Test with None values
    obj_with_nones = TestStashObject(
        id="none_test", name="Test", description=None, tags=None
    )
    assert obj_with_nones.id == "none_test"
    assert obj_with_nones.description is None

    # Hash should still work with None description/tags
    expected_hash = hash(("TestStash", "none_test"))
    assert hash(obj_with_nones) == expected_hash


@pytest.mark.unit
def test_performance_considerations() -> None:
    """Test performance-related aspects of hash and equality."""
    # Create many objects with same ID to test hash consistency
    objects = [TestStashObject(id="perf_test", name=f"Test {i}") for i in range(100)]

    # All should have same hash
    expected_hash = hash(("TestStash", "perf_test"))
    for obj in objects:
        assert hash(obj) == expected_hash

    # All should be equal to each other
    for i in range(len(objects)):
        for j in range(i + 1, len(objects)):
            assert objects[i] == objects[j]


@pytest.mark.unit
def test_hash_stability() -> None:
    """Test that hash values are stable across object lifecycle."""
    obj = TestStashObject(id="stable_test", name="Test")

    # Get initial hash
    initial_hash = hash(obj)

    # Perform various operations that shouldn't change hash
    obj.mark_dirty()
    assert hash(obj) == initial_hash

    obj.mark_clean()
    assert hash(obj) == initial_hash

    obj.name = "Changed Name"
    assert hash(obj) == initial_hash

    obj.description = "New Description"
    assert hash(obj) == initial_hash

    # Hash should remain stable throughout object lifecycle
    assert hash(obj) == initial_hash


# =============================================================================
# Direct Method Call Tests (To ensure coverage of StashObject methods)
# =============================================================================


@pytest.mark.unit
def test_direct_stash_object_hash_method() -> None:
    """Test StashObject.__hash__ method directly (Line 596)."""
    obj = TestStashObject(id="direct_hash_test", name="Test")

    # Call the StashObject.__hash__ method directly to ensure coverage
    hash_value = StashObject.__hash__(obj)
    expected_hash = hash(("TestStash", "direct_hash_test"))
    assert hash_value == expected_hash

    # Also test with different values
    obj2 = TestStashObject(id="different_id", name="Test")
    hash_value2 = StashObject.__hash__(obj2)
    expected_hash2 = hash(("TestStash", "different_id"))
    assert hash_value2 == expected_hash2
    assert hash_value != hash_value2


@pytest.mark.unit
def test_direct_stash_object_eq_method() -> None:
    """Test StashObject.__eq__ method directly (Lines 607-609)."""
    obj1 = TestStashObject(id="direct_eq_test", name="Test 1")
    obj2 = TestStashObject(
        id="direct_eq_test", name="Test 2"
    )  # Same ID, different name
    obj3 = TestStashObject(id="different_id", name="Test 1")
    obj4 = TestStashObjectNoCreate(id="direct_eq_test", name="Test 1")  # Different type

    # Call the StashObject.__eq__ method directly to ensure coverage
    # Same type and ID should be equal
    assert StashObject.__eq__(obj1, obj2) is True

    # Different ID should not be equal
    assert StashObject.__eq__(obj1, obj3) is False

    # Different type should not be equal
    assert StashObject.__eq__(obj1, obj4) is False

    # Non-StashObject should return NotImplemented
    assert StashObject.__eq__(obj1, "not_a_stash_object") is NotImplemented
    assert StashObject.__eq__(obj1, 123) is NotImplemented
    assert StashObject.__eq__(obj1, []) is NotImplemented
    assert StashObject.__eq__(obj1, {}) is NotImplemented
    assert StashObject.__eq__(obj1, None) is NotImplemented


@pytest.mark.unit
def test_direct_methods_with_special_ids() -> None:
    """Test direct method calls with special ID values."""
    # Test with None ID
    obj_none = TestStashObject(id="temp", name="Test")
    obj_none.id = None  # type: ignore  # Intentionally setting to None for testing

    # Test hash with None ID
    hash_none = StashObject.__hash__(obj_none)
    expected_hash_none = hash(("TestStash", None))
    assert hash_none == expected_hash_none

    # Test equality with None ID
    obj_none2 = TestStashObject(id="temp2", name="Test")
    obj_none2.id = None  # type: ignore  # Intentionally setting to None for testing
    assert StashObject.__eq__(obj_none, obj_none2) is True

    # Test with empty string ID
    obj_empty = TestStashObject(id="", name="Test")
    hash_empty = StashObject.__hash__(obj_empty)
    expected_hash_empty = hash(("TestStash", ""))
    assert hash_empty == expected_hash_empty
