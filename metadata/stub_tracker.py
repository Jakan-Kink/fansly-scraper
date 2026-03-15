"""Stub tracker for incomplete records awaiting enrichment."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .models import StubTracker, get_store


async def register_stub(
    table_name: str,
    record_id: int,
    reason: str | None = None,
) -> None:
    """Register a record as a stub awaiting enrichment. Idempotent."""
    store = get_store()
    stub = StubTracker(
        table_name=table_name,
        record_id=record_id,
        created_at=datetime.now(UTC),
        reason=reason,
    )
    await store.insert_record(stub)


async def remove_stub(
    table_name: str,
    record_id: int,
) -> bool:
    """Remove stub tracking entry after enrichment."""
    store = get_store()
    return await store.delete_record(
        StubTracker,
        table_name=table_name,
        record_id=record_id,
    )


async def is_stub(
    table_name: str,
    record_id: int,
) -> bool:
    """Check if a record is tracked as a stub."""
    store = get_store()
    rows = await store.find_records(
        StubTracker,
        table_name=table_name,
        record_id=record_id,
    )
    return len(rows) > 0


async def get_stubs(
    table_name: str,
    limit: int | None = None,
) -> list[int]:
    """Get all stub record IDs for a table."""
    store = get_store()
    rows = await store.find_records(StubTracker, table_name=table_name)
    rows.sort(key=lambda r: r["created_at"])
    if limit:
        rows = rows[:limit]
    return [row["record_id"] for row in rows]


async def get_all_stubs_by_table() -> dict[str, list[dict[str, Any]]]:
    """Get all stubs grouped by table with details."""
    store = get_store()
    rows = await store.find_records(StubTracker)
    rows.sort(key=lambda r: (r["table_name"], r["created_at"]))

    by_table: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        table = row["table_name"]
        if table not in by_table:
            by_table[table] = []
        by_table[table].append(
            {
                "record_id": row["record_id"],
                "created_at": row["created_at"],
                "reason": row["reason"],
            }
        )
    return by_table


async def count_stubs(
    table_name: str | None = None,
) -> int:
    """Count stub records."""
    store = get_store()
    if table_name:
        rows = await store.find_records(StubTracker, table_name=table_name)
    else:
        rows = await store.find_records(StubTracker)
    return len(rows)
