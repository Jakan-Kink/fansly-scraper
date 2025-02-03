"""add_wall_performance_indexes

Revision ID: 06658bf47c03
Revises: cfe472c5a1ae
Create Date: 2025-02-02 04:44:06.092253

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "06658bf47c03"
down_revision: str | None = "cfe472c5a1ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add wall-related indexes
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_wall_account_created "
        "ON walls(accountId, createdAt)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_wall_posts_post " "ON wall_posts(postId)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_wall_posts_wall_post "
        "ON wall_posts(wallId, postId)"
    )

    # Add case-insensitive hashtag lookup index
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_hashtags_value_lower "
        "ON hashtags(lower(value))"
    )

    # Add content hash lookup index
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_media_content_hash " "ON media(content_hash)"
    )

    # Add post mentions indexes with partial conditions
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_post_mentions_account "
        "ON post_mentions(postId, accountId) "
        "WHERE accountId IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_post_mentions_handle "
        "ON post_mentions(postId, handle) "
        "WHERE handle IS NOT NULL"
    )


def downgrade() -> None:
    # Remove indexes in reverse order
    op.execute("DROP INDEX IF EXISTS ix_post_mentions_handle")
    op.execute("DROP INDEX IF EXISTS ix_post_mentions_account")
    op.drop_index("ix_media_content_hash", table_name="media")
    op.execute("DROP INDEX IF EXISTS ix_hashtags_value_lower")
    op.drop_index("idx_wall_posts_wall_post", table_name="wall_posts")
    op.drop_index("idx_wall_posts_post", table_name="wall_posts")
    op.drop_index("idx_wall_account_created", table_name="walls")
