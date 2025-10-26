"""Tests for stash.types.base - Bulk Update Types

Tests BulkUpdateStrings and BulkUpdateIds input types defined in the base module.
These are GraphQL input types used for bulk operations on string and ID fields.

Coverage targets: BulkUpdateStrings, BulkUpdateIds classes
"""

import pytest
from strawberry import ID

from stash.types.base import BulkUpdateIds, BulkUpdateStrings, StashObject
from stash.types.enums import BulkUpdateIdMode


# =============================================================================
# Bulk Update Types Tests
# =============================================================================


@pytest.mark.unit
def test_bulk_update_strings() -> None:
    """Test BulkUpdateStrings input type."""
    assert hasattr(BulkUpdateStrings, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field
        for field in BulkUpdateStrings.__strawberry_definition__.fields
    }
    assert "values" in fields
    assert "mode" in fields

    # Test instantiation
    bulk_update = BulkUpdateStrings(
        values=["test1", "test2"], mode=BulkUpdateIdMode.SET
    )
    assert bulk_update.values == ["test1", "test2"]
    assert bulk_update.mode == BulkUpdateIdMode.SET


@pytest.mark.unit
def test_bulk_update_ids() -> None:
    """Test BulkUpdateIds input type."""
    assert hasattr(BulkUpdateIds, "__strawberry_definition__")

    # Test field types
    fields = {
        field.name: field for field in BulkUpdateIds.__strawberry_definition__.fields
    }
    assert "ids" in fields
    assert "mode" in fields

    # Test instantiation
    bulk_update = BulkUpdateIds(ids=[ID("1"), ID("2")], mode=BulkUpdateIdMode.ADD)
    assert bulk_update.ids == [ID("1"), ID("2")]
    assert bulk_update.mode == BulkUpdateIdMode.ADD


@pytest.mark.unit
def test_stash_object_interface() -> None:
    """Test StashObject interface definition."""
    assert hasattr(StashObject, "__strawberry_definition__")

    # Test that it's decorated as an interface
    assert StashObject.__strawberry_definition__.is_interface

    # Test required fields
    fields = {
        field.name: field for field in StashObject.__strawberry_definition__.fields
    }
    assert "id" in fields
