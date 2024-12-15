from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .media import Media
    from .post import Post
    from .wall import Wall


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    displayName: Mapped[str | None] = mapped_column(String, nullable=True)
    flags: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timelineStats: Mapped[TimelineStats | None] = relationship(
        "TimelineStats", back_populates="account", lazy="joined"
    )
    about: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    pinnedPosts: Mapped[set[Post]] = relationship(
        "Post",
        secondary="pinned_posts",
        collection_class=set,
        back_populates="pinnedAccounts",
        lazy="joined",
    )
    walls: Mapped[set[Wall]] = relationship(
        "Wall", collection_class=set, back_populates="account", lazy="joined"
    )
    following: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    avatar: Mapped[Media | None] = relationship(
        "Media",
        secondary="account_avatar",
        back_populates="avatarAccounts",
        lazy="joined",
    )
    banner: Mapped[Media | None] = relationship(
        "Media",
        secondary="account_banner",
        back_populates="bannerAccounts",
        lazy="joined",
    )
    profileAccess: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    accountMedia: Mapped[set[AccountMedia]] = relationship(
        "AccountMedia",
        back_populates="account",
        lazy="joined",
        collection_class=set,
    )


class TimelineStats(Base):
    __tablename__ = "timeline_stats"
    accountId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), primary_key=True
    )
    account: Mapped[Account] = relationship("Account", back_populates="timelineStats")
    imageCount: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    videoCount: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    bundleCount: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    bundleImageCount: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=0
    )
    bundleVideoCount: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=0
    )
    fetchedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


account_avatar = Table(
    "account_avatar",
    Base.metadata,
    Column("accountId", Integer, ForeignKey("accounts.id")),
    Column("mediaId", Integer, ForeignKey("media.id")),
    UniqueConstraint("accountId", "mediaId"),
)
account_banner = Table(
    "account_banner",
    Base.metadata,
    Column("accountId", Integer, ForeignKey("accounts.id")),
    Column("mediaId", Integer, ForeignKey("media.id")),
    UniqueConstraint("accountId", "mediaId"),
)


class AccountMedia(Base):
    __tablename__ = "account_media"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), primary_key=True
    )
    account: Mapped[Account] = relationship("Account", back_populates="accountMedia")
    mediaId: Mapped[int] = mapped_column(
        Integer, ForeignKey("media.id"), primary_key=True
    )
    media: Mapped[Media] = relationship("Media", foreign_keys=[mediaId])
    previewId: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("media.id"), nullable=True
    )
    preview: Mapped[Media] = relationship("Media", foreign_keys=[previewId])
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deletedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    access: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AccountMediaBundle(Base):
    __tablename__ = "account_media_bundles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=False
    )
    account: Mapped[Account] = relationship("Account")
    previewId: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("media.id"), nullable=True
    )
    preview: Mapped[Media] = relationship("Media", foreign_keys=[previewId])
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deletedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    accountMediaIds: Mapped[set[int]] = relationship("Media", collection_class=set)
    access: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    purchased: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    whitelisted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    UniqueConstraint("accountId", "mediaId")
