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
        "Attachment",
        back_populates="post",
        cascade="all, delete-orphan",
        order_by="Attachment.pos",
        lazy="select",
    )
    accountMentions: Mapped[list[Account]] = relationship(
        "Account",
        secondary="post_mentions",
        lazy="select",
    )
    walls: Mapped[list[Wall]] = relationship(
        "Wall",
        secondary="wall_posts",
        back_populates="posts",
        lazy="select",
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
    config: FanslyConfig, account: Account, posts: list[dict[str, any]], session=None
) -> None:
    """Process pinned posts.

    Args:
        config: FanslyConfig instance
        account: Account instance
        posts: List of post data dictionaries
        session: Optional SQLAlchemy session. If not provided, a new session will be created.
    """
    json_output(1, "meta/post - p_p_p - posts", posts)

    def _process_posts(session):
        for post in posts:
            # Check if the post exists in the database
            post_exists = (
                session.query(Post).filter_by(id=post["postId"]).first() is not None
            )
            if not post_exists:
                json_output(
                    1,
                    "meta/post - p_p_p - skipping_missing_post",
                    {
                        "postId": post["postId"],
                        "accountId": account.id,
                        "reason": "Post does not exist in database",
                    },
                )
                continue

            # Convert timestamp once to avoid repeated conversions
            created_at = datetime.fromtimestamp(
                (post["createdAt"] / 1000), timezone.utc
            )

            insert_stmt = sqlite_insert(pinned_posts).values(
                postId=post["postId"],
                accountId=account.id,
                pos=post["pos"],
                createdAt=created_at,
            )
            update_stmt = insert_stmt.on_conflict_do_update(
                index_elements=["postId", "accountId"],
                set_=dict(
                    pos=post["pos"],
                    createdAt=created_at,
                ),
            )
            session.execute(update_stmt)
            session.flush()

    if session is not None:
        # Use existing session
        _process_posts(session)
    else:
        # Create new session if none provided
        with config._database.sync_session() as new_session:
            _process_posts(new_session)
            new_session.commit()


def process_timeline_posts(
    config: FanslyConfig, state: DownloadState, posts: list[dict[str, any]]
) -> None:
    """Process timeline posts."""
    from .account import process_account_data, process_media_bundles
    from .media import process_media_info

    json_output(1, "meta/post - p_t_posts - posts", posts)

    # Process main timeline posts
    tl_posts = posts["posts"]
    for post in tl_posts:
        _process_timeline_post(config, post)

    # Process aggregated posts if present
    if "aggregatedPosts" in posts:
        for post in posts["aggregatedPosts"]:
            _process_timeline_post(config, post)

    # Process media
    accountMedia = posts["accountMedia"]
    for media in accountMedia:
        process_media_info(config, media)

    # Process media bundles if present
    if "accountMediaBundles" in posts:
        # Get the account ID from the first post or media if available
        account_id = None
        if tl_posts:
            account_id = tl_posts[0].get("accountId")
        elif accountMedia:
            account_id = accountMedia[0].get("accountId")

        if account_id:
            process_media_bundles(config, account_id, posts["accountMediaBundles"])

    # Process accounts
    accounts = posts["accounts"]
    for account in accounts:
        process_account_data(config, data=account)


def _process_timeline_post(config: FanslyConfig, post: dict[str, any]) -> None:
    """Process a single timeline post."""
    # Known attributes that are handled separately
    known_relations = {
        # Handled relationships
        "attachments",
        "accountMentions",
        "walls",
        # Intentionally ignored fields
    }

    # Process post data
    json_output(1, "meta/post - _p_t_p - post", post)
    filtered_post, _ = Post.process_data(
        post, known_relations, "meta/post - _p_t_p", ("createdAt", "expiresAt")
    )
    json_output(1, "meta/post - _p_t_p - filtered", filtered_post)

    with config._database.sync_session() as session:
        # Query first approach
        post_obj = session.query(Post).get(filtered_post["id"])

        # Ensure required fields are present before proceeding
        if "accountId" not in filtered_post:
            json_output(
                1,
                "meta/post - missing_required_field",
                {"postId": filtered_post.get("id"), "missing_field": "accountId"},
            )
            return  # Skip this post if accountId is missing

        if not post_obj:
            post_obj = Post(**filtered_post)
            session.add(post_obj)

        # Update fields that have changed
        for key, value in filtered_post.items():
            if getattr(post_obj, key) != value:
                setattr(post_obj, key, value)

        session.flush()

        # Process attachments if present
        if "attachments" in post:
            # Known attributes for attachments that are handled separately
            attachment_known_relations = {
                "post",
                "media",
                "preview",
                "variants",
                "message",
                "attachmentFlags",
                "attachmentStatus",
                "attachmentType",
                "permissions",
            }

            json_output(1, "meta/post - _p_t_p - attachments", post["attachments"])
            for attachment_data in post["attachments"]:
                # Skip tipGoals attachments
                if attachment_data.get("contentType") == 7100:
                    continue

                attachment_data["postId"] = post_obj.id

                # Convert contentType to enum
                try:
                    attachment_data["contentType"] = ContentType(
                        attachment_data["contentType"]
                    )
                except ValueError:
                    old_content_type = attachment_data["contentType"]
                    attachment_data["contentType"] = None
                    json_output(
                        2,
                        f"meta/post - _p_t_p - invalid_content_type: {old_content_type}",
                        attachment_data,
                    )

                # Process attachment data
                filtered_attachment, _ = Attachment.process_data(
                    attachment_data,
                    attachment_known_relations,
                    "meta/post - _p_t_p-attach",
                )

                # Query existing attachment
                attachment = (
                    session.query(Attachment)
                    .filter_by(
                        postId=post_obj.id, contentId=filtered_attachment["contentId"]
                    )
                    .first()
                )

                if attachment is None:
                    attachment = Attachment(**filtered_attachment)
                    session.add(attachment)
                # Update fields that have changed
                for key, value in filtered_attachment.items():
                    if getattr(attachment, key) != value:
                        setattr(attachment, key, value)

                session.flush()

        session.commit()
