from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .media import Media
    from .post import Post


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    postId: Mapped[int] = mapped_column(Integer, ForeignKey("post.id"), nullable=False)
    contentId: Mapped[int] = mapped_column(
        Integer, ForeignKey("media.id"), nullable=False
    )
    pos: Mapped[int] = mapped_column(Integer, nullable=False)
    contentType: Mapped[int] = mapped_column(Integer, nullable=True)
    post: Mapped[Post] = relationship("Post", back_populates="attachments")
    content: Mapped[Media] = relationship("Media", back_populates="attachments")
