from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import column, table

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "84146fdb359d"
down_revision: str | None = "39e07e00a3f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Fix spelling of variants table and add group lastMessageId.

    Note: PostgreSQL enforces foreign keys by default. The application handles
    data integrity at the business logic level to accommodate API data that
    may arrive in non-standard order.
    """
    # PostgreSQL: Foreign key behavior is controlled at constraint level (DEFERRABLE, etc.)
    # No PRAGMA equivalent needed

    # Create the new "media_variants" table
    op.create_table(
        "media_variants",
        sa.Column("mediaId", sa.Integer(), nullable=False),
        sa.Column("variantId", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["mediaId"],
            ["media.id"],
        ),
        sa.ForeignKeyConstraint(
            ["variantId"],
            ["media.id"],
        ),
        sa.PrimaryKeyConstraint("mediaId", "variantId"),
        sa.UniqueConstraint("mediaId", "variantId"),
    )
    # Copy data from old table to new table using SQLAlchemy operations
    media_varients = table(
        "media_varients",
        column("mediaId", sa.Integer),
        column("varientId", sa.Integer),
    )
    media_variants = table(
        "media_variants",
        column("mediaId", sa.Integer),
        column("variantId", sa.Integer),
    )

    connection = op.get_bind()
    # Select data from old table
    select_stmt = sa.select(
        media_varients.c.mediaId,
        media_varients.c.varientId,
    )
    # Insert into new table
    connection.execute(
        media_variants.insert().from_select(["mediaId", "variantId"], select_stmt)
    )

    # Drop the old "media_varients" table
    op.drop_table("media_varients")

    # Alter the "groups" table to add the "lastMessageId" column and create the foreign key
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.add_column(sa.Column("lastMessageId", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "group_lastMessageId_fkey", "messages", ["lastMessageId"], ["id"]
        )


def downgrade() -> None:
    """Revert variant spelling fix and group lastMessageId.

    Note: PostgreSQL enforces foreign keys by default. The application handles
    data integrity at the business logic level.
    """
    # PostgreSQL: No PRAGMA equivalent needed

    # Revert changes to the "groups" table
    with op.batch_alter_table("groups", schema=None) as batch_op:
        batch_op.drop_constraint("group_lastMessageId_fkey", type_="foreignkey")
        batch_op.drop_column("lastMessageId")

    # Recreate the old "media_varients" table
    op.create_table(
        "media_varients",
        sa.Column("mediaId", sa.INTEGER(), nullable=False),
        sa.Column("varientId", sa.INTEGER(), nullable=False),
        sa.ForeignKeyConstraint(
            ["mediaId"],
            ["media.id"],
        ),
        sa.ForeignKeyConstraint(
            ["varientId"],
            ["media.id"],
        ),
        sa.PrimaryKeyConstraint("mediaId", "varientId"),
        sa.UniqueConstraint("mediaId", "varientId"),
    )
    # Copy data from new table back to old table using SQLAlchemy operations
    media_variants = table(
        "media_variants",
        column("mediaId", sa.Integer),
        column("variantId", sa.Integer),
    )
    media_varients = table(
        "media_varients",
        column("mediaId", sa.Integer),
        column("varientId", sa.Integer),
    )

    connection = op.get_bind()
    # Select data from new table
    select_stmt = sa.select(
        media_variants.c.mediaId,
        media_variants.c.variantId,
    )
    # Insert into old table
    connection.execute(
        media_varients.insert().from_select(["mediaId", "varientId"], select_stmt)
    )

    # Drop the new "media_variants" table
    op.drop_table("media_variants")
