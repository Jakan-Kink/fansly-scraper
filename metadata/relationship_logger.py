"""Helper module for logging and tracking missing database relationships."""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from textio import json_output

# Track missing relationships by type
missing_relationships: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))


async def log_missing_relationship(
    session: Session | AsyncSession,
    table_name: str,
    field_name: str,
    missing_id: Any,
    referenced_table: str,
    context: dict[str, Any] | None = None,
) -> bool:
    """Log a missing relationship and check if it exists.

    Args:
        session: SQLAlchemy session (sync or async)
        table_name: Name of the table containing the foreign key
        field_name: Name of the foreign key field
        missing_id: ID that's missing from the referenced table
        referenced_table: Name of the table being referenced
        context: Optional additional context about the relationship

    Returns:
        bool: True if the relationship exists, False if it's missing
    """
    # Convert ID to string for consistent handling in logs
    str_id = str(missing_id)

    # Convert ID to integer for database query
    try:
        int_id = int(missing_id)
    except (ValueError, TypeError):
        # If ID can't be converted to integer, treat as missing
        int_id = None

    if int_id is not None:
        # Check if the ID exists in the referenced table
        query = text(f"SELECT 1 FROM {referenced_table} WHERE id = :id")
        params = {"id": int_id}

        if isinstance(session, AsyncSession):
            result = await session.execute(query, params)
        else:
            result = session.execute(query, params)

        row = result.first()
        exists = row is not None
    else:
        exists = False

    if not exists:
        # Check if this relationship is already logged
        was_logged = str_id in missing_relationships[referenced_table][table_name]

        # Add to missing relationships tracking
        missing_relationships[referenced_table][table_name].add(str_id)

        # Only log if this is a new missing relationship
        if not was_logged:
            # Prepare context for logging
            log_context = {
                "table": table_name,
                "field": field_name,
                "missing_id": str_id,
                "referenced_table": referenced_table,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }
            if context:
                log_context.update(context)

            # Log the missing relationship
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
