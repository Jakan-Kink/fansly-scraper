"""Returning the recipientId

Revision ID: 39e07e00a3f6
Revises: 64dc46541521
Create Date: 2024-12-16 03:45:22.757390

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "39e07e00a3f6"
down_revision: str | None = "64dc46541521"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create a new table with the desired schema
    op.create_table(
        "_messages_new",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("groupId", sa.Integer(), nullable=True),
        sa.Column("senderId", sa.Integer(), nullable=False),
        sa.Column("recipientId", sa.Integer(), nullable=True),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["groupId"], ["groups.id"]),
        sa.ForeignKeyConstraint(["recipientId"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["senderId"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Copy data from old table
    op.execute(
        """
        INSERT INTO _messages_new (
            id, groupId, senderId, content, createdAt, deletedAt, deleted
        )
        SELECT id, groupId, senderId, content, createdAt, deletedAt, deleted
        FROM messages
        """
    )

    # Drop old table and rename new one
    op.drop_table("messages")
    op.rename_table("_messages_new", "messages")

    # Create indexes
    op.create_index(
        "ix_messages_recipientId", "messages", ["recipientId"], unique=False
    )


def downgrade() -> None:
    # Drop the index first
    try:
        op.drop_index("ix_messages_recipientId", table_name="messages")
    except Exception:
        pass  # Index might not exist

    # Create a new table without recipientId
    op.create_table(
        "_messages_new",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("groupId", sa.Integer(), nullable=True),
        sa.Column("senderId", sa.Integer(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["groupId"], ["groups.id"]),
        sa.ForeignKeyConstraint(["senderId"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Copy data from old table
    op.execute(
        """
        INSERT INTO _messages_new (
            id, groupId, senderId, content, createdAt, deletedAt, deleted
        )
        SELECT id, groupId, senderId, content, createdAt, deletedAt, deleted
        FROM messages
        """
    )

    # Drop old table and rename new one
    op.drop_table("messages")
    op.rename_table("_messages_new", "messages")
