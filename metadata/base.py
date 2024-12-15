from __future__ import annotations

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    postId: Mapped[int] = mapped_column(Integer, ForeignKey("post.id"), nullable=False)
    contentId: Mapped[int] = mapped_column(
        Integer, ForeignKey("media.id"), nullable=False
    )
    pos: Mapped[int] = mapped_column(Integer, nullable=False)
    contentType: Mapped[int] = mapped_column(Integer, nullable=True)
