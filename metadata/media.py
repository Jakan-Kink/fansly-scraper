from __future__ import annotations

from datetime import datetime

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

from .base import Base

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
    pass
