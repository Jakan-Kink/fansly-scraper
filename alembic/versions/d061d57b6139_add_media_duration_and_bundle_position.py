"""Add media duration and bundle position columns.

This migration adds:
- duration column to media table for storing video duration in seconds
- pos column to account_media_bundle_media table for ordering bundle content

Revision ID: d061d57b6139
Revises: b4e07e00b3f8
Create Date: 2024-12-21 07:34:08.977425
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d061d57b6139"
down_revision: str | None = "b4e07e00b3f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add duration column to media table
    with op.batch_alter_table("media") as batch_op:
        batch_op.add_column(sa.Column("duration", sa.Float(), nullable=True))

    # Add pos column to account_media_bundle_media table
    with op.batch_alter_table("account_media_bundle_media") as batch_op:
        batch_op.add_column(
            sa.Column("pos", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    # Remove pos column from account_media_bundle_media table
    with op.batch_alter_table("account_media_bundle_media") as batch_op:
        batch_op.drop_column("pos")

    # Remove duration column from media table
    with op.batch_alter_table("media") as batch_op:
        batch_op.drop_column("duration")
