from __future__ import annotations

import copy
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
    and_,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from config.decorators import with_database_session
from textio import json_output

from .account import process_media_bundles_data
from .attachment import Attachment, ContentType
from .base import Base
from .database import require_database_config
from .hashtag import Hashtag, process_post_hashtags

if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

    from .account import Account
    from .wall import Wall


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=False
    )
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
        lazy="selectin",
    )
    accountMentions: Mapped[list[Account]] = relationship(
        "Account",
        secondary="post_mentions",
        lazy="selectin",
    )
    walls: Mapped[list[Wall]] = relationship(
        "Wall",
        secondary="wall_posts",
        back_populates="posts",
        lazy="selectin",
    )
    hashtags: Mapped[list[Hashtag]] = relationship(
        "Hashtag",
        secondary="post_hashtags",
        back_populates="posts",
        lazy="selectin",
    )
    stash_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


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
    Column("accountId", Integer, ForeignKey("accounts.id"), nullable=True),
    Column(
        "handle", String, nullable=False
    ),  # Make handle required since it's our fallback
    # Composite unique constraint: either (postId, accountId) or (postId, handle) must be unique
    UniqueConstraint("postId", "accountId", name="uix_post_mentions_account"),
    UniqueConstraint("postId", "handle", name="uix_post_mentions_handle"),
)


async def process_posts_metadata(
    config: FanslyConfig,
    metadata: dict[str, any],
) -> None:
    """Process posts metadata.

    Args:
        config: FanslyConfig instance
        metadata: Dictionary containing posts metadata
    """
    json_output(1, "meta/post - p_p_metadata - metadata", metadata)
    # TODO: Implement posts metadata processing


@require_database_config
@with_database_session(async_session=True)
async def process_pinned_posts(
    config: FanslyConfig,
    account: Account,
    posts: list[dict[str, any]],
    session: AsyncSession | None = None,
) -> None:
    """Process pinned posts.

    Args:
        config: FanslyConfig instance
        account: Account instance
        posts: List of post data dictionaries
        session: Optional AsyncSession for database operations
    """
    posts = copy.deepcopy(posts)
    json_output(1, "meta/post - p_p_p - posts", posts)

    for post in posts:
        # Check if the post exists in the database
        result = await session.execute(select(Post).where(Post.id == post["postId"]))
        post_exists = result.scalar_one_or_none() is not None
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
        created_at = datetime.fromtimestamp((post["createdAt"] / 1000), timezone.utc)

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
        await session.execute(update_stmt)
        await session.flush()


async def process_timeline_posts(
    config: FanslyConfig,
    state: DownloadState,
    posts: list[dict[str, any]],
) -> None:
    """Process timeline posts.

    Args:
        config: FanslyConfig instance
        state: Current download state
        posts: List of post data dictionaries
    """
    from .account import process_account_data
    from .media import process_media_info

    posts = copy.deepcopy(posts)

    json_output(1, "meta/post - p_t_posts - posts", posts)

    # Process main timeline posts
    tl_posts = posts["posts"]
    for post in tl_posts:
        await _process_timeline_post(config, post)

    # Process aggregated posts if present
    if "aggregatedPosts" in posts:
        for post in posts["aggregatedPosts"]:
            await _process_timeline_post(config, post)

    # Process media
    accountMedia = posts["accountMedia"]
    for media in accountMedia:
        await process_media_info(config, media)

    # Process media bundles if present
    await process_media_bundles_data(config, posts, id_fields=["accountId"])

    # Process accounts
    accounts = posts["accounts"]
    for account in accounts:
        await process_account_data(config, data=account)


async def _process_post_mentions(
    session: AsyncSession,
    post_obj: Post,
    mentions: list[dict[str, any]],
) -> None:
    """Process mentions for a post.

    Args:
        session: SQLAlchemy async session
        post_obj: Post instance
        mentions: List of mention data dictionaries
    """
    for mention in mentions:
        handle = mention.get("handle", "").strip()
        account_id = mention.get("accountId", None)

        # Skip if we have neither a handle nor an accountId
        if not handle and account_id is None:
            json_output(
                2,
                "meta/post - _p_t_p - skipping mention with no handle or accountId",
                {"postId": post_obj.id, "mention": mention},
            )
            continue

        mention_data = {
            "postId": post_obj.id,
            "accountId": account_id,
            "handle": handle,
        }

        # Log if we're storing a mention without an accountId
        if account_id is None:
            json_output(
                2,
                "meta/post - _p_t_p - storing mention with handle only",
                {"postId": post_obj.id, "handle": handle},
            )

        # Try to insert/update the mention
        insert_stmt = sqlite_insert(post_mentions).values(mention_data)

        # If there's a conflict, update the handle if we have an accountId,
        # or update the accountId if we have one now
        update_stmt = insert_stmt.on_conflict_do_update(
            index_elements=(
                ["postId", "handle"] if account_id is None else ["postId", "accountId"]
            ),
            set_=(
                {"handle": handle}
                if account_id is None
                else {"accountId": account_id, "handle": handle}
            ),
            where=(
                # Only update if we have new information
                # Only update if we have a new accountId and the existing one is NULL
                post_mentions.c.accountId.is_(None)
                if account_id is not None
                else None
            ),
        )
        await session.execute(update_stmt)


async def _process_post_attachments(
    session: AsyncSession,
    post_obj: Post,
    attachments: list[dict[str, any]],
    config: FanslyConfig,
) -> None:
    """Process attachments for a post.

    Args:
        session: SQLAlchemy async session
        post_obj: Post instance
        attachments: List of attachment data dictionaries
        config: FanslyConfig instance
    """
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

    json_output(1, "meta/post - _p_t_p - attachments", attachments)
    for attachment_data in attachments:
        # Skip tipGoals attachments
        if attachment_data.get("contentType") == 7100:
            continue

        await Attachment.process_attachment(
            attachment_data,
            post_obj,
            attachment_known_relations,
            "postId",
            session=session,
            context="post",
        )


@require_database_config
@with_database_session(async_session=True)
async def _process_timeline_post(
    config: FanslyConfig,
    post: dict[str, any],
    session: AsyncSession | None = None,
) -> None:
    """Process a single timeline post.

    Args:
        config: FanslyConfig instance
        post: Post data dictionary
        session: Optional AsyncSession for database operations
    """
    # Known attributes that are handled separately
    known_relations = {
        # Handled relationships
        "attachments",
        "accountMentions",
        "walls",
        # Intentionally ignored fields
        "fypFlags",
        "likeCount",
        "timelineReadPermissionFlags",
        "accountTimelineReadPermissionFlags",
        "mediaLikeCount",
        "tipAmount",
        "totalTipAmount",
        "attachmentTipAmount",
        "replyPermissionFlags",
        "replyCount",
        "postReplyPermissionFlags",
        "liked",
    }

    # Process post data
    json_output(1, "meta/post - _p_t_p - post", post)
    filtered_post, _ = Post.process_data(
        post, known_relations, "meta/post - _p_t_p", ("createdAt", "expiresAt")
    )
    json_output(1, "meta/post - _p_t_p - filtered", filtered_post)

    # Ensure required fields are present before proceeding
    if "accountId" not in filtered_post:
        json_output(
            1,
            "meta/post - missing_required_field",
            {"postId": filtered_post.get("id"), "missing_field": "accountId"},
        )
        return  # Skip this post if accountId is missing

    # Get or create post
    post_obj, created = await Post.async_get_or_create(
        session,
        {"id": filtered_post["id"]},
        filtered_post,
    )

    # Update fields
    Base.update_fields(post_obj, filtered_post)
    await session.flush()

    # Process account mentions if present
    if "accountMentions" in post:
        await _process_post_mentions(session, post_obj, post["accountMentions"])

    # Process hashtags from content
    if post_obj.content:
        await process_post_hashtags(config, post_obj, post_obj.content, session=session)
        await session.flush()

    # Process attachments if present
    if "attachments" in post:
        await _process_post_attachments(session, post_obj, post["attachments"], config)
