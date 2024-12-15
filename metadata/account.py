from __future__ import annotations

from datetime import datetime

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

from .account_media import AccountMedia
from .base import Base
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
        "TimelineStats", backref="accountId", lazy="joined"
    )
    about: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    pinnedPosts: Mapped[set[Post]] = relationship(
        "Post",
        secondary="pinned_posts",
        collection_class=set,
        backref="accountId",
        lazy="joined",
    )
    walls: Mapped[set[Wall]] = relationship(
        "Wall", collection_class=set, backref="accountId", lazy="joined"
    )
    following: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    avatar: Mapped[Media | None] = relationship(
        "Media", secondary="account_avatar", backref="accountId", lazy="joined"
    )
    banner: Mapped[Media | None] = relationship(
        "Media", secondary="account_banner", backref="accountId", lazy="joined"
    )
    profileAccess: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    accountMedia: Mapped[set[AccountMedia]] = relationship(
        "AccountMedia",
        backref="accountId",
        lazy="joined",
        collection_class=set,
    )


class TimelineStats(Base):
    __tablename__ = "timeline_stats"
    accountId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), primary_key=True
    )
    imageCount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    videoCount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bundleCount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bundleImageCount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bundleVideoCount: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
