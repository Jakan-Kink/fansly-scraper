from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from stashapi.stashapp import StashInterface

from .types import StashTagProtocol


@dataclass
class Tag(StashTagProtocol):
    id: str
    name: str
    description: str | None = None
    aliases: list[str] = field(default_factory=list)
    ignore_auto_tag: bool = False
    image_path: str | None = None
    favorite: bool = False
    parents: list[Tag] = field(default_factory=list)
    children: list[Tag] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @staticmethod
    def find(id: str, interface: StashInterface) -> Tag | None:
        """Find a tag by ID.

        Args:
            id: The ID of the tag to find
            interface: StashInterface instance to use for querying

        Returns:
            Tag instance if found, None otherwise
        """
        data = interface.find_tag(id)
        return Tag.from_dict(data) if data else None

    @staticmethod
    def find_all(
        interface: StashInterface, filter: dict = {"per_page": -1}, q: str = ""
    ) -> list[Tag]:
        """Find all tags matching the filter/query.

        Args:
            interface: StashInterface instance to use for querying
            filter: Filter parameters for the query
            q: Query string to search for

        Returns:
            List of Tag instances matching the criteria
        """
        data = interface.find_tags(filter=filter, q=q)
        return [Tag.from_dict(t) for t in data]

    def save(self, interface: StashInterface) -> None:
        """Save changes to this tag in stash.

        Args:
            interface: StashInterface instance to use for updating
        """
        interface.update_tag(self.to_dict())

    @staticmethod
    def create_batch(interface: StashInterface, tags: list[Tag]) -> list[dict]:
        """Create multiple tags at once.

        Args:
            interface: StashInterface instance to use for creation
            tags: List of Tag instances to create

        Returns:
            List of created tag data from stash
        """
        inputs = [t.to_create_input_dict() for t in tags]
        return interface.create_tags(inputs)

    @staticmethod
    def update_batch(interface: StashInterface, tags: list[Tag]) -> list[dict]:
        """Update multiple tags at once.

        Args:
            interface: StashInterface instance to use for updating
            tags: List of Tag instances to update

        Returns:
            List of updated tag data from stash
        """
        updates = [t.to_update_input_dict() for t in tags]
        return interface.update_tags(updates)

    def to_dict(self) -> dict:
        """Convert the tag object to a dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "aliases": self.aliases,
            "ignore_auto_tag": self.ignore_auto_tag,
            "image_path": self.image_path,
            "favorite": self.favorite,
            "parents": [p.to_dict() for p in self.parents],
            "children": [c.to_dict() for c in self.children],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_create_input_dict(self) -> dict:
        """Converts the Tag object into a dictionary matching the TagCreateInput GraphQL definition."""
        return {
            "name": self.name,
            "description": self.description,
            "aliases": self.aliases,
            "ignore_auto_tag": self.ignore_auto_tag,
            "image_path": self.image_path,
            "favorite": self.favorite,
            "parent_ids": [p.id for p in self.parents],
            "child_ids": [c.id for c in self.children],
        }

    def to_update_input_dict(self) -> dict:
        """Converts the Tag object into a dictionary matching the TagUpdateInput GraphQL definition."""
        return {"id": self.id, **self.to_create_input_dict()}

    def stash_create(self, interface: StashInterface) -> dict:
        """Creates the tag in stash using the interface.

        Args:
            interface: StashInterface instance to use for creation

        Returns:
            dict: Response from stash containing the created tag data
        """
        return interface.create_tag(self.to_create_input_dict())

    @classmethod
    def from_dict(cls, data: dict) -> Tag:
        """Create a Tag instance from a dictionary.

        Args:
            data: Dictionary containing tag data from GraphQL or other sources.

        Returns:
            A new Tag instance.
        """
        # Handle both GraphQL response format and direct dictionary format
        tag_data = data.get("tag", data)

        from .stash_context import StashQL

        # Convert string dates to datetime objects using StashQL's robust datetime handling
        created_at = StashQL.sanitize_datetime(tag_data.get("created_at"))
        updated_at = StashQL.sanitize_datetime(tag_data.get("updated_at"))

        # Handle parent tags if present
        parents = []
        if tag_data.get("parents"):
            parents = [cls.from_dict(p) for p in tag_data["parents"]]

        # Handle child tags if present
        children = []
        if tag_data.get("children"):
            children = [cls.from_dict(c) for c in tag_data["children"]]

        # Create the tag instance
        tag = cls(
            id=str(tag_data.get("id", "")),
            name=tag_data["name"],
            description=tag_data.get("description"),
            aliases=list(tag_data.get("aliases", [])),
            ignore_auto_tag=bool(tag_data.get("ignore_auto_tag", False)),
            image_path=tag_data.get("image_path"),
            favorite=bool(tag_data.get("favorite", False)),
            parents=parents,
            children=children,
            created_at=created_at or datetime.now(),
            updated_at=updated_at or datetime.now(),
        )

        return tag
