from __future__ import annotations

import copy
import json
import traceback
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import (
    Mapped,
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

# Global cache of media locations
media_locations_by_media: dict[int, set[int]] = {}


media_variants = Table(
    "media_variants",
    Base.metadata,
    Column("mediaId", BigInteger, ForeignKey("media.id"), primary_key=True),
    Column("variantId", BigInteger, ForeignKey("media.id"), primary_key=True),
    UniqueConstraint("mediaId", "variantId"),
)


class MediaLocation(Base):
    """Represents a storage location for media content.

    This class maps the physical location of media files, including variants
    and previews. Each media item can have multiple locations for different
    purposes (e.g., CDN URLs, local paths).

    Attributes:
        mediaId: ID of the media this location belongs to (snowflake ID)
        locationId: CDN location identifier (integer, API sends as "1", "102", "103" strings)
        location: The actual URL or path where the media is stored
        media: Relationship to the parent Media object
    """

    __tablename__ = "media_locations"

    mediaId: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("media.id"), primary_key=True
    )
    locationId: Mapped[int] = mapped_column(BigInteger, primary_key=True)
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
    __table_args__ = (
        # Content hash lookup
        Index("ix_media_content_hash", "content_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    accountId: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=False
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
    content_hash: Mapped[str] = mapped_column(String, nullable=True)
    is_downloaded: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        server_default="false",  # Set explicit server default
    )
    variants: Mapped[set[Media]] = relationship(
        "Media",
        collection_class=set,
        secondary="media_variants",
        lazy="select",  # Use select loading for lazy loading
        primaryjoin=id == media_variants.c.mediaId,
        secondaryjoin=id == media_variants.c.variantId,
    )
    locations: Mapped[dict[int, MediaLocation]] = relationship(
        "MediaLocation",
        collection_class=attribute_mapped_collection("locationId"),
        cascade="all, delete-orphan",
        lazy="select",  # Use select loading for lazy loading
        back_populates="media",
    )
    stash_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MediaBatch:
    def __init__(self) -> None:
        self.media_items = []  # Media records to insert/update
        self.locations = []  # Location records to insert
        self.variants = []  # Variant relationships to insert
        self.seen_media = set()  # Track media IDs we've seen
        self.seen_locations = set()  # Track (mediaId, locationId) pairs
        self.seen_variants = set()  # Track (mediaId, variantId) pairs

    def add_media(
        self,
        media_item: dict,
        account_id: int | None = None,
        parent_created_at: int | None = None,
    ) -> None:
        """Add a media item and all its variants recursively.

        Args:
            media_item: The media item to process
            account_id: Optional account ID if not in media_item
            parent_created_at: Optional createdAt from parent media for variants
        """
        # Process media item first to get converted IDs
        filtered_media = self._prepare_media_data(media_item, account_id)
        media_id = filtered_media["id"]  # Use converted integer ID

        if media_id in self.seen_media:
            return

        # For variants without createdAt, use parent's
        if parent_created_at is not None and "createdAt" not in media_item:
            filtered_media["createdAt"] = datetime.fromtimestamp(parent_created_at, UTC)

        self.media_items.append(filtered_media)
        self.seen_media.add(media_id)

        # Process locations
        if "locations" in media_item:
            for loc in media_item["locations"]:
                # Convert locationId to int (API returns strings)
                location_id = (
                    int(loc["locationId"])
                    if isinstance(loc["locationId"], str)
                    else loc["locationId"]
                )
                loc_key = (media_id, location_id)
                if loc_key not in self.seen_locations:
                    self.locations.append(
                        {
                            "mediaId": media_id,
                            "locationId": location_id,
                            "location": loc["location"],
                        }
                    )
                    self.seen_locations.add(loc_key)

        # SKIP VARIANTS - we only download and need to track the primary media
        # Variants (720p, 480p, thumbnails, etc.) are not downloaded, so no need to store them
        # This prevents bloating the database with hundreds of unused variant records per media

    def _prepare_media_data(self, media_item: dict, account_id: int | None) -> dict:
        """Extract and prepare media data for database operations.

        This method:
        1. Filters known fields and relationships
        2. Processes metadata (dimensions, duration, etc.)
        3. Converts timestamps
        4. Sets default values

        Args:
            media_item: Raw media item dictionary
            account_id: Optional account ID to use if not in media_item

        Returns:
            Processed and filtered media data dictionary
        """
        # Make a deep copy to prevent mutations
        media_item = copy.deepcopy(media_item)

        # Define known relations and fields to ignore
        known_relations = {
            # Handled relationships
            "locations",
            "variants",
            "preview",  # Preview media is processed separately
            # Intentionally ignored fields
            "filename",
            "metadata",  # Handled by mapping to meta_info
            "variantHash",  # Not used in database
            "permissionFlags",
            "price",
            "permissions",
            "likeCount",
            "purchased",
            "whitelisted",
            "accountPermissionFlags",
            "liked",
        }

        # Process data using Base class method
        filtered_media, unknown_attrs = Media.process_data(
            media_item,
            known_relations,
            "meta/media - batch",
            convert_timestamps_fields=("createdAt", "updatedAt"),
        )

        # Log unknown attributes at higher level if present
        if unknown_attrs:
            json_output(2, "meta/media - unknown_attrs", unknown_attrs)

        # Set account ID if provided
        filtered_media["accountId"] = filtered_media.get("accountId", account_id)

        # Process metadata for video dimensions and duration
        if "metadata" in media_item:
            filtered_media["meta_info"] = media_item["metadata"]
            if media_item.get("mimetype", "").startswith("video/"):
                try:
                    metadata = json.loads(media_item["metadata"])
                    if "original" in metadata:
                        filtered_media.update(
                            {
                                "width": metadata["original"].get("width"),
                                "height": metadata["original"].get("height"),
                            }
                        )
                    if "duration" in metadata:
                        filtered_media["duration"] = float(metadata.get("duration"))
                except (
                    json.JSONDecodeError,
                    ValueError,
                    AttributeError,
                    KeyError,
                ) as e:
                    json_output(
                        2,
                        "meta/media - metadata_error",
                        {
                            "error": str(e),
                            "media_id": media_item.get("id"),
                            "metadata": media_item["metadata"],
                        },
                    )

        return filtered_media


async def _get_or_create_media(
    session: AsyncSession,
    items: list[dict],
    phase_name: str,
) -> dict[int, Media]:
    """Get existing media records or create new ones.

    This function:
    1. Gets all existing media in one query
    2. Creates new records with minimum fields
    3. Updates changed fields on existing records
    4. Returns a mapping of media IDs to objects
    """
    existing_media = {}
    new_count = 0
    updated_count = 0

    # Process items in batches
    for batch_items in _chunk_items(items, 100):
        for item in batch_items:
            # Prepare minimum fields for get_or_create
            item_id = int(item["id"])
            filters = {"id": item_id}
            defaults = {
                "accountId": item["accountId"],
                "type": item.get("type"),
                "status": item.get("status"),
            }

            # Get or create the media object
            media, created = await Media.async_get_or_create(
                session,
                filters=filters,
                defaults=defaults,
            )

            # Update additional fields if needed
            if Base.update_fields(media, item, exclude={"id"}):
                updated_count += 0 if created else 1

            if created:
                new_count += 1

            existing_media[item_id] = media

    # Log changes
    if new_count:
        json_output(
            1,
            f"meta/media - batch - {phase_name}",
            {"new_objects": new_count},
        )

    if updated_count:
        json_output(
            1,
            f"meta/media - batch - {phase_name}",
            {"updated_objects": updated_count},
        )

    return existing_media


async def _sync_relationships(
    session: AsyncSession,
    batch: MediaBatch,
) -> None:
    """Synchronize media relationships (locations and variants).

    This function:
    1. Gets existing relationships in one query
    2. Creates new relationships that don't exist
    3. Updates changed locations
    4. Maintains global location cache
    """
    # Process locations using bulk insert with conflict handling
    if batch.locations:
        try:
            # Use PostgreSQL-specific upsert with ON CONFLICT DO NOTHING
            # This handles duplicates gracefully and avoids autoflush issues
            insert_stmt = pg_insert(MediaLocation.__table__).values(batch.locations)
            # On conflict (duplicate key), do nothing
            upsert_stmt = insert_stmt.on_conflict_do_nothing(
                index_elements=["mediaId", "locationId"]
            )
            await session.execute(upsert_stmt)

            json_output(
                1,
                "meta/media - batch - locations",
                {"total_locations": len(batch.locations)},
            )
        except Exception as e:
            json_output(
                2,  # Warning level
                "meta/media - batch - locations - error",
                {
                    "error": str(e),
                    "location_count": len(batch.locations),
                },
            )

    # SKIP VARIANTS - variants are not tracked anymore to reduce database bloat
    # We only store the primary media that is actually downloaded


async def _process_media_batch(
    session: AsyncSession,
    batch: MediaBatch,
) -> None:
    """Process all collected media operations.

    Note: This function does NOT create its own savepoint. The caller is responsible
    for transaction management.
    """
    if not batch.media_items:
        return

    try:
        # Split into parents and variants
        parent_media = []
        variant_media = []
        for item in batch.media_items:
            is_variant = any(v["variantId"] == item["id"] for v in batch.variants)
            (variant_media if is_variant else parent_media).append(item)

        json_output(
            1,
            "meta/media - batch - split",
            {
                "parent_count": len(parent_media),
                "variant_count": len(variant_media),
            },
        )

        # Process parents first, then variants
        for phase, items in enumerate([parent_media, variant_media]):
            if items:  # Skip empty phases
                phase_name = "parent" if phase == 0 else "variant"
                await _get_or_create_media(session, items, phase_name)

        # Flush media records to database before processing relationships
        # This ensures media IDs exist for foreign key constraints
        await session.flush()

        # Process relationships
        await _sync_relationships(session, batch)

        # Force flush to catch any constraint violations immediately
        await session.flush()
    except Exception as e:
        json_output(
            0,
            "meta/media - batch - ERROR",
            {
                "error_type": type(e).__name__,
                "error": str(e),
                "parent_count": len(parent_media) if "parent_media" in locals() else 0,
                "variant_count": len(variant_media)
                if "variant_media" in locals()
                else 0,
            },
        )
        raise


def _chunk_items(items: list[dict], chunk_size: int) -> Iterator[list[dict]]:
    """Split items into manageable chunks to avoid memory issues."""
    for i in range(0, len(items), chunk_size):
        yield items[i : i + chunk_size]


async def _process_account_media_batch(
    session: AsyncSession,
    account_media_batch: list[dict],
) -> None:
    """Process a batch of AccountMedia records.

    Args:
        session: SQLAlchemy async session
        account_media_batch: List of AccountMedia records to process
    """
    from .account import AccountMedia

    if not account_media_batch:
        return

    # Get existing records
    result = await session.execute(
        select(AccountMedia.id).where(
            AccountMedia.id.in_([am["id"] for am in account_media_batch])
        )
    )
    existing_account_media = set(result.scalars().all())

    # Split into inserts and updates
    to_insert = []
    to_update = []
    for am in account_media_batch:
        if am["id"] in existing_account_media:
            to_update.append(am)
        else:
            to_insert.append(am)

    # Bulk insert new records using PostgreSQL upsert
    if to_insert:
        insert_stmt = pg_insert(AccountMedia.__table__).values(to_insert)
        upsert_stmt = insert_stmt.on_conflict_do_nothing()
        await session.execute(upsert_stmt)

    # Bulk update existing records
    if to_update:
        for item in to_update:
            am_id = item.pop("id")
            # Only update fields that are present and not None
            update_values = {k: v for k, v in item.items() if v is not None}
            if update_values:  # Only update if we have values to update
                await session.execute(
                    update(AccountMedia)
                    .where(AccountMedia.id == am_id)
                    .values(**update_values)
                )


@require_database_config
@with_database_session(async_session=True)
async def process_media_info(
    _config: FanslyConfig,
    media_infos: dict,
    session: AsyncSession | None = None,
) -> None:
    """Process media info and store in the database.

    Args:
        config: FanslyConfig instance for database access
        media_infos: Dictionary containing media info data or a batch of media infos
        session: Optional AsyncSession for database operations
    """
    from .account import AccountMedia

    # Create batch collector
    batch = MediaBatch()

    # Process single item or batch
    items_to_process = media_infos.get("batch", [media_infos])

    # Process AccountMedia records first
    account_media_batch = []
    for media_info in items_to_process:
        # Process AccountMedia data
        known_relations = {
            "media",
            "preview",
            "permissionFlags",
            "price",
            "permissions",
            "likeCount",
            "purchased",
            "whitelisted",
            "accountPermissionFlags",
            "liked",
        }
        filtered_account_media, _ = AccountMedia.process_data(
            media_info,
            known_relations,
            "meta/media - p_m_i",
            ("createdAt", "deletedAt"),
        )
        account_media_batch.append(filtered_account_media)

        # Add media and preview to batch
        if "media" in media_info:
            batch.add_media(
                media_info["media"],
                filtered_account_media["accountId"],
            )
        if "preview" in media_info:
            batch.add_media(
                media_info["preview"],
                filtered_account_media["accountId"],
            )

    # Check if we're already in a transaction
    if session.in_transaction():
        # We're already in a transaction, so just process the data without creating a new savepoint
        # IMPORTANT: Process Media BEFORE AccountMedia since AccountMedia.mediaId is a FK to Media.id
        # Use no_autoflush to prevent premature flushes during queries
        with session.no_autoflush:
            await _process_media_batch(session, batch)
            await _process_account_media_batch(session, account_media_batch)
    else:
        # Not in a transaction, create a nested savepoint
        # IMPORTANT: Process Media BEFORE AccountMedia since AccountMedia.mediaId is a FK to Media.id
        async with await session.begin_nested():
            with session.no_autoflush:
                await _process_media_batch(session, batch)
                await _process_account_media_batch(session, account_media_batch)


# async def _process_single_media_info(
#     config: FanslyConfig, media_infos: dict, session: AsyncSession
# ) -> None:
#     """Process a single media info item.

#     Args:
#         config: FanslyConfig instance for database access
#         media_infos: Dictionary containing media info data
#         session: SQLAlchemy async session
#     """
#     from .account import AccountMedia

#     media_infos = copy.deepcopy(media_infos)
#     json_output(1, "meta/media - p_m_i", media_infos)

#     # Known attributes that are handled separately
#     known_relations = {
#         # Handled relationships
#         "media",
#         "preview",
#         # Intentionally ignored fields
#         "permissionFlags",
#         "price",
#         "permissions",
#         "likeCount",
#         "purchased",
#         "whitelisted",
#         "accountPermissionFlags",
#         "liked",
#     }

#     # Process media data
#     filtered_account_media, _ = AccountMedia.process_data(
#         media_infos, known_relations, "meta/media - p_m_i", ("createdAt", "deletedAt")
#     )
#     json_output(1, "meta/media - p_m_i - filtered", filtered_account_media)

#     # Get or create account media
#     account_media, created = await AccountMedia.async_get_or_create(
#         session,
#         {
#             "id": filtered_account_media["id"],
#             "accountId": filtered_account_media["accountId"],
#             "mediaId": filtered_account_media["mediaId"],
#         },
#         filtered_account_media,
#     )

#     # Update fields
#     Base.update_fields(account_media, filtered_account_media)

#     # Process related media items
#     for field in ["media", "preview"]:
#         if field in media_infos:
#             await process_media_item_dict(config, media_infos[field], session=session)


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
        async with config._database.async_session_scope() as db_session:
            await _process_media_item_dict_inner(config, media_item, session=db_session)
    else:
        await _process_media_item_dict_inner(config, media_item, session=session)


# async def _process_media_locations(
#     session: AsyncSession,
#     media: Media,
#     locations: list[dict[str, any]],
# ) -> None:
#     """Process media locations.

#     Args:
#         session: SQLAlchemy async session
#         media: Media instance
#         locations: List of location data dictionaries
#     """
#     # Get all location IDs
#     media_id = media.id
#     wanted_location_ids = {loc["locationId"] for loc in locations}

#     # Get existing locations in one query
#     result = await session.execute(
#         select(MediaLocation).where(
#             MediaLocation.mediaId == media_id,
#             MediaLocation.locationId.in_(wanted_location_ids),
#         )
#     )
#     existing_locations = result.scalars().all()
#     existing_location_ids = {loc.locationId for loc in existing_locations}

#     # Add new locations
#     locations_to_add = []
#     for location_id in wanted_location_ids - existing_location_ids:
#         locations_to_add.append(
#             MediaLocation(
#                 mediaId=media_id,
#                 locationId=location_id,
#                 location=next(
#                     loc["location"]
#                     for loc in locations
#                     if loc["locationId"] == location_id
#                 ),
#             )
#         )

#     # Update cache
#     media_locations_by_media[media_id] = wanted_location_ids

#     # Bulk insert new locations
#     if locations_to_add:
#         session.add_all(locations_to_add)


# async def _process_media_variants(
#     session: AsyncSession,
#     config: FanslyConfig,
#     media: Media,
#     variants: list[dict[str, any]],
#     account_id: int,
# ) -> None:
#     """Process media variants.

#     Args:
#         session: SQLAlchemy async session
#         config: FanslyConfig instance
#         media: Media instance
#         variants: List of variant data dictionaries
#         account_id: ID of the account that owns the media
#     """
#     # Get existing variants to avoid duplicates
#     result = await session.execute(
#         select(media_variants.c.variantId).where(media_variants.c.mediaId == media.id)
#     )
#     existing_variants = {row[0] for row in result.fetchall()}

#     # Filter out existing variants
#     new_variants = [v for v in variants if v["id"] not in existing_variants]
#     if not new_variants:
#         return

#     # Process all variants in one transaction
#     async with session.begin_nested():
#         # Process all variant media items
#         for variant in new_variants:
#             await _process_media_item_dict_inner(
#                 config,
#                 variant,
#                 session=session,
#                 account_id=account_id,
#             )

#         # Batch insert all variant relationships
#         if new_variants:
#             await session.execute(
#                 media_variants.insert()
#                 .prefix_with("OR IGNORE")
#                 .values(
#                     [
#                         {"mediaId": media.id, "variantId": variant["id"]}
#                         for variant in new_variants
#                     ]
#                 )
#             )


@with_database_session(async_session=True)
async def _process_media_item_dict_inner(
    _config: FanslyConfig,
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
    # Check if media_item is the correct type
    if not isinstance(media_item, dict):
        json_output(
            2,
            "meta/media - invalid_media_item_type",
            {"type": type(media_item).__name__, "value": str(media_item)},
        )
        return

    if not isinstance(session, AsyncSession):
        json_output(1, "meta/media - _p_m_i_d_i - no_session", type(session))
        raise TypeError("No session provided for database operations")

    # Create batch collector and add the media item
    batch = MediaBatch()
    batch.add_media(media_item, account_id)

    # Process all data in a single transaction
    await _process_media_batch(session, batch)


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
    _config: FanslyConfig,
    state: DownloadState,
    media: MediaItem | dict[str, any],
    session: AsyncSession | None = None,
    is_preview: bool = False,
) -> Media | None:
    """Process a media item for download and return its Media record.

    Args:
        config: FanslyConfig instance
        state: Current download state
        media: MediaItem to process
        session: Optional session to use, will create one if not provided
        is_preview: If True, process as preview content

    Returns:
        Media record if found or created, None if media should be skipped

    Raises:
        ValueError: If creator_id is not available in state
    """
    media = copy.deepcopy(media)
    json_output(1, "meta/media - p_m_d", media)
    if isinstance(media, MediaItem):
        # Query first approach
        media_id = media.media_id
        mimetype = media.preview_mimetype if is_preview else media.mimetype
        created_at = media.created_at

        existing_media = (
            await session.execute(select(Media).where(Media.id == media_id))
        ).scalar_one_or_none()
    else:
        media_id = media.get("id", -1)
        mimetype = media.get("mimetype")
        created_at = media.get("createdAt")

        existing_media = (
            await session.execute(select(Media).where(Media.id == media_id))
        ).scalar_one_or_none()
    # If found and already downloaded with hash, skip it
    if _should_skip_media(existing_media):
        json_output(
            1,
            "process_media_download",
            {
                "action": "skipping_media",
                "media_id": media_id,
                "has_existing": existing_media is not None,
                "is_downloaded": (
                    existing_media.is_downloaded if existing_media else None
                ),
                "has_hash": (
                    bool(existing_media.content_hash) if existing_media else None
                ),
                "local_filename": (
                    existing_media.local_filename if existing_media else None
                ),
            },
        )
        return None

    media_obj: Media | None = existing_media if existing_media else None

    if not isinstance(media, MediaItem):
        # Get or create media record
        media_obj, created = await Media.async_get_or_create(
            session,
            {"id": media_id},
            {
                "accountId": int(state.creator_id),
                "mimetype": mimetype,
                "createdAt": (
                    datetime.fromtimestamp(created_at, tz=UTC) if created_at else None
                ),
                "updatedAt": (
                    datetime.fromtimestamp(media["updatedAt"], tz=UTC)
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
    # For MediaItem, create record if it doesn't exist
    elif media_obj is None:
        media_obj = Media(
            id=media_id,
            accountId=int(state.creator_id),
            mimetype=mimetype,
            createdAt=(
                datetime.fromtimestamp(created_at, tz=UTC) if created_at else None
            ),
        )
        session.add(media_obj)
        await session.flush()

    # Ensure creator_id is available
    if not state.creator_id:
        raise ValueError(
            "Cannot create Media record: creator_id is required but not available in state"
        )

    return media_obj


async def process_media_download_accessible(
    config: FanslyConfig, state: DownloadState, media_infos: list[dict]
) -> bool:
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
    else:
        return True
