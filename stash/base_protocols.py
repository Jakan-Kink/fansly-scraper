"""Base protocols for Stash types."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from stashapi.stashapp import StashInterface


@runtime_checkable
class StashQLProtocol(Protocol):
    """Protocol defining the base interface for Stash types."""

    id: str
    created_at: datetime
    updated_at: datetime
    urls: list[str]
    tags: list
    relationships: dict[str, list]

    def to_dict(self) -> dict: ...

    @classmethod
    def from_dict(cls, data: dict): ...

    @staticmethod
    def sanitize_datetime(value: str | datetime | None) -> datetime | None: ...

    @classmethod
    def stash_create(cls, interface: StashInterface): ...

    def find(self, interface: StashInterface): ...

    def save(self, interface: StashInterface): ...
