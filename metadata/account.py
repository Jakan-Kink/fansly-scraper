from __future__ import annotations

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
        timelineStats: Statistics about the account's timeline content
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
    """

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    displayName: Mapped[str | None] = mapped_column(String, nullable=True)
    flags: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timelineStats: Mapped[TimelineStats | None] = relationship(
        "TimelineStats", back_populates="account", lazy="select"
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
        "Wall", collection_class=set, back_populates="account", lazy="select"
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
    )
    accountMediaBundles: Mapped[set[AccountMediaBundle]] = relationship(
        "AccountMediaBundle",
        back_populates="account",
        lazy="select",
        collection_class=set,
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
        Integer, ForeignKey("accounts.id"), primary_key=True, index=True
    )
    account: Mapped[Account] = relationship("Account", back_populates="accountMedia")
    mediaId: Mapped[int] = mapped_column(
        Integer, ForeignKey("media.id"), primary_key=True
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


@retry_on_locked_db
def process_account_data(
    config: FanslyConfig,
    data: dict,
    state: DownloadState = None,
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

    with config._database.sync_session() as session:
        try:
            # Process base account data
            account_id = data["id"]
            existing_account = session.query(Account).get(account_id)

            if existing_account is None:
                # Only create a new Account if one doesn't exist
                existing_account = Account(id=account_id)
                session.add(existing_account)

            # Get valid column names for Account
            account_columns = {column.name for column in inspect(Account).columns}

            # Known attributes that are handled separately
            known_relations = {
                "timelineStats",
                "pinnedPosts",
                "walls",
                "accountMediaBundles",
                "avatar",
                "banner",
                "subscription",
                "subscriptionTiers",
                "profileSocials",
                "profileBadges",
            }

            # Log truly unknown attributes (not in columns and not handled separately)
            unknown_attrs = {
                k: v
                for k, v in data.items()
                if k not in account_columns and k not in known_relations
            }
            if unknown_attrs:
                json_output(1, "meta/account - unknown_attributes", unknown_attrs)

            # Update account attributes directly, only if they've changed
            for key, value in data.items():
                if key in account_columns:
                    if getattr(existing_account, key) != value:
                        setattr(existing_account, key, value)

            json_output(
                1,
                f"meta/account - p_{context[0]}_data - account_update",
                {key: getattr(existing_account, key) for key in account_columns},
            )

            session.flush()

            # Process timeline stats
            if "timelineStats" in data:
                process_timeline_stats(session, data)

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
                    config, account_id, data["accountMediaBundles"], session
                )

            # Process avatar
            if "avatar" in data:
                process_avatar(config, existing_account, data["avatar"], session)

            # Process banner
            if "banner" in data:
                process_banner(config, existing_account, data["banner"], session)

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
def process_timeline_stats(session: Session, data: dict) -> None:
    """Process timeline statistics for an account.

    Updates or creates timeline statistics for an account, handling timestamp
    conversion and data persistence.

    Args:
        session: SQLAlchemy session for database operations
        data: Dictionary containing account data with timelineStats
    """
    account_id = data["id"]
    timeline_stats = data["timelineStats"]
    timeline_stats["accountId"] = account_id  # Ensure accountId is set
    timeline_stats["fetchedAt"] = (
        datetime.fromtimestamp(timeline_stats["fetchedAt"] / 1000, tz=timezone.utc)
        if timeline_stats["fetchedAt"]
        else None
    )

    # Get or create timeline stats
    existing_timeline_stats = session.query(TimelineStats).get(account_id)
    if existing_timeline_stats is None:
        existing_timeline_stats = TimelineStats(accountId=account_id)
        session.add(existing_timeline_stats)

    # Get valid column names for TimelineStats
    timeline_stats_columns = {column.name for column in inspect(TimelineStats).columns}

    # Log any unknown attributes
    unknown_attrs = {
        k: v for k, v in timeline_stats.items() if k not in timeline_stats_columns
    }
    if unknown_attrs:
        json_output(
            1, "meta/account - timeline_stats_unknown_attributes", unknown_attrs
        )

    # Update attributes only if they've changed
    for key, value in timeline_stats.items():
        if key in timeline_stats_columns:
            if getattr(existing_timeline_stats, key) != value:
                setattr(existing_timeline_stats, key, value)

    session.flush()


@retry_on_locked_db
def process_media_bundles(
    config: FanslyConfig,
    account_id: int,
    bundles: list[dict[str, any]],
    session: Session = None,
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

    def _process_bundles(session: Session) -> None:
        from .media import _process_media_item_dict_inner

        for bundle in bundles:
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

            # Get valid column names for AccountMediaBundle
            bundle_columns = {
                column.name for column in inspect(AccountMediaBundle).columns
            }

            # Convert timestamps to datetime objects first
            date_fields = ("createdAt", "deletedAt")
            for date_field in date_fields:
                if date_field in bundle and bundle[date_field]:
                    bundle[date_field] = datetime.fromtimestamp(
                        (
                            bundle[date_field] / 1000
                            if bundle[date_field] > 1e10
                            else bundle[date_field]
                        ),
                        timezone.utc,
                    )

            # Get or create bundle
            existing_bundle = session.query(AccountMediaBundle).get(bundle["id"])
            if existing_bundle is None:
                # Prepare initial data with required fields
                bundle_data = {
                    "id": bundle["id"],
                    "accountId": account_id,
                    "createdAt": bundle.get("createdAt") or datetime.now(timezone.utc),
                    "deleted": bundle.get("deleted", False),
                    "access": bundle.get("access", False),
                    "purchased": bundle.get("purchased", False),
                    "whitelisted": bundle.get("whitelisted", False),
                }
                existing_bundle = AccountMediaBundle(**bundle_data)
                session.add(existing_bundle)

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

            # Known attributes that are handled separately
            known_bundle_relations = {"accountMediaIds"}
            known_exclude_attrs = {
                "permissionFlags",
                "permissions",
                "accountPermissionFlags",
            }

            # Log truly unknown attributes
            unknown_attrs = {
                k: v
                for k, v in bundle.items()
                if k not in bundle_columns
                and k not in known_bundle_relations
                and k not in known_exclude_attrs
            }
            if unknown_attrs:
                json_output(
                    1, "meta/account - bundle_unknown_attributes", unknown_attrs
                )

            # Update bundle attributes only if they've changed
            for key, value in bundle.items():
                if (
                    key in bundle_columns
                    and key != "accountMediaIds"  # Handle media items separately
                    and getattr(existing_bundle, key) != value
                ):
                    setattr(existing_bundle, key, value)

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
