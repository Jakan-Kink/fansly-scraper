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
    with op.batch_alter_table("messages", schema=None) as batch_op:
        # Add the new column
        batch_op.add_column(sa.Column("recipientId", sa.Integer(), nullable=True))
        # Create the foreign key constraint
        batch_op.create_foreign_key(
            "messages_recipientId", "accounts", ["recipientId"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("messages", schema=None) as batch_op:
        # Drop the foreign key constraint
        batch_op.drop_constraint("messages_recipientId", type_="foreignkey")
        # Drop the column
        batch_op.drop_column("recipientId")
