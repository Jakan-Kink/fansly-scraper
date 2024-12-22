"""add index to accountId on AccountMedia

Revision ID: 1e508a50d2fa
Revises: d061d57b6139
Create Date: 2024-12-22 00:00:04.494490

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1e508a50d2fa"
down_revision: str | None = "d061d57b6139"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create new index
    op.create_index(
        op.f("ix_account_media_accountId"), "account_media", ["accountId"], unique=False
    )

    # Modify account_media_bundle_media column
    with op.batch_alter_table("account_media_bundle_media") as batch_op:
        batch_op.alter_column(
            "pos",
            existing_type=sa.INTEGER(),
            server_default=None,
            existing_nullable=False,
        )

    # Handle media_locations constraints
    with op.batch_alter_table("media_locations") as batch_op:
        batch_op.drop_constraint("uq_media_locations", type_="unique")
        batch_op.drop_constraint("fk_media_locations_media_id", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_media_locations_media_id_new", "media", ["mediaId"], ["id"]
        )

    # Handle wall_posts constraints
    with op.batch_alter_table("wall_posts") as batch_op:
        batch_op.drop_constraint("fk_wall_posts_post_id", type_="foreignkey")
        batch_op.drop_constraint("fk_wall_posts_wall_id", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_wall_posts_wall_id_new", "walls", ["wallId"], ["id"]
        )
        batch_op.create_foreign_key(
            "fk_wall_posts_post_id_new", "posts", ["postId"], ["id"]
        )


def downgrade() -> None:
    # Handle wall_posts constraints
    with op.batch_alter_table("wall_posts") as batch_op:
        batch_op.drop_constraint("fk_wall_posts_wall_id_new", type_="foreignkey")
        batch_op.drop_constraint("fk_wall_posts_post_id_new", type_="foreignkey")
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
        batch_op.drop_constraint("fk_media_locations_media_id_new", type_="foreignkey")
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

    # Modify account_media_bundle_media column
    with op.batch_alter_table("account_media_bundle_media") as batch_op:
        batch_op.alter_column(
            "pos",
            existing_type=sa.INTEGER(),
            server_default=sa.text("'0'"),
            existing_nullable=False,
        )

    # Drop index
    op.drop_index(op.f("ix_account_media_accountId"), table_name="account_media")
