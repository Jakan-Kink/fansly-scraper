from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .account import Account


class Wall(Base):
    __tablename__ = "walls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accountId = mapped_column(Integer, ForeignKey("accounts.id"), nullable=False)
    account: Mapped[Account] = relationship(
        "Account", foreign_keys=[accountId], back_populates="walls", lazy="joined"
    )
    pos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(String, nullable=True)
    # metadata: Mapped[str] = mapped_column(String, nullable=True)


def process_walls_metadata(metadata: dict) -> None:
    pass
