from __future__ import annotations

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
    __tablename__ = "media_locations"

    mediaId: Mapped[int] = mapped_column(
        Integer, ForeignKey("media.id"), primary_key=True
    )
    locationId: Mapped[str] = mapped_column(String, primary_key=True)
    location: Mapped[str] = mapped_column(String, nullable=False)
    media: Mapped[Media] = relationship("Media", back_populates="locations")


class Media(Base):
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
    type: Mapped[int] = mapped_column(Integer, nullable=True)
    status: Mapped[int] = mapped_column(Integer, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    variants: Mapped[set[Media]] = relationship(
        "Media",
        collection_class=set,
        secondary="media_variants",
        lazy="joined",
        primaryjoin=id == media_variants.c.mediaId,
        secondaryjoin=id == media_variants.c.variantId,
    )
    locations: Mapped[dict[str, MediaLocation]] = relationship(
        "MediaLocation",
        collection_class=attribute_mapped_collection("locationId"),
        cascade="all, delete-orphan",
        lazy="joined",
        back_populates="media",
    )


def process_media_metadata(metadata: dict) -> None:
    json_output(1, "meta/media - p_m_m", metadata)
    pass


def process_media_info(config: FanslyConfig, media_infos: dict) -> None:
    from .account import AccountMedia

    json_output(1, "meta/media - p_m_i", media_infos)
    account_media_columns = {column.name for column in inspect(AccountMedia).columns}
    media_infos["createdAt"] = datetime.fromtimestamp(
        media_infos["createdAt"], timezone.utc
    )
    if media_infos.get("deletedAt") is not None:
        media_infos["deletedAt"] = datetime.fromtimestamp(
            media_infos["deletedAt"], timezone.utc
        )
    filtered_account_media = {
        k: v for k, v in media_infos.items() if k in account_media_columns
    }
    json_output(1, "meta/media - p_m_i - filtered", filtered_account_media)
    with config._database.sync_session() as session:
        existing_account_media = (
            session.query(AccountMedia)
            .filter_by(
                id=filtered_account_media["id"],
                accountId=filtered_account_media["accountId"],
                mediaId=filtered_account_media["mediaId"],
            )
            .first()
        )
        if existing_account_media:
            for key, value in filtered_account_media.items():
                setattr(existing_account_media, key, value)
        else:
            session.add(AccountMedia(**filtered_account_media))
        session.flush()
        session.commit()
        if "media" in media_infos:
            process_media_item_dict(config, media_infos["media"], session)
        if "preview" in media_infos:
            process_media_item_dict(config, media_infos["preview"], session)


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
    config: FanslyConfig, media_item: dict, session: Session, account_id: int = None
) -> None:
    media_columns = {column.name for column in inspect(Media).columns}
    session.flush()
    if "createdAt" in media_item and media_item["createdAt"] is not None:
        media_item["createdAt"] = datetime.fromtimestamp(
            media_item["createdAt"], timezone.utc
        )
    if "updatedAt" in media_item and media_item["updatedAt"] is not None:
        media_item["updatedAt"] = datetime.fromtimestamp(
            media_item["updatedAt"], timezone.utc
        )
    filtered_media = {k: v for k, v in media_item.items() if k in media_columns}
    filtered_media["accountId"] = filtered_media.get("accountId", account_id)

    # Handle metadata field
    if "metadata" in media_item:
        filtered_media["meta_info"] = media_item["metadata"]
    existing_media = session.query(Media).filter_by(id=filtered_media["id"]).first()
    if existing_media:
        for key, value in filtered_media.items():
            setattr(existing_media, key, value)
    else:
        session.add(Media(**filtered_media))
    session.flush()
    existing_media = session.query(Media).filter_by(id=filtered_media["id"]).first()

    # Process locations
    if "locations" in media_item:
        # Clear existing locations
        session.query(MediaLocation).filter_by(mediaId=existing_media.id).delete()
        for location_data in media_item["locations"]:
            location = MediaLocation(
                mediaId=existing_media.id,
                locationId=location_data["locationId"],
                location=location_data["location"],
            )
            session.add(location)

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
                .values(mediaId=existing_media.id, variantId=variant["id"])
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
