from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .account import AccountMedia, AccountMediaBundle  # noqa: F401
    from .messages import Message
    from .post import Post


class ContentType(Enum):
    ACCOUNT_MEDIA = 1
    ACCOUNT_MEDIA_BUNDLE = 2


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    postId: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("posts.id"), nullable=True
    )
    messageId: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("messages.id"), nullable=True
    )
    contentId: Mapped[int] = mapped_column(Integer, nullable=False)
    pos: Mapped[int] = mapped_column(Integer, nullable=False)
    contentType: Mapped[ContentType | None] = mapped_column(
        SQLEnum(ContentType), nullable=True
    )
    post: Mapped[Post | None] = relationship("Post", back_populates="attachments")
    message: Mapped[Message | None] = relationship(
        "Message", back_populates="attachments"
    )
    __table_args__ = (
        CheckConstraint(
            "(postId IS NULL OR messageId IS NULL)",  # Either postId or messageId must be NULL
            name="check_post_or_message_exclusivity",
        ),
    )

    def resolve_content(self, session: Session):
        """
        Resolves the content based on contentType and contentId.

        :param session: SQLAlchemy session for querying the database
        :return: The related AccountMedia or AccountMediaBundle object, or None
        """
        if self.contentType == ContentType.ACCOUNT_MEDIA:
            return (
                session.query("AccountMedia").filter_by(id=self.contentId).one_or_none()
            )
        elif self.contentType == ContentType.ACCOUNT_MEDIA_BUNDLE:
            return (
                session.query("AccountMediaBundle")
                .filter_by(id=self.contentId)
                .one_or_none()
            )
        return None

    @property
    def is_account_media(self) -> bool:
        """Return True if the contentType indicates AccountMedia."""
        return self.contentType == ContentType.ACCOUNT_MEDIA

    @property
    def is_account_media_bundle(self) -> bool:
        """Return True if the contentType indicates AccountMediaBundle."""
        return self.contentType == ContentType.ACCOUNT_MEDIA_BUNDLE
