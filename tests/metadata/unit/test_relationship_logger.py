"""Unit tests for relationship logger functionality."""

import json
import logging
from datetime import datetime

import pytest

from metadata import Account
from metadata.relationship_logger import (
    clear_missing_relationships,
    log_missing_relationship,
    missing_relationships,
    print_missing_relationships_summary,
)
from tests.fixtures.utils.test_isolation import snowflake_id


def _parse_json_output_records(caplog, prefix: str) -> list[tuple[str, dict]]:
    """Extract (log_type, payload) pairs from caplog for the given prefix.

    json_output formats messages as ``[log_type]\\n{json_payload}`` and routes
    them through loguru's ``json_logger``. pytest-loguru bridges loguru records
    into caplog. Filtering by the ``[prefix]`` keeps assertions resilient to
    incidental log lines from autouse fixtures or sibling code paths.
    """
    parsed: list[tuple[str, dict]] = []
    for record in caplog.records:
        message = record.getMessage()
        if not message.startswith(f"[{prefix}"):
            continue
        first_line, _, payload_text = message.partition("\n")
        log_type = first_line.strip("[]")
        parsed.append((log_type, json.loads(payload_text)))
    return parsed


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
async def test_log_missing_relationship_nonexistent(entity_store, caplog):
    """Test logging when relationship doesn't exist."""
    clear_missing_relationships()
    caplog.set_level(logging.INFO)

    nonexistent_id = snowflake_id()

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
    assert str(nonexistent_id) in missing_relationships["accounts"]["referencing_table"]

    parsed = _parse_json_output_records(caplog, "meta/relationships/missing/")
    assert len(parsed) == 1
    log_type, log_data = parsed[0]
    assert log_type == "meta/relationships/missing/accounts/referencing_table"
    assert log_data["table"] == "referencing_table"
    assert log_data["field"] == "test_field"
    assert log_data["missing_id"] == str(nonexistent_id)
    assert log_data["referenced_table"] == "accounts"
    assert log_data["extra"] == "info"
    assert isinstance(datetime.fromisoformat(log_data["timestamp"]), datetime)


def test_print_missing_relationships_summary_empty(caplog):
    """Test printing summary when no missing relationships."""
    clear_missing_relationships()
    caplog.set_level(logging.INFO)

    print_missing_relationships_summary()

    parsed = _parse_json_output_records(caplog, "meta/relationships/summary")
    assert len(parsed) == 1
    log_type, log_data = parsed[0]
    assert log_type == "meta/relationships/summary"
    assert log_data == {"message": "No missing relationships found"}


def test_print_missing_relationships_summary_with_data(caplog):
    """Test printing summary with missing relationships."""
    clear_missing_relationships()
    caplog.set_level(logging.INFO)

    missing_relationships["table1"]["ref1"].add("1")
    missing_relationships["table1"]["ref1"].add("2")
    missing_relationships["table1"]["ref2"].add("3")
    missing_relationships["table2"]["ref3"].add("4")

    print_missing_relationships_summary()

    parsed = _parse_json_output_records(caplog, "meta/relationships/summary")
    assert len(parsed) == 1
    log_type, summary = parsed[0]
    assert log_type == "meta/relationships/summary"

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
async def test_log_missing_relationship_multiple_times(entity_store, caplog):
    """Test logging same missing relationship multiple times."""
    clear_missing_relationships()
    caplog.set_level(logging.INFO)

    missing_id = snowflake_id()

    for _ in range(2):
        await log_missing_relationship(
            table_name="test_table",
            field_name="test_field",
            missing_id=missing_id,
            referenced_table="accounts",
        )

    assert len(missing_relationships["accounts"]["test_table"]) == 1
    parsed = _parse_json_output_records(caplog, "meta/relationships/missing/")
    assert len(parsed) == 1, (
        "Duplicate missing-relationship logs should be deduplicated by was_logged guard"
    )


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


@pytest.mark.asyncio
async def test_log_existing_entity_skips_logging(entity_store, test_account):
    """relationship_logger.py 49→53: entity IS in DB → exists=True → no missing log."""
    clear_missing_relationships()

    exists = await log_missing_relationship(
        table_name="media",
        field_name="accountId",
        missing_id=test_account.id,
        referenced_table="accounts",
    )

    assert exists is True
    assert str(test_account.id) not in missing_relationships.get("accounts", {}).get(
        "media", set()
    )


@pytest.mark.asyncio
async def test_unknown_table_skips_db_lookup(entity_store):
    """relationship_logger.py 49→53: referenced_table not in _TABLE_MODEL_MAP
    → model_type is None → skip DB lookup → exists stays False."""
    clear_missing_relationships()

    exists = await log_missing_relationship(
        table_name="some_table",
        field_name="someId",
        missing_id=snowflake_id(),
        referenced_table="unknown_table_not_in_map",
    )

    assert exists is False
