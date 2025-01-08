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
        group = list(group)
        if not group:
            continue

        # Use the first occurrence (lowest ID) as the canonical version
        canonical = group[0]
        duplicates = group[1:]

        if duplicates:
            # Log what we're merging
            print(
                f"Merging hashtags: keeping {canonical[1]} (id={canonical[0]}), "
                f"merging {', '.join(f'{d[1]}(id={d[0]})' for d in duplicates)}"
            )

            # Update all post_hashtags references to point to the canonical version
            for dupe in duplicates:
                # Update references
                update_stmt = (
                    post_hashtags.update()
                    .where(post_hashtags.c.hashtagId == dupe[0])
                    .values(hashtagId=canonical[0])
                )
                conn.execute(update_stmt)

                # Delete duplicate
                delete_stmt = hashtags.delete().where(hashtags.c.id == dupe[0])
                conn.execute(delete_stmt)

    # Now we can safely add the case-insensitive unique index
    with op.batch_alter_table("hashtags", schema=None) as batch_op:
        batch_op.drop_index("ix_hashtags_value")
        # Create new case-insensitive unique index
        batch_op.create_index(
            "ix_hashtags_value", ["value"], unique=True, sqlite_collation="NOCASE"
        )


def downgrade() -> None:
    # Revert to case-sensitive unique constraint
    with op.batch_alter_table("hashtags", schema=None) as batch_op:
        batch_op.drop_index("ix_hashtags_value")
        batch_op.create_index("ix_hashtags_value", ["value"], unique=True)
