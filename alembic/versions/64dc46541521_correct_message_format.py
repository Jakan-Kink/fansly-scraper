"""Correct message format

Revision ID: 64dc46541521
Revises: 1c766f50e19a
Create Date: 2024-12-16 03:40:42.955958

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "64dc46541521"
down_revision: str | None = "1c766f50e19a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    with op.batch_alter_table("messages", schema=None) as batch_op:
        # Add the new column
        batch_op.add_column(sa.Column("content", sa.String(), nullable=False))
        # Drop the old columns
        batch_op.drop_column("text")
        batch_op.drop_column("recipientId")


def downgrade() -> None:
    with op.batch_alter_table("messages", schema=None) as batch_op:
        # Re-add the old columns
        batch_op.add_column(sa.Column("recipientId", sa.INTEGER(), nullable=False))
        batch_op.add_column(sa.Column("text", sa.VARCHAR(), nullable=False))
        # Recreate the foreign key
        batch_op.create_foreign_key(None, "accounts", ["recipientId"], ["id"])
        # Drop the new column
        batch_op.drop_column("content")
