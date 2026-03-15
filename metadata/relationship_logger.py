"""Helper module for logging and tracking missing database relationships."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from textio import json_output

from .models import Account, Group, Media, Message, get_store


# Table name → model type mapping for existence checks
_TABLE_MODEL_MAP: dict[str, type] = {
    "accounts": Account,
    "media": Media,
    "messages": Message,
    "groups": Group,
}

# Track missing relationships by type
missing_relationships: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))


async def log_missing_relationship(
    table_name: str,
    field_name: str,
    missing_id: Any,
    referenced_table: str,
    context: dict[str, Any] | None = None,
) -> bool:
    """Log a missing relationship and check if it exists.

    Returns:
        True if the relationship exists, False if it's missing
    """
    store = get_store()
    str_id = str(missing_id)

    try:
        int_id = int(missing_id)
    except (ValueError, TypeError):
        int_id = None

    exists = False
    if int_id is not None:
        model_type = _TABLE_MODEL_MAP.get(referenced_table)
        if model_type:
            obj = await store.get(model_type, int_id)
            exists = obj is not None

    if not exists:
        was_logged = str_id in missing_relationships[referenced_table][table_name]
        missing_relationships[referenced_table][table_name].add(str_id)

        if not was_logged:
            log_context = {
                "table": table_name,
                "field": field_name,
                "missing_id": str_id,
                "referenced_table": referenced_table,
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }
            if context:
                log_context.update(context)

            json_output(
                1,
                f"meta/relationships/missing/{referenced_table}/{table_name}",
                log_context,
            )

    return exists


def print_missing_relationships_summary() -> None:
    """Print a summary of all missing relationships."""
    if not missing_relationships:
        json_output(
            1,
            "meta/relationships/summary",
            {"message": "No missing relationships found"},
        )
        return

    summary = {
        "missing_relationships": {
            referenced_table: {
                referencing_table: list(ids)
                for referencing_table, ids in tables.items()
            }
            for referenced_table, tables in missing_relationships.items()
        },
        "counts": {
            referenced_table: {
                referencing_table: len(ids) for referencing_table, ids in tables.items()
            }
            for referenced_table, tables in missing_relationships.items()
        },
    }

    json_output(1, "meta/relationships/summary", summary)


def clear_missing_relationships() -> None:
    """Clear the missing relationships tracking."""
    missing_relationships.clear()
