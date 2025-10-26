"""case_insensitive_hashtag_value

Revision ID: 0c4cb91b36d5
Revises: 4416b99f028e
Create Date: 2025-01-08 22:16:59.153714

"""

from collections.abc import Sequence
from itertools import groupby

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.sql import table

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0c4cb91b36d5"
down_revision: str | None = "4416b99f028e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # First, find and merge case-variant duplicates
    conn = op.get_bind()

    # Define tables for SQLAlchemy queries
    hashtags = table(
        "hashtags",
        sa.Column("id", sa.Integer),
        sa.Column("value", sa.String),
        sa.Column("stash_id", sa.Integer),
    )
    post_hashtags = table(
        "post_hashtags",
        sa.Column("postId", sa.Integer),
        sa.Column("hashtagId", sa.Integer),
    )

    # Find all hashtag values that have case-variants using a subquery
    subq = (
        select(
            func.lower(hashtags.c.value).label("lower_value"),
            func.count().label("count"),  # pylint: disable=not-callable
        )
        .group_by(func.lower(hashtags.c.value))
        .having(func.count() > 1)  # pylint: disable=not-callable
        .alias("lowercase_groups")
    )

    # Main query to get duplicates
    stmt = (
        select(hashtags.c.id, hashtags.c.value, hashtags.c.stash_id)
        .join(subq, func.lower(hashtags.c.value) == subq.c.lower_value)
        .order_by(func.lower(hashtags.c.value), hashtags.c.id)
    )

    duplicates = conn.execute(stmt).fetchall()

    # Group duplicates by their lowercase value
    for _, group in groupby(duplicates, key=lambda x: x[1].lower()):
        group_items = list(group)
        if not group_items:
            continue

        # Use the first occurrence (lowest ID) as the canonical version
        canonical = group_items[0]
        duplicates = group_items[1:]

        if duplicates:
            # Log what we're merging
            print(
                f"Merging hashtags: keeping {canonical[1]} (id={canonical[0]}), "
                f"merging {', '.join(f'{d[1]}(id={d[0]})' for d in duplicates)}"
            )

            # Update all post_hashtags references to point to the canonical version
            for dupe in duplicates:
                # First, find posts that would have conflicts
                conflict_posts = conn.execute(
                    select(post_hashtags.c.postId)
                    .where(post_hashtags.c.hashtagId == dupe[0])
                    .where(
                        post_hashtags.c.postId.in_(
                            select(post_hashtags.c.postId).where(
                                post_hashtags.c.hashtagId == canonical[0]
                            )
                        )
                    )
                ).fetchall()

                # For posts with conflicts, delete the duplicate reference
                if conflict_posts:
                    print(
                        f"Found {len(conflict_posts)} posts with conflicting hashtags "
                        f"between {dupe[1]}(id={dupe[0]}) and {canonical[1]}(id={canonical[0]})"
                    )
                    delete_conflicts = (
                        post_hashtags.delete()
                        .where(post_hashtags.c.hashtagId == dupe[0])
                        .where(
                            post_hashtags.c.postId.in_([p[0] for p in conflict_posts])
                        )
                    )
                    conn.execute(delete_conflicts)

                # Update remaining references that won't cause conflicts
                update_stmt = (
                    post_hashtags.update()
                    .where(post_hashtags.c.hashtagId == dupe[0])
                    .values(hashtagId=canonical[0])
                )
                conn.execute(update_stmt)

                # Delete duplicate hashtag
                delete_stmt = hashtags.delete().where(hashtags.c.id == dupe[0])
                conn.execute(delete_stmt)

    # Now we can safely add the case-insensitive unique index
    # Drop the old unique constraint first
    op.drop_constraint("uq_hashtags_value", "hashtags", type_="unique")

    # Drop the old index if it exists
    op.execute("DROP INDEX IF EXISTS ix_hashtags_value")

    # PostgreSQL: Use functional index with LOWER() for case-insensitive uniqueness
    # This is the PostgreSQL equivalent of SQLite's COLLATE NOCASE
    op.execute("CREATE UNIQUE INDEX ix_hashtags_value_lower ON hashtags (LOWER(value))")


def downgrade() -> None:
    # Revert to case-sensitive unique constraint
    op.execute("DROP INDEX IF EXISTS ix_hashtags_value_lower")
    op.create_index("ix_hashtags_value", "hashtags", ["value"], unique=True)
    op.create_unique_constraint("uq_hashtags_value", "hashtags", ["value"])
