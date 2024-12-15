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


class Wall(Base):
    __tablename__ = "walls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId = mapped_column(Integer, ForeignKey("account.id"), nullable=False)
    pos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(String, nullable=True)
    metadata: Mapped[str] = mapped_column(String, nullable=True)


def process_walls_metadata(metadata: dict) -> None:
    pass
