"""Tests for StashQL class."""

from datetime import date, datetime, timezone

import pytest

from stash.stash_context import StashQL


@pytest.mark.parametrize(
    "input_value,expected",
    [
        # Test None input
        (None, None),
        # Test UTC datetime object
        (
            datetime(2024, 3, 21, 15, 30, tzinfo=timezone.utc),
            datetime(2024, 3, 21, 15, 30, tzinfo=timezone.utc),
        ),
        # Test naive datetime object
        (
            datetime(2024, 3, 21, 15, 30),
            datetime(2024, 3, 21, 15, 30, tzinfo=timezone.utc),
        ),
        # Test date object
        (
            date(2024, 3, 21),
            datetime(2024, 3, 21, 0, 0, tzinfo=timezone.utc),
        ),
        # Test ISO format string with Z
        (
            "2024-03-21T15:30:00Z",
            datetime(2024, 3, 21, 15, 30, tzinfo=timezone.utc),
        ),
        # Test ISO format string with offset
        (
            "2024-03-21T15:30:00+02:00",
            datetime(2024, 3, 21, 13, 30, tzinfo=timezone.utc),
        ),
    ],
)
def test_sanitize_datetime_valid(input_value, expected):
    """Test sanitize_datetime with valid inputs."""
    result = StashQL.sanitize_datetime(input_value)
    assert result == expected


@pytest.mark.parametrize(
    "input_value,expected_error",
    [
        # Test invalid string
        ("invalid", ValueError),
        # Test invalid type
        (123, TypeError),
        # Test invalid object
        (object(), TypeError),
    ],
)
def test_sanitize_datetime_invalid(input_value, expected_error):
    """Test sanitize_datetime with invalid inputs."""
    with pytest.raises(expected_error):
        StashQL.sanitize_datetime(input_value)


@pytest.mark.performance
def test_sanitize_datetime_performance():
    """Test performance of datetime sanitization."""
    # Test with 1000 datetime objects
    test_dates = [
        datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc) for _ in range(1000)
    ]

    start = datetime.now()
    for dt in test_dates:
        StashQL.sanitize_datetime(dt)
    end = datetime.now()

    total_time = (end - start).total_seconds()
    avg_time = total_time / len(test_dates)
    assert avg_time < 0.0001  # Average time should be less than 100 microseconds


@pytest.mark.performance
def test_sanitize_datetime_parsing_performance():
    """Test performance of datetime string parsing."""
    # Test with 1000 ISO format strings
    test_strings = [
        "2024-01-01T12:00:00Z",
        "2024-01-01T12:00:00+00:00",
        "2024-01-01T14:00:00+02:00",
    ] * 334

    start = datetime.now()
    for dt_str in test_strings:
        StashQL.sanitize_datetime(dt_str)
    end = datetime.now()

    total_time = (end - start).total_seconds()
    avg_time = total_time / len(test_strings)
    assert avg_time < 0.0002  # Average time should be less than 200 microseconds
