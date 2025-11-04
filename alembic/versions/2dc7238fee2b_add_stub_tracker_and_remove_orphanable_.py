"""add_stub_tracker_and_remove_orphanable_fks

This migration:
1. Creates stub_tracker table for tracking incomplete records
2. Removes FK constraints that can reference missing/external data:
   - post_mentions.accountId (can be non-Fansly users like @twitter)
   - group_users.accountId (partner accounts fetched later)

These columns remain as indexed BigInteger columns but without FK enforcement,
allowing the application to use stub-first pattern for missing accounts.

Revision ID: 2dc7238fee2b
Revises: ebb4481bb4c7
Create Date: 2025-10-28 21:16:53.807187

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "2dc7238fee2b"
down_revision: str | None = "ebb4481bb4c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply migration: create stub_tracker and remove orphanable FK constraints."""

    # Get database connection
    conn = op.get_bind()
    inspector = inspect(conn)

    # Create stub_tracker table
    op.create_table(
        "stub_tracker",
        sa.Column("table_name", sa.String(), nullable=False),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("table_name", "record_id"),
        sa.UniqueConstraint("table_name", "record_id", name="uix_stub_tracker"),
    )

    # Create indexes on stub_tracker
    op.create_index(
        "ix_stub_tracker_table_name", "stub_tracker", ["table_name"], unique=False
    )
    op.create_index(
        "ix_stub_tracker_created_at", "stub_tracker", ["created_at"], unique=False
    )

    # Drop FK constraint from post_mentions.accountId (if it exists)
    # This allows storing mentions of non-Fansly users (@twitter, @instagram, etc.)
    post_mentions_fks = [
        fk["name"]
        for fk in inspector.get_foreign_keys("post_mentions")
        if fk["constrained_columns"] == ["accountId"]
    ]
    if post_mentions_fks:
        op.drop_constraint(post_mentions_fks[0], "post_mentions", type_="foreignkey")

    # Re-create index on accountId (was part of FK, now separate)
    # Only create if it doesn't exist
    post_mentions_indexes = [
        idx["name"] for idx in inspector.get_indexes("post_mentions")
    ]
    if "ix_post_mentions_accountId" not in post_mentions_indexes:
        op.create_index(
            "ix_post_mentions_accountId", "post_mentions", ["accountId"], unique=False
        )

    # Drop FK constraint from group_users.accountId (if it exists)
    # This allows stub-first pattern for partner accounts
    group_users_fks = [
        fk["name"]
        for fk in inspector.get_foreign_keys("group_users")
        if fk["constrained_columns"] == ["accountId"]
    ]
    if group_users_fks:
        op.drop_constraint(group_users_fks[0], "group_users", type_="foreignkey")

    # Re-create index on accountId (was part of FK, now separate)
    # Only create if it doesn't exist
    group_users_indexes = [idx["name"] for idx in inspector.get_indexes("group_users")]
    if "ix_group_users_accountId" not in group_users_indexes:
        op.create_index(
            "ix_group_users_accountId", "group_users", ["accountId"], unique=False
        )


def downgrade() -> None:
    """Revert migration: drop stub_tracker and restore FK constraints."""
    # Get database connection
    conn = op.get_bind()
    inspector = inspect(conn)

    # Drop indexes on post_mentions and group_users (if they exist)
    post_mentions_indexes = [
        idx["name"] for idx in inspector.get_indexes("post_mentions")
    ]
    if "ix_post_mentions_accountId" in post_mentions_indexes:
        op.drop_index("ix_post_mentions_accountId", table_name="post_mentions")

    group_users_indexes = [idx["name"] for idx in inspector.get_indexes("group_users")]
    if "ix_group_users_accountId" in group_users_indexes:
        op.drop_index("ix_group_users_accountId", table_name="group_users")

    # Restore FK constraints (if they don't already exist)
    # WARNING: This may fail if orphaned references exist!
    post_mentions_fks = [
        fk["name"]
        for fk in inspector.get_foreign_keys("post_mentions")
        if fk["constrained_columns"] == ["accountId"]
    ]
    if not post_mentions_fks:
        op.create_foreign_key(
            "post_mentions_accountId_fkey",
            "post_mentions",
            "accounts",
            ["accountId"],
            ["id"],
        )

    group_users_fks = [
        fk["name"]
        for fk in inspector.get_foreign_keys("group_users")
        if fk["constrained_columns"] == ["accountId"]
    ]
    if not group_users_fks:
        op.create_foreign_key(
            "group_users_accountId_fkey",
            "group_users",
            "accounts",
            ["accountId"],
            ["id"],
        )

    # Drop stub_tracker indexes (if they exist)
    stub_tracker_indexes = [
        idx["name"] for idx in inspector.get_indexes("stub_tracker")
    ]
    if "ix_stub_tracker_created_at" in stub_tracker_indexes:
        op.drop_index("ix_stub_tracker_created_at", table_name="stub_tracker")
    if "ix_stub_tracker_table_name" in stub_tracker_indexes:
        op.drop_index("ix_stub_tracker_table_name", table_name="stub_tracker")

    # Drop stub_tracker table
    op.drop_table("stub_tracker")
