from __future__ import annotations

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
)
from sqlalchemy.inspection import inspect

# from sqlalchemy.dialects.sqlite import insert as sqlite_insert
# from sqlalchemy.exc import IntegrityError
# from sqlalchemy.ext.asyncio import AsyncSession
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


def process_media_info(config: FanslyConfig, media_infos: dict) -> None:
    from .account import AccountMedia

    json_output(1, "meta/media - p_m_i", media_infos)
    account_media_columns = {column.name for column in inspect(AccountMedia).columns}
    # Convert timestamps to datetime objects
    date_fields = ("createdAt", "deletedAt")
    for date_field in date_fields:
        if date_field in media_infos and media_infos[date_field]:
            media_infos[date_field] = datetime.fromtimestamp(
                (
                    media_infos[date_field] / 1000
                    if media_infos[date_field] > 1e10
                    else media_infos[date_field]
                ),
                timezone.utc,
            )
    filtered_account_media = {
        k: v for k, v in media_infos.items() if k in account_media_columns
    }
    json_output(1, "meta/media - p_m_i - filtered", filtered_account_media)
    with config._database.sync_session() as session:
        account_media = (
            session.query(AccountMedia)
            .filter_by(
                id=filtered_account_media["id"],
                accountId=filtered_account_media["accountId"],
                mediaId=filtered_account_media["mediaId"],
            )
            .first()
        )
        if not account_media:
            account_media = AccountMedia(**filtered_account_media)
            session.add(account_media)

        # Log any unknown attributes
        unknown_attrs = {
            k: v
            for k, v in filtered_account_media.items()
            if k not in account_media_columns
        }
        if unknown_attrs:
            json_output(
                1, "meta/media - account_media_unknown_attributes", unknown_attrs
            )

        # Update fields that have changed
        for key, value in filtered_account_media.items():
            if key in account_media_columns:
                if getattr(account_media, key) != value:
                    setattr(account_media, key, value)

        session.flush()

        if "media" in media_infos:
            process_media_item_dict(config, media_infos["media"], session)
        if "preview" in media_infos:
            process_media_item_dict(config, media_infos["preview"], session)

        session.commit()


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
    # Check if media_item is the correct type
    if not isinstance(media_item, dict):
        json_output(
            2,
            "meta/media - invalid_media_item_type",
            {"type": type(media_item).__name__, "value": str(media_item)},
        )
        return

    media_columns = {column.name for column in inspect(Media).columns}
    session.flush()
    # Convert timestamps to datetime objects
    date_fields = ("createdAt", "updatedAt")
    for date_field in date_fields:
        if date_field in media_item and media_item[date_field]:
            media_item[date_field] = datetime.fromtimestamp(
                (
                    media_item[date_field] / 1000
                    if media_item[date_field] > 1e10
                    else media_item[date_field]
                ),
                timezone.utc,
            )
    filtered_media = {k: v for k, v in media_item.items() if k in media_columns}
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
    media = session.query(Media).filter_by(id=filtered_media["id"]).first()
    if not media:
        media = Media(**filtered_media)
        session.add(media)

    # Log any unknown attributes
    unknown_attrs = {k: v for k, v in filtered_media.items() if k not in media_columns}
    if unknown_attrs:
        json_output(1, "meta/media - media_unknown_attributes", unknown_attrs)

    # Update fields that have changed
    for key, value in filtered_media.items():
        if key in media_columns:
            if getattr(media, key) != value:
                setattr(media, key, value)

    session.flush()

    # Process locations
    if "locations" in media_item:
        # Get existing locations
        existing_locations = {
            loc.locationId: loc
            for loc in session.query(MediaLocation).filter_by(mediaId=media.id).all()
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


def process_media_download(
    config: FanslyConfig, state: DownloadState, media: MediaItem
) -> None:
    json_output(1, "meta/media - p_m_d", media)
    pass


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
