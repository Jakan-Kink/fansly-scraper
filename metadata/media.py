from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime
from sqlalchemy import Enum as SQLAEnum
from sqlalchemy import (
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    and_,
    select,
)

# from sqlalchemy.dialects.sqlite import insert as sqlite_insert
# from sqlalchemy.exc import IntegrityError
# from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class MediaType(Enum):
    POST = 1
    MESSAGE = 2
    VARIENT = 3
    BUNDLE = 4
    AVATAR = 5
    BANNER = 6


class MediaFormat(Enum):
    IMAGE = 1
    VIDEO = 2
    AUDIO = 3


class Media(Base):
    __tablename__ = "media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId: Mapped[int] = mapped_column(
        Integer, ForeignKey("account.id"), nullable=False
    )
    metadata: Mapped[str] = mapped_column(String, nullable=True)
    location: Mapped[str] = mapped_column(String, nullable=True)
    mimetype: Mapped[str] = mapped_column(String, nullable=True)
    height: Mapped[int] = mapped_column(Integer, nullable=True)
    width: Mapped[int] = mapped_column(Integer, nullable=True)
    type: Mapped[int] = mapped_column(Integer, nullable=True)
    status: Mapped[int] = mapped_column(Integer, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    varients: Mapped[set[dict]] = relationship("MediaVarient", back_populates="media")


media_varients = Table(
    "media_varients",
    Base.metadata,
    Column("mediaId", Integer, ForeignKey("media.id"), primary_key=True),
    Column("location", String, primary_key=True),
    Column("mimetype", String, nullable=False),
    Column("height", Integer, nullable=False),
    Column("width", Integer, nullable=False),
    Column("type", Integer, nullable=False),
    Column("status", Integer, nullable=False),
    Column("createdAt", DateTime(timezone=True), nullable=False),
    Column("updatedAt", DateTime(timezone=True), nullable=False),
    UniqueConstraint("mediaId", "location"),
)


def process_media_metadata(metadata: dict) -> None:
    pass
