"""change_is_downloaded_to_boolean

Revision ID: ebb4481bb4c7
Revises: 06658bf47c03
Create Date: 2025-10-10 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "ebb4481bb4c7"
down_revision: str | None = "06658bf47c03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Change is_downloaded column from Integer to Boolean."""
    # PostgreSQL requires dropping the default before changing the type
    # because it can't auto-cast the INTEGER default (0) to BOOLEAN default (false)
    with op.batch_alter_table("media", schema=None) as batch_op:
        # First, drop the server default
        batch_op.alter_column(
            "is_downloaded",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default=None,
        )

        # Then change the type with USING clause to convert data
        batch_op.alter_column(
            "is_downloaded",
            existing_type=sa.Integer(),
            type_=sa.Boolean(),
            existing_nullable=False,
            postgresql_using="CASE WHEN is_downloaded = 0 THEN false ELSE true END",
        )

        # Finally, set the new boolean default
        batch_op.alter_column(
            "is_downloaded",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.text("false"),
        )


def downgrade() -> None:
    """Revert is_downloaded column from Boolean back to Integer."""
    # Same process in reverse: drop default, change type, set new default
    with op.batch_alter_table("media", schema=None) as batch_op:
        # First, drop the server default
        batch_op.alter_column(
            "is_downloaded",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=None,
        )

        # Then change the type with USING clause to convert data
        batch_op.alter_column(
            "is_downloaded",
            existing_type=sa.Boolean(),
            type_=sa.Integer(),
            existing_nullable=False,
            postgresql_using="CASE WHEN is_downloaded = false THEN 0 ELSE 1 END",
        )

        # Finally, set the new integer default
        batch_op.alter_column(
            "is_downloaded",
            existing_type=sa.Integer(),
            existing_nullable=False,
            server_default=sa.text("0"),
        )
