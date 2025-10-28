"""Fixtures for testing Stash GraphQL types and interactions.

This module provides test fixtures for the Stash integration components,
including mock objects, sample data, and factory functions for testing
StashObject implementations and GraphQL operations.
"""

import gc
from typing import Any, ClassVar
from unittest.mock import AsyncMock, Mock

import pytest
import strawberry
from strawberry import ID

from stash.types.base import StashObject
from stash.types.enums import BulkUpdateIdMode


# =============================================================================
# Test Isolation Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_stash_field_names_cache():
    """Reset dynamically generated __field_names__ cache while preserving ClassVar definitions.

    This prevents cross-test contamination while keeping manually defined field names.
    Applied automatically to all tests that import stash fixtures.
    """
    # Store classes with manually defined __field_names__ ClassVar to preserve them
    preserved_field_names = {}

    for obj in gc.get_objects():
        try:
            if isinstance(obj, type) and issubclass(obj, StashObject):
                try:
                    # Only preserve __field_names__ if it's defined directly in this class
                    # (not inherited) and is a ClassVar definition
                    if "__field_names__" in obj.__dict__:
                        preserved_field_names[obj] = obj.__field_names__
                    elif hasattr(obj, "__field_names__"):
                        # This is likely dynamically generated or inherited, safe to clear
                        delattr(obj, "__field_names__")
                except (AttributeError, TypeError):
                    continue
        except ReferenceError:
            # Object was garbage collected, skip it
            continue

    yield

    # After test: restore preserved field names and clear any new dynamic ones
    for obj in gc.get_objects():
        try:
            if isinstance(obj, type) and issubclass(obj, StashObject):
                try:
                    # Restore preserved manually defined field names
                    if obj in preserved_field_names:
                        obj.__field_names__ = preserved_field_names[obj]
                    # Clear dynamically generated ones that weren't preserved
                    elif (
                        hasattr(obj, "__field_names__")
                        and "__field_names__" not in obj.__dict__
                    ):
                        delattr(obj, "__field_names__")
                except (AttributeError, TypeError):
                    continue
        except ReferenceError:
            # Object was garbage collected, skip it
            continue


# =============================================================================
# Test StashObject Implementation
# =============================================================================


@strawberry.input
class TestStashCreateInput:
    """Test input type for creating TestStashObject."""

    name: str | None = None  # Make name optional to avoid test failures
    description: str | None = None
    tag_ids: list[str] | None = (
        None  # For relationship processing - only IDs, not tag objects
    )


@strawberry.input
class TestStashUpdateInput:
    """Test input type for updating TestStashObject."""

    id: ID
    name: str | None = None
    description: str | None = None
    tag_ids: list[str] | None = (
        None  # For relationship processing - only IDs, not tag objects
    )


@strawberry.type
class TestStashObject(StashObject):
    """Concrete test implementation of StashObject for testing."""

    # Required class variables
    __type_name__: ClassVar[str] = "TestStash"
    __update_input_type__: ClassVar[type] = TestStashUpdateInput
    __create_input_type__: ClassVar[type | None] = TestStashCreateInput
    __field_names__: ClassVar[set[str]] = {"id", "name", "description", "tags"}
    __tracked_fields__: ClassVar[set[str]] = {"name", "description", "tags"}

    # Field conversion functions
    __field_conversions__: ClassVar[dict[str, Any]] = {
        "name": lambda x: str(x).strip() if x else None,
        "description": lambda x: str(x).strip() if x else None,
    }

    # Relationship mappings
    __relationships__: ClassVar[dict[str, tuple[str, bool, Any]]] = {
        "tags": (
            "tag_ids",
            True,
            None,  # Use default _get_id transform which extracts .id from objects or dict["id"]
        ),
    }

    # Fields
    id: str
    name: str
    description: str | None = None
    tags: list[Any] | None = None  # List of Tag objects (MockTag, dict with id, etc.)

    def __post_init__(self) -> None:
        """Initialize object after strawberry dataclass creation."""
        # Let the parent class handle the initialization of tracking fields
        super().__post_init__()

    def __setattr__(self, name: str, value: Any) -> None:
        """Track changes to fields, overriding Strawberry's __setattr__."""
        StashObject.__setattr__(self, name, value)

    def __hash__(self) -> int:
        """Make object hashable based on type and ID."""
        return hash((self.__type_name__, self.id))

    def __eq__(self, other: object) -> bool:
        """Compare objects based on type and ID only."""
        if not isinstance(other, StashObject):
            return NotImplemented
        return (self.__type_name__, self.id) == (other.__type_name__, other.id)


@strawberry.type
class TestStashObjectNoCreate(StashObject):
    """Test StashObject implementation without create support."""

    # Required class variables
    __type_name__: ClassVar[str] = "TestStashNoCreate"
    __update_input_type__: ClassVar[type] = TestStashUpdateInput
    __create_input_type__: ClassVar[type | None] = None  # No create support
    __field_names__: ClassVar[set[str]] = {"id", "name"}
    __tracked_fields__: ClassVar[set[str]] = {"name"}

    # Fields
    id: str
    name: str

    def __post_init__(self) -> None:
        """Initialize object after strawberry dataclass creation."""
        # Let the parent class handle the initialization of tracking fields
        super().__post_init__()

    def __setattr__(self, name: str, value: Any) -> None:
        """Track changes to fields, overriding Strawberry's __setattr__."""
        StashObject.__setattr__(self, name, value)

    def __hash__(self) -> int:
        """Make object hashable based on type and ID."""
        return hash((self.__type_name__, self.id))

    def __eq__(self, other: object) -> bool:
        """Compare objects based on type and ID only."""
        if not isinstance(other, StashObject):
            return NotImplemented
        return (self.__type_name__, self.id) == (other.__type_name__, other.id)


class TestStashObjectNoStrawberry:
    """Test object without strawberry definition for fallback testing."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize test object."""
        for key, value in kwargs.items():
            setattr(self, key, value)


# =============================================================================
# Mock Tag Object
# =============================================================================


class MockTag:
    """Mock tag object for relationship testing."""

    def __init__(self, id: str, name: str) -> None:
        """Initialize mock tag."""
        self.id = id
        self.name = name


# =============================================================================
# Fixture Functions
# =============================================================================


@pytest.fixture
def test_stash_object() -> TestStashObject:
    """Create a test StashObject instance."""
    return TestStashObject(
        id="test_123",
        name="Test Object",
        description="Test description",
        tags=[MockTag("tag1", "Tag 1"), MockTag("tag2", "Tag 2")],  # Use Tag objects
    )


@pytest.fixture
def test_stash_object_no_create() -> TestStashObjectNoCreate:
    """Create a test StashObject instance that doesn't support creation."""
    return TestStashObjectNoCreate(
        id="test_456",
        name="Test No Create",
    )


@pytest.fixture
def test_stash_object_new() -> TestStashObject:
    """Create a new test StashObject instance (for creation testing)."""
    return TestStashObject(
        id="new",
        name="New Object",
        description="New description",
        tags=[MockTag("new_tag", "New Tag")],  # Use Tag object
    )


@pytest.fixture
def test_stash_object_no_strawberry() -> TestStashObjectNoStrawberry:
    """Create a test object without strawberry definition."""
    return TestStashObjectNoStrawberry(
        id="no_strawberry",
        name="No Strawberry",
        unknown_field="should_be_filtered",
    )


@pytest.fixture
def mock_tags() -> list[MockTag]:
    """Create mock tag objects for relationship testing."""
    return [
        MockTag("tag_1", "Tag One"),
        MockTag("tag_2", "Tag Two"),
        MockTag("tag_3", "Tag Three"),
    ]


@pytest.fixture
def mock_stash_client_with_responses() -> Mock:
    """Create a mock Stash client with predefined responses."""
    client = Mock()

    # Mock successful responses
    client.execute = AsyncMock()

    # Set up different responses for different operations
    def mock_execute(
        query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Mock execute method with different responses based on query."""
        variables = variables or {}

        # Find operation response
        if "findTestStash" in query:
            if variables.get("id") == "existing_123":
                return {
                    "findTestStash": {
                        "id": "existing_123",
                        "name": "Existing Object",
                        "description": "Existing description",
                        "tags": [
                            {"id": "existing_tag", "name": "Existing Tag"}
                        ],  # Return Tag objects
                    }
                }
            return {"findTestStash": None}

        # Create operation response
        if "createTestStash" in query or "testStashCreate" in query:
            return {
                "testStashCreate": {
                    "id": "created_456",
                }
            }

        # Update operation response
        if "updateTestStash" in query or "testStashUpdate" in query:
            return {
                "testStashUpdate": {
                    "id": variables.get("input", {}).get("id", "updated_789"),
                }
            }

        # Default empty response
        return {}

    client.execute.side_effect = mock_execute
    return client


@pytest.fixture
def mock_stash_client_with_errors() -> Mock:
    """Create a mock Stash client that raises errors."""
    client = Mock()
    client.execute = AsyncMock(side_effect=Exception("GraphQL error"))
    return client


# =============================================================================
# Sample Data Generators
# =============================================================================


def generate_stash_object_data(
    object_id: str = "test_123",
    name: str = "Test Object",
    description: str | None = "Test description",
    tags: list[Any] | None = None,
) -> dict[str, Any]:
    """Generate sample StashObject data."""
    if tags is None:
        tags = [MockTag("tag1", "Tag 1"), MockTag("tag2", "Tag 2")]  # Use Tag objects

    return {
        "id": object_id,
        "name": name,
        "description": description,
        "tags": tags,
    }


def generate_graphql_response(
    operation: str,
    data: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate a GraphQL response."""
    response = {}

    if data:
        response.update(data)

    if errors:
        response["errors"] = errors

    return response


# =============================================================================
# Bulk Update Fixtures
# =============================================================================


@pytest.fixture
def bulk_update_strings_data() -> dict[str, Any]:
    """Create sample BulkUpdateStrings data."""
    return {
        "values": ["value1", "value2", "value3"],
        "mode": BulkUpdateIdMode.SET,
    }


@pytest.fixture
def bulk_update_ids_data() -> dict[str, Any]:
    """Create sample BulkUpdateIds data."""
    return {
        "ids": [ID("1"), ID("2"), ID("3")],
        "mode": BulkUpdateIdMode.ADD,
    }


# =============================================================================
# Performance and Edge Case Data
# =============================================================================


@pytest.fixture
def large_stash_object_data() -> dict[str, Any]:
    """Generate large StashObject data for performance testing."""
    return {
        "id": "large_test",
        "name": "Large Test Object",
        "description": "A" * 10000,  # Large description
        "tags": [f"tag_{i}" for i in range(1000)],  # Many tags
    }


@pytest.fixture
def edge_case_stash_data() -> list[dict[str, Any]]:
    """Generate edge case data for testing."""
    return [
        # Empty strings
        {
            "id": "empty_test",
            "name": "",
            "description": "",
            "tags": [],
        },
        # None values
        {
            "id": "none_test",
            "name": "None Test",
            "description": None,
            "tags": None,
        },
        # Unicode characters
        {
            "id": "unicode_test",
            "name": "测试对象 🎭",
            "description": "Unicode description with émojis 🚀",
            "tags": [
                MockTag("标签1", "标签1"),
                MockTag("🏷️tag", "🏷️tag"),
            ],  # Use Tag objects
        },
        # Very long values
        {
            "id": "long_test",
            "name": "x" * 1000,
            "description": "y" * 5000,
            "tags": [
                MockTag(f"tag_{i}", f"Tag {i}") for i in range(50)
            ],  # Use Tag objects
        },
    ]


# =============================================================================
# Relationship Testing Fixtures
# =============================================================================


@pytest.fixture
def complex_relationship_data() -> dict[str, Any]:
    """Create complex relationship data for testing."""
    return {
        "id": "complex_test",
        "name": "Complex Object",
        "description": "Has complex relationships",
        "tags": [
            MockTag("rel_1", "Related One"),  # Object with .id attribute
            MockTag("rel_2", "Related Two"),  # Object with .id attribute
            {"id": "dict_tag", "name": "Dict Tag"},  # Dict with "id" key
            # Note: removed "string_tag" since tags should be Tag objects, not strings
        ],
    }


# =============================================================================
# Module exports
# =============================================================================

__all__ = [
    "MockTag",
    "TestStashCreateInput",
    # Test classes
    "TestStashObject",
    "TestStashObjectNoCreate",
    "TestStashObjectNoStrawberry",
    "TestStashUpdateInput",
    "bulk_update_ids_data",
    # Data fixtures
    "bulk_update_strings_data",
    "complex_relationship_data",
    "edge_case_stash_data",
    "generate_graphql_response",
    # Data generators
    "generate_stash_object_data",
    "large_stash_object_data",
    "mock_stash_client_with_errors",
    # Client fixtures
    "mock_stash_client_with_responses",
    "mock_tags",
    # Test isolation
    "reset_stash_field_names_cache",
    # Basic fixtures
    "test_stash_object",
    "test_stash_object_new",
    "test_stash_object_no_create",
    "test_stash_object_no_strawberry",
]
