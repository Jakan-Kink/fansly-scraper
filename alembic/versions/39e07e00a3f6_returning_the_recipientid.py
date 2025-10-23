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
    # Add recipientId column to existing messages table
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("recipientId", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_messages_recipientId", "accounts", ["recipientId"], ["id"]
        )
        batch_op.create_index("ix_messages_recipientId", ["recipientId"], unique=False)


def downgrade() -> None:
    # Remove recipientId column and its dependencies
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_index("ix_messages_recipientId")
        batch_op.drop_constraint("fk_messages_recipientId", type_="foreignkey")
        batch_op.drop_column("recipientId")
