"""Tests for stash.types.scalars module.

Tests scalar types including Time and Timestamp scalars.
"""

from datetime import UTC, datetime

import pytest
from strawberry.types.scalar import ScalarWrapper

from stash.types.scalars import Time, Timestamp, _parse_timestamp


@pytest.mark.unit
def test_time_scalar() -> None:
    """Test Time scalar type."""
    # Test that it's a ScalarWrapper (created by @strawberry.scalar)
    assert isinstance(Time, ScalarWrapper)

    # Ensure the scalar has the expected attributes
    assert hasattr(Time, "_scalar_definition")

    # Test scalar properties
    assert Time._scalar_definition.name == "Time"
    assert Time._scalar_definition.description == "An RFC3339 timestamp"

    # Test serialization function exists
    assert hasattr(Time._scalar_definition, "serialize")
    assert hasattr(Time._scalar_definition, "parse_value")


@pytest.mark.unit
def test_timestamp_scalar() -> None:
    """Test Timestamp scalar type."""
    # Test that it's a ScalarWrapper (created by @strawberry.scalar)
    assert isinstance(Timestamp, ScalarWrapper)

    # Ensure the scalar has the expected attributes
    assert hasattr(Timestamp, "_scalar_definition")

    # Test scalar properties
    assert Timestamp._scalar_definition.name == "Timestamp"
    assert Timestamp._scalar_definition.description is not None
    assert "point in time" in Timestamp._scalar_definition.description
    assert "RFC3339" in Timestamp._scalar_definition.description

    # Test serialization function exists
    assert hasattr(Timestamp._scalar_definition, "serialize")
    assert hasattr(Timestamp._scalar_definition, "parse_value")


@pytest.mark.unit
def test_time_serialization() -> None:
    """Test Time scalar serialization."""
    # Ensure scalar has the expected attributes
    assert hasattr(Time, "_scalar_definition")

    # Test with datetime object
    dt = datetime(2023, 12, 25, 10, 30, 0)
    result = Time._scalar_definition.serialize(dt)
    assert isinstance(result, str)
    assert "2023-12-25T10:30:00" in result

    # Test with non-datetime object (should pass through)
    result = Time._scalar_definition.serialize("test")
    assert result == "test"


@pytest.mark.unit
def test_time_parsing() -> None:
    """Test Time scalar parsing."""
    # Ensure scalar has the expected attributes
    assert hasattr(Time, "_scalar_definition")

    # Test parsing ISO string
    result = Time._scalar_definition.parse_value("2023-12-25T10:30:00")
    assert isinstance(result, datetime)
    assert result.year == 2023
    assert result.month == 12
    assert result.day == 25

    # Test with non-string (should pass through)
    dt = datetime.now(UTC)
    result = Time._scalar_definition.parse_value(dt)
    assert result == dt


@pytest.mark.unit
def test_timestamp_serialization() -> None:
    """Test Timestamp scalar serialization."""
    # Ensure scalar has the expected attributes
    assert hasattr(Timestamp, "_scalar_definition")

    # Test with datetime object
    dt = datetime(2023, 12, 25, 10, 30, 0)
    result = Timestamp._scalar_definition.serialize(dt)
    assert isinstance(result, str)
    assert "2023-12-25T10:30:00" in result

    # Test with non-datetime object (should pass through)
    result = Timestamp._scalar_definition.serialize("test")
    assert result == "test"


@pytest.mark.unit
def test_timestamp_parsing() -> None:
    """Test Timestamp scalar parsing."""
    # Ensure scalar has the expected attributes
    assert hasattr(Timestamp, "_scalar_definition")

    # Test parsing ISO string
    result = Timestamp._scalar_definition.parse_value("2023-12-25T10:30:00")
    assert isinstance(result, datetime)

    # Test with relative time (past)
    result = Timestamp._scalar_definition.parse_value("<4h")
    assert isinstance(result, datetime)

    # Test with relative time (future)
    result = Timestamp._scalar_definition.parse_value(">5m")
    assert isinstance(result, datetime)

    # Test with non-string (should pass through)
    dt = datetime.now(UTC)
    result = Timestamp._scalar_definition.parse_value(dt)
    assert result == dt


@pytest.mark.unit
def test_parse_timestamp_function() -> None:
    """Test _parse_timestamp helper function."""
    # Test RFC3339 parsing
    result = _parse_timestamp("2023-12-25T10:30:00")
    assert isinstance(result, datetime)
    assert result.year == 2023

    # Test relative time in past
    before = datetime.now(UTC)
    result = _parse_timestamp("<1h")

    # Should be in the past
    assert result < before

    # Test relative time in future
    before = datetime.now(UTC)
    result = _parse_timestamp(">1m")

    # Should be in the future
    assert result > before


@pytest.mark.unit
def test_parse_timestamp_formats() -> None:
    """Test various timestamp formats."""
    # Test different relative time formats - only h and m units are supported per GraphQL schema
    test_cases = [
        "<5m",  # 5 minutes ago
        "<2h",  # 2 hours ago
        ">10m",  # 10 minutes from now
        ">3h",  # 3 hours from now
    ]

    for test_case in test_cases:
        result = _parse_timestamp(test_case)
        assert isinstance(result, datetime), f"Failed to parse {test_case}"


@pytest.mark.unit
def test_parse_timestamp_invalid_units() -> None:
    """Test that invalid time units are rejected."""
    invalid_cases = [
        "<1s",  # seconds not supported
        "<1d",  # days not supported
        ">30s",  # seconds not supported
        ">2d",  # days not supported
    ]

    for test_case in invalid_cases:
        with pytest.raises(ValueError, match="Invalid time unit"):
            _parse_timestamp(test_case)


@pytest.mark.unit
def test_strawberry_decorations() -> None:
    """Test that scalars are properly decorated with strawberry."""
    scalars_to_test = [Time, Timestamp]

    for scalar_class in scalars_to_test:
        assert isinstance(scalar_class, ScalarWrapper), (
            f"{scalar_class.__name__ if hasattr(scalar_class, '__name__') else 'Scalar'} is not a ScalarWrapper"
        )
        assert scalar_class._scalar_definition.name is not None
