"""Stub tracker for incomplete records awaiting enrichment."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class StubTracker(Base):
    """Track records created as stubs awaiting full data enrichment.

    When we create stub records (Account with just ID, etc.) to satisfy FK
    constraints before we have full data, we record them here. When the stub
    is enriched with full data, we remove the tracking entry.

    This allows:
    - Monitoring which records are incomplete
    - Batch enrichment jobs to fetch missing data
    - Debugging missing data issues
    - Query "which accounts are just stubs?"

    Attributes:
        table_name: Name of the table containing the stub (e.g., "accounts")
        record_id: ID of the stub record
        created_at: When the stub was created
        reason: Optional context about why stub was created
    """

    __tablename__ = "stub_tracker"
    __table_args__ = (
        UniqueConstraint("table_name", "record_id", name="uix_stub_tracker"),
    )

    table_name: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    record_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,  # For finding old stubs
    )
    reason: Mapped[str | None] = mapped_column(String, nullable=True)


async def register_stub(
    session: AsyncSession,
    table_name: str,
    record_id: int,
    reason: str | None = None,
) -> StubTracker:
    """Register a record as a stub awaiting enrichment.

    Uses get-or-create to be idempotent - won't create duplicate entries.

    Args:
        session: Database session
        table_name: Name of table (e.g., "accounts", "media")
        record_id: ID of the stub record
        reason: Optional context (e.g., "group_partner", "message_recipient")

    Returns:
        StubTracker instance
    """
    stub, _created = await StubTracker.async_get_or_create(
        session,
        filters={"table_name": table_name, "record_id": record_id},
        defaults={"created_at": datetime.now(UTC), "reason": reason},
    )
    return stub


async def remove_stub(
    session: AsyncSession,
    table_name: str,
    record_id: int,
) -> bool:
    """Remove stub tracking entry after enrichment.

    Args:
        session: Database session
        table_name: Name of table
        record_id: ID of the enriched record

    Returns:
        True if stub was removed, False if didn't exist
    """
    result = await session.execute(
        select(StubTracker).where(
            StubTracker.table_name == table_name, StubTracker.record_id == record_id
        )
    )
    stub = result.scalar_one_or_none()

    if stub:
        await session.delete(stub)
        await session.flush()
        return True
    return False


async def is_stub(
    session: AsyncSession,
    table_name: str,
    record_id: int,
) -> bool:
    """Check if a record is tracked as a stub.

    Args:
        session: Database session
        table_name: Name of table
        record_id: ID to check

    Returns:
        True if record is a stub, False otherwise
    """
    result = await session.execute(
        select(StubTracker.record_id).where(
            StubTracker.table_name == table_name, StubTracker.record_id == record_id
        )
    )
    return result.scalar_one_or_none() is not None


async def get_stubs(
    session: AsyncSession,
    table_name: str,
    limit: int | None = None,
) -> list[int]:
    """Get all stub record IDs for a table.

    Args:
        session: Database session
        table_name: Name of table
        limit: Optional limit on number of stubs to return

    Returns:
        List of stub record IDs
    """
    stmt = (
        select(StubTracker.record_id)
        .where(StubTracker.table_name == table_name)
        .order_by(StubTracker.created_at)
    )

    if limit:
        stmt = stmt.limit(limit)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_all_stubs_by_table(
    session: AsyncSession,
) -> dict[str, list[dict[str, Any]]]:
    """Get all stubs grouped by table with details.

    Args:
        session: Database session

    Returns:
        Dict mapping table_name to list of stub details
    """
    result = await session.execute(
        select(StubTracker).order_by(StubTracker.table_name, StubTracker.created_at)
    )
    stubs = result.scalars().all()

    by_table: dict[str, list[dict[str, Any]]] = {}
    for stub in stubs:
        if stub.table_name not in by_table:
            by_table[stub.table_name] = []
        by_table[stub.table_name].append(
            {
                "record_id": stub.record_id,
                "created_at": stub.created_at,
                "reason": stub.reason,
            }
        )

    return by_table


async def count_stubs(
    session: AsyncSession,
    table_name: str | None = None,
) -> int:
    """Count stub records.

    Args:
        session: Database session
        table_name: Optional table name to count stubs for

    Returns:
        Number of stub records
    """
    from sqlalchemy import func

    stmt = select(func.count()).select_from(StubTracker)
    if table_name:
        stmt = stmt.where(StubTracker.table_name == table_name)

    result = await session.execute(stmt)
    return result.scalar_one()
