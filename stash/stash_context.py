"""Core functionality for interacting with Stash."""

from datetime import date, datetime, timezone
from typing import Any, ClassVar, Optional, Type, TypeVar

from stashapi.stashapp import StashInterface

from .base_protocols import StashQLProtocol

T = TypeVar("T", bound="StashQL")


class StashQL(StashQLProtocol):
    """Base class for Stash GraphQL objects."""

    id: str
    created_at: datetime
    updated_at: datetime
    urls: list[str]
    tags: list
    relationships: dict[str, list]

    @staticmethod
    def sanitize_datetime(value: str | date | datetime | None) -> datetime | None:
        """Convert various datetime formats to UTC datetime object.

        Args:
            value: The value to convert to datetime. Can be:
                - None: Returns None
                - str: ISO format string
                - date: Converted to datetime at midnight UTC
                - datetime: Converted to UTC if needed

        Returns:
            datetime object in UTC timezone if conversion successful

        Raises:
            ValueError: If the string format is invalid
            TypeError: If the value type is not supported
        """
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

    def __init__(
        self,
        id: str,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        urls: list[str] | None = None,
        tags: list | None = None,
        relationships: dict[str, list] | None = None,
    ) -> None:
        """Initialize StashQL object.

        Args:
            id: The ID of the object
            created_at: When the object was created
            updated_at: When the object was last updated
            urls: List of URLs associated with the object
            tags: List of tags associated with the object
            relationships: Dictionary of relationships to other objects
        """
        self.id = id
        self.created_at = self.sanitize_datetime(created_at)
        self.updated_at = self.sanitize_datetime(updated_at)
        self.urls = urls or []
        self.tags = tags or []
        self.relationships = relationships or {}

    def to_dict(self) -> dict:
        """Convert the object to a dictionary.

        Returns:
            Dictionary representation of the object
        """
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "urls": self.urls,
            "tags": self.tags,
            "relationships": self.relationships,
        }

    @classmethod
    def from_dict(cls: type[T], data: dict) -> T:
        """Create an instance from a dictionary.

        Args:
            data: Dictionary containing object data

        Returns:
            New instance of the class
        """
        return cls(
            id=str(data.get("id", "")),
            created_at=data.get("created_at", None),
            updated_at=data.get("updated_at", None),
            urls=list(data.get("urls", [])),
            tags=list(data.get("tags", [])),
            relationships=dict(data.get("relationships", {})),
        )

    @classmethod
    def stash_create(cls: type[T], interface: StashInterface) -> T:
        """Create a new instance in Stash.

        Args:
            interface: StashInterface instance to use for creation

        Returns:
            New instance of the class
        """
        raise NotImplementedError("Subclasses must implement stash_create")

    def find(self, interface: StashInterface) -> None:
        """Find this object in Stash.

        Args:
            interface: StashInterface instance to use for querying
        """
        raise NotImplementedError("Subclasses must implement find")

    def save(self, interface: StashInterface) -> None:
        """Save changes to this object in Stash.

        Args:
            interface: StashInterface instance to use for updating
        """
        raise NotImplementedError("Subclasses must implement save")


class StashContext:
    """Context for interacting with Stash."""

    def __init__(self, conn: dict) -> None:
        """Initialize StashContext.

        Args:
            conn: Dictionary containing connection details for StashInterface
        """
        from requests.structures import CaseInsensitiveDict

        self.conn = CaseInsensitiveDict(conn)
        self.interface = StashInterface(conn=self.conn)

    def get_interface(self) -> StashInterface:
        """Get the StashInterface instance.

        Returns:
            StashInterface instance
        """
        return self.interface
