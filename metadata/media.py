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
from sqlalchemy.orm import Mapped, mapped_column, relationship

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


class Media(Base):
    __tablename__ = "media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=False
    )
    # metadata: Mapped[str] = mapped_column(String, nullable=True)
    location: Mapped[str] = mapped_column(String, nullable=True)
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
        session.commit()
        # modified_media = session.query(AccountMedia).get(filtered_account_media["id"])
        if "media" in media_infos:
            media_infos["media"].pop("variants", None)
            json_output(1, "meta/media - p_m_i - media (no var)", media_infos["media"])
        if "preview" in media_infos:
            media_infos["preview"].pop("variants", None)
            json_output(
                1, "meta/media - p_m_i - preview (no var)", media_infos["preview"]
            )


def process_media_download(
    config: FanslyConfig, state: DownloadState, media: dict
) -> None:
    json_output(1, "meta/media - p_m_d", media)
    pass


def process_media_download_accessible(
    config: FanslyConfig, state: DownloadState, media_infos: list[dict]
) -> bool:
    json_output(1, "meta/media - p_m_d_a", media_infos)
    pass


def process_media_download_handler(
    config: FanslyConfig, state: DownloadState, media: dict
) -> None:
    json_output(1, "meta/media - p_m_d_h", media)
    pass
