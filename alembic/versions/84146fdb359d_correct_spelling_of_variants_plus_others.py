from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.sql import text

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "84146fdb359d"
down_revision: str | None = "39e07e00a3f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
    connection = op.get_bind()
    connection.execute(
        text(
            "INSERT INTO media_variants (mediaId, variantId) SELECT mediaId, varientId FROM media_varients"
        )
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
    connection = op.get_bind()
    connection.execute(
        """
        INSERT INTO media_varients (mediaId, varientId)
        SELECT mediaId, variantId FROM media_variants
        """
    )

    # Drop the new "media_variants" table
    op.drop_table("media_variants")
