"""Base class for content that can be attached to posts or messages."""

from __future__ import annotations

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from .base import Base


class Attachable(Base):
    """Base class for content that can be attached to posts or messages.

    This is an abstract base class that provides the common interface and
    functionality for all types of content that can be attached to posts
    or messages.

    Attributes:
        id: Unique identifier for the attachable content
        type: String identifier for the type of content (used for polymorphism)
    """

    __abstract__ = True

    @declared_attr
    def __tablename__(self) -> str:
        return self.__name__.lower()

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(String(50))

    __mapper_args__ = {
        "polymorphic_identity": "attachable",
        "polymorphic_on": type,
    }
