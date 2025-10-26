"""modify_post_mentions_constraints

Revision ID: 7f057c9b00e0
Revises: 1941514875f1
Create Date: 2025-01-09 00:33:29.442274

"""

import contextlib
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "7f057c9b00e0"
down_revision: str | None = "1941514875f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Check for existing constraints before attempting to drop them
    conn = op.get_bind()
    inspector = inspect(conn)

    # Get primary key info
    pk_constraint = inspector.get_pk_constraint("post_mentions")
    has_pk = (
        pk_constraint.get("name") == "post_mentions_pkey" if pk_constraint else False
    )

    # Get unique constraints
    unique_constraints = {
        uc["name"] for uc in inspector.get_unique_constraints("post_mentions")
    }

    # Update post_mentions structure using batch_alter_table
    with op.batch_alter_table(
        "post_mentions", schema=None, recreate="always"
    ) as batch_op:
        # Update handle column to be non-nullable with default for existing NULL values
        batch_op.alter_column(
            "handle", existing_type=sa.String(), nullable=False, server_default=""
        )

        # Update accountId to be nullable
        batch_op.alter_column("accountId", existing_type=sa.Integer(), nullable=True)

        # Drop old constraints only if they exist
        if has_pk:
            batch_op.drop_constraint("post_mentions_pkey", type_="primary")
        if "post_mentions_postId_accountId_key" in unique_constraints:
            batch_op.drop_constraint(
                "post_mentions_postId_accountId_key", type_="unique"
            )

        # Create new constraints
        batch_op.create_primary_key("post_mentions_pkey", ["postId", "handle"])
        batch_op.create_unique_constraint(
            "uix_post_mentions_account", ["postId", "accountId"]
        )
        batch_op.create_unique_constraint(
            "uix_post_mentions_handle", ["postId", "handle"]
        )


def downgrade() -> None:
    # Revert post_mentions structure using batch_alter_table
    with op.batch_alter_table(
        "post_mentions", schema=None, recreate="always"
    ) as batch_op:
        # Drop new constraints
        with contextlib.suppress(Exception):
            batch_op.drop_constraint("uix_post_mentions_handle", type_="unique")
        with contextlib.suppress(Exception):
            batch_op.drop_constraint("uix_post_mentions_account", type_="unique")
        with contextlib.suppress(Exception):
            batch_op.drop_constraint("post_mentions_pkey", type_="primary")

        # Revert accountId to be non-nullable
        batch_op.alter_column("accountId", existing_type=sa.Integer(), nullable=False)

        # Revert handle to be nullable
        batch_op.alter_column(
            "handle", existing_type=sa.String(), nullable=True, server_default=None
        )

        # Create original constraints
        batch_op.create_primary_key("post_mentions_pkey", ["postId", "accountId"])
        batch_op.create_unique_constraint(
            "post_mentions_postId_accountId_key", ["postId", "accountId"]
        )
