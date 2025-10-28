"""Story model for SQLAlchemy."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


if TYPE_CHECKING:
    from .account import Account


class Story(Base):
    """Represents a story post.

    This class represents a story post that can be attached to posts or messages.
    Stories contain text content with a title and description, and are authored
    by an account.

    Attributes:
        id: Unique identifier for the story
        authorId: ID of the account that authored this story
        author: Relationship to the author Account
        title: Story title
        description: Story description
        content: Main story content
        createdAt: When this story was created
        updatedAt: When this story was last updated
    """

    __tablename__ = "stories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    authorId: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=False
    )
    author: Mapped[Account] = relationship(
        "Account",
        back_populates="stories",
        lazy="noload",  # Don't auto-load author to reduce SQL queries
        cascade="all, delete, save-update",
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    content: Mapped[str] = mapped_column(String, nullable=False)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updatedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __init__(self, **kwargs: Any) -> None:
        """Initialize a Story instance with timestamp conversion."""
        self.convert_timestamps(kwargs, ("createdAt", "updatedAt"))
        super().__init__(**kwargs)
