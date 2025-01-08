"""clean_malformed_hashtags

Revision ID: 1941514875f1
Revises: 0c4cb91b36d5
Create Date: 2025-01-08 22:35:25.092773

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.sql import table

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1941514875f1"
down_revision: str | None = "0c4cb91b36d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def extract_hashtags(content: str) -> list[str]:
    """Extract hashtags from malformed content."""
    if not content:
        return []
    # Split on # and filter empty/whitespace
    parts = [p.strip().lower() for p in content.split("#")]
    return [p for p in parts if p and p.isalnum()]


def upgrade() -> None:
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

    # Find all hashtags
    stmt = select(hashtags.c.id, hashtags.c.value)
    all_hashtags = conn.execute(stmt).fetchall()

    # Process each hashtag
    for hashtag_id, value in all_hashtags:
        # Skip if value is None or empty
        if not value or not value.strip():
            print(f"Removing empty hashtag id={hashtag_id}")
            # Delete references and hashtag
            conn.execute(
                post_hashtags.delete().where(post_hashtags.c.hashtagId == hashtag_id)
            )
            conn.execute(hashtags.delete().where(hashtags.c.id == hashtag_id))
            continue

        # Extract potential multiple hashtags from value
        extracted = extract_hashtags(value)

        # If no valid hashtags extracted or just the same as original
        if not extracted or (len(extracted) == 1 and extracted[0] == value.lower()):
            continue

        print(f"Processing malformed hashtag: {value} -> {extracted}")

        # For each extracted hashtag
        for new_value in extracted:
            # Check if this hashtag already exists
            existing = conn.execute(
                select(hashtags.c.id).where(func.lower(hashtags.c.value) == new_value)
            ).scalar()

            if existing:
                # Update post references to point to existing hashtag
                print(f"  Merging with existing hashtag: {new_value} (id={existing})")
                conn.execute(
                    post_hashtags.update()
                    .where(post_hashtags.c.hashtagId == hashtag_id)
                    .values(hashtagId=existing)
                )
            else:
                # Create new hashtag
                print(f"  Creating new hashtag: {new_value}")
                result = conn.execute(
                    hashtags.insert().values(value=new_value).returning(hashtags.c.id)
                )
                new_id = result.scalar()

                # Copy post references to new hashtag
                conn.execute(
                    post_hashtags.insert().from_select(
                        ["postId", "hashtagId"],
                        select(post_hashtags.c.postId, sa.literal(new_id)).where(
                            post_hashtags.c.hashtagId == hashtag_id
                        ),
                    )
                )

        # Delete the original malformed hashtag
        conn.execute(
            post_hashtags.delete().where(post_hashtags.c.hashtagId == hashtag_id)
        )
        conn.execute(hashtags.delete().where(hashtags.c.id == hashtag_id))


def downgrade() -> None:
    pass
