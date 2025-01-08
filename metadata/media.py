from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
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
from sqlalchemy.orm import (
    Mapped,
    Session,
    attribute_mapped_collection,
    mapped_column,
    relationship,
)

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
    is_downloaded: Mapped[bool] = mapped_column(Integer, default=False, nullable=False)
    variants: Mapped[set[Media]] = relationship(
        "Media",
        collection_class=set,
        secondary="media_variants",
        lazy="select",
        primaryjoin=id == media_variants.c.mediaId,
        secondaryjoin=id == media_variants.c.variantId,
    )
    locations: Mapped[dict[str, MediaLocation]] = relationship(
        "MediaLocation",
        collection_class=attribute_mapped_collection("locationId"),
        cascade="all, delete-orphan",
        lazy="select",
        back_populates="media",
    )


def process_media_metadata(metadata: dict) -> None:
    json_output(1, "meta/media - p_m_m", metadata)
    pass


@require_database_config
def process_media_info(config: FanslyConfig, media_infos: dict) -> None:
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

    with config._database.sync_session() as session:
        # Query first approach
        account_media = session.execute(
            select(AccountMedia).where(
                AccountMedia.id == filtered_account_media["id"],
                AccountMedia.accountId == filtered_account_media["accountId"],
                AccountMedia.mediaId == filtered_account_media["mediaId"],
            )
        ).scalar_one_or_none()

        # Create if doesn't exist with minimum required fields
        if account_media is None:
            account_media = AccountMedia(**filtered_account_media)
            session.add(account_media)

        # Update fields that have changed
        for key, value in filtered_account_media.items():
            if getattr(account_media, key) != value:
                setattr(account_media, key, value)

        session.flush()

        # Process related media items
        if "media" in media_infos:
            process_media_item_dict(config, media_infos["media"], session)
        if "preview" in media_infos:
            process_media_item_dict(config, media_infos["preview"], session)

        session.commit()


@require_database_config
def process_media_item_dict(
    config: FanslyConfig, media_item: dict, session: Session | None = None
) -> None:
    json_output(1, "meta/media - p_m_i_h - media_item[dict]", media_item)
    if session is None:
        with config._database.sync_session() as session:
            _process_media_item_dict_inner(config, media_item, session)
    else:
        _process_media_item_dict_inner(config, media_item, session)


def _process_media_item_dict_inner(
    config: FanslyConfig,
    media_item: dict[str, any],
    session: Session,
    account_id: int | None = None,
) -> None:
    """Process a media item dictionary and store it in the database.

    Args:
        config: FanslyConfig instance for database access
        media_item: Dictionary containing media data including metadata
        session: SQLAlchemy session for database operations
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

    # Handle metadata field
    if "metadata" in media_item:
        filtered_media["meta_info"] = media_item["metadata"]
        if media_item.get("mimetype", "").startswith("video/"):
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

    # Ensure required fields are present before proceeding
    if "accountId" not in filtered_media:
        json_output(
            1,
            "meta/media - missing_required_field",
            {"mediaId": filtered_media.get("id"), "missing_field": "accountId"},
        )
        return  # Skip this media if accountId is missing
    # Query first approach
    media = session.execute(
        select(Media).where(Media.id == filtered_media["id"])
    ).scalar_one_or_none()

    if media is None:
        media = Media(**filtered_media)
        session.add(media)

    # Update fields that have changed
    for key, value in filtered_media.items():
        if getattr(media, key) != value:
            setattr(media, key, value)

    session.flush()

    # Process locations
    if "locations" in media_item:
        # Get existing locations
        existing_locations = {
            loc.locationId: loc
            for loc in session.execute(
                select(MediaLocation).where(MediaLocation.mediaId == media.id)
            )
            .scalars()
            .all()
        }

        # Process each location
        for location_data in media_item["locations"]:
            location_id = location_data["locationId"]
            if location_id in existing_locations:
                # Update if location changed
                if (
                    existing_locations[location_id].location
                    != location_data["location"]
                ):
                    existing_locations[location_id].location = location_data["location"]
                # Remove from existing_locations to track what needs to be deleted
                del existing_locations[location_id]
            else:
                # Add new location
                location = MediaLocation(
                    mediaId=media.id,
                    locationId=location_id,
                    location=location_data["location"],
                )
                session.add(location)

        # Delete locations that no longer exist
        for location in existing_locations.values():
            session.delete(location)

    # Process variants
    if "variants" in media_item:
        for variant in media_item["variants"]:
            _process_media_item_dict_inner(
                config,
                variant,
                session,
                account_id=filtered_media["accountId"],
            )
            session.execute(
                media_variants.insert()
                .values(mediaId=media.id, variantId=variant["id"])
                .prefix_with("OR IGNORE")
            )
    session.commit()


@require_database_config
def process_media_download(
    config: FanslyConfig, state: DownloadState, media: MediaItem
) -> Media | None:
    """Process a media item for download and return its Media record.

    Args:
        config: FanslyConfig instance
        state: Current download state
        media: MediaItem to process

    Returns:
        Media record if found or created, None if media should be skipped
    """
    media = copy.deepcopy(media)
    json_output(1, "meta/media - p_m_d", media)

    with config._database.sync_session() as session:
        # Query first approach
        existing_media = session.execute(
            select(Media).where(Media.id == media.media_id)
        ).scalar_one_or_none()

        # If found and already downloaded with hash, skip it
        if (
            existing_media
            and existing_media.is_downloaded
            and existing_media.content_hash
            and existing_media.local_filename
        ):
            return None

        # If not found or missing required fields, create/update record
        if not existing_media:
            if not state.creator_id:
                raise ValueError(
                    "Cannot create Media record: creator_id is required but not available in state"
                )

            # Create base record if doesn't exist
            if not existing_media:
                existing_media = Media(
                    id=media.media_id,
                    accountId=int(state.creator_id),
                    mimetype=media.mimetype,
                    createdAt=(
                        datetime.fromtimestamp(media.created_at, tz=timezone.utc)
                        if media.created_at
                        else None
                    ),
                )
                session.add(existing_media)
            else:
                # Update existing record with new info
                existing_media.accountId = int(state.creator_id)
                if media.mimetype:
                    existing_media.mimetype = media.mimetype
                if media.created_at:
                    existing_media.createdAt = datetime.fromtimestamp(
                        media.created_at, tz=timezone.utc
                    )

            session.commit()

        return existing_media


def process_media_download_accessible(
    config: FanslyConfig, state: DownloadState, media_infos: list[MediaItem]
) -> bool:
    json_output(1, "meta/media - p_m_d_a", media_infos)
    pass


def process_media_download_handler(
    config: FanslyConfig, state: DownloadState, media: dict
) -> None:
    json_output(1, "meta/media - p_m_d_h", media)
    pass
