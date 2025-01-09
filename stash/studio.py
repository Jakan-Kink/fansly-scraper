from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from stashapi.stashapp import StashInterface

from .types import StashGroupProtocol, StashStudioProtocol, StashTagProtocol


@dataclass
class Studio(StashStudioProtocol):
    id: str
    name: str
    url: str | None = None
    parent_studio: Studio | None = None
    child_studios: list[Studio] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    tags: list[StashTagProtocol] = field(default_factory=list)
    ignore_auto_tag: bool = False
    image_path: str | None = None
    rating100: int | None = None
    favorite: bool = False
    details: str | None = None
    groups: list[StashGroupProtocol] = field(default_factory=list)
    stash_ids: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @staticmethod
    def find(id: str, interface: StashInterface) -> Studio | None:
        """Find a studio by ID.

        Args:
            id: The ID of the studio to find
            interface: StashInterface instance to use for querying

        Returns:
            Studio instance if found, None otherwise
        """
        data = interface.find_studio(id)
        return Studio.from_dict(data) if data else None

    @staticmethod
    def find_all(
        interface: StashInterface, filter: dict = {"per_page": -1}, q: str = ""
    ) -> list[Studio]:
        """Find all studios matching the filter/query.

        Args:
            interface: StashInterface instance to use for querying
            filter: Filter parameters for the query
            q: Query string to search for

        Returns:
            List of Studio instances matching the criteria
        """
        data = interface.find_studios(filter=filter, q=q)
        return [Studio.from_dict(s) for s in data]

    def save(self, interface: StashInterface) -> None:
        """Save changes to this studio in stash.

        Args:
            interface: StashInterface instance to use for updating
        """
        interface.update_studio(self.to_dict())

    @staticmethod
    def create_batch(interface: StashInterface, studios: list[Studio]) -> list[dict]:
        """Create multiple studios at once.

        Args:
            interface: StashInterface instance to use for creation
            studios: List of Studio instances to create

        Returns:
            List of created studio data from stash
        """
        inputs = [s.to_create_input_dict() for s in studios]
        return interface.create_studios(inputs)

    @staticmethod
    def update_batch(interface: StashInterface, studios: list[Studio]) -> list[dict]:
        """Update multiple studios at once.

        Args:
            interface: StashInterface instance to use for updating
            studios: List of Studio instances to update

        Returns:
            List of updated studio data from stash
        """
        updates = [s.to_update_input_dict() for s in studios]
        return interface.update_studios(updates)

    def to_dict(self) -> dict:
        """Convert the studio object to a dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "parent_studio": (
                self.parent_studio.to_dict() if self.parent_studio else None
            ),
            "child_studios": [s.to_dict() for s in self.child_studios],
            "aliases": self.aliases,
            "tags": [t.to_dict() for t in self.tags],
            "ignore_auto_tag": self.ignore_auto_tag,
            "image_path": self.image_path,
            "rating100": self.rating100,
            "favorite": self.favorite,
            "details": self.details,
            "groups": [g.to_dict() for g in self.groups],
            "stash_ids": self.stash_ids,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_create_input_dict(self) -> dict:
        """Converts the Studio object into a dictionary matching the StudioCreateInput GraphQL definition."""
        return {
            "name": self.name,
            "url": self.url,
            "parent_id": self.parent_studio.id if self.parent_studio else None,
            "aliases": self.aliases,
            "tag_ids": [t.id for t in self.tags],
            "ignore_auto_tag": self.ignore_auto_tag,
            "image_path": self.image_path,
            "rating100": self.rating100,
            "favorite": self.favorite,
            "details": self.details,
            "stash_ids": self.stash_ids,
        }

    def to_update_input_dict(self) -> dict:
        """Converts the Studio object into a dictionary matching the StudioUpdateInput GraphQL definition."""
        return {"id": self.id, **self.to_create_input_dict()}

    def stash_create(self, interface: StashInterface) -> dict:
        """Creates the studio in stash using the interface.

        Args:
            interface: StashInterface instance to use for creation

        Returns:
            dict: Response from stash containing the created studio data
        """
        return interface.create_studio(self.to_create_input_dict())

    @classmethod
    def from_dict(cls, data: dict) -> Studio:
        """Create a Studio instance from a dictionary.

        Args:
            data: Dictionary containing studio data from GraphQL or other sources.

        Returns:
            A new Studio instance.
        """
        # Handle both GraphQL response format and direct dictionary format
        studio_data = data.get("studio", data)

        from .stash_context import StashQL

        # Convert string dates to datetime objects using StashQL's robust datetime handling
        created_at = StashQL.sanitize_datetime(studio_data.get("created_at"))
        updated_at = StashQL.sanitize_datetime(studio_data.get("updated_at"))

        # Handle relationships
        tags = []
        if "tags" in studio_data:
            from .tag import Tag

            tags = [Tag.from_dict(t) for t in studio_data["tags"]]

        groups = []
        if "groups" in studio_data:
            from .group import Group

            groups = [Group.from_dict(g) for g in studio_data["groups"]]

        # Handle parent studio if present
        parent_studio = None
        if studio_data.get("parent_studio"):
            parent_studio = cls.from_dict(studio_data["parent_studio"])

        # Handle child studios if present
        child_studios = []
        if studio_data.get("child_studios"):
            child_studios = [cls.from_dict(s) for s in studio_data["child_studios"]]

        # Create the studio instance
        studio = cls(
            id=str(studio_data.get("id", "")),
            name=studio_data["name"],
            url=studio_data.get("url"),
            parent_studio=parent_studio,
            child_studios=child_studios,
            aliases=list(studio_data.get("aliases", [])),
            tags=tags,
            ignore_auto_tag=bool(studio_data.get("ignore_auto_tag", False)),
            image_path=studio_data.get("image_path"),
            rating100=studio_data.get("rating100"),
            favorite=bool(studio_data.get("favorite", False)),
            details=studio_data.get("details"),
            groups=groups,
            stash_ids=list(studio_data.get("stash_ids", [])),
            created_at=created_at or datetime.now(),
            updated_at=updated_at or datetime.now(),
        )

        return studio
