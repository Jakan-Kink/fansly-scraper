from __future__ import annotations

from datetime import datetime
from enum import Enum

# from sqlalchemy import Enum as SQLAEnum
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
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

from .account import Account
from .account_media import Attachment
from .base import Base

# from .wall import Wall


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId = mapped_column(Integer, ForeignKey("account.id"), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=True, default="")
    fypFlag: Mapped[int] = mapped_column(Integer, nullable=True, default=0)
    inReplyTo: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    inReplyToRoot: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    expiresAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attachments: Mapped[set[Attachment | None]] = relationship(
        "Attachment", back_populates="post"
    )
    accountMentions: Mapped[set[Account]] = relationship(
        "Account", secondary="post_mentions"
    )


pinned_posts = Table(
    "pinned_posts",
    Base.metadata,
    Column("postId", Integer, ForeignKey("post.id"), primary_key=True),
    Column("accountId", Integer, ForeignKey("account.id"), primary_key=True),
    Column("pos", Integer, nullable=False),
    Column("createdAt", DateTime(timezone=True), nullable=True),
)

post_mentions = Table(
    "post_mentions",
    Base.metadata,
    Column("postId", Integer, ForeignKey("post.id"), primary_key=True),
    Column("accountId", Integer, ForeignKey("account.id"), primary_key=True),
    Column("handle", String, nullable=True),
    UniqueConstraint("postId", "accountId"),
)


def process_posts_metadata(metadata: dict[str, any]) -> None:
    """Process posts metadata."""
    pass
