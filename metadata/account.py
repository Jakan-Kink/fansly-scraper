from __future__ import annotations

import copy
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    exc,
)
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from textio import json_output

from .base import Base
from .database import require_database_config

if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

    from .media import Media
    from .post import Post
    from .wall import Wall


MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds


def retry_on_locked_db(func):
    """Decorator to retry operations when database is locked."""

    def wrapper(*args, **kwargs):
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except exc.OperationalError as e:
                if "database is locked" in str(e) and attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                raise

    return wrapper


class Account(Base):
    """Represents a Fansly account with all its associated data.

    This class is the central model for user accounts, containing both basic profile
    information and relationships to various types of content (media, posts, etc.).
    It handles both creator and regular user accounts.

    Attributes:
        id: Unique identifier for the account
        username: Account's username (unique)
        displayName: Optional display name
        flags: Account flags for special handling
        version: Account version number
        createdAt: When the account was created
        subscribed: Whether the authenticated user is subscribed to this account
        timelineStats: Statistics about the account's timeline content
        mediaStoryState: State information about the account's stories
        about: Profile description/bio
        location: Profile location information
        pinnedPosts: Set of posts pinned to the profile
        walls: Set of content walls owned by the account
        following: Whether the authenticated user follows this account
        avatar: Profile avatar media
        banner: Profile banner media
        profileAccess: Whether the authenticated user has access to the profile
        accountMedia: Set of media items owned by this account
        accountMediaBundles: Set of media bundles owned by this account

    Note:
        The following fields from the API are intentionally ignored as they are not
        needed for the application's functionality:
        - followCount: Number of accounts this account follows
        - subscriberCount: Number of subscribers to this account
        - permissions: Account permission flags
        - accountMediaLikes: Liked media items
        - profileFlags: Profile-specific flags
        - postLikes: Liked posts
        - statusId: Account status identifier
        - lastSeenAt: Last activity timestamp
        - streaming: Streaming status information
        - profileAccessFlags: Profile access flags
    """

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    displayName: Mapped[str | None] = mapped_column(String, nullable=True)
    flags: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    createdAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    subscribed: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    timelineStats: Mapped[TimelineStats | None] = relationship(
        "TimelineStats", back_populates="account", lazy="select"
    )
    mediaStoryState: Mapped[MediaStoryState | None] = relationship(
        "MediaStoryState", back_populates="account", lazy="select"
    )
    about: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    pinnedPosts: Mapped[set[Post]] = relationship(
        "Post",
        secondary="pinned_posts",
        collection_class=set,
        lazy="select",
    )
    walls: Mapped[set[Wall]] = relationship(
        "Wall",
        collection_class=set,
        back_populates="account",
        lazy="select",
        cascade="all, delete-orphan",
    )
    following: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    avatar: Mapped[Media | None] = relationship(
        "Media",
        secondary="account_avatar",
        lazy="select",
    )
    banner: Mapped[Media | None] = relationship(
        "Media",
        secondary="account_banner",
        lazy="select",
    )
    profileAccess: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    accountMedia: Mapped[set[AccountMedia]] = relationship(
        "AccountMedia",
        back_populates="account",
        lazy="select",
        collection_class=set,
        cascade="all, delete",  # Use delete to ensure child objects are deleted
        passive_deletes=True,  # Allow database-level cascade
        single_parent=True,  # Ensure each AccountMedia has only one parent Account
        overlaps="media,preview",  # Avoid overlapping relationships
        cascade_backrefs=False,  # Disable cascading through backrefs
        post_update=True,  # Enable post-update to avoid circular dependencies
    )
    accountMediaBundles: Mapped[set[AccountMediaBundle]] = relationship(
        "AccountMediaBundle",
        back_populates="account",
        lazy="select",
        collection_class=set,
    )


class MediaStoryState(Base):
    """Represents the state of an account's media stories.

    This class tracks the story state for an account, including counts and status.

    Attributes:
        accountId: ID of the account these stats belong to
        account: Relationship to the parent Account
        status: Status code for the story state
        storyCount: Number of stories
        version: Version number of the story state
        createdAt: When the story state was created
        updatedAt: When the story state was last updated
        hasActiveStories: Whether the account has active stories
    """

    __tablename__ = "media_story_states"
    accountId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), primary_key=True
    )
    account: Mapped[Account] = relationship("Account", back_populates="mediaStoryState")
    status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storyCount: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    createdAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updatedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hasActiveStories: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, default=False
    )


class TimelineStats(Base):
    """Statistics about an account's timeline content.

    This class tracks various counts of content types in an account's timeline,
    helping to provide overview information about the account's content.

    Attributes:
        accountId: ID of the account these stats belong to
        account: Relationship to the parent Account
        imageCount: Number of individual images
        videoCount: Number of individual videos
        bundleCount: Number of media bundles
        bundleImageCount: Number of images in bundles
        bundleVideoCount: Number of videos in bundles
        fetchedAt: When these stats were last updated
    """

    __tablename__ = "timeline_stats"
    accountId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), primary_key=True
    )
    account: Mapped[Account] = relationship("Account", back_populates="timelineStats")
    imageCount: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    videoCount: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    bundleCount: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    bundleImageCount: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=0
    )
    bundleVideoCount: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=0
    )
    fetchedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


account_avatar = Table(
    "account_avatar",
    Base.metadata,
    Column("accountId", Integer, ForeignKey("accounts.id")),
    Column("mediaId", Integer, ForeignKey("media.id")),
    UniqueConstraint("accountId", "mediaId"),
)
account_banner = Table(
    "account_banner",
    Base.metadata,
    Column("accountId", Integer, ForeignKey("accounts.id")),
    Column("mediaId", Integer, ForeignKey("media.id")),
    UniqueConstraint("accountId", "mediaId"),
)
account_media_bundle_media = Table(
    "account_media_bundle_media",
    Base.metadata,
    Column(
        "bundle_id",
        Integer,
        ForeignKey("account_media_bundles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "media_id",
        Integer,
        ForeignKey("account_media.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("pos", Integer, nullable=False),
)


class AccountMedia(Base):
    """Associates media items with accounts and handles media access control.

    This class serves as a junction between accounts and media items, adding
    account-specific metadata and access control. It also handles preview images
    for media items.

    Attributes:
        id: Unique identifier for this account-media association
        accountId: ID of the account that owns this media
        account: Relationship to the owning Account
        mediaId: ID of the associated media item
        media: Relationship to the Media object
        previewId: ID of the preview media (if any)
        preview: Relationship to the preview Media object
        createdAt: When this media was added to the account
        deletedAt: When this media was deleted (if applicable)
        deleted: Whether this media is marked as deleted
        access: Whether the authenticated user has access to this media
    """

    __tablename__ = "account_media"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    account: Mapped[Account] = relationship(
        "Account",
        back_populates="accountMedia",
        passive_deletes=True,  # Enable passive deletes for cascading
        cascade="all, delete",  # Use delete to ensure child objects are deleted
        single_parent=True,  # Ensure each AccountMedia has only one parent Account
        overlaps="media,preview",  # Avoid overlapping relationships
        cascade_backrefs=False,  # Disable cascading through backrefs
        post_update=True,  # Enable post-update to avoid circular dependencies
    )

    @classmethod
    def __declare_last__(cls):
        """Set up event listeners after all configuration is complete."""
        from sqlalchemy import event

        @event.listens_for(Account, "after_delete")
        def delete_account_media(mapper, connection, target):
            """Delete all AccountMedia records when Account is deleted."""
            connection.execute(cls.__table__.delete().where(cls.accountId == target.id))

    mediaId: Mapped[int] = mapped_column(
        Integer, ForeignKey("media.id", ondelete="CASCADE"), primary_key=True
    )
    media: Mapped[Media] = relationship(
        "Media",
        foreign_keys=[mediaId],
        cascade="all, delete-orphan",
        passive_deletes=True,
        single_parent=True,
        lazy="select",
    )
    previewId: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("media.id"), nullable=True
    )
    preview: Mapped[Media] = relationship(
        "Media",
        foreign_keys=[previewId],
        cascade="all, delete-orphan",
        passive_deletes=True,
        single_parent=True,
        lazy="select",
    )
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deletedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    access: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AccountMediaBundle(Base):
    """A collection of media items grouped together by an account.

    This class represents a bundle of media items, maintaining their order and
    providing a preview image. Bundles can have their own access control separate
    from individual media items.

    Attributes:
        id: Unique identifier for this bundle
        accountId: ID of the account that owns this bundle
        account: Relationship to the owning Account
        previewId: ID of the preview media (if any)
        preview: Relationship to the preview Media object
        createdAt: When this bundle was created
        deletedAt: When this bundle was deleted (if applicable)
        deleted: Whether this bundle is marked as deleted
        accountMediaIds: Ordered set of AccountMedia items in this bundle
        access: Whether the authenticated user has access to this bundle
        purchased: Whether the authenticated user has purchased this bundle
        whitelisted: Whether the authenticated user is whitelisted for this bundle
    Note:
        The following fields from the API are intentionally ignored as they are not
        needed for the application's functionality:
        - permissionFlags: Bundle permission flags
        - permissions: Bundle permissions
        - accountPermissionFlags: Account-specific permission flags
        - price: Bundle price information
    """

    __tablename__ = "account_media_bundles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=False
    )
    account: Mapped[Account] = relationship("Account")
    previewId: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("media.id"), nullable=True
    )
    preview: Mapped[Media] = relationship(
        "Media", foreign_keys=[previewId], lazy="select"
    )
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deletedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    accountMediaIds: Mapped[set[int]] = relationship(
        "AccountMedia",
        secondary=account_media_bundle_media,
        primaryjoin="AccountMediaBundle.id == account_media_bundle_media.c.bundle_id",
        secondaryjoin="AccountMedia.id == account_media_bundle_media.c.media_id",
        collection_class=set,
        lazy="select",
        order_by=account_media_bundle_media.c.pos,
        cascade="all, delete-orphan",
        passive_deletes=True,
        single_parent=True,
    )
    access: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    purchased: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    whitelisted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    UniqueConstraint("accountId", "mediaId")


@require_database_config
@retry_on_locked_db
def process_account_data(
    config: FanslyConfig,
    data: dict,
    state: DownloadState | None = None,
    *,
    context: str = "account",
) -> None:
    """Process account or creator data and store it in the database.

    Args:
        config: FanslyConfig instance
        data: Dictionary containing account/creator data
        state: Optional DownloadState instance
        context: String indicating the context ("account" or "creator") for logging
    """
    from .post import process_pinned_posts
    from .wall import process_account_walls

    # Create deep copy of input data
    data = copy.deepcopy(data)

    with config._database.sync_session() as session:
        try:
            # Process base account data
            account_id = data["id"]
            existing_account = session.query(Account).get(account_id)

            if existing_account is None:
                # Only create a new Account if one doesn't exist
                existing_account = Account(id=account_id)
                session.add(existing_account)

            # Known attributes that are handled separately
            known_relations = {
                # Handled relationships
                "timelineStats",
                "pinnedPosts",
                "walls",
                "accountMediaBundles",
                "avatar",
                "banner",
                "mediaStoryState",
                # Intentionally ignored fields
                "subscription",
                "subscriptionTiers",
                "profileSocials",
                "profileBadges",
                "followCount",
                "subscriberCount",
                "permissions",
                "accountMediaLikes",
                "profileFlags",
                "postLikes",
                "statusId",
                "lastSeenAt",
                "streaming",
                "profileAccessFlags",
            }

            # Process account data
            filtered_data, _ = Account.process_data(
                data, known_relations, "meta/account - p_a_d", ("createdAt",)
            )

            # Update account attributes directly, only if they've changed
            for key, value in filtered_data.items():
                if getattr(existing_account, key) != value:
                    setattr(existing_account, key, value)

            # Get all model columns for logging the full account state
            model_columns = {column.name for column in inspect(Account).columns}
            json_output(
                1,
                f"meta/account - p_{context[0]}_data - account_update",
                {key: getattr(existing_account, key) for key in model_columns},
            )

            session.flush()

            # Process timeline stats
            if "timelineStats" in data:
                process_timeline_stats(session, data)

            # Process media story state
            if "mediaStoryState" in data:
                process_media_story_state(
                    config, existing_account, data["mediaStoryState"], session=session
                )

            # Process pinned posts
            if "pinnedPosts" in data:
                process_pinned_posts(
                    config, existing_account, data["pinnedPosts"], session=session
                )

            # Process walls
            if "walls" in data:
                process_account_walls(
                    config, existing_account, data["walls"], session=session
                )

            # Process media bundles
            if "accountMediaBundles" in data:
                process_media_bundles(
                    config, account_id, data["accountMediaBundles"], session=session
                )

            # Process avatar
            if "avatar" in data:
                process_avatar(
                    config, existing_account, data["avatar"], session=session
                )

            # Process banner
            if "banner" in data:
                process_banner(
                    config, existing_account, data["banner"], session=session
                )

            session.commit()
        except (exc.SQLAlchemyError, exc.DBAPIError) as e:
            session.rollback()
            raise exc.SQLAlchemyError(
                f"Database error while processing account data: {str(e)}"
            ) from e


def process_creator_data(
    config: FanslyConfig, state: DownloadState, data: dict
) -> None:
    """Process creator data by calling process_account_data with creator context."""
    return process_account_data(config, data, state, context="creator")


@retry_on_locked_db
def process_media_story_state(
    config: FanslyConfig, account: Account, story_state_data: dict, *, session: Session
) -> None:
    """Process media story state for an account.

    Updates or creates media story state for an account, handling timestamp
    conversion and data persistence.

    Args:
        config: FanslyConfig instance
        account: Account instance to process story state for
        story_state_data: Dictionary containing media story state data
        session: SQLAlchemy session for database operations
    """
    story_state_data = copy.deepcopy(story_state_data)

    # Known attributes that are handled separately
    known_relations = {
        "accountId",  # Handled separately
    }

    # Process story state data
    filtered_data, _ = MediaStoryState.process_data(
        story_state_data,
        known_relations,
        "meta/account - p_m_s_s",
        ("createdAt", "updatedAt"),
    )

    # Get or create story state
    story_state = session.query(MediaStoryState).get(account.id)
    if story_state is None:
        story_state = MediaStoryState(accountId=account.id)
        session.add(story_state)

    # Update story state attributes
    for key, value in filtered_data.items():
        if getattr(story_state, key) != value:
            setattr(story_state, key, value)

    session.flush()


@retry_on_locked_db
def process_timeline_stats(session: Session, data: dict) -> None:
    """Process timeline statistics for an account.

    Updates or creates timeline statistics for an account, handling timestamp
    conversion and data persistence.

    Args:
        session: SQLAlchemy session for database operations
        data: Dictionary containing account data with timelineStats
    """
    data = copy.deepcopy(data)
    account_id = data["id"]
    timeline_stats = data["timelineStats"]
    timeline_stats["accountId"] = account_id  # Ensure accountId is set

    # Known attributes that are handled separately
    known_relations = {
        "accountId",  # Handled separately
    }

    # Process timeline stats data
    filtered_data, _ = TimelineStats.process_data(
        timeline_stats,
        known_relations,
        "meta/account - p_t_s",
        ("fetchedAt",),  # This will handle the milliseconds conversion
    )

    # Get or create timeline stats
    existing_timeline_stats = session.query(TimelineStats).get(account_id)
    if existing_timeline_stats is None:
        existing_timeline_stats = TimelineStats(accountId=account_id)
        session.add(existing_timeline_stats)

    # Update attributes only if they've changed
    for key, value in filtered_data.items():
        if getattr(existing_timeline_stats, key) != value:
            setattr(existing_timeline_stats, key, value)

    session.flush()


@require_database_config
@retry_on_locked_db
def process_media_bundles(
    config: FanslyConfig,
    account_id: int,
    media_bundles: list[dict],
    session: Session | None = None,
) -> None:
    """Process media bundles for an account.

    Processes a list of media bundles, creating or updating bundle records and their
    relationships with media items. Handles bundle content ordering through the pos field.

    Args:
        config: FanslyConfig instance for database access
        account_id: ID of the account the bundles belong to
        bundles: List of bundle data dictionaries containing bundle information and content
        session: Optional SQLAlchemy session. If not provided, a new session will be created.
    """
    # Create deep copy of input data
    media_bundles = copy.deepcopy(media_bundles)

    def _process_bundles(session: Session) -> None:
        from .media import _process_media_item_dict_inner

        for bundle in media_bundles:
            # Process bundle preview if it exists
            if "preview" in bundle:
                preview = bundle["preview"]
                bundle.pop("preview")  # Remove preview from bundle data
                if (
                    not isinstance(preview, (dict, str))
                    or isinstance(preview, str)
                    and not preview.strip()
                ):
                    json_output(
                        2,
                        "meta/account - invalid_preview_type",
                        {"type": type(preview).__name__, "value": str(preview)},
                    )
                    continue
                if isinstance(preview, dict):
                    _process_media_item_dict_inner(config, preview, session)

            # Known attributes that are handled separately
            known_relations = {
                "preview",  # Handled separately above
                "media",  # Handled separately
                "accountId",  # Set explicitly
                "accountMediaIds",  # Handled separately below
                "bundleContent",  # Handled separately below
                "permissionFlags",
                "permissions",
                "accountPermissionFlags",
                "price",
                "likeCount",
                "mediaId",  # not sure what this mediaId is for
            }

            # Process bundle data
            filtered_data, _ = AccountMediaBundle.process_data(
                bundle,
                known_relations,
                "meta/account - p_m_b-_p_b",
                ("createdAt", "deletedAt"),
            )

            # Get or create bundle
            existing_bundle = session.query(AccountMediaBundle).get(bundle["id"])
            if existing_bundle is None:
                # Create new bundle with filtered data
                filtered_data["accountId"] = account_id  # Ensure accountId is set
                filtered_data.setdefault("createdAt", datetime.now(timezone.utc))
                filtered_data.setdefault("deleted", False)
                filtered_data.setdefault("access", False)
                filtered_data.setdefault("purchased", False)
                filtered_data.setdefault("whitelisted", False)
                existing_bundle = AccountMediaBundle(**filtered_data)
                session.add(existing_bundle)

            # Update bundle attributes
            for key, value in filtered_data.items():
                if getattr(existing_bundle, key) != value:
                    setattr(existing_bundle, key, value)

            # Process bundleContent if present
            if "bundleContent" in bundle:
                for content in bundle["bundleContent"]:
                    try:
                        media_id = int(content["accountMediaId"])
                        session.execute(
                            account_media_bundle_media.insert()
                            .prefix_with("OR IGNORE")
                            .values(
                                bundle_id=bundle["id"],
                                media_id=media_id,
                                pos=content["pos"],
                            )
                        )
                    except (ValueError, KeyError) as e:
                        json_output(
                            2,
                            "meta/account - invalid_bundle_content",
                            {
                                "error": str(e),
                                "content": content,
                                "bundle_id": bundle["id"],
                            },
                        )
                        continue
            bundle.pop("bundleContent", None)  # Remove bundleContent from bundle data

            session.flush()

            # Process media items
            if "accountMediaIds" in bundle:
                for pos, media_item in enumerate(bundle["accountMediaIds"]):
                    # Handle string media IDs
                    if isinstance(media_item, str):
                        if media_item.isdigit():
                            try:
                                media_id = int(media_item)
                                if not (-(2**63) <= media_id <= 2**63 - 1):
                                    json_output(
                                        2,
                                        "meta/account - media_id_out_of_range",
                                        {
                                            "media_id": media_id,
                                            "bundle_id": bundle["id"],
                                        },
                                    )
                                    continue
                            except ValueError:
                                json_output(
                                    2,
                                    "meta/account - invalid_media_id",
                                    {"media_id": media_item, "bundle_id": bundle["id"]},
                                )
                                continue
                        else:
                            json_output(
                                2,
                                "meta/account - non_numeric_media_id",
                                {"media_id": media_item, "bundle_id": bundle["id"]},
                            )
                        continue
                    else:
                        if not isinstance(media_item, dict):
                            json_output(
                                2,
                                "meta/account - invalid_media_item_type",
                                {
                                    "type": type(media_item).__name__,
                                    "value": str(media_item),
                                    "bundle_id": bundle["id"],
                                    "pos": pos,
                                },
                            )
                            continue

                        # Process media item if it's a dict
                        _process_media_item_dict_inner(config, media_item, session)
                        media_id = media_item["id"]

                    # Link media to bundle
                    session.execute(
                        account_media_bundle_media.insert()
                        .prefix_with("OR IGNORE")
                        .values(
                            bundle_id=bundle["id"],
                            media_id=media_id,
                            pos=pos,
                        )
                    )
                    session.flush()

    if session is not None:
        # Use existing session
        _process_bundles(session)
    else:
        # Create new session if none provided
        with config._database.sync_session() as new_session:
            _process_bundles(new_session)
            new_session.commit()


@retry_on_locked_db
def process_avatar(
    config: FanslyConfig, account: Account, avatar_data: dict, session: Session
) -> None:
    """Process avatar media for an account.

    Args:
        config: FanslyConfig instance for database access
        account: Account object that owns the avatar
        avatar_data: Dictionary containing avatar media data
        session: SQLAlchemy session for database operations
    """
    avatar_data = copy.deepcopy(avatar_data)
    from .media import _process_media_item_dict_inner

    # Process avatar media and its variants first
    _process_media_item_dict_inner(config, avatar_data, session)

    # First delete any existing avatar relationship
    session.execute(
        account_avatar.delete().where(account_avatar.c.accountId == account.id)
    )

    # Then insert the new one
    session.execute(
        account_avatar.insert().values(accountId=account.id, mediaId=avatar_data["id"])
    )


@retry_on_locked_db
def process_banner(
    config: FanslyConfig, account: Account, banner_data: dict, session: Session
) -> None:
    """Process banner media for an account.

    Args:
        config: FanslyConfig instance for database access
        account: Account object that owns the banner
        banner_data: Dictionary containing banner media data
        session: SQLAlchemy session for database operations
    """
    banner_data = copy.deepcopy(banner_data)
    from .media import _process_media_item_dict_inner

    # Process banner media and its variants first
    _process_media_item_dict_inner(config, banner_data, session)

    # First delete any existing banner relationship
    session.execute(
        account_banner.delete().where(account_banner.c.accountId == account.id)
    )

    # Then insert the new one
    session.execute(
        account_banner.insert().values(accountId=account.id, mediaId=banner_data["id"])
    )
