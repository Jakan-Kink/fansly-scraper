from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, Integer, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .account import AccountMedia, AccountMediaBundle  # noqa: F401
    from .messages import Message
    from .post import Post


class ContentType(Enum):
    """Content types for attachments.

    Defines the possible types of content that can be attached to posts or messages.
    Each type corresponds to a specific kind of content in the system.

    Attributes:
        ACCOUNT_MEDIA: Individual media item
        ACCOUNT_MEDIA_BUNDLE: Collection of media items
        AGGREGATED_POSTS: Array of aggregated post dictionaries
        TIP_GOALS: Tip goal content
        STORY: Story content
        POLL: Poll content
    """

    ACCOUNT_MEDIA = 1
    ACCOUNT_MEDIA_BUNDLE = 2
    AGGREGATED_POSTS = 8
    TIP_GOALS = 7100
    STORY = 32001
    POLL = 42001


class Attachment(Base):
    """Represents an attachment to a post or message.

    This class handles attachments to posts and messages, maintaining their order
    and content type. Each attachment can reference different types of content
    (media, bundles, etc.) and is ordered within its parent through the pos field.

    Attributes:
        id: Unique identifier for the attachment
        postId: ID of the post this attachment belongs to (if any)
        messageId: ID of the message this attachment belongs to (if any)
        contentId: ID of the referenced content
        pos: Position/order of this attachment within its parent
        contentType: Type of the referenced content (from ContentType enum)
        post: Relationship to the parent Post (if any)
        message: Relationship to the parent Message (if any)

    Note:
        An attachment can only belong to either a post or a message, not both.
        This is enforced by the check_post_or_message_exclusivity constraint.
    """

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
            return session.execute(
                select("AccountMedia").where(text("id = :id")).params(id=self.contentId)
            ).scalar_one_or_none()
        elif self.contentType == ContentType.ACCOUNT_MEDIA_BUNDLE:
            return session.execute(
                select("AccountMediaBundle")
                .where(text("id = :id"))
                .params(id=self.contentId)
            ).scalar_one_or_none()
        return None

    @property
    def is_account_media(self) -> bool:
        """Return True if the contentType indicates AccountMedia."""
        return self.contentType == ContentType.ACCOUNT_MEDIA

    @property
    def is_account_media_bundle(self) -> bool:
        """Return True if the contentType indicates AccountMediaBundle."""
        return self.contentType == ContentType.ACCOUNT_MEDIA_BUNDLE
