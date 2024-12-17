from __future__ import annotations

from datetime import datetime, timezone
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
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Mapped, mapped_column, relationship

from textio import json_output

from .base import Base

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from config import FanslyConfig
    from download.core import DownloadState

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
        lazy="joined",
    )
    walls: Mapped[set[Wall]] = relationship(
        "Wall", collection_class=set, back_populates="account", lazy="joined"
    )
    following: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    avatar: Mapped[Media | None] = relationship(
        "Media",
        secondary="account_avatar",
        lazy="joined",
    )
    banner: Mapped[Media | None] = relationship(
        "Media",
        secondary="account_banner",
        lazy="joined",
    )
    profileAccess: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    accountMedia: Mapped[set[AccountMedia]] = relationship(
        "AccountMedia",
        back_populates="account",
        lazy="joined",
        collection_class=set,
    )
    accountMediaBundles: Mapped[set[AccountMediaBundle]] = relationship(
        "AccountMediaBundle",
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
account_media_bundle_media = Table(
    "account_media_bundle_media",
    Base.metadata,
    Column(
        "bundle_id",
        Integer,
        ForeignKey("account_media_bundles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "media_id",
        Integer,
        ForeignKey("account_media.id", ondelete="CASCADE"),
        primary_key=True,
    ),
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
    media: Mapped[Media] = relationship(
        "Media",
        foreign_keys=[mediaId],
        cascade="all, delete-orphan",  # Ensures orphan AccountMedia are deleted
        passive_deletes=True,  # Ensures database cascade is respected
        single_parent=True,
    )
    previewId: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("media.id"), nullable=True
    )
    preview: Mapped[Media] = relationship(
        "Media",
        foreign_keys=[previewId],
        cascade="all, delete-orphan",  # Ensures orphan AccountMedia are deleted
        passive_deletes=True,  # Ensures database cascade is respected
        single_parent=True,
    )
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
    # Unidirectional relationship to AccountMedia
    accountMediaIds: Mapped[set[int]] = relationship(
        "AccountMedia",
        secondary=account_media_bundle_media,
        primaryjoin="AccountMediaBundle.id == account_media_bundle_media.c.bundle_id",
        secondaryjoin="AccountMedia.id == account_media_bundle_media.c.media_id",
        collection_class=set,
        lazy="joined",
        cascade="all, delete-orphan",  # Ensures orphan AccountMedia are deleted
        passive_deletes=True,  # Ensures database cascade is respected
        single_parent=True,
    )
    access: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    purchased: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    whitelisted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    UniqueConstraint("accountId", "mediaId")


def process_account_data(
    config: FanslyConfig, state: DownloadState = None, data: dict = None
) -> None:
    from .post import process_pinned_posts

    account_columns = {column.name for column in inspect(Account).columns}
    filtered_account = {key: data[key] for key in data if key in account_columns}
    json_output(1, "meta/account - p_a_data - filtered_account", filtered_account)
    with config._database.sync_session() as session:
        existing_account = session.query(Account).get(filtered_account["id"])
        if existing_account:
            for key, value in filtered_account.items():
                setattr(existing_account, key, value)
        else:
            session.add(Account(**filtered_account))
        session.commit()
        modified_account = session.query(Account).get(filtered_account["id"])
        if "timelineStats" in data:
            procress_timeline_stats(session, data)
        if "pinnedPosts" in data:
            process_pinned_posts(config, modified_account, data["pinnedPosts"])


def process_creator_data(config: FanslyConfig, state, data: dict) -> None:
    from .post import process_pinned_posts

    account_columns = {column.name for column in inspect(Account).columns}
    filtered_account = {key: data[key] for key in data if key in account_columns}
    json_output(1, "meta/account - p_c_data - filtered_account", filtered_account)
    with config._database.sync_session() as session:
        existing_account = session.query(Account).get(filtered_account["id"])
        if existing_account:
            for key, value in filtered_account.items():
                setattr(existing_account, key, value)
        else:
            session.add(Account(**filtered_account))
        session.commit()
        modified_account = session.query(Account).get(filtered_account["id"])
        if "timelineStats" in data:
            procress_timeline_stats(session, data)
        if "pinnedPosts" in data:
            process_pinned_posts(config, modified_account, data["pinnedPosts"])


def procress_timeline_stats(session: Session, data: dict) -> None:
    account_id = data["id"]
    timeline_stats = data["timelineStats"]
    timeline_stats["fetchedAt"] = (
        datetime.fromtimestamp(timeline_stats["fetchedAt"] / 1000, tz=timezone.utc)
        if timeline_stats["fetchedAt"]
        else None
    )
    existing_timeline_stats = session.query(TimelineStats).get(account_id)
    if existing_timeline_stats:
        for key, value in timeline_stats.items():
            setattr(existing_timeline_stats, key, value)
    else:
        session.add(TimelineStats(**timeline_stats))
    session.commit()
