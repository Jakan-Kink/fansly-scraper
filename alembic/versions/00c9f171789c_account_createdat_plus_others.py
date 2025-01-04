"""account createdAt plus others

Revision ID: 00c9f171789c
Revises: merge_recent_migrations
Create Date: 2024-12-23 17:29:40.906442

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "00c9f171789c"
down_revision: str | None = "merge_recent_migrations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add account timestamps and update foreign key constraints.

    Note: Foreign keys are intentionally disabled during this migration
    because the API data needs to be imported in a specific order that may
    not match the foreign key constraints. The application handles data
    integrity at the business logic level.
    """
    conn = op.get_bind()
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))

    # Create media_story_states table with foreign key included in creation
    op.create_table(
        "media_story_states",
        sa.Column("accountId", sa.Integer(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("storyCount", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updatedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hasActiveStories", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["accountId"], ["accounts.id"], name="fk_media_story_states_account_id"
        ),
        sa.PrimaryKeyConstraint("accountId"),
    )

    # Handle account_media_bundle_media table changes
    with op.batch_alter_table(
        "account_media_bundle_media", recreate="always"
    ) as batch_op:
        # First drop existing indexes and constraints
        batch_op.drop_index("ix_account_media_bundle_media_bundle_id")
        batch_op.drop_index("ix_account_media_bundle_media_media_id")

        # Then recreate with new constraints
        batch_op.create_foreign_key(
            "fk_account_media_bundle_media_bundle",
            "account_media_bundles",
            ["bundle_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_account_media_bundle_media_media",
            "account_media",
            ["media_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # Handle accounts table changes
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(
            sa.Column("createdAt", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("subscribed", sa.Boolean(), nullable=True))

    # Create walls index if it doesn't exist
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_walls_accountId'"
        )
    )
    if result.fetchone() is None:
        op.create_index(
            op.f("ix_walls_accountId"), "walls", ["accountId"], unique=False
        )


def downgrade() -> None:
    """Revert account timestamps and foreign key changes.

    Note: Foreign keys remain disabled to maintain consistency with
    the application's data integrity approach.
    """
    conn = op.get_bind()
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))

    # Drop walls index if it exists
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_walls_accountId'"
        )
    )
    if result.fetchone() is not None:
        op.drop_index(op.f("ix_walls_accountId"), table_name="walls")

    # Handle accounts table changes
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("subscribed")
        batch_op.drop_column("createdAt")

    # Handle account_media_bundle_media table changes
    with op.batch_alter_table(
        "account_media_bundle_media", recreate="always"
    ) as batch_op:
        # Drop new constraints
        batch_op.drop_constraint(
            "fk_account_media_bundle_media_bundle", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_account_media_bundle_media_media", type_="foreignkey"
        )

        # Recreate original indexes
        batch_op.create_index(
            "ix_account_media_bundle_media_media_id", ["media_id"], unique=False
        )
        batch_op.create_index(
            "ix_account_media_bundle_media_bundle_id", ["bundle_id"], unique=False
        )

    # Drop media_story_states table
    op.drop_table("media_story_states")
