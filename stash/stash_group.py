from datetime import datetime

from stashapi.stashapp import StashInterface

from .group_description import StashGroupDescription as GroupDescription
from .stash_context import StashQL
from .stash_scene import StashScene
from .stash_tag import StashTag


class StashGroup(StashQL):
    @staticmethod
    def find(id: str, interface: StashInterface) -> "StashGroup":
        data = interface.find_group(id)
        return StashGroup.from_dict(data) if data else None

    def save(self, interface: StashInterface) -> None:
        interface.update_group(self.to_dict())

    name: str
    aliases: str | None
    duration: int | None
    date: str | None
    rating100: int | None
    director: str | None
    synopsis: str | None
    urls: list[str]
    front_image_path: str | None
    back_image_path: str | None
    studio: None
    tags: list[StashTag]
    containing_groups: list[GroupDescription]
    sub_groups: list[GroupDescription]
    scenes: list[StashScene]

    def to_dict(self) -> dict:
        base_dict = super().to_dict()
        group_dict = {
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
        return {**base_dict, **group_dict}

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
        super().__init__(id=id, urls=urls, created_at=created_at, updated_at=updated_at)
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
        self.containing_groups: list[GroupDescription] = []
        self.sub_groups: list[GroupDescription] = []
        self.scenes = []

    def scene_count(self, depth: int | None = None) -> int:
        # Implement logic to count scenes
        return 0

    def sub_group_count(self, depth: int | None = None) -> int:
        # Implement logic to count sub-groups
        return 0
