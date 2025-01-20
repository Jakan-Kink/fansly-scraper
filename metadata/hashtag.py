from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import (
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
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .post import Post


class Hashtag(Base):
    __tablename__ = "hashtags"
    __table_args__ = (UniqueConstraint("value", name="uq_hashtags_value"),)

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    value: Mapped[str] = mapped_column(
        String,
        unique=True,
        nullable=False,
        index=True,
    )
    stash_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    posts: Mapped[list[Post]] = relationship(
        "Post",
        secondary="post_hashtags",
        back_populates="hashtags",
        lazy="selectin",
    )


# Association table for the many-to-many relationship between posts and hashtags
post_hashtags = Table(
    "post_hashtags",
    Base.metadata,
    Column(
        "postId", Integer, ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True
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
    pattern = r"(?:^|\b)#+([a-zA-Z0-9_]+)"

    # Find all matches and extract the captured group (tag without #)
    # Convert to lowercase for case-insensitive uniqueness
    hashtags = []
    for tag in re.findall(pattern, content):
        # Skip if tag is empty or just whitespace
        tag = tag.strip()
        if not tag:
            continue
        # Convert to lowercase for case-insensitive uniqueness
        tag = tag.lower()
        hashtags.append(tag)

    # Remove duplicates while preserving order
    seen = set()
    return [tag for tag in hashtags if not (tag in seen or seen.add(tag))]


def process_post_hashtags(session: Session, post_obj: Post, content: str) -> None:
    """Process hashtags for a post.

    Args:
        session: SQLAlchemy session
        post_obj: Post instance
        content: Post content string
    """
    if not content:
        return

    hashtag_values = extract_hashtags(content)
    if not hashtag_values:
        return

    for value in hashtag_values:
        # First try to get existing hashtag
        hashtag = session.execute(
            select(Hashtag).where(func.lower(Hashtag.value) == func.lower(value))
        ).scalar()

        if not hashtag:
            # If hashtag doesn't exist, create it
            hashtag = Hashtag(value=value)
            session.add(hashtag)
            session.flush()  # Ensure the hashtag has an ID

        # Add association using the association table
        insert_stmt = sqlite_insert(post_hashtags).values(
            postId=post_obj.id,
            hashtagId=hashtag.id,
        )
        update_stmt = insert_stmt.on_conflict_do_nothing()
        session.execute(update_stmt)
