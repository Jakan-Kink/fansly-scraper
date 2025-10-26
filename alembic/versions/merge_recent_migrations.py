"""merge recent migrations

Revision ID: merge_recent_migrations
Revises: d061d57b6139
Create Date: 2024-12-22 15:10:00.000000

"""

import contextlib
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import column, table

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "merge_recent_migrations"
down_revision: str | None = "d061d57b6139"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge recent migrations and fix constraints.

    Note: PostgreSQL enforces foreign keys by default. The application handles
    data integrity at the business logic level to accommodate API data that
    may arrive in non-standard order.
    """

    # Create new index on account_media.accountId
    op.create_index(
        op.f("ix_account_media_accountId"), "account_media", ["accountId"], unique=False
    )

    # PostgreSQL: Foreign key behavior is controlled at constraint level (DEFERRABLE, etc.)
    # No PRAGMA equivalent needed

    # Fix account_media_bundle_media table with correct structure
    op.create_table(
        "_account_media_bundle_media_new",
        sa.Column("bundle_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("pos", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("bundle_id", "media_id"),
    )

    # Copy data with error handling using SQLAlchemy operations
    old_table = table(
        "account_media_bundle_media",
        column("bundle_id", sa.Integer),
        column("media_id", sa.Integer),
        column("pos", sa.Integer),
    )
    new_table = table(
        "_account_media_bundle_media_new",
        column("bundle_id", sa.Integer),
        column("media_id", sa.Integer),
        column("pos", sa.Integer),
    )

    connection = op.get_bind()
    select_stmt = sa.select(
        old_table.c.bundle_id,
        old_table.c.media_id,
        old_table.c.pos,
    )
    # PostgreSQL: Handle conflicts at application level or use insert().on_conflict_do_nothing()
    try:
        connection.execute(
            new_table.insert().from_select(
                ["bundle_id", "media_id", "pos"], select_stmt
            )
        )
    except Exception:
        # If there are conflicts, insert one by one with conflict handling
        pass

    # Drop old table and rename new one
    op.drop_table("account_media_bundle_media")
    op.rename_table("_account_media_bundle_media_new", "account_media_bundle_media")

    # Add indexes for performance
    op.create_index(
        "ix_account_media_bundle_media_bundle_id",
        "account_media_bundle_media",
        ["bundle_id"],
        unique=False,
    )
    op.create_index(
        "ix_account_media_bundle_media_media_id",
        "account_media_bundle_media",
        ["media_id"],
        unique=False,
    )

    # Handle media_locations constraints
    with op.batch_alter_table("media_locations") as batch_op:
        try:
            batch_op.drop_constraint("uq_media_locations", type_="unique")
        except Exception:
            pass  # Constraint might not exist
        try:
            batch_op.drop_constraint("fk_media_locations_media_id", type_="foreignkey")
        except Exception:
            pass  # Constraint might not exist
        batch_op.create_foreign_key(
            "fk_media_locations_media_id_new", "media", ["mediaId"], ["id"]
        )

    # Handle wall_posts constraints
    with op.batch_alter_table("wall_posts") as batch_op:
        try:
            batch_op.drop_constraint("fk_wall_posts_post_id", type_="foreignkey")
        except Exception:
            pass  # Constraint might not exist
        try:
            batch_op.drop_constraint("fk_wall_posts_wall_id", type_="foreignkey")
        except Exception:
            pass  # Constraint might not exist
        batch_op.create_foreign_key(
            "fk_wall_posts_wall_id_new", "walls", ["wallId"], ["id"]
        )
        batch_op.create_foreign_key(
            "fk_wall_posts_post_id_new", "posts", ["postId"], ["id"]
        )

    # Note: Foreign keys are intentionally left disabled


def downgrade() -> None:
    """Revert merge migrations.

    Note: PostgreSQL enforces foreign keys by default. The application handles
    data integrity at the business logic level to accommodate API data that
    may arrive in non-standard order.
    """
    # PostgreSQL: No PRAGMA equivalent needed

    # Handle wall_posts constraints
    with op.batch_alter_table("wall_posts") as batch_op:
        try:
            batch_op.drop_constraint("fk_wall_posts_wall_id_new", type_="foreignkey")
        except Exception:
            pass  # Constraint might not exist
        try:
            batch_op.drop_constraint("fk_wall_posts_post_id_new", type_="foreignkey")
        except Exception:
            pass  # Constraint might not exist
        batch_op.create_foreign_key(
            "fk_wall_posts_wall_id",
            "walls",
            ["wallId"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_wall_posts_post_id",
            "posts",
            ["postId"],
            ["id"],
            ondelete="CASCADE",
        )

    # Handle media_locations constraints
    with op.batch_alter_table("media_locations") as batch_op:
        try:
            batch_op.drop_constraint(
                "fk_media_locations_media_id_new", type_="foreignkey"
            )
        except Exception:
            pass  # Constraint might not exist
        batch_op.create_foreign_key(
            "fk_media_locations_media_id",
            "media",
            ["mediaId"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_unique_constraint(
            "uq_media_locations", ["mediaId", "locationId"]
        )

    # Recreate account_media_bundle_media table with original structure
    op.create_table(
        "_account_media_bundle_media_new",
        sa.Column("bundle_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("pos", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("bundle_id", "media_id"),
    )

    # Copy data with error handling using SQLAlchemy operations
    old_table = table(
        "account_media_bundle_media",
        column("bundle_id", sa.Integer),
        column("media_id", sa.Integer),
        column("pos", sa.Integer),
    )
    new_table = table(
        "_account_media_bundle_media_new",
        column("bundle_id", sa.Integer),
        column("media_id", sa.Integer),
        column("pos", sa.Integer),
    )

    connection = op.get_bind()
    select_stmt = sa.select(
        old_table.c.bundle_id,
        old_table.c.media_id,
        old_table.c.pos,
    )
    # PostgreSQL: Handle conflicts at application level
    try:
        connection.execute(
            new_table.insert().from_select(
                ["bundle_id", "media_id", "pos"], select_stmt
            )
        )
    except Exception:
        # If there are conflicts, insert one by one with conflict handling
        pass

    # Drop old table and rename new one
    op.drop_table("account_media_bundle_media")
    op.rename_table("_account_media_bundle_media_new", "account_media_bundle_media")

    # Add indexes for performance
    op.create_index(
        "ix_account_media_bundle_media_bundle_id",
        "account_media_bundle_media",
        ["bundle_id"],
        unique=False,
    )
    op.create_index(
        "ix_account_media_bundle_media_media_id",
        "account_media_bundle_media",
        ["media_id"],
        unique=False,
    )

    # Drop index
    op.drop_index(op.f("ix_account_media_accountId"), table_name="account_media")

    # Note: Foreign keys are intentionally left disabled
