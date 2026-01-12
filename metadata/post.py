from __future__ import annotations

import copy
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    and_,
    bindparam,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config.decorators import with_database_session
from textio import json_output

from .attachment import Attachment, ContentType
from .base import Base
from .database import require_database_config
from .hashtag import Hashtag, process_post_hashtags


if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

    from .account import Account
    from .wall import Wall


# Table definitions must come before the Post class to allow direct references in relationships
pinned_posts = Table(
    "pinned_posts",
    Base.metadata,
    Column("postId", BigInteger, ForeignKey("posts.id"), primary_key=True),
    Column("accountId", BigInteger, ForeignKey("accounts.id"), primary_key=True),
    Column("pos", Integer, nullable=False),
    Column("createdAt", DateTime(timezone=True), nullable=True),
)

post_mentions = Table(
    "post_mentions",
    Base.metadata,
    Column(
        "postId",
        BigInteger,
        ForeignKey("posts.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("accountId", BigInteger, nullable=True),  # NO FK - can be non-Fansly user
    Column(
        "handle", String, nullable=False
    ),  # Make handle required since it's our fallback
    # Composite unique constraint: either (postId, accountId) or (postId, handle) must be unique
    UniqueConstraint("postId", "accountId", name="uix_post_mentions_account"),
    UniqueConstraint("postId", "handle", name="uix_post_mentions_handle"),
    # Indexes for efficient lookups
    Index("ix_post_mentions_accountId", "accountId"),  # Simple index on accountId
    Index(
        "ix_post_mentions_account",
        "postId",
        "accountId",
        postgresql_where=Column("accountId").isnot(None),
    ),
    Index(
        "ix_post_mentions_handle",
        "postId",
        "handle",
        postgresql_where=Column("handle").isnot(None),
    ),
)


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    accountId: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=False
    )
    account: Mapped[Account] = relationship(
        "Account",
        foreign_keys=[accountId],
        lazy="joined",  # Use joined loading since we always need account info
        back_populates="posts",  # Add back reference
    )
    content: Mapped[str] = mapped_column(String, nullable=True, default="")
    fypFlag: Mapped[int] = mapped_column(Integer, nullable=True, default=0)
    inReplyTo: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, default=None
    )
    inReplyToRoot: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, default=None
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
        lazy="joined",  # Use joined loading for attachments since we always need them
    )
    accountMentions: Mapped[list[Account]] = relationship(
        "Account",
        secondary=post_mentions,
        primaryjoin="Post.id == post_mentions.c.postId",
        secondaryjoin="post_mentions.c.accountId == Account.id",
        lazy="select",  # Use select loading since mentions are accessed less frequently
        viewonly=True,  # Mark as viewonly since accountId in post_mentions may not reference an existing Account
    )
    walls: Mapped[list[Wall]] = relationship(
        "Wall",
        secondary="wall_posts",
        back_populates="posts",
        lazy="select",  # Use select loading since walls are accessed less frequently
    )
    hashtags: Mapped[list[Hashtag]] = relationship(
        "Hashtag",
        secondary="post_hashtags",
        back_populates="posts",
    )
    stash_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


@require_database_config
@with_database_session(async_session=True)
async def process_pinned_posts(
    config: FanslyConfig,  # noqa: ARG001
    account: Account,
    posts: list[dict[str, any]],
    session: AsyncSession | None = None,
) -> None:
    """Process pinned posts using batch operations.

    Args:
        config: FanslyConfig instance
        account: Account instance
        posts: List of post data dictionaries
        session: Optional AsyncSession for database operations
    """
    posts = copy.deepcopy(posts)
    json_output(1, "meta/post - p_p_p - posts", posts)

    if not posts:
        return

    # Batch check if posts exist in the database
    post_ids = [post["postId"] for post in posts]
    result = await session.execute(select(Post.id).where(Post.id.in_(post_ids)))
    existing_post_ids = {row[0] for row in result.fetchall()}

    # Collect values for batch upsert
    values_to_upsert = []
    for post in posts:
        post_id = post["postId"]
        if post_id not in existing_post_ids:
            json_output(
                1,
                "meta/post - p_p_p - skipping_missing_post",
                {
                    "postId": post_id,
                    "accountId": account.id,
                    "reason": "Post does not exist in database",
                },
            )
            continue

        # Convert timestamp once to avoid repeated conversions
        created_at = datetime.fromtimestamp((post["createdAt"] / 1000), UTC)

        values_to_upsert.append(
            {
                "postId": post_id,
                "accountId": account.id,
                "pos": post["pos"],
                "createdAt": created_at,
            }
        )

    if not values_to_upsert:
        return

    # Batch upsert all pinned posts in one operation
    stmt = insert(pinned_posts).values(values_to_upsert)
    stmt = stmt.on_conflict_do_update(
        index_elements=["postId", "accountId"],
        set_={
            "pos": stmt.excluded.pos,
            "createdAt": stmt.excluded.createdAt,
        },
    )
    await session.execute(stmt)


@require_database_config
@with_database_session(async_session=True)
async def process_timeline_posts(
    config: FanslyConfig,
    state: DownloadState,
    posts_data: dict[str, Any],
    session: AsyncSession | None = None,
) -> None:
    """Process timeline posts and related data.

    Args:
        config: FanslyConfig instance
        state: Current download state
        posts_data: Post data from API response
        session: Optional AsyncSession for database operations
    """
    # Avoid circular imports
    from .account import Account, process_account_data
    from .media import process_media_info

    start_time = time.time()
    posts = copy.deepcopy(posts_data)
    copy_time = time.time() - start_time

    json_output(1, "meta/post - p_t_posts - posts", posts)

    # Process accounts
    accounts_start = time.time()

    # First ensure creator account exists and is tracked
    account = None
    if state.creator_id:
        # Get the account by ID first
        stmt = select(Account).where(Account.id == state.creator_id)
        result = await session.execute(stmt)
        account = result.scalar_one_or_none()

        if not account and "account" in posts:
            # If no account found but we have account data, process it
            await process_account_data(config, data=posts["account"], session=session)
            # Get the fresh account instance
            result = await session.execute(stmt)
            account = result.scalar_one()

    if account:
        session.add(account)  # Ensure account is tracked

    # Process other accounts in the data
    accounts = posts.get("accounts", [])
    for account_data in accounts:
        account_start = time.time()
        # Process account without refresh to avoid None errors
        await process_account_data(config, data=account_data, session=session)
        json_output(
            2,
            "meta/post - timing - single_account",
            {
                "account_id": account_data.get("id"),
                "time_taken": time.time() - account_start,
            },
        )
    accounts_time = time.time() - accounts_start

    # Process main timeline posts
    tl_posts = posts["posts"]
    posts_start = time.time()
    for post in tl_posts:
        post_start = time.time()
        await _process_timeline_post(config, post, session=session)
        json_output(
            2,
            "meta/post - timing - single_post",
            {"post_id": post.get("id"), "time_taken": time.time() - post_start},
        )
    posts_time = time.time() - posts_start

    # Process aggregated posts if present
    agg_time = 0
    if "aggregatedPosts" in posts:
        agg_start = time.time()
        for post in posts["aggregatedPosts"]:
            await _process_timeline_post(config, post, session=session)
        agg_time = time.time() - agg_start

    # Process media in batches
    media_start = time.time()
    account_media = posts["accountMedia"]
    batch_size = 15  # Process one timeline page worth of media at a time

    for i in range(0, len(account_media), batch_size):
        batch = account_media[i : i + batch_size]
        batch_start = time.time()

        # Process the entire batch at once
        await process_media_info(config, {"batch": batch}, session=session)

        json_output(
            2,
            "meta/post - timing - media_batch",
            {
                "batch_start": i + 1,
                "batch_end": min(i + batch_size, len(account_media)),
                "total_media": len(account_media),
                "batch_size": len(batch),
                "time_taken": time.time() - batch_start,
            },
        )
    media_time = time.time() - media_start

    # Process media bundles if present
    bundles_start = time.time()
    from .account import (
        process_media_bundles_data,  # Import here to avoid circular import
    )

    await process_media_bundles_data(
        config, posts, id_fields=["accountId"], session=session
    )
    bundles_time = time.time() - bundles_start

    total_time = time.time() - start_time
    json_output(
        2,
        "meta/post - timing - timeline_breakdown",
        {
            "total_time": total_time,
            "copy_time": copy_time,
            "posts_time": posts_time,
            "aggregated_posts_time": agg_time,
            "media_time": media_time,
            "bundles_time": bundles_time,
            "accounts_time": accounts_time,
            "counts": {
                "posts": len(tl_posts),
                "aggregated_posts": len(posts.get("aggregatedPosts", [])),
                "media": len(account_media),
                "accounts": len(accounts),
            },
        },
    )


async def _process_post_mentions(
    session: AsyncSession,
    post_obj: Post,
    mentions: list[dict[str, any]],
) -> None:
    """Process mentions for a post using PostgreSQL upsert.

    Args:
        session: SQLAlchemy async session
        post_obj: Post instance
        mentions: List of mention data dictionaries
    """
    # Collect and deduplicate mentions by (postId, handle)
    # Use a dict to keep only the first occurrence of each unique key
    mentions_dict = {}
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

        # Use (postId, handle) as key for deduplication (matches primary key)
        key = (post_obj.id, handle)
        if key in mentions_dict:
            json_output(
                2,
                "meta/post - _p_t_p - duplicate mention in API data",
                {"postId": post_obj.id, "handle": handle, "accountId": account_id},
            )
            continue

        mentions_dict[key] = {
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

    if not mentions_dict:
        return

    # Use PostgreSQL upsert to handle inserts and updates in one operation
    # ON CONFLICT on primary key (postId, handle), update accountId if provided
    stmt = insert(post_mentions).values(list(mentions_dict.values()))

    # On conflict, update accountId only if the new value is not NULL
    # This allows us to fill in accountId when we initially only had a handle
    stmt = stmt.on_conflict_do_update(
        index_elements=["postId", "handle"],
        set_={
            "accountId": stmt.excluded.accountId,
        },
        where=stmt.excluded.accountId.isnot(None),
    )

    await session.execute(stmt)
    await session.flush()


async def _process_post_attachments(
    session: AsyncSession,
    post_obj: Post,
    attachments: list[dict[str, any]],
    _config: FanslyConfig,
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

    # Filter and collect attachment data
    attachment_data_list = []
    for attachment_data in attachments:
        # Skip tipGoals attachments
        if attachment_data.get("contentType") == 7100:
            continue

        # Convert contentType to enum first
        try:
            attachment_data["contentType"] = ContentType(attachment_data["contentType"])
        except ValueError:
            old_content_type = attachment_data["contentType"]
            attachment_data["contentType"] = None
            json_output(
                2,
                f"meta/post - invalid_content_type: {old_content_type}",
                attachment_data,
            )
            continue  # Skip invalid content types

        # Process data for this attachment
        filtered_data, _ = Attachment.process_data(
            attachment_data,
            attachment_known_relations,
            "meta/post - _p_t_p - attachment",
        )
        filtered_data["postId"] = post_obj.id
        attachment_data_list.append(filtered_data)

    if not attachment_data_list:
        return

    # Get all existing attachments in one query
    content_ids = [a["contentId"] for a in attachment_data_list]
    result = await session.execute(
        select(Attachment).where(
            and_(
                Attachment.postId == post_obj.id,
                Attachment.contentId.in_(content_ids),
            )
        )
    )
    existing = {a.contentId: a for a in (result.scalars().all())}

    # Process attachments in batches
    to_update = []
    to_insert = []
    for data in attachment_data_list:
        if data["contentId"] in existing:
            # Update if needed
            existing_attachment = existing[data["contentId"]]
            if existing_attachment.contentType != data[
                "contentType"
            ] or existing_attachment.pos != data.get("pos", 0):
                to_update.append(data)
        else:
            to_insert.append(data)

    # Batch update
    if to_update:
        # Transform keys to use b_ prefix for bind parameters
        update_params = [
            {
                "b_postId": a["postId"],
                "b_contentId": a["contentId"],
                "b_contentType": a["contentType"],
                "b_pos": a.get("pos", 0),
            }
            for a in to_update
        ]
        await session.execute(
            update(Attachment)
            .where(
                and_(
                    Attachment.postId == bindparam("b_postId"),
                    Attachment.contentId == bindparam("b_contentId"),
                )
            )
            .values(
                contentType=bindparam("b_contentType"),
                pos=bindparam("b_pos"),
            ),
            update_params,
        )

    # Batch insert
    if to_insert:
        await session.execute(
            Attachment.__table__.insert(),
            to_insert,
        )


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
    start_time = time.time()
    post_id = post.get("id")

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
    filter_start = time.time()
    filtered_post, _ = Post.process_data(
        post, known_relations, "meta/post - _p_t_p", ("createdAt", "expiresAt")
    )
    filter_time = time.time() - filter_start
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
    db_start = time.time()
    post_obj, _created = await Post.async_get_or_create(
        session,
        {"id": filtered_post["id"]},
        filtered_post,
    )

    # Update fields
    Base.update_fields(post_obj, filtered_post)
    await session.flush()
    db_time = time.time() - db_start

    # Process mentions using dedicated function
    mentions_time = 0
    if "accountMentions" in post:
        mentions_start = time.time()
        await _process_post_mentions(session, post_obj, post["accountMentions"])
        mentions_time = time.time() - mentions_start

    # Process hashtags using dedicated function
    hashtags_time = 0
    if post_obj.content:
        hashtags_start = time.time()
        await process_post_hashtags(config, post_obj, post_obj.content, session=session)
        hashtags_time = time.time() - hashtags_start

    # Process attachments using dedicated function
    attachments_time = 0
    if "attachments" in post:
        attachments_start = time.time()
        await _process_post_attachments(session, post_obj, post["attachments"], config)
        attachments_time = time.time() - attachments_start

    total_time = time.time() - start_time
    json_output(
        2,
        "meta/post - timing - post_breakdown",
        {
            "post_id": post_id,
            "total_time": total_time,
            "filter_time": filter_time,
            "db_time": db_time,
            "mentions_time": mentions_time,
            "hashtags_time": hashtags_time,
            "attachments_time": attachments_time,
            "counts": {
                "mentions": len(post.get("accountMentions", [])),
                "hashtags": (
                    len(post_obj.content.split("#")) - 1 if post_obj.content else 0
                ),
                "attachments": len(post.get("attachments", [])),
            },
        },
    )
