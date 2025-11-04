"""Wall management module.

This module handles content walls in user profiles, including their creation,
updates, and post associations. Walls are collections of posts that can be
organized and displayed separately from the main timeline.

Features:
- Wall creation and management
- Post-to-wall associations
- Ordered wall listings
- Wall post processing
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm.attributes import set_committed_value

from config.decorators import with_database_session
from textio import json_output

from .base import Base
from .database import require_database_config
from .post import Post, process_timeline_posts


if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

    from .account import Account


class Wall(Base):
    """A wall is a collection of posts that can be organized and displayed separately
    from the main timeline. Walls have their own metadata and can be ordered within
    a profile.

    Attributes:
        id: Unique identifier for the wall
        accountId: ID of the account that owns this wall
        account: Relationship to the owning Account
        pos: Position/order of this wall in the profile
        name: Display name of the wall
        description: Wall description text
        posts: List of Post objects associated with this wall
    Note:
        The following fields from the API are intentionally ignored as they are not
        needed for the application's functionality:
        - metadata: Arbitrary metadata for the wall
    """

    __table_args__ = (
        # Composite index for efficient account+created_at lookups
        Index("idx_wall_account_created", "accountId", "createdAt"),
    )

    __tablename__ = "walls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    accountId = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=False, index=True
    )
    account: Mapped[Account] = relationship(
        "Account",
        foreign_keys=[accountId],
        back_populates="walls",
    )
    pos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(String, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    # metadata: Mapped[str] = mapped_column(String, nullable=True)
    posts: Mapped[list[Post]] = relationship(
        "Post",
        secondary="wall_posts",
        back_populates="walls",
    )
    stash_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


wall_posts = Table(
    "wall_posts",
    Base.metadata,
    Column("wallId", BigInteger, ForeignKey("walls.id"), primary_key=True),
    Column("postId", BigInteger, ForeignKey("posts.id"), primary_key=True),
    # Add indexes for efficient lookups
    Index("idx_wall_posts_post", "postId"),
    Index("idx_wall_posts_wall_post", "wallId", "postId"),
)


@require_database_config
@with_database_session(async_session=True)
async def process_account_walls(
    config: FanslyConfig,  # noqa: ARG001
    account: Account,
    walls_data: list[dict[str, any]],
    session: AsyncSession | None = None,
) -> None:
    """Process walls data for an account.

    Updates or creates walls for an account based on provided data. Handles wall
    ordering, metadata updates, and cleanup of removed walls.

    Args:
        config: FanslyConfig instance for database access
        account: Account object that owns the walls
        walls_data: List of wall data dictionaries from the API
        session: Optional AsyncSession for database operations

    Note:
        - Existing walls not in walls_data will be removed
        - Wall positions are preserved through the pos field
        - Wall-post associations are maintained separately
    """
    # Ensure we have a valid session
    if not session:
        raise RuntimeError("Database session is required")

    walls_data = copy.deepcopy(walls_data)
    # Known attributes that are handled separately
    known_relations = {
        # Handled relationships
        "posts",
        # Intentionally ignored fields
        "metadata",
        "wallFlags",
        "wallStatus",
        "wallType",
        "permissions",
    }

    # Process each wall
    for wall_data in walls_data:
        # Process wall data
        filtered_wall, _ = Wall.process_data(
            wall_data, known_relations, "meta/wall - p_a_w-_p_w"
        )

        # Query first approach
        wall = await session.get(Wall, wall_data["id"])

        # Ensure required fields are present before proceeding
        if "id" not in filtered_wall:
            json_output(
                1,
                "meta/wall - missing_required_field",
                {"missing_field": "id"},
            )
            continue  # Skip this wall if id is missing

        # Create if doesn't exist with minimum required fields
        if wall is None:
            filtered_wall["accountId"] = account.id  # Ensure accountId is set
            wall = Wall(**filtered_wall)
            session.add(wall)
        # Update fields that have changed
        for key, value in filtered_wall.items():
            new_value = value
            if isinstance(value, str):
                # remove surrogate code units to avoid UnicodeEncodeError
                new_value = "".join(
                    ch for ch in value if not (0xD800 <= ord(ch) <= 0xDFFF)
                )
            if getattr(wall, key) != new_value:
                set_committed_value(wall, key, new_value)
        await session.flush()

    # Only delete walls if this is a full account data update
    # This function is called from process_account_data which gets all walls for an account
    if len(walls_data) > 0:  # Only if we have any walls data
        current_wall_ids = {wall_data["id"] for wall_data in walls_data}
        result = await session.execute(select(Wall).where(Wall.accountId == account.id))
        existing_walls = result.scalars().all()
        json_output(
            1, "meta/wall - existing_walls", [wall.id for wall in existing_walls]
        )
        json_output(1, "meta/wall - current_wall_ids", list(current_wall_ids))
        for wall in existing_walls:
            if wall.accountId != account.id:
                continue
            if wall.id not in current_wall_ids:
                await session.delete(wall)


@require_database_config
@with_database_session(async_session=True)
async def process_wall_posts(
    config: FanslyConfig,
    state: DownloadState,
    wall_id: str,
    posts_data: dict,
    session: AsyncSession | None = None,
) -> None:
    """Process posts from a specific wall.

    This function wraps process_timeline_posts to add wall association to posts.

    Args:
        config: FanslyConfig instance
        state: Current download state
        wall_id: ID of the wall these posts belong to
        posts_data: Timeline-style posts data from the API
        session: Optional AsyncSession for database operations
    """
    # Ensure we have a valid session
    if not session:
        raise RuntimeError("Database session is required")

    posts_data = copy.deepcopy(posts_data)
    json_output(1, "meta/wall - p_w_p - posts_data", posts_data)

    # First process posts normally
    await process_timeline_posts(config, state, posts_data, session=session)

    wall = await session.get(Wall, wall_id)
    if wall is None:
        wall = Wall(id=wall_id, accountId=state.creator_id)
        session.add(wall)
        await session.flush()

    # Get all posts in a single query, with unique deduplication applied in the query
    post_ids = [post["id"] for post in posts_data["posts"]]
    stmt = select(Post).where(Post.id.in_(post_ids))
    result = await session.execute(stmt)
    result = result.unique()
    raw_posts = result.scalars().all()
    if asyncio.iscoroutine(raw_posts):
        posts = await raw_posts
    else:
        posts = raw_posts

    # Load existing posts via awaitable_attrs to avoid greenlet errors
    existing_posts = await wall.awaitable_attrs.posts
    existing_post_ids = {p.id for p in existing_posts}

    # Add new posts to wall's posts list without removing existing ones
    for post in posts:
        if post.id not in existing_post_ids:
            wall.posts.append(post)
            existing_post_ids.add(post.id)
    await session.flush()
