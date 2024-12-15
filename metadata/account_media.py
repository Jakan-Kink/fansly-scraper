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
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .account import Account
from .base import Base
from .media import Media


class AccountMedia(Base):
    __tablename__ = "account_media"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), primary_key=True
    )
    mediaId: Mapped[int] = mapped_column(
        Integer, ForeignKey("media.id"), primary_key=True
    )
    previewId: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("media.id"), nullable=True
    )
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deletedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    media: Mapped[Media] = relationship("Media")
    preview: Mapped[Media] = relationship("Media")
    account: Mapped[Account] = relationship("Account")
    access: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    UniqueConstraint("accountId", "mediaId")


class AccountMediaBundle(Base):
    __tablename__ = "account_media_bundles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=False
    )
    previewId: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("media.id"), nullable=True
    )
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deletedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    accountMediaIds: Mapped[set[int]] = relationship("Media")
    account: Mapped[Account] = relationship("Account")
    access: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    purchased: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    whitelisted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    UniqueConstraint("accountId", "mediaId")


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    postId: Mapped[int] = mapped_column(Integer, ForeignKey("post.id"), nullable=False)
    contentId: Mapped[int] = mapped_column(
        Integer, ForeignKey("media.id"), nullable=False
    )
    pos: Mapped[int] = mapped_column(Integer, nullable=False)
    contentType: Mapped[int] = mapped_column(Integer, nullable=True)
