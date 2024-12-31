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

import copy
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from textio import json_output

from .base import Base
from .database import require_database_config
from .post import process_timeline_posts

if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

    from .account import Account
    from .post import Post


class Wall(Base):
    """Represents a content wall in a user's profile.

    A wall is a collection of posts that can be organized and displayed separately
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

    __tablename__ = "walls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=False, index=True
    )
    account: Mapped[Account] = relationship(
        "Account",
        foreign_keys=[accountId],
        back_populates="walls",
        lazy="select",
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
        lazy="select",
    )


wall_posts = Table(
    "wall_posts",
    Base.metadata,
    Column("wallId", Integer, ForeignKey("walls.id"), primary_key=True),
    Column("postId", Integer, ForeignKey("posts.id"), primary_key=True),
)


@require_database_config
def process_account_walls(
    config: FanslyConfig,
    account: Account,
    walls_data: list[dict[str, any]],
    session=None,
) -> None:
    """Process walls data for an account.

    Updates or creates walls for an account based on provided data. Handles wall
    ordering, metadata updates, and cleanup of removed walls.

    Args:
        config: FanslyConfig instance for database access
        account: Account object that owns the walls
        walls_data: List of wall data dictionaries from the API
        session: Optional SQLAlchemy session. If not provided, a new session will be created.

    Note:
        - Existing walls not in walls_data will be removed
        - Wall positions are preserved through the pos field
        - Wall-post associations are maintained separately
    """
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

    def _process_walls(session: Session) -> None:
        # Process each wall
        for wall_data in walls_data:
            # Process wall data
            filtered_wall, _ = Wall.process_data(
                wall_data, known_relations, "meta/wall - p_a_w-_p_w"
            )

            # Query first approach
            wall = session.query(Wall).get(wall_data["id"])

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
                if getattr(wall, key) != value:
                    setattr(wall, key, value)

            session.flush()

        # After processing all walls, remove any that no longer exist
        current_wall_ids = {wall_data["id"] for wall_data in walls_data}
        existing_walls = session.query(Wall).filter(Wall.accountId == account.id).all()
        for wall in existing_walls:
            if wall.id not in current_wall_ids:
                session.delete(wall)

    if session is not None:
        # Use existing session
        _process_walls(session)
    else:
        # Create new session if none provided
        with config._database.sync_session() as new_session:
            _process_walls(new_session)
            new_session.commit()


@require_database_config
def process_wall_posts(
    config: FanslyConfig, state: DownloadState, wall_id: str, posts_data: dict
) -> None:
    """Process posts from a specific wall.

    This function wraps process_timeline_posts to add wall association to posts.

    Args:
        config: FanslyConfig instance
        state: Current download state
        wall_id: ID of the wall these posts belong to
        posts_data: Timeline-style posts data from the API
    """
    posts_data = copy.deepcopy(posts_data)
    json_output(1, "meta/wall - p_w_p - posts_data", posts_data)

    # First process posts normally
    process_timeline_posts(config, state, posts_data)

    # Then add wall association for each post
    session: Session
    with config._database.sync_session() as session:
        wall = session.query(Wall).get(wall_id)
        if not wall:
            return

        for post_data in posts_data["posts"]:
            post_id = post_data["id"]
            # Add wall-post association using upsert to avoid conflicts
            session.execute(
                wall_posts.insert()
                .values(wallId=wall_id, postId=post_id)
                .prefix_with("OR IGNORE")
            )
        session.commit()
