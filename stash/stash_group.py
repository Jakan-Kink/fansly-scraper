from datetime import datetime

from stashapi.stashapp import StashInterface

from .types import StashGroupDescriptionProtocol, StashGroupProtocol


class StashGroup(StashGroupProtocol):
    @staticmethod
    def find(id: str, interface: StashInterface) -> "StashGroup":
        data = interface.find_group(id)
        return StashGroup.from_dict(data) if data else None

    def save(self, interface: StashInterface) -> None:
        interface.update_group(self.to_dict())

    def to_dict(self) -> dict:
        group_dict = {
            "id": self.id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "name": self.name,
            "aliases": self.aliases,
            "duration": self.duration,
            "date": self.date,
            "rating100": self.rating100,
            "director": self.director,
            "synopsis": self.synopsis,
            "urls": self.urls,
            "front_image_path": self.front_image_path,
            "back_image_path": self.back_image_path,
            "studio": self.studio.id if self.studio else None,
            "tags": [tag.id for tag in self.tags],
            "containing_groups": [group.id for group in self.containing_groups],
            "sub_groups": [group.id for group in self.sub_groups],
            "scenes": [scene.id for scene in self.scenes],
        }
        return group_dict

    def __init__(
        self,
        id: str,
        name: str,
        aliases: str | None = None,
        duration: int | None = None,
        date: str | None = None,
        rating100: int | None = None,
        director: str | None = None,
        synopsis: str | None = None,
        urls: list[str] = [],
        front_image_path: str | None = None,
        back_image_path: str | None = None,
        created_at: datetime | str | None = None,
        updated_at: datetime | str | None = None,
    ):
        StashGroupProtocol.__init__(
            self=self, id=id, urls=urls, created_at=created_at, updated_at=updated_at
        )
        self.name = name
        self.aliases = aliases
        self.duration = duration
        self.date = date
        self.rating100 = rating100
        self.director = director
        self.synopsis = synopsis
        self.front_image_path = front_image_path
        self.back_image_path = back_image_path
        self.studio = None
        self.tags = []
        self.containing_groups: list[StashGroupDescriptionProtocol] = []
        self.sub_groups: list[StashGroupDescriptionProtocol] = []
        self.scenes = []

    def scene_count(self, depth: int | None = None) -> int:
        # Implement logic to count scenes
        return 0

    def sub_group_count(self, depth: int | None = None) -> int:
        # Implement logic to count sub-groups
        return 0

    @classmethod
    def from_dict(cls, data: dict) -> "StashGroup":
        """Create a StashGroup instance from a dictionary.

        Args:
            data: Dictionary containing group data from GraphQL or other sources.

        Returns:
            A new StashGroup instance.
        """
        # Handle both GraphQL response format and direct dictionary format
        group_data = data.get("group", data)

        # Create the base group object
        group = cls(
            id=str(group_data.get("id", "")),
            name=str(group_data.get("name", "")),
            aliases=group_data.get("aliases"),
            duration=group_data.get("duration"),
            date=group_data.get("date"),
            rating100=group_data.get("rating100"),
            director=group_data.get("director"),
            synopsis=group_data.get("synopsis"),
            urls=list(group_data.get("urls", [])),
            front_image_path=group_data.get("front_image_path"),
            back_image_path=group_data.get("back_image_path"),
            created_at=group_data.get("created_at"),
            updated_at=group_data.get("updated_at"),
        )

        # Handle studio if present
        if "studio" in group_data and group_data["studio"]:
            from .stash_studio import StashStudio

            group.studio = StashStudio.from_dict(group_data["studio"])

        # Handle tags if present
        if "tags" in group_data:
            from .stash_tag import StashTag

            group.tags = [StashTag.from_dict(t) for t in group_data["tags"]]

        # Handle scenes if present
        if "scenes" in group_data:
            from .stash_scene import StashScene

            group.scenes = [StashScene.from_dict(s) for s in group_data["scenes"]]

        # Handle containing_groups if present
        if "containing_groups" in group_data:
            from .group_description import StashGroupDescription

            group.containing_groups = [
                StashGroupDescription.from_dict(g)
                for g in group_data["containing_groups"]
            ]

        # Handle sub_groups if present
        if "sub_groups" in group_data:
            from .group_description import StashGroupDescription

            group.sub_groups = [
                StashGroupDescription.from_dict(g) for g in group_data["sub_groups"]
            ]

        return group
