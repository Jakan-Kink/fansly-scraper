"""merge recent migrations

Revision ID: merge_recent_migrations
Revises: d061d57b6139
Create Date: 2024-12-22 15:10:00.000000

"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "merge_recent_migrations"
down_revision: str | None = "d061d57b6139"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create new index on account_media.accountId
    op.create_index(
        op.f("ix_account_media_accountId"), "account_media", ["accountId"], unique=False
    )

    # Disable foreign key checks
    op.execute(text("PRAGMA foreign_keys=OFF"))

    # Fix account_media_bundle_media table with correct foreign keys
    op.execute(
        text(
            """
        CREATE TABLE _account_media_bundle_media_new (
            bundle_id INTEGER NOT NULL,
            media_id INTEGER NOT NULL,
            pos INTEGER NOT NULL,
            PRIMARY KEY (bundle_id, media_id)
        )
    """
        )
    )

    # Copy data
    op.execute(
        text(
            """
        INSERT INTO _account_media_bundle_media_new (bundle_id, media_id, pos)
        SELECT bundle_id, media_id, pos FROM account_media_bundle_media
    """
        )
    )

    # Drop old table and rename new one
    op.execute(text("DROP TABLE account_media_bundle_media"))
    op.execute(
        text(
            "ALTER TABLE _account_media_bundle_media_new RENAME TO account_media_bundle_media"
        )
    )

    # Add foreign key constraints after table creation
    op.execute(
        text(
            "CREATE INDEX ix_account_media_bundle_media_bundle_id ON account_media_bundle_media(bundle_id);"
        )
    )
    op.execute(
        text(
            "CREATE INDEX ix_account_media_bundle_media_media_id ON account_media_bundle_media(media_id);"
        )
    )

    # Re-enable foreign key checks
    op.execute(text("PRAGMA foreign_keys=ON"))

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

    # Disable foreign key checks
    op.execute(text("PRAGMA foreign_keys=OFF"))

    # Recreate account_media_bundle_media table with original structure
    op.execute(
        text(
            """
        CREATE TABLE _account_media_bundle_media_new (
            bundle_id INTEGER NOT NULL,
            media_id INTEGER NOT NULL,
            pos INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (bundle_id, media_id)
        )
    """
        )
    )

    # Copy data
    op.execute(
        text(
            """
        INSERT INTO _account_media_bundle_media_new (bundle_id, media_id, pos)
        SELECT bundle_id, media_id, pos FROM account_media_bundle_media
    """
        )
    )

    # Drop old table and rename new one
    op.execute(text("DROP TABLE account_media_bundle_media"))
    op.execute(
        text(
            "ALTER TABLE _account_media_bundle_media_new RENAME TO account_media_bundle_media"
        )
    )

    # Add indexes
    op.execute(
        text(
            "CREATE INDEX ix_account_media_bundle_media_bundle_id ON account_media_bundle_media(bundle_id);"
        )
    )
    op.execute(
        text(
            "CREATE INDEX ix_account_media_bundle_media_media_id ON account_media_bundle_media(media_id);"
        )
    )

    # Re-enable foreign key checks
    op.execute(text("PRAGMA foreign_keys=ON"))

    # Drop index
    op.drop_index(op.f("ix_account_media_accountId"), table_name="account_media")
