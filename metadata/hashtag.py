from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config.decorators import with_database_session

from .base import Base
from .database import require_database_config


if TYPE_CHECKING:
    from config import FanslyConfig

    from .post import Post


class Hashtag(Base):
    __tablename__ = "hashtags"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    value: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    stash_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    posts: Mapped[list[Post]] = relationship(
        "Post",
        secondary="post_hashtags",
        back_populates="hashtags",
    )
    __table_args__ = (
        # Case-insensitive unique index using LOWER()
        # This is defined in migration 0c4cb91b36d5
        Index("ix_hashtags_value_lower", func.lower(value), unique=True),
    )


# Association table for the many-to-many relationship between posts and hashtags
post_hashtags = Table(
    "post_hashtags",
    Base.metadata,
    Column(
        "postId",
        BigInteger,
        ForeignKey("posts.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "hashtagId",
        Integer,
        ForeignKey("hashtags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    UniqueConstraint("postId", "hashtagId", name="pk_post_hashtags"),
    Index("ix_post_hashtags_postId", "postId"),
    Index("ix_post_hashtags_hashtagId", "hashtagId"),
)


def extract_hashtags(content: str) -> list[str]:
    """Extract hashtags from post content.

    Args:
        content: The post content string

    Returns:
        List of hashtag values (without the # symbol)

    Examples:
        >>> extract_hashtags("Hello #world")
        ['world']
        >>> extract_hashtags("Hello#world")
        ['world']
        >>> extract_hashtags("text#tag1#tag2")
        ['tag1', 'tag2']
        >>> extract_hashtags("text##tag")
        ['tag']
        >>> extract_hashtags("#tag,#another")
        ['tag', 'another']
        >>> extract_hashtags("#Nederlands #nederlands #NEDERLANDS")
        ['nederlands']
        >>> extract_hashtags("#atmn#a2m")
        ['atmn', 'a2m']
        >>> extract_hashtags("#messy#drool")
        ['messy', 'drool']
        >>> extract_hashtags("##mistress")
        ['mistress']
        >>> extract_hashtags("#latex #catsuit #domme")
        ['latex', 'catsuit', 'domme']
        >>> extract_hashtags("#strapon #latex #pegging")
        ['strapon', 'latex', 'pegging']
        >>> extract_hashtags("#latex #latexfetish #latexmodel")
        ['latex', 'latexfetish', 'latexmodel']
        >>> extract_hashtags("#  ")  # Empty/whitespace hashtag
        []
        >>> extract_hashtags("# ")  # Just whitespace after #
        []
        >>> extract_hashtags("#")  # Just the # symbol
        []
        >>> extract_hashtags("#\t")  # Tab after #
        []
        >>> extract_hashtags("#\n")  # Newline after #
        []
    """
    if not content:
        return []

    # Pattern matches:
    # 1. Word boundary or start of string
    # 2. One or more # symbols
    # 3. One or more word characters (letters, numbers, underscore)
    # This handles:
    # - Regular hashtags: #tag
    # - Multiple hashtags: #tag1#tag2
    # - Multiple # symbols: ##tag
    # - No space before hashtag: word#tag
    # Match any # followed by word characters, ignoring word boundaries
    pattern = r"#+([\w]+)"

    # Find all matches and extract the captured group (tag without #)
    # Convert to lowercase for case-insensitive uniqueness
    hashtags = []
    for raw_tag in re.findall(pattern, content):
        # Skip if tag is empty or just whitespace
        tag = raw_tag.strip()
        if not tag:
            continue
        # Convert to lowercase for case-insensitive uniqueness
        tag = tag.lower()
        hashtags.append(tag)

    # Remove duplicates while preserving order
    seen = set()
    return [tag for tag in hashtags if not (tag in seen or seen.add(tag))]


@require_database_config
@with_database_session(async_session=True)
async def process_post_hashtags(
    _config: FanslyConfig,
    post_obj: Post,
    content: str,
    session: AsyncSession | None = None,
) -> None:
    """Process hashtags for a post.

    Args:
        config: FanslyConfig instance
        post_obj: Post instance
        content: Post content string
        session: Optional AsyncSession for database operations
    """
    if not content:
        return

    hashtag_values = extract_hashtags(content)
    if not hashtag_values:
        return

    # Get all existing hashtags in one query
    # Use case-insensitive matching with LOWER() function
    result = await session.execute(
        select(Hashtag.id, Hashtag.value).where(
            func.lower(Hashtag.value).in_([v.lower() for v in hashtag_values])
        )
    )
    existing_rows = result.all()
    existing_hashtags = {
        row[1].lower(): Hashtag(id=row[0], value=row[1]) for row in existing_rows
    }

    # Create missing hashtags in one batch
    missing_values = [v for v in hashtag_values if v.lower() not in existing_hashtags]
    if missing_values:
        # Batch insert missing hashtags with ON CONFLICT on the functional index
        # PostgreSQL requires the functional expression in ON CONFLICT clause
        # We use a text construct to ensure the exact SQL matches the index
        from sqlalchemy import text

        # Build the INSERT statement with ON CONFLICT
        values_clause = ", ".join([f"(:value_{i})" for i in range(len(missing_values))])
        sql = text(
            f"INSERT INTO hashtags (value) VALUES {values_clause} "
            "ON CONFLICT (LOWER(value)) DO NOTHING"
        )
        params = {f"value_{i}": v for i, v in enumerate(missing_values)}
        await session.execute(sql, params)
        await session.flush()

        # Get the newly created hashtags
        result = await session.execute(
            select(Hashtag.id, Hashtag.value).where(
                func.lower(Hashtag.value).in_([v.lower() for v in missing_values])
            )
        )
        new_rows = result.all()
        new_hashtags = {
            row[1].lower(): Hashtag(id=row[0], value=row[1]) for row in new_rows
        }
        existing_hashtags.update(new_hashtags)

    # Create all associations in one batch
    associations = [
        {
            "postId": post_obj.id,
            "hashtagId": existing_hashtags[v.lower()].id,
        }
        for v in hashtag_values
    ]
    if associations:
        insert_stmt = insert(post_hashtags).values(associations)
        update_stmt = insert_stmt.on_conflict_do_nothing(
            index_elements=["postId", "hashtagId"]
        )
        await session.execute(update_stmt)
