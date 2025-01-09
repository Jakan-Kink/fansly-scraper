"""Integration tests for datetime handling in Stash workflows."""

from datetime import datetime, timezone

import pytest
from stashapi.stashapp import StashInterface

from stash.performer import Performer
from stash.stash_context import StashQL


@pytest.mark.asyncio
async def test_performer_datetime_workflow(mock_stash_interface: StashInterface):
    """Test datetime handling in performer workflow."""
    # Create test data
    test_birthdate = datetime(1990, 1, 1, tzinfo=timezone.utc)
    test_death_date = datetime(2024, 3, 21, tzinfo=timezone.utc)

    # Configure mock
    mock_stash_interface.create_performer.return_value = {
        "id": "test-id",
        "name": "Test Performer",
        "birthdate": test_birthdate.isoformat(),
        "death_date": test_death_date.isoformat(),
    }
    mock_stash_interface.find_performer.return_value = {
        "id": "test-id",
        "name": "Test Performer",
        "birthdate": test_birthdate.isoformat(),
        "death_date": test_death_date.isoformat(),
    }

    # Create performer with dates
    performer = Performer(
        id="new",
        name="Test Performer",
        birthdate=test_birthdate,
        death_date=test_death_date,
    )

    # Create in Stash
    created_data = performer.stash_create(mock_stash_interface)
    assert created_data is not None
    assert "id" in created_data

    # Verify dates are preserved
    created_performer = Performer.from_dict(created_data)
    assert created_performer.birthdate == test_birthdate
    assert created_performer.death_date == test_death_date

    # Update dates
    new_birthdate = datetime(1991, 1, 1, tzinfo=timezone.utc)
    mock_stash_interface.find_performer.return_value = {
        "id": "test-id",
        "name": "Test Performer",
        "birthdate": new_birthdate.isoformat(),
        "death_date": test_death_date.isoformat(),
    }
    created_performer.birthdate = new_birthdate
    created_performer.save(mock_stash_interface)

    # Verify update
    updated_data = mock_stash_interface.find_performer(created_performer.id)
    updated_performer = Performer.from_dict(updated_data)
    assert updated_performer.birthdate == new_birthdate
    assert updated_performer.death_date == test_death_date


@pytest.mark.asyncio
async def test_performer_datetime_serialization(mock_stash_interface: StashInterface):
    """Test datetime serialization in performer workflow."""
    # Test with various datetime formats
    test_cases = [
        # UTC string
        "1990-01-01T00:00:00Z",
        # Offset string
        "1990-01-01T02:00:00+02:00",
        # UTC datetime
        datetime(1990, 1, 1, tzinfo=timezone.utc),
        # Naive datetime
        datetime(1990, 1, 1),
    ]

    expected_date = datetime(1990, 1, 1, tzinfo=timezone.utc)
    mock_stash_interface.create_performer.return_value = {
        "id": "test-id",
        "name": "Test Performer",
        "birthdate": expected_date.isoformat(),
    }

    for test_date in test_cases:
        # Create performer
        performer = Performer(
            id="new",
            name=f"Test Performer {test_date}",
            birthdate=StashQL.sanitize_datetime(test_date),
        )

        # Create in Stash
        created_data = performer.stash_create(mock_stash_interface)
        assert created_data is not None
        assert "id" in created_data

        # Verify date is normalized to UTC
        created_performer = Performer.from_dict(created_data)
        assert created_performer.birthdate == expected_date

        # Verify serialization
        serialized = created_performer.to_dict()
        assert created_performer.birthdate.isoformat() == serialized.get("birthdate")
