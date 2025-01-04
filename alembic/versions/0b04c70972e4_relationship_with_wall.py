"""relationship_with_wall

Revision ID: 0b04c70972e4
Revises: 7c3779509867
Create Date: 2024-12-28 00:48:25.876570

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0b04c70972e4"
down_revision: str | None = "7c3779509867"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Update account_media foreign key to use CASCADE and set media defaults.

    Note: Foreign keys are intentionally left disabled after this migration
    because the API data needs to be imported in a specific order that may
    not match the foreign key constraints. The application handles data
    integrity at the business logic level.
    """
    conn = op.get_bind()
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))

    # Create a new table with the desired schema
    op.create_table(
        "_account_media_new",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("accountId", sa.Integer(), nullable=False),
        sa.Column("mediaId", sa.Integer(), nullable=False),
        sa.Column("previewId", sa.Integer(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("access", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["accountId"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["mediaId"],
            ["media.id"],
            ondelete="CASCADE",
            name="fk_account_media_mediaId_cascade",
        ),
        sa.ForeignKeyConstraint(
            ["previewId"],
            ["media.id"],
        ),
        sa.PrimaryKeyConstraint("id", "accountId", "mediaId"),
    )

    # Copy data from old table
    op.execute(
        """
        INSERT INTO _account_media_new (
            id, accountId, mediaId, previewId, createdAt, deletedAt, deleted, access
        )
        SELECT id, accountId, mediaId, previewId, createdAt, deletedAt, deleted, access
        FROM account_media
        """
    )

    # Drop old table and rename new one
    op.drop_table("account_media")
    op.rename_table("_account_media_new", "account_media")

    # Set media defaults
    with op.batch_alter_table("media") as batch_op:
        batch_op.alter_column(
            "is_downloaded",
            existing_type=sa.INTEGER(),
            server_default=sa.text("0"),
            existing_nullable=False,
        )

    # Note: Foreign keys are intentionally left disabled
    # ### end Alembic commands ###


def downgrade() -> None:
    """Revert account_media foreign key and media defaults."""
    conn = op.get_bind()
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))

    # Create a new table with the original schema
    op.create_table(
        "_account_media_new",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("accountId", sa.Integer(), nullable=False),
        sa.Column("mediaId", sa.Integer(), nullable=False),
        sa.Column("previewId", sa.Integer(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("access", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["accountId"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["mediaId"],
            ["media.id"],
            name="fk_account_media_mediaId",
        ),
        sa.ForeignKeyConstraint(
            ["previewId"],
            ["media.id"],
        ),
        sa.PrimaryKeyConstraint("id", "accountId", "mediaId"),
    )

    # Copy data from current table
    op.execute(
        """
        INSERT INTO _account_media_new (
            id, accountId, mediaId, previewId, createdAt, deletedAt, deleted, access
        )
        SELECT id, accountId, mediaId, previewId, createdAt, deletedAt, deleted, access
        FROM account_media
        """
    )

    # Drop current table and rename new one
    op.drop_table("account_media")
    op.rename_table("_account_media_new", "account_media")

    # Set media defaults
    with op.batch_alter_table("media") as batch_op:
        batch_op.alter_column(
            "is_downloaded",
            existing_type=sa.INTEGER(),
            server_default=sa.text("0"),
            existing_nullable=False,
        )

    # Note: Foreign keys are intentionally left disabled
    # ### end Alembic commands ###
