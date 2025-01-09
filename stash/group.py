from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from stashapi.stashapp import StashInterface

from .stash_context import StashQL
from .types import (
    StashGalleryProtocol,
    StashGroupDescriptionProtocol,
    StashGroupProtocol,
    StashImageProtocol,
    StashPerformerProtocol,
    StashSceneProtocol,
    StashStudioProtocol,
    StashTagProtocol,
)


@dataclass
class GroupDescription(StashGroupDescriptionProtocol):
    containing_group: StashGroupProtocol
    sub_group: StashGroupProtocol
    description: str = ""

    def to_dict(self) -> dict:
        """Convert the group description to a dictionary."""
        return {
            "containing_group": self.containing_group.to_dict(),
            "sub_group": self.sub_group.to_dict(),
            "description": self.description,
        }


@dataclass
class Group(StashGroupProtocol):
    id: str
    name: str
    aliases: str | None = None
    duration: int | None = None
    date: datetime | None = None
    rating100: int | None = None
    director: str | None = None
    synopsis: str | None = None
    urls: list[str] = field(default_factory=list)
    front_image_path: str | None = None
    back_image_path: str | None = None
    studio: StashStudioProtocol | None = None
    tags: list[StashTagProtocol] = field(default_factory=list)
    containing_groups: list[GroupDescription] = field(default_factory=list)
    sub_groups: list[GroupDescription] = field(default_factory=list)
    scenes: list[StashSceneProtocol] = field(default_factory=list)
    performers: list[StashPerformerProtocol] = field(default_factory=list)
    galleries: list[StashGalleryProtocol] = field(default_factory=list)
    images: list[StashImageProtocol] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @staticmethod
    def find(id: str, interface: StashInterface) -> Group | None:
        """Find a group by ID.

        Args:
            id: The ID of the group to find
            interface: StashInterface instance to use for querying

        Returns:
            Group instance if found, None otherwise
        """
        data = interface.find_group(id)
        return Group.from_dict(data) if data else None

    @staticmethod
    def find_all(
        interface: StashInterface, filter: dict = {"per_page": -1}, q: str = ""
    ) -> list[Group]:
        """Find all groups matching the filter/query.

        Args:
            interface: StashInterface instance to use for querying
            filter: Filter parameters for the query
            q: Query string to search for

        Returns:
            List of Group instances matching the criteria
        """
        data = interface.find_groups(filter=filter, q=q)
        return [Group.from_dict(g) for g in data]

    def save(self, interface: StashInterface) -> None:
        """Save changes to this group in stash.

        Args:
            interface: StashInterface instance to use for updating
        """
        interface.update_group(self.to_dict())

    @staticmethod
    def create_batch(interface: StashInterface, groups: list[Group]) -> list[dict]:
        """Create multiple groups at once.

        Args:
            interface: StashInterface instance to use for creation
            groups: List of Group instances to create

        Returns:
            List of created group data from stash
        """
        inputs = [g.to_create_input_dict() for g in groups]
        return interface.create_groups(inputs)

    @staticmethod
    def update_batch(interface: StashInterface, groups: list[Group]) -> list[dict]:
        """Update multiple groups at once.

        Args:
            interface: StashInterface instance to use for updating
            groups: List of Group instances to update

        Returns:
            List of updated group data from stash
        """
        updates = [g.to_update_input_dict() for g in groups]
        return interface.update_groups(updates)

    def to_dict(self) -> dict:
        """Convert the group object to a dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "aliases": self.aliases,
            "duration": self.duration,
            "date": self.date.isoformat() if self.date else None,
            "rating100": self.rating100,
            "director": self.director,
            "synopsis": self.synopsis,
            "urls": self.urls,
            "front_image_path": self.front_image_path,
            "back_image_path": self.back_image_path,
            "studio": self.studio.to_dict() if self.studio else None,
            "tags": [t.to_dict() for t in self.tags],
            "containing_groups": [g.to_dict() for g in self.containing_groups],
            "sub_groups": [g.to_dict() for g in self.sub_groups],
            "scenes": [s.to_dict() for s in self.scenes],
            "performers": [p.to_dict() for p in self.performers],
            "galleries": [g.to_dict() for g in self.galleries],
            "images": [i.to_dict() for i in self.images],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_create_input_dict(self) -> dict:
        """Converts the Group object into a dictionary matching the GroupCreateInput GraphQL definition."""
        return {
            "name": self.name,
            "aliases": self.aliases,
            "duration": self.duration,
            "date": self.date.isoformat() if self.date else None,
            "rating100": self.rating100,
            "director": self.director,
            "synopsis": self.synopsis,
            "urls": self.urls,
            "front_image_path": self.front_image_path,
            "back_image_path": self.back_image_path,
            "studio_id": self.studio.id if self.studio else None,
            "tag_ids": [t.id for t in self.tags],
            "scene_ids": [s.id for s in self.scenes],
            "performer_ids": [p.id for p in self.performers],
            "gallery_ids": [g.id for g in self.galleries],
            "image_ids": [i.id for i in self.images],
        }

    def to_update_input_dict(self) -> dict:
        """Converts the Group object into a dictionary matching the GroupUpdateInput GraphQL definition."""
        return {"id": self.id, **self.to_create_input_dict()}

    def stash_create(self, interface: StashInterface) -> dict:
        """Creates the group in stash using the interface.

        Args:
            interface: StashInterface instance to use for creation

        Returns:
            dict: Response from stash containing the created group data
        """
        return interface.create_group(self.to_create_input_dict())

    @classmethod
    def from_dict(cls, data: dict) -> Group:
        """Create a Group instance from a dictionary.

        Args:
            data: Dictionary containing group data from GraphQL or other sources.

        Returns:
            A new Group instance.
        """
        # Handle both GraphQL response format and direct dictionary format
        group_data = data.get("group", data)

        # Convert string dates to datetime objects using StashQL's robust datetime handling
        created_at = StashQL.sanitize_datetime(group_data.get("created_at"))
        updated_at = StashQL.sanitize_datetime(group_data.get("updated_at"))
        date = StashQL.sanitize_datetime(group_data.get("date"))

        # Handle relationships
        studio = None
        if group_data.get("studio"):
            from .studio import Studio

            studio = Studio.from_dict(group_data["studio"])

        tags = []
        if "tags" in group_data:
            from .tag import Tag

            tags = [Tag.from_dict(t) for t in group_data["tags"]]

        scenes = []
        if "scenes" in group_data:
            from .scene import Scene

            scenes = [Scene.from_dict(s) for s in group_data["scenes"]]

        performers = []
        if "performers" in group_data:
            from .performer import Performer

            performers = [Performer.from_dict(p) for p in group_data["performers"]]

        galleries = []
        if "galleries" in group_data:
            from .gallery import Gallery

            galleries = [Gallery.from_dict(g) for g in group_data["galleries"]]

        images = []
        if "images" in group_data:
            from .image import Image

            images = [Image.from_dict(i) for i in group_data["images"]]

        # Handle group relationships
        containing_groups = []
        if group_data.get("containing_groups"):
            containing_groups = [
                GroupDescription(
                    containing_group=cls.from_dict(g["containing_group"]),
                    sub_group=cls.from_dict(g["sub_group"]),
                    description=g.get("description", ""),
                )
                for g in group_data["containing_groups"]
            ]

        sub_groups = []
        if group_data.get("sub_groups"):
            sub_groups = [
                GroupDescription(
                    containing_group=cls.from_dict(g["containing_group"]),
                    sub_group=cls.from_dict(g["sub_group"]),
                    description=g.get("description", ""),
                )
                for g in group_data["sub_groups"]
            ]

        # Create the group instance
        group = cls(
            id=str(group_data.get("id", "")),
            name=group_data["name"],
            aliases=group_data.get("aliases"),
            duration=group_data.get("duration"),
            date=date,
            rating100=group_data.get("rating100"),
            director=group_data.get("director"),
            synopsis=group_data.get("synopsis"),
            urls=list(group_data.get("urls", [])),
            front_image_path=group_data.get("front_image_path"),
            back_image_path=group_data.get("back_image_path"),
            studio=studio,
            tags=tags,
            containing_groups=containing_groups,
            sub_groups=sub_groups,
            scenes=scenes,
            performers=performers,
            galleries=galleries,
            images=images,
            created_at=created_at or datetime.now(),
            updated_at=updated_at or datetime.now(),
        )

        return group
