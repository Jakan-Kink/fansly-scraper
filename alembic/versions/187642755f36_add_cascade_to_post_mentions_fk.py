"""Add CASCADE to post_mentions FK

This migration adds ON DELETE CASCADE to the post_mentions.postId foreign key
to automatically delete mention records when a post is deleted.

Without CASCADE, PostgreSQL raises:
  ForeignKeyViolationError: update or delete on table "posts" violates
  foreign key constraint "post_mentions_postId_fkey" on table "post_mentions"

Revision ID: 187642755f36
Revises: 2dc7238fee2b
Create Date: 2025-10-29 10:09:14.017191

"""

from collections.abc import Sequence

from sqlalchemy import inspect

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "187642755f36"
down_revision: str | None = "2dc7238fee2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add CASCADE to post_mentions FK constraint."""
    # Get database connection and inspector
    conn = op.get_bind()
    inspector = inspect(conn)

    # Check if the FK constraint exists
    post_mentions_fks = [
        fk["name"]
        for fk in inspector.get_foreign_keys("post_mentions")
        if fk["constrained_columns"] == ["postId"]
    ]

    # Drop the existing FK constraint if it exists
    if post_mentions_fks:
        op.drop_constraint(
            post_mentions_fks[0],
            "post_mentions",
            type_="foreignkey",
        )

    # Create/re-create the FK constraint with ON DELETE CASCADE
    # Only create if it doesn't already exist with CASCADE
    # (we dropped it above if it existed without CASCADE)
    op.create_foreign_key(
        "post_mentions_postId_fkey",
        "post_mentions",
        "posts",
        ["postId"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Remove CASCADE from post_mentions FK constraint."""
    # Get database connection and inspector
    conn = op.get_bind()
    inspector = inspect(conn)

    # Check if the FK constraint exists
    post_mentions_fks = [
        fk["name"]
        for fk in inspector.get_foreign_keys("post_mentions")
        if fk["constrained_columns"] == ["postId"]
    ]

    # Drop the FK constraint with CASCADE if it exists
    if post_mentions_fks:
        op.drop_constraint(
            post_mentions_fks[0],
            "post_mentions",
            type_="foreignkey",
        )

    # Re-create the FK constraint without CASCADE
    op.create_foreign_key(
        "post_mentions_postId_fkey",
        "post_mentions",
        "posts",
        ["postId"],
        ["id"],
    )
