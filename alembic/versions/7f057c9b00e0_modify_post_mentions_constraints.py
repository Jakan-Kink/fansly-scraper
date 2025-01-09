"""modify_post_mentions_constraints

Revision ID: 7f057c9b00e0
Revises: 1941514875f1
Create Date: 2025-01-09 00:33:29.442274

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f057c9b00e0"
down_revision: str | None = "1941514875f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create a new temporary table with the new structure
    op.create_table(
        "post_mentions_new",
        sa.Column("postId", sa.Integer(), nullable=False),
        sa.Column("accountId", sa.Integer(), nullable=True),  # Changed to nullable
        sa.Column("handle", sa.String(), nullable=False),  # Changed to non-nullable
        sa.ForeignKeyConstraint(
            ["accountId"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["postId"],
            ["posts.id"],
        ),
        sa.PrimaryKeyConstraint("postId", "handle"),  # Changed primary key
        sa.UniqueConstraint("postId", "accountId", name="uix_post_mentions_account"),
        sa.UniqueConstraint("postId", "handle", name="uix_post_mentions_handle"),
    )

    # Copy data from the old table to the new table
    # We'll need to handle any NULL handles by using a default value
    op.execute(
        "INSERT INTO post_mentions_new (postId, accountId, handle) "
        "SELECT postId, accountId, COALESCE(handle, '') as handle "
        "FROM post_mentions "
        "WHERE handle IS NOT NULL OR accountId IS NOT NULL"
    )

    # Drop the old table
    op.drop_table("post_mentions")

    # Rename the new table to the original name
    op.rename_table("post_mentions_new", "post_mentions")


def downgrade() -> None:
    # Create a new temporary table with the old structure
    op.create_table(
        "post_mentions_old",
        sa.Column("postId", sa.Integer(), nullable=False),
        sa.Column("accountId", sa.Integer(), nullable=False),
        sa.Column("handle", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["accountId"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["postId"],
            ["posts.id"],
        ),
        sa.PrimaryKeyConstraint("postId", "accountId"),
        sa.UniqueConstraint("postId", "accountId"),
    )

    # Copy data from the current table to the old structure
    # We'll only copy records that have an accountId since that's required in the old structure
    op.execute(
        "INSERT INTO post_mentions_old (postId, accountId, handle) "
        "SELECT postId, accountId, handle "
        "FROM post_mentions "
        "WHERE accountId IS NOT NULL"
    )

    # Drop the current table
    op.drop_table("post_mentions")

    # Rename the old structure table to the original name
    op.rename_table("post_mentions_old", "post_mentions")
