"""relationship_with_wall

Revision ID: 0b04c70972e4
Revises: 7c3779509867
Create Date: 2024-12-28 00:48:25.876570

"""

import contextlib
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0b04c70972e4"
down_revision: str | None = "7c3779509867"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Update account_media foreign key to use CASCADE and set media defaults.

    Note: PostgreSQL enforces foreign keys by default. The application handles
    data integrity at the business logic level to accommodate API data that
    may arrive in non-standard order.
    """
    # PostgreSQL: Foreign key behavior is controlled at constraint level (DEFERRABLE, etc.)
    # No PRAGMA equivalent needed

    # Update foreign key constraint on account_media table
    with op.batch_alter_table("account_media") as batch_op:
        # Drop existing foreign key constraint
        with contextlib.suppress(Exception):
            batch_op.drop_constraint("account_media_mediaId_fkey", type_="foreignkey")

        # Create new foreign key with CASCADE
        batch_op.create_foreign_key(
            "fk_account_media_mediaId_cascade",
            "media",
            ["mediaId"],
            ["id"],
            ondelete="CASCADE",
        )

    # Set media defaults
    with op.batch_alter_table("media") as batch_op:
        batch_op.alter_column(
            "is_downloaded",
            existing_type=sa.INTEGER(),
            server_default=sa.text("0"),
            existing_nullable=False,
        )


def downgrade() -> None:
    """Revert account_media foreign key and media defaults."""
    # PostgreSQL: No PRAGMA equivalent needed

    conn = op.get_bind()
    inspector = inspect(conn)

    # Revert foreign key constraint on account_media table
    # Check if the constraint exists before dropping
    fks = inspector.get_foreign_keys("account_media")
    fk_names = [fk["name"] for fk in fks]

    if "fk_account_media_mediaId_cascade" in fk_names:
        with op.batch_alter_table("account_media") as batch_op:
            batch_op.drop_constraint(
                "fk_account_media_mediaId_cascade", type_="foreignkey"
            )

    # Check if the original constraint exists before creating (idempotency)
    if "account_media_mediaId_fkey" not in fk_names:
        with op.batch_alter_table("account_media") as batch_op:
            # Create original foreign key without CASCADE
            batch_op.create_foreign_key(
                "account_media_mediaId_fkey", "media", ["mediaId"], ["id"]
            )

    # Revert media defaults
    with op.batch_alter_table("media") as batch_op:
        batch_op.alter_column(
            "is_downloaded",
            existing_type=sa.INTEGER(),
            server_default=None,
            existing_nullable=False,
        )
