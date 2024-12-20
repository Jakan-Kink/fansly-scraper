from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Column, ForeignKey, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from textio import json_output

from .base import Base
from .post import process_timeline_posts

if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

    from .account import Account
    from .post import Post


class Wall(Base):
    __tablename__ = "walls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId = mapped_column(Integer, ForeignKey("accounts.id"), nullable=False)
    account: Mapped[Account] = relationship(
        "Account", foreign_keys=[accountId], back_populates="walls", lazy="joined"
    )
    pos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(String, nullable=True)
    # metadata: Mapped[str] = mapped_column(String, nullable=True)
    posts: Mapped[list[Post]] = relationship(
        "Post", secondary="wall_posts", back_populates="walls"
    )


wall_posts = Table(
    "wall_posts",
    Base.metadata,
    Column("wallId", Integer, ForeignKey("walls.id"), primary_key=True),
    Column("postId", Integer, ForeignKey("posts.id"), primary_key=True),
)


def process_account_walls(
    config: FanslyConfig, account: Account, walls_data: list
) -> None:
    with config._database.sync_session() as session:
        # Fetch existing walls for this account
        existing_walls = {
            wall.id: wall
            for wall in session.query(Wall).filter(Wall.accountId == account.id).all()
        }

        # Update or add new walls
        for wall_data in walls_data:
            wall_id = wall_data["id"]
            # First try to get any existing wall with this ID
            wall = session.query(Wall).get(wall_id)

            if wall:
                # Wall exists, update it
                wall.accountId = account.id  # Ensure it belongs to current account
                wall.pos = wall_data["pos"]
                wall.name = wall_data["name"]
                wall.description = wall_data["description"]
            else:
                # Create new wall
                wall = Wall(
                    id=wall_id,
                    accountId=account.id,  # Use current account's ID
                    pos=wall_data["pos"],
                    name=wall_data["name"],
                    description=wall_data["description"],
                )
                session.add(wall)
            session.flush()

        # Remove walls that no longer belong to this account
        new_wall_ids = {wall_data["id"] for wall_data in walls_data}
        for wall_id in existing_walls:
            if wall_id not in new_wall_ids:
                session.delete(existing_walls[wall_id])
        session.commit()


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
    with config._database.sync_session() as session:
        wall = session.query(Wall).get(wall_id)
        if not wall:
            return

        for post_data in posts_data["posts"]:
            post_id = post_data["id"]
            # Add wall-post association
            session.execute(
                wall_posts.insert().values(
                    wallId=wall_id,
                    postId=post_id,
                )
            )
        session.commit()
