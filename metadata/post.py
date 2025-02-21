from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
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
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from config.decorators import with_database_session
from textio import json_output

from .attachment import Attachment, ContentType
from .base import Base
from .database import require_database_config
from .hashtag import Hashtag, extract_hashtags, post_hashtags, process_post_hashtags

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
    account: Mapped[Account] = relationship(
        "Account",
        foreign_keys=[accountId],
        lazy="joined",  # Use joined loading since we always need account info
        back_populates="posts",  # Add back reference
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
        lazy="joined",  # Use joined loading for attachments since we always need them
    )
    accountMentions: Mapped[list[Account]] = relationship(
        "Account",
        secondary="post_mentions",
        lazy="select",  # Use select loading since mentions are accessed less frequently
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
    # Partial indexes for efficient lookups
    Index(
        "ix_post_mentions_account",
        "postId",
        "accountId",
        sqlite_where=Column("accountId").isnot(None),
    ),
    Index(
        "ix_post_mentions_handle",
        "postId",
        "handle",
        sqlite_where=Column("handle").isnot(None),
    ),
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


@require_database_config
@with_database_session(async_session=True)
async def process_timeline_posts(
    config: FanslyConfig,
    state: DownloadState,
    posts: list[dict[str, any]],
    session: AsyncSession | None = None,
) -> None:
    """Process timeline posts.

    Args:
        config: FanslyConfig instance
        state: Current download state
        posts: List of post data dictionaries
        session: Optional AsyncSession for database operations
    """
    import time

    from .account import process_account_data
    from .media import process_media_info

    start_time = time.time()
    posts = copy.deepcopy(posts)
    copy_time = time.time() - start_time

    json_output(1, "meta/post - p_t_posts - posts", posts)

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
    accountMedia = posts["accountMedia"]
    batch_size = 15  # Process one timeline page worth of media at a time

    for i in range(0, len(accountMedia), batch_size):
        batch = accountMedia[i : i + batch_size]
        batch_start = time.time()

        # Process the entire batch at once
        await process_media_info(config, {"batch": batch}, session=session)

        json_output(
            2,
            "meta/post - timing - media_batch",
            {
                "batch_start": i + 1,
                "batch_end": min(i + batch_size, len(accountMedia)),
                "total_media": len(accountMedia),
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

    # Process accounts
    accounts_start = time.time()
    accounts = posts["accounts"]
    for account in accounts:
        account_start = time.time()
        await process_account_data(config, data=account, session=session)
        json_output(
            2,
            "meta/post - timing - single_account",
            {
                "account_id": account.get("id"),
                "time_taken": time.time() - account_start,
            },
        )
    accounts_time = time.time() - accounts_start

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
                "media": len(accountMedia),
                "accounts": len(accounts),
            },
        },
    )


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
    # Collect all valid mentions first
    mentions_data = []
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

        mentions_data.append(
            {
                "postId": post_obj.id,
                "accountId": account_id,
                "handle": handle,
            }
        )

        # Log if we're storing a mention without an accountId
        if account_id is None:
            json_output(
                2,
                "meta/post - _p_t_p - storing mention with handle only",
                {"postId": post_obj.id, "handle": handle},
            )

    if not mentions_data:
        return

    # Get all existing mentions in one query
    account_ids = [m["accountId"] for m in mentions_data if m["accountId"] is not None]
    handles = [m["handle"] for m in mentions_data]

    result = await session.execute(
        select(post_mentions).where(
            and_(
                post_mentions.c.postId == post_obj.id,
                or_(
                    (
                        post_mentions.c.accountId.in_(account_ids)
                        if account_ids
                        else False
                    ),
                    post_mentions.c.handle.in_(handles) if handles else False,
                ),
            )
        )
    )
    existing = {(r.accountId, r.handle): r for r in result.fetchall()}

    # Process mentions in batches
    to_update_handle = []  # Update handle where accountId matches
    to_update_account = []  # Update accountId where handle matches
    to_insert = []

    for mention in mentions_data:
        # Try by accountId first
        if mention["accountId"] is not None:
            account_key = (mention["accountId"], None)
            if account_key in existing:
                # Update handle if it changed
                if existing[account_key].handle != mention["handle"]:
                    to_update_handle.append(mention)
                continue

        # Then try by handle
        handle_key = (None, mention["handle"])
        if handle_key in existing:
            # Update accountId if we have one and it's not set
            if mention["accountId"] is not None:
                to_update_account.append(mention)
            continue

        # If no match found, insert new one
        to_insert.append(mention)

    # Batch update handles
    if to_update_handle:
        await session.execute(
            post_mentions.update()
            .where(
                and_(
                    post_mentions.c.postId == bindparam("postId"),
                    post_mentions.c.accountId == bindparam("accountId"),
                )
            )
            .values(handle=bindparam("handle")),
            to_update_handle,
        )

    # Batch update accountIds
    if to_update_account:
        await session.execute(
            post_mentions.update()
            .where(
                and_(
                    post_mentions.c.postId == bindparam("postId"),
                    post_mentions.c.handle == bindparam("handle"),
                )
            )
            .values(accountId=bindparam("accountId")),
            to_update_account,
        )

    # Batch insert new mentions
    if to_insert:
        await session.execute(
            post_mentions.insert(),
            to_insert,
        )


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

    # Filter and collect attachment data
    attachment_data_list = []
    for attachment_data in attachments:
        # Skip tipGoals attachments
        if attachment_data.get("contentType") == 7100:
            continue

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
    existing = {a.contentId: a for a in result.scalars()}

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
        await session.execute(
            update(Attachment)
            .where(
                and_(
                    Attachment.postId == bindparam("postId"),
                    Attachment.contentId == bindparam("contentId"),
                )
            )
            .values(
                contentType=bindparam("contentType"),
                pos=bindparam("pos"),
            ),
            to_update,
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
    import time

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

    # Collect all operations for this post
    operations = {
        "mentions": [],  # All mentions for this post
        "hashtags": set(),  # Unique hashtags for this post
        "post_hashtags": [],  # Post-hashtag associations
        "attachments": [],  # All attachments for this post
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
    post_obj, created = await Post.async_get_or_create(
        session,
        {"id": filtered_post["id"]},
        filtered_post,
    )

    # Update fields
    Base.update_fields(post_obj, filtered_post)
    await session.flush()
    db_time = time.time() - db_start

    # Collect mentions
    mentions_time = 0
    if "accountMentions" in post:
        mentions_start = time.time()
        for mention in post["accountMentions"]:
            handle = mention.get("handle", "").strip()
            account_id = mention.get("accountId", None)
            if not handle and account_id is None:
                json_output(
                    2,
                    "meta/post - _p_t_p - skipping mention with no handle or accountId",
                    {"postId": post_obj.id, "mention": mention},
                )
                continue
            operations["mentions"].append(
                {
                    "postId": post_obj.id,
                    "accountId": account_id,
                    "handle": handle,
                }
            )

        # Process all mentions in one batch
        if operations["mentions"]:
            # First try to find existing mentions by accountId
            for mention in operations["mentions"]:
                if mention["accountId"] is not None:
                    result = await session.execute(
                        select(post_mentions).where(
                            and_(
                                post_mentions.c.postId == mention["postId"],
                                post_mentions.c.accountId == mention["accountId"],
                            )
                        )
                    )
                    existing = result.first()
                    if existing:
                        # Update handle if it changed
                        if existing.handle != mention["handle"]:
                            await session.execute(
                                post_mentions.update()
                                .where(
                                    and_(
                                        post_mentions.c.postId == mention["postId"],
                                        post_mentions.c.accountId
                                        == mention["accountId"],
                                    )
                                )
                                .values(handle=mention["handle"])
                            )
                        continue

                # Then try by handle
                result = await session.execute(
                    select(post_mentions).where(
                        and_(
                            post_mentions.c.postId == mention["postId"],
                            post_mentions.c.handle == mention["handle"],
                        )
                    )
                )
                existing = result.first()
                if existing:
                    # Update accountId if we have one and it's not set
                    if mention["accountId"] is not None and existing.accountId is None:
                        await session.execute(
                            post_mentions.update()
                            .where(
                                and_(
                                    post_mentions.c.postId == mention["postId"],
                                    post_mentions.c.handle == mention["handle"],
                                )
                            )
                            .values(accountId=mention["accountId"])
                        )
                    continue

                # If no existing mention found, insert new one
                await session.execute(post_mentions.insert().values(mention))
        mentions_time = time.time() - mentions_start

    # Process hashtags
    hashtags_time = 0
    if post_obj.content:
        hashtags_start = time.time()
        hashtag_values = extract_hashtags(post_obj.content)
        if hashtag_values:
            operations["hashtags"].update(hashtag_values)
            operations["post_hashtags"].extend(
                [{"postId": post_obj.id, "hashtag": tag} for tag in hashtag_values]
            )

            # Process all hashtags in one batch
            # Get all existing hashtags in one query
            result = await session.execute(
                select(Hashtag).where(
                    func.lower(Hashtag.value).in_(
                        [v.lower() for v in operations["hashtags"]]
                    )
                )
            )
            existing_hashtags = {h.value.lower(): h for h in result.scalars()}

            # Create missing hashtags in one batch
            missing_values = [
                v for v in operations["hashtags"] if v.lower() not in existing_hashtags
            ]
            if missing_values:
                # Batch insert missing hashtags
                insert_stmt = sqlite_insert(Hashtag.__table__).values(
                    [{"value": v} for v in missing_values]
                )
                update_stmt = insert_stmt.on_conflict_do_nothing()
                await session.execute(update_stmt)
                await session.flush()

                # Get the newly created hashtags
                result = await session.execute(
                    select(Hashtag).where(
                        func.lower(Hashtag.value).in_(
                            [v.lower() for v in missing_values]
                        )
                    )
                )
                new_hashtags = {h.value.lower(): h for h in result.scalars()}
                existing_hashtags.update(new_hashtags)

            # Create all associations in one batch
            associations = [
                {
                    "postId": post_obj.id,
                    "hashtagId": existing_hashtags[ph["hashtag"].lower()].id,
                }
                for ph in operations["post_hashtags"]
            ]
            if associations:
                insert_stmt = sqlite_insert(post_hashtags).values(associations)
                update_stmt = insert_stmt.on_conflict_do_nothing()
                await session.execute(update_stmt)
        hashtags_time = time.time() - hashtags_start

    # Collect and process attachments
    attachments_time = 0
    if "attachments" in post:
        attachments_start = time.time()
        for attachment in post["attachments"]:
            if attachment.get("contentType") == 7100:  # Skip tipGoals
                continue
            operations["attachments"].append(
                {
                    "postId": post_obj.id,
                    "contentId": attachment["contentId"],
                    "contentType": ContentType(attachment["contentType"]),
                    "pos": attachment.get("pos", 0),
                }
            )

        # Process all attachments in one batch
        if operations["attachments"]:
            # First try to find existing attachments
            for attachment in operations["attachments"]:
                result = await session.execute(
                    select(Attachment).where(
                        and_(
                            Attachment.postId == attachment["postId"],
                            Attachment.contentId == attachment["contentId"],
                        )
                    )
                )
                existing = result.scalar()
                if existing:
                    # Update if needed
                    if (
                        existing.contentType != attachment["contentType"]
                        or existing.pos != attachment["pos"]
                    ):
                        await session.execute(
                            update(Attachment)
                            .where(
                                and_(
                                    Attachment.postId == attachment["postId"],
                                    Attachment.contentId == attachment["contentId"],
                                )
                            )
                            .values(
                                contentType=attachment["contentType"],
                                pos=attachment["pos"],
                            )
                        )
                    continue

                # If no existing attachment found, insert new one
                await session.execute(Attachment.__table__.insert().values(attachment))
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
