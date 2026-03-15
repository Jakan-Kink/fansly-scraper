"""Unit tests for relationship logger functionality."""

from datetime import datetime
from unittest.mock import patch

import pytest

from metadata import Account
from metadata.relationship_logger import (
    clear_missing_relationships,
    log_missing_relationship,
    missing_relationships,
    print_missing_relationships_summary,
)
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio
async def test_log_missing_relationship_existing(entity_store):
    """Test logging when relationship exists in the store."""
    store = entity_store

    account_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    clear_missing_relationships()

    exists = await log_missing_relationship(
        table_name="referencing_table",
        field_name="test_field",
        missing_id=account_id,
        referenced_table="accounts",
    )

    assert exists is True
    assert not missing_relationships


@pytest.mark.asyncio
async def test_log_missing_relationship_nonexistent(entity_store):
    """Test logging when relationship doesn't exist."""
    clear_missing_relationships()

    nonexistent_id = snowflake_id()

    with patch("metadata.relationship_logger.json_output") as mock_json_output:
        exists = await log_missing_relationship(
            table_name="referencing_table",
            field_name="test_field",
            missing_id=nonexistent_id,
            referenced_table="accounts",
            context={"extra": "info"},
        )

        assert exists is False
        assert "accounts" in missing_relationships
        assert "referencing_table" in missing_relationships["accounts"]
        assert (
            str(nonexistent_id)
            in missing_relationships["accounts"]["referencing_table"]
        )

        mock_json_output.assert_called_once()
        args = mock_json_output.call_args[0]
        assert args[0] == 1
        assert args[1] == "meta/relationships/missing/accounts/referencing_table"
        log_data = args[2]
        assert log_data["table"] == "referencing_table"
        assert log_data["field"] == "test_field"
        assert log_data["missing_id"] == str(nonexistent_id)
        assert log_data["referenced_table"] == "accounts"
        assert log_data["extra"] == "info"
        assert isinstance(datetime.fromisoformat(log_data["timestamp"]), datetime)


def test_print_missing_relationships_summary_empty():
    """Test printing summary when no missing relationships."""
    clear_missing_relationships()
    with patch("metadata.relationship_logger.json_output") as mock_json_output:
        print_missing_relationships_summary()

        mock_json_output.assert_called_once_with(
            1,
            "meta/relationships/summary",
            {"message": "No missing relationships found"},
        )


def test_print_missing_relationships_summary_with_data():
    """Test printing summary with missing relationships."""
    clear_missing_relationships()

    missing_relationships["table1"]["ref1"].add("1")
    missing_relationships["table1"]["ref1"].add("2")
    missing_relationships["table1"]["ref2"].add("3")
    missing_relationships["table2"]["ref3"].add("4")

    with patch("metadata.relationship_logger.json_output") as mock_json_output:
        print_missing_relationships_summary()

        mock_json_output.assert_called_once()
        args = mock_json_output.call_args[0]
        assert args[0] == 1
        assert args[1] == "meta/relationships/summary"
        summary = args[2]

        assert "table1" in summary["missing_relationships"]
        assert "table2" in summary["missing_relationships"]
        assert set(summary["missing_relationships"]["table1"]["ref1"]) == {"1", "2"}
        assert set(summary["missing_relationships"]["table1"]["ref2"]) == {"3"}
        assert set(summary["missing_relationships"]["table2"]["ref3"]) == {"4"}

        assert summary["counts"]["table1"]["ref1"] == 2
        assert summary["counts"]["table1"]["ref2"] == 1
        assert summary["counts"]["table2"]["ref3"] == 1


def test_clear_missing_relationships():
    """Test clearing missing relationships tracking."""
    missing_relationships["table1"]["ref1"].add("1")
    missing_relationships["table2"]["ref2"].add("2")

    assert len(missing_relationships) > 0
    clear_missing_relationships()
    assert len(missing_relationships) == 0


@pytest.mark.asyncio
async def test_log_missing_relationship_with_none_id(entity_store):
    """Test logging with None ID."""
    clear_missing_relationships()

    exists = await log_missing_relationship(
        table_name="test_table",
        field_name="test_field",
        missing_id=None,
        referenced_table="accounts",
    )

    assert exists is False
    assert "accounts" in missing_relationships
    assert "test_table" in missing_relationships["accounts"]
    assert "None" in missing_relationships["accounts"]["test_table"]


@pytest.mark.asyncio
async def test_log_missing_relationship_multiple_times(entity_store):
    """Test logging same missing relationship multiple times."""
    clear_missing_relationships()

    missing_id = snowflake_id()

    with patch("metadata.relationship_logger.json_output") as mock_json_output:
        for _ in range(2):
            await log_missing_relationship(
                table_name="test_table",
                field_name="test_field",
                missing_id=missing_id,
                referenced_table="accounts",
            )

        assert len(missing_relationships["accounts"]["test_table"]) == 1
        assert mock_json_output.call_count == 1


@pytest.mark.parametrize(
    "missing_id",
    [
        123,
        "456",
        123.45,
        True,
    ],
)
@pytest.mark.asyncio
async def test_log_missing_relationship_id_types(entity_store, missing_id):
    """Test logging with different ID types."""
    clear_missing_relationships()

    exists = await log_missing_relationship(
        table_name="test_table",
        field_name="test_field",
        missing_id=missing_id,
        referenced_table="accounts",
    )

    assert exists is False
    assert str(missing_id) in missing_relationships["accounts"]["test_table"]
