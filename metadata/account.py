"""Account module for SQLAlchemy models and processing functions.

This module handles account-related models and their processing functions, including
media bundles, timeline stats, and story states.
"""

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
    select,
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
    from .story import Story
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


def process_media_bundles_data(
    session: Session,
    data: dict[str, any],
    config: FanslyConfig,
    id_fields: list[str] = ["senderId", "recipientId"],
) -> None:
    """Process media bundles from data.

    Args:
        session: SQLAlchemy session
        data: Dictionary containing data and media bundles
        config: FanslyConfig instance for database access
        id_fields: List of field names to check for account ID
    """
    if "accountMediaBundles" not in data:
        return

    # Try to get account ID from the first item
    account_id = None
    if data.get("messages") or data.get("posts"):
        first_item = (data.get("messages") or data.get("posts"))[0]
        for field in id_fields:
            if account_id := first_item.get(field):
                break

    if account_id:
        process_media_bundles(config, account_id, data["accountMediaBundles"], session)
        session.commit()


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
    stories: Mapped[set[Story]] = relationship(
        "Story",
        back_populates="author",
        lazy="select",
        collection_class=set,
    )
    stash_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


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

    def __init__(self, **kwargs):
        """Initialize a Story instance with timestamp conversion."""
        self.convert_timestamps(kwargs, ("createdAt", "updatedAt"))
        super().__init__(**kwargs)


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
        attachments: Relationship to attachments that reference this media
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
    stash_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


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
        attachments: Relationship to attachments that reference this bundle
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
    stash_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


def _process_bundle_content(
    session: Session,
    bundle: dict,
) -> None:
    """Process bundle content items.

    Args:
        session: SQLAlchemy session
        bundle: Bundle data dictionary
    """
    from .media_utils import link_media_to_bundle, validate_media_id

    if "bundleContent" not in bundle:
        return

    for content in bundle["bundleContent"]:
        try:
            media_id = validate_media_id(
                content["accountMediaId"],
                bundle["id"],
                content.get("pos"),
            )
            if media_id is None:
                continue

            link_media_to_bundle(
                session,
                bundle["id"],
                media_id,
                content["pos"],
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


def _process_bundle_media_items(
    session: Session,
    bundle: dict,
    config: FanslyConfig,
) -> None:
    """Process media items in a bundle.

    Args:
        session: SQLAlchemy session
        bundle: Bundle data dictionary
        config: FanslyConfig instance
    """
    from .media import _process_media_item_dict_inner
    from .media_utils import link_media_to_bundle, validate_media_id

    if "accountMediaIds" not in bundle:
        return

    for pos, media_item in enumerate(bundle["accountMediaIds"]):
        media_id = validate_media_id(media_item, bundle["id"], pos)
        if media_id is None and not isinstance(media_item, dict):
            continue

        if isinstance(media_item, dict):
            _process_media_item_dict_inner(config, media_item, session)
            media_id = media_item["id"]

        link_media_to_bundle(session, bundle["id"], media_id, pos)


def _process_single_bundle(
    session: Session,
    bundle: dict,
    account_id: int,
    config: FanslyConfig,
) -> None:
    """Process a single media bundle.

    Args:
        session: SQLAlchemy session
        bundle: Bundle data dictionary
        account_id: ID of the account that owns the bundle
        config: FanslyConfig instance
    """
    from .media_utils import process_preview

    # Process bundle preview if it exists
    if "preview" in bundle:
        preview = bundle.pop("preview")  # Remove preview from bundle data
        process_preview(session, config, bundle, preview)

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
        "liked",
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
    bundle_obj, created = AccountMediaBundle.get_or_create(
        session,
        {"id": bundle["id"]},
        {
            "accountId": account_id,
            "createdAt": datetime.now(timezone.utc),
            "deleted": False,
            "access": False,
            "purchased": False,
            "whitelisted": False,
        },
    )

    # Update bundle attributes
    Base.update_fields(bundle_obj, filtered_data)
    session.flush()

    # Process bundle content and media items
    _process_bundle_content(session, bundle)
    _process_bundle_media_items(session, bundle, config)
    session.flush()


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
        media_bundles: List of bundle data dictionaries containing bundle information and content
        session: Optional SQLAlchemy session. If not provided, a new session will be created.
    """
    # Create deep copy of input data
    media_bundles = copy.deepcopy(media_bundles)

    def _process_bundles(session: Session) -> None:
        for bundle in media_bundles:
            _process_single_bundle(session, bundle, account_id, config)

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

    # Process avatar media
    _process_media_item_dict_inner(config, avatar_data, session)

    # Link avatar to account
    session.execute(
        account_avatar.insert()
        .prefix_with("OR IGNORE")
        .values(accountId=account.id, mediaId=avatar_data["id"])
    )
    session.flush()


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

    # Process banner media
    _process_media_item_dict_inner(config, banner_data, session)

    # Link banner to account
    session.execute(
        account_banner.insert()
        .prefix_with("OR IGNORE")
        .values(accountId=account.id, mediaId=banner_data["id"])
    )
    session.flush()


@retry_on_locked_db
def process_account_data(
    config: FanslyConfig,
    data: dict[str, any],
    state: DownloadState | None = None,
    session: Session | None = None,
) -> None:
    """Process account data.

    Args:
        config: FanslyConfig instance for database access
        data: Dictionary containing account data
        state: Optional DownloadState instance for tracking progress
        session: Optional SQLAlchemy session. If not provided, a new session will be created.
    """
    data = copy.deepcopy(data)

    def _process_data(session: Session) -> None:
        # Known attributes that are handled separately
        known_relations = {
            "avatar",  # Handled separately
            "banner",  # Handled separately
            "timelineStats",  # Handled separately
            "mediaStoryState",  # Handled separately
            "pinnedPosts",  # Handled separately
            "walls",  # Handled separately
            "accountMedia",  # Handled separately
            "accountMediaBundles",  # Handled separately
            "accountMediaLikes",  # Intentionally ignored
            "postLikes",  # Intentionally ignored
            "followCount",  # Intentionally ignored
            "subscriberCount",  # Intentionally ignored
            "permissions",  # Intentionally ignored
            "profileFlags",  # Intentionally ignored
            "statusId",  # Intentionally ignored
            "lastSeenAt",  # Intentionally ignored
            "streaming",  # Intentionally ignored
            "profileAccessFlags",  # Intentionally ignored
            "profileSocials",  # Intentionally ignored
            "profileBadges",  # Intentionally ignored
            "subscriptionTiers",  # Intentionally ignored
        }

        # Process account data
        filtered_data, _ = Account.process_data(
            data,
            known_relations,
            "meta/account - p_a_d",
            ("createdAt",),
        )

        # Get or create account
        account, created = Account.get_or_create(
            session,
            {"id": filtered_data["id"]},
            {"username": filtered_data["username"]},
        )

        # Update account attributes
        Base.update_fields(account, filtered_data)
        session.flush()

        # Process avatar if present
        if "avatar" in data:
            process_avatar(config, account, data["avatar"], session)

        # Process banner if present
        if "banner" in data:
            process_banner(config, account, data["banner"], session)

        # Process timeline stats if present
        if "timelineStats" in data:
            stats_data = data["timelineStats"]
            stats_data["accountId"] = account.id
            stats_data["fetchedAt"] = datetime.now(timezone.utc)

            # Get or create timeline stats
            stats, created = TimelineStats.get_or_create(
                session,
                {"accountId": account.id},
                {
                    "imageCount": 0,
                    "videoCount": 0,
                    "bundleCount": 0,
                    "bundleImageCount": 0,
                    "bundleVideoCount": 0,
                },
            )

            # Update stats attributes
            Base.update_fields(stats, stats_data)
            session.flush()

        # Process media story state if present
        if "mediaStoryState" in data:
            story_data = data["mediaStoryState"]
            story_data["accountId"] = account.id

            # Convert timestamps in story_data before creating/updating the instance
            Base.convert_timestamps(story_data, ("createdAt", "updatedAt"))

            # Get or create story state
            story, created = MediaStoryState.get_or_create(
                session,
                {"accountId": account.id},
                {
                    "status": 0,
                    "storyCount": 0,
                    "version": 0,
                    "hasActiveStories": False,
                },
            )

            # Update story state attributes
            Base.update_fields(story, story_data)
            session.flush()

    if session is not None:
        # Use existing session
        _process_data(session)
    else:
        # Create new session if none provided
        with config._database.sync_session() as new_session:
            _process_data(new_session)
            new_session.commit()
