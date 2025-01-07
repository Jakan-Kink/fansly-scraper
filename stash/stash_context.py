from datetime import date, datetime, timezone

from requests.structures import CaseInsensitiveDict
from stashapi.stashapp import StashInterface

from .base_protocols import StashQLProtocol


class StashContext:
    def __init__(self, conn: dict):
        self.conn = CaseInsensitiveDict(conn)
        self.interface = StashInterface(conn=self.conn)

    def get_interface(self) -> StashInterface:
        return self.interface


class StashQL(StashQLProtocol):
    """Base class implementing StashQLProtocol."""

    id: str
    created_at: datetime
    updated_at: datetime
    urls: list[str]
    tags: list
    relationships: dict[str, list]

    def __init__(
        self,
        id: str,
        urls: list[str] = [],
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        self.id = id
        self.urls = urls
        self.created_at = self.sanitize_datetime(created_at)
        self.updated_at = self.sanitize_datetime(updated_at)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "urls": self.urls or [],
            "tags": self.tags or [],
            "relationships": self.relationships or {},
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            id=data.get("id", ""),
            urls=data.get("urls", []),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    @staticmethod
    def sanitize_datetime(value: str | date | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value).astimezone(timezone.utc)
            except ValueError:
                raise ValueError(f"Invalid date string: {value}")
        if isinstance(value, date) and not isinstance(value, datetime):
            value = datetime.combine(value, datetime.min.time())
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        raise TypeError(f"Unsupported type: {type(value)}")

    @classmethod
    def stash_create(self, interface: StashInterface):
        raise NotImplementedError("stash_create must be implemented in subclasses.")

    def find(self, interface: StashInterface):
        raise NotImplementedError("find must be implemented in subclasses.")

    def save(self, interface: StashInterface):
        raise NotImplementedError("save must be implemented in subclasses.")
