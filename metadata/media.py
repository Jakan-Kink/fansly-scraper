from __future__ import annotations

import copy
import json
import traceback
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import (
    Mapped,
    Session,
    attribute_mapped_collection,
    mapped_column,
    relationship,
)

from config.decorators import with_database_session
from media import MediaItem
from textio import json_output

from .base import Base
from .database import require_database_config

if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

media_variants = Table(
    "media_variants",
    Base.metadata,
    Column("mediaId", Integer, ForeignKey("media.id"), primary_key=True),
    Column("variantId", Integer, ForeignKey("media.id"), primary_key=True),
    UniqueConstraint("mediaId", "variantId"),
)


class MediaLocation(Base):
    """Represents a storage location for media content.

    This class maps the physical location of media files, including variants
    and previews. Each media item can have multiple locations for different
    purposes (e.g., CDN URLs, local paths).

    Attributes:
        mediaId: ID of the media this location belongs to
        locationId: Unique identifier for this location
        location: The actual URL or path where the media is stored
        media: Relationship to the parent Media object
    """

    __tablename__ = "media_locations"

    mediaId: Mapped[int] = mapped_column(
        Integer, ForeignKey("media.id"), primary_key=True
    )
    locationId: Mapped[str] = mapped_column(String, primary_key=True)
    location: Mapped[str] = mapped_column(String, nullable=False)
    media: Mapped[Media] = relationship("Media", back_populates="locations")


class Media(Base):
    """Represents a media item with its metadata and variants.

    This class handles all types of media (images, videos) and their associated
    metadata. It supports video-specific attributes like duration and dimensions,
    and can manage multiple variants of the same media (e.g., different resolutions).

    Attributes:
        id: Unique identifier for the media
        accountId: ID of the account that owns this media
        meta_info: JSON string containing additional metadata
        location: Primary location of the media
        flags: Media flags for special handling
        mimetype: MIME type of the media (e.g., video/mp4, image/jpeg)
        height: Height of the media in pixels
        width: Width of the media in pixels
        duration: Duration in seconds for video content
        type: Type identifier for the media
        status: Current status of the media
        createdAt: Timestamp when the media was created
        updatedAt: Timestamp when the media was last updated
        variants: Set of variant Media objects (e.g., different resolutions)
        locations: Dictionary of MediaLocation objects keyed by locationId
    """

    __tablename__ = "media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=False
    )
    meta_info: Mapped[str] = mapped_column(String, nullable=True)
    location: Mapped[str] = mapped_column(String, nullable=True)
    flags: Mapped[int] = mapped_column(Integer, nullable=True)
    mimetype: Mapped[str] = mapped_column(String, nullable=True)
    height: Mapped[int] = mapped_column(Integer, nullable=True)
    width: Mapped[int] = mapped_column(Integer, nullable=True)
    duration: Mapped[float] = mapped_column(Float, nullable=True)
    type: Mapped[int] = mapped_column(Integer, nullable=True)
    status: Mapped[int] = mapped_column(Integer, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    local_filename: Mapped[str] = mapped_column(String, nullable=True)
    content_hash: Mapped[str] = mapped_column(String, nullable=True, index=True)
    is_downloaded: Mapped[bool] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        server_default="0",  # Set explicit server default
    )
    variants: Mapped[set[Media]] = relationship(
        "Media",
        collection_class=set,
        secondary="media_variants",
        lazy="selectin",
        primaryjoin=id == media_variants.c.mediaId,
        secondaryjoin=id == media_variants.c.variantId,
    )
    locations: Mapped[dict[str, MediaLocation]] = relationship(
        "MediaLocation",
        collection_class=attribute_mapped_collection("locationId"),
        cascade="all, delete-orphan",
        lazy="selectin",
        back_populates="media",
    )
    stash_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


def process_media_metadata(metadata: dict) -> None:
    json_output(1, "meta/media - p_m_m", metadata)
    pass


@require_database_config
@with_database_session(async_session=True)
async def process_media_info(
    config: FanslyConfig, media_infos: dict, session: AsyncSession | None = None
) -> None:
    """Process media info and store in the database.

    Args:
        config: FanslyConfig instance for database access
        media_infos: Dictionary containing media info data
    """
    from .account import AccountMedia

    media_infos = copy.deepcopy(media_infos)
    json_output(1, "meta/media - p_m_i", media_infos)

    # Known attributes that are handled separately
    known_relations = {
        # Handled relationships
        "media",
        "preview",
        # Intentionally ignored fields
        "permissionFlags",
        "price",
        "permissions",
        "likeCount",
        "purchased",
        "whitelisted",
        "accountPermissionFlags",
        "liked",
    }

    # Process media data
    filtered_account_media, _ = AccountMedia.process_data(
        media_infos, known_relations, "meta/media - p_m_i", ("createdAt", "deletedAt")
    )
    json_output(1, "meta/media - p_m_i - filtered", filtered_account_media)

    # Get or create account media
    account_media, created = await AccountMedia.async_get_or_create(
        session,
        {
            "id": filtered_account_media["id"],
            "accountId": filtered_account_media["accountId"],
            "mediaId": filtered_account_media["mediaId"],
        },
        filtered_account_media,
    )

    # Update fields
    Base.update_fields(account_media, filtered_account_media)
    await session.flush()

    # Process related media items
    for field in ["media", "preview"]:
        if field in media_infos:
            await process_media_item_dict(config, media_infos[field], session=session)


@require_database_config
@with_database_session(async_session=True)
async def process_media_item_dict(
    config: FanslyConfig, media_item: dict, session: AsyncSession | None = None
) -> None:
    """Process a media item dictionary and store it in the database.

    Args:
        config: FanslyConfig instance for database access
        media_item: Dictionary containing media data
        session: Optional AsyncSession for database operations
    """
    json_output(1, "meta/media - p_m_i_h - media_item[dict]", media_item)
    if session is None:
        async with config._database.async_session_scope() as session:
            await _process_media_item_dict_inner(config, media_item, session=session)
    else:
        await _process_media_item_dict_inner(config, media_item, session=session)


def _process_media_metadata(
    media_item: dict[str, any],
    filtered_media: dict[str, any],
) -> None:
    """Process media metadata and update filtered_media.

    Args:
        media_item: Raw media item dictionary
        filtered_media: Dictionary of filtered media data to update
    """
    if "metadata" not in media_item:
        return

    filtered_media["meta_info"] = media_item["metadata"]
    if not media_item.get("mimetype", "").startswith("video/"):
        return

    try:
        metadata = json.loads(media_item["metadata"])
        # Extract original dimensions and duration if available
        if "original" in metadata:
            filtered_media["width"] = metadata["original"].get("width")
            filtered_media["height"] = metadata["original"].get("height")
        if "duration" in metadata:
            filtered_media["duration"] = float(metadata.get("duration"))
    except (json.JSONDecodeError, ValueError, AttributeError, KeyError) as e:
        json_output(1, "meta/media - p_m_i_d - metadata error", str(e))


async def _process_media_locations(
    session: AsyncSession,
    media: Media,
    locations: list[dict[str, any]],
) -> None:
    """Process media locations.

    Args:
        session: SQLAlchemy async session
        media: Media instance
        locations: List of location data dictionaries
    """
    # Get existing locations using get() to avoid identity map issues
    existing_locations = {}
    for location_data in locations:
        location_id = location_data["locationId"]
        result = (
            await session.execute(
                select(MediaLocation).where(
                    MediaLocation.mediaId == media.id,
                    MediaLocation.locationId == location_id,
                )
            )
        ).scalar_one_or_none()
        if result:
            existing_locations[location_id] = result

    # Process each location
    for location_data in locations:
        location_id = location_data["locationId"]
        if location_id in existing_locations:
            # Update if location changed
            if existing_locations[location_id].location != location_data["location"]:
                existing_locations[location_id].location = location_data["location"]
            # Remove from existing_locations to track what needs to be deleted
            del existing_locations[location_id]
        else:
            # Add new location
            # Try to get existing location first
            existing_location = (
                await session.execute(
                    select(MediaLocation).filter_by(
                        mediaId=media.id,
                        locationId=location_id,
                    )
                )
            ).scalar_one_or_none()

            if existing_location:
                # Update existing location
                existing_location.location = location_data["location"]
            else:
                # Create new location
                location = MediaLocation(
                    mediaId=media.id,
                    locationId=location_id,
                    location=location_data["location"],
                )
                session.add(location)

    # Delete locations that no longer exist
    if existing_locations:
        await session.execute(
            MediaLocation.__table__.delete().where(
                MediaLocation.mediaId == media.id,
                MediaLocation.locationId.in_(existing_locations.keys()),
            )
        )


async def _process_media_variants(
    session: AsyncSession,
    config: FanslyConfig,
    media: Media,
    variants: list[dict[str, any]],
    account_id: int,
) -> None:
    """Process media variants.

    Args:
        session: SQLAlchemy async session
        config: FanslyConfig instance
        media: Media instance
        variants: List of variant data dictionaries
        account_id: ID of the account that owns the media
    """
    # Get existing variants to avoid duplicates
    result = await session.execute(
        select(media_variants.c.variantId).where(media_variants.c.mediaId == media.id)
    )
    existing_variants = {row[0] for row in result.fetchall()}

    # Process each variant
    for variant in variants:
        # Skip if variant already exists
        if variant["id"] in existing_variants:
            continue

        # Process variant media
        await _process_media_item_dict_inner(
            config,
            variant,
            session=session,
            account_id=account_id,
        )

        # Add variant relationship using direct SQL to avoid identity map issues
        await session.execute(
            media_variants.insert()
            .values(mediaId=media.id, variantId=variant["id"])
            .prefix_with("OR IGNORE")
        )


@with_database_session(async_session=True)
async def _process_media_item_dict_inner(
    config: FanslyConfig,
    media_item: dict[str, any],
    account_id: int | None = None,
    session: AsyncSession | None = None,
) -> None:
    """Process a media item dictionary and store it in the database.

    Args:
        config: FanslyConfig instance for database access
        media_item: Dictionary containing media data including metadata
        session: SQLAlchemy async session for database operations
        account_id: Optional account ID if not present in media_item
    """
    # Create deep copy of input data
    media_item = copy.deepcopy(media_item)

    # Check if media_item is the correct type
    if not isinstance(media_item, dict):
        json_output(
            2,
            "meta/media - invalid_media_item_type",
            {"type": type(media_item).__name__, "value": str(media_item)},
        )
        return

    # Known attributes that are handled separately
    known_relations = {
        # Handled relationships
        "locations",
        "variants",
        "filename",
        # Intentionally ignored fields
        "metadata",  # Handled by mapping to meta_info
        "variantHash",  # Not used in database
    }

    # Process media data
    filtered_media, _ = Media.process_data(
        media_item,
        known_relations,
        "meta/media - _p_m_i_d_i",
        ("createdAt", "updatedAt"),
    )
    filtered_media["accountId"] = filtered_media.get("accountId", account_id)

    # Process metadata
    _process_media_metadata(media_item, filtered_media)

    # Ensure required fields are present before proceeding
    if "accountId" not in filtered_media:
        json_output(
            1,
            "meta/media - missing_required_field",
            {"mediaId": filtered_media.get("id"), "missing_field": "accountId"},
        )
        return  # Skip this media if accountId is missing

    if not isinstance(session, AsyncSession):
        json_output(1, "meta/media - _p_m_i_d_i - no_session", type(session))
        raise TypeError("No session provided for database operations")

    # Begin a nested transaction
    async with session.begin_nested():
        # Get or create media
        media, created = await Media.async_get_or_create(
            session,
            {"id": filtered_media["id"]},
            filtered_media,
        )

        # Update fields
        Base.update_fields(media, filtered_media)

        # Flush to ensure media exists in DB before processing relations
        await session.flush()

        # Process locations if present
        if "locations" in media_item:
            await _process_media_locations(session, media, media_item["locations"])
            await session.flush()

        # Process variants if present
        if "variants" in media_item:
            await _process_media_variants(
                session,
                config,
                media,
                media_item["variants"],
                filtered_media["accountId"],
            )
            await session.flush()


def _should_skip_media(media_obj: Media) -> bool:
    """Check if media should be skipped.

    Args:
        media_obj: Media instance to check

    Returns:
        True if media should be skipped, False otherwise
    """
    return bool(
        media_obj
        and media_obj.is_downloaded
        and media_obj.content_hash
        and media_obj.local_filename
    )


@require_database_config
@with_database_session(async_session=True)
async def process_media_download(
    config: FanslyConfig,
    state: DownloadState,
    media: MediaItem | dict[str, any],
    session: AsyncSession | None = None,
) -> Media | None:
    """Process a media item for download and return its Media record.

    Args:
        config: FanslyConfig instance
        state: Current download state
        media: MediaItem to process
        session: Optional session to use, will create one if not provided

    Returns:
        Media record if found or created, None if media should be skipped

    Raises:
        ValueError: If creator_id is not available in state
    """
    media = copy.deepcopy(media)
    json_output(1, "meta/media - p_m_d", media)
    if isinstance(media, MediaItem):
        # Query first approach
        existing_media = (
            await session.execute(select(Media).where(Media.id == media.media_id))
        ).scalar_one_or_none()
    else:
        existing_media = (
            await session.execute(select(Media).where(Media.id == media.get("id", -1)))
        ).scalar_one_or_none()
    media_obj: Media | None = None if not existing_media else existing_media

    if not isinstance(media, MediaItem):
        # Get or create media record
        media_obj, created = await Media.async_get_or_create(
            session,
            {"id": media["id"]},
            {
                "accountId": int(state.creator_id),
                "mimetype": media["mimetype"],
                "createdAt": (
                    datetime.fromtimestamp(media["createdAt"], tz=timezone.utc)
                    if media["createdAt"]
                    else None
                ),
                "updatedAt": (
                    datetime.fromtimestamp(media["updatedAt"], tz=timezone.utc)
                    if media["updatedAt"]
                    else None
                ),
                "type": media["type"],
                "status": media["status"],
                "flags": media["flags"],
                "meta_info": media["metadata"],
                "location": media["location"],
                "height": media["height"],
                "width": media["width"],
                "duration": float(media["duration"] if media.get("duration") else 0),
            },
        )

        # Update fields if media already exists
        if not created:
            Base.update_fields(
                media_obj,
                {
                    "mimetype": media["mimetype"],
                    "type": media["type"],
                    "status": media["status"],
                    "flags": media["flags"],
                    "meta_info": media["metadata"],
                    "location": media["location"],
                    "height": media["height"],
                    "width": media["width"],
                    "duration": float(
                        media["duration"] if media.get("duration") else 0
                    ),
                },
            )
    # If found and already downloaded with hash, skip it
    if _should_skip_media(existing_media):
        return None

    # Ensure creator_id is available
    if not state.creator_id:
        raise ValueError(
            "Cannot create Media record: creator_id is required but not available in state"
        )

    return media_obj


async def process_media_download_accessible(
    config: FanslyConfig, state: DownloadState, media_infos: list[dict]
) -> None:
    """Process a list of media items to check accessibility.

    Args:
        config: FanslyConfig instance
        state: Current download state
        media_infos: List of dict objects to process

    Returns:
        True if all media items are accessible, False otherwise
    """
    try:
        json_output(1, "meta/media - p_m_d_a", media_infos)
        for media_info in media_infos:
            await process_media_download(config, state, media_info["media"])
    except Exception as e:
        json_output(1, "meta/media - p_m_d_a - error", str(e))
        json_output(1, "meta/media - p_m_d_a - error", traceback.format_exc())
        return False


async def process_media_download_handler(
    config: FanslyConfig, state: DownloadState, media: dict
) -> None:
    """Handle media download processing.

    Args:
        config: FanslyConfig instance
        state: Current download state
        media: Dictionary containing media data
    """
    json_output(1, "meta/media - p_m_d_h", media)
    # TODO: Implement media download handling
