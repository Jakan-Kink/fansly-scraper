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

from typing import TYPE_CHECKING

from sqlalchemy import Column, ForeignKey, Integer, String, Table
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from textio import json_output

from .base import Base
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

    def _process_walls(session: Session) -> None:
        # Fetch existing walls for this account
        existing_walls = {
            wall.id: wall
            for wall in session.query(Wall).filter(Wall.accountId == account.id).all()
        }

        # Update or add new walls
        for wall_data in walls_data:
            wall_id = wall_data["id"]
            # Query for existing wall
            wall = session.query(Wall).get(wall_id)
            if not wall:
                wall = Wall(
                    id=wall_id,
                    accountId=account.id,
                    pos=wall_data["pos"],
                    name=wall_data["name"],
                    description=wall_data["description"],
                )
                session.add(wall)
            else:
                # Get valid column names for Wall
                wall_columns = {column.name for column in inspect(Wall).columns}

                # Log any unknown attributes
                unknown_attrs = {
                    k: v for k, v in wall_data.items() if k not in wall_columns
                }
                if unknown_attrs:
                    json_output(1, "meta/wall - wall_unknown_attributes", unknown_attrs)

                # Update wall attributes only if they've changed
                for field in ["pos", "name", "description"]:
                    if field in wall_columns and field in wall_data:
                        if getattr(wall, field) != wall_data[field]:
                            setattr(wall, field, wall_data[field])

            session.flush()

        # Remove walls that no longer belong to this account
        new_wall_ids = {wall_data["id"] for wall_data in walls_data}
        for wall_id in existing_walls:
            if wall_id not in new_wall_ids:
                session.delete(existing_walls[wall_id])

    if session is not None:
        # Use existing session
        _process_walls(session)
    else:
        # Create new session if none provided
        with config._database.sync_session() as new_session:
            _process_walls(new_session)
            new_session.commit()


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
