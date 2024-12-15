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


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deletedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    groupId: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("groups.id"), nullable=True
    )
    senderId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=False
    )
    recipientId: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id"), nullable=False
    )
    text: Mapped[str] = mapped_column(String, nullable=False)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deletedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sender: Mapped[Account] = relationship("Account", foreign_keys=[senderId])
    recipient: Mapped[Account] = relationship("Account", foreign_keys=[recipientId])
    UniqueConstraint("senderId", "recipientId", "createdAt")


def process_messages_metadata(config, state, messages):
    pass


def process_groups_aggregation_data(config, state, aggregation_data: dict):
    groups: set = aggregation_data.get("groups", {})
    accounts: set = aggregation_data.get("accounts", {})
    for group in groups:
        pass
    for account in accounts:
        pass
    pass
