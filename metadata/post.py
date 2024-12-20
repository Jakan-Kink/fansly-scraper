from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.inspection import inspect

# from sqlalchemy.exc import IntegrityError
# from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from textio import json_output

from .attachment import Attachment, ContentType
from .base import Base

if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

    from .account import Account
    from .wall import Wall


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId = mapped_column(Integer, ForeignKey("accounts.id"), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=True, default="")
    fypFlag: Mapped[int] = mapped_column(Integer, nullable=True, default=0)
    inReplyTo: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    inReplyToRoot: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    expiresAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attachments: Mapped[list[Attachment | None]] = relationship(
        "Attachment", back_populates="post", cascade="all, delete-orphan"
    )
    accountMentions: Mapped[list[Account]] = relationship(
        "Account", secondary="post_mentions"
    )
    walls: Mapped[list[Wall]] = relationship(
        "Wall", secondary="wall_posts", back_populates="posts"
    )


pinned_posts = Table(
    "pinned_posts",
    Base.metadata,
    Column("postId", Integer, ForeignKey("posts.id"), primary_key=True),
    Column("accountId", Integer, ForeignKey("accounts.id"), primary_key=True),
    Column("pos", Integer, nullable=False),
    Column("createdAt", DateTime(timezone=True), nullable=True),
)

post_mentions = Table(
    "post_mentions",
    Base.metadata,
    Column("postId", Integer, ForeignKey("posts.id"), primary_key=True),
    Column("accountId", Integer, ForeignKey("accounts.id"), primary_key=True),
    Column("handle", String, nullable=True),
    UniqueConstraint("postId", "accountId"),
)


def process_posts_metadata(metadata: dict[str, any]) -> None:
    """Process posts metadata."""
    json_output(1, "meta/post - p_p_metadata - metadata", metadata)
    pass


def process_pinned_posts(
    config: FanslyConfig, account: Account, posts: list[dict[str, any]]
) -> None:
    """Process pinned posts."""
    json_output(1, "meta/post - p_p_p - posts", posts)
    with config._database.sync_session() as session:
        for post in posts:
            insert_stmt = sqlite_insert(pinned_posts).values(
                postId=post["postId"],
                accountId=account.id,
                pos=post["pos"],
                createdAt=datetime.fromtimestamp(
                    (post["createdAt"] / 1000), timezone.utc
                ),
            )
            update_stmt = insert_stmt.on_conflict_do_update(
                index_elements=["postId", "accountId"],
                set_=dict(
                    pos=post["pos"],
                    createdAt=datetime.fromtimestamp(
                        (post["createdAt"] / 1000), timezone.utc
                    ),
                ),
            )
            session.execute(update_stmt)
            session.flush()
        session.commit()
    pass


def process_timeline_posts(
    config: FanslyConfig, state: DownloadState, posts: list[dict[str, any]]
) -> None:
    """Process timeline posts."""
    from .account import process_account_data
    from .media import process_media_info

    json_output(1, "meta/post - p_t_posts - posts", posts)
    tl_posts = posts["posts"]
    for post in tl_posts:
        _process_timeline_post(config, post)
    accountMedia = posts["accountMedia"]
    for media in accountMedia:
        process_media_info(config, media)
    accounts = posts["accounts"]
    for account in accounts:
        process_account_data(config, data=account)
    pass


def _process_timeline_post(config: FanslyConfig, post: dict[str, any]) -> None:
    """Process a single timeline post."""
    post["createdAt"] = datetime.fromtimestamp((post["createdAt"]), timezone.utc)
    post["expiresAt"] = (
        datetime.fromtimestamp((post["expiresAt"] / 1000), timezone.utc)
        if post.get("expiresAt")
        else None
    )
    json_output(1, "meta/post - _p_t_p - post", post)
    post_columns = {column.name for column in inspect(Post).columns}
    filtered_post = {k: v for k, v in post.items() if k in post_columns}
    json_output(1, "meta/post - _p_t_p - filtered", filtered_post)
    with config._database.sync_session() as session:
        existing_post = session.query(Post).get(filtered_post["id"])
        if existing_post:
            for key, value in filtered_post.items():
                setattr(existing_post, key, value)
        else:
            session.add(Post(**filtered_post))
        session.flush()
        modified_post = session.query(Post).get(filtered_post["id"])
        if "attachments" in post:
            json_output(1, "meta/post - _p_t_p - attachments", post["attachments"])
            for attachment in post["attachments"]:
                attachment["postId"] = modified_post.id
                # Convert contentType to enum
                try:
                    attachment["contentType"] = ContentType(attachment["contentType"])
                except ValueError:
                    old_content_type = attachment["contentType"]
                    attachment["contentType"] = None
                    json_output(
                        2,
                        f"meta/post - _p_t_p - invalid_content_type: {old_content_type}",
                        attachment,
                    )
                existing_attachment = (
                    session.query(Attachment)
                    .filter_by(
                        postId=modified_post.id, contentId=attachment["contentId"]
                    )
                    .first()
                )
                if existing_attachment:
                    for key, value in attachment.items():
                        setattr(existing_attachment, key, value)
                else:
                    session.add(Attachment(**attachment))
                session.flush()
        session.commit()
