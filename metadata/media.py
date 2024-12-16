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

# from sqlalchemy.dialects.sqlite import insert as sqlite_insert
# from sqlalchemy.exc import IntegrityError
# from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from textio import json_output

from .base import Base

if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

media_varients = Table(
    "media_varients",
    Base.metadata,
    Column("mediaId", Integer, ForeignKey("media.id"), primary_key=True),
    Column("varientId", Integer, ForeignKey("media.id"), primary_key=True),
    UniqueConstraint("mediaId", "varientId"),
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
    varients: Mapped[set[Media]] = relationship(
        "Media",
        collection_class=set,
        secondary="media_varients",
        lazy="joined",
        primaryjoin=id == media_varients.c.mediaId,
        secondaryjoin=id == media_varients.c.varientId,
    )


def process_media_metadata(metadata: dict) -> None:
    json_output(1, "meta/media - p_m_m", metadata)
    pass


def process_media_info(config: FanslyConfig, media_infos: list[dict]) -> None:
    json_output(1, "meta/media - p_m_i", media_infos)
    pass


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
