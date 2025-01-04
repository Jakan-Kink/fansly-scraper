from datetime import datetime

from .group_description import StashGroupDescription as GroupDescription


class StashGroup:
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
        created_at: datetime = datetime.now(),
        updated_at: datetime = datetime.now(),
    ):
        self.id = id
        self.name = name
        self.aliases = aliases
        self.duration = duration
        self.date = date
        self.rating100 = rating100
        self.director = director
        self.synopsis = synopsis
        self.urls = urls
        self.front_image_path = front_image_path
        self.back_image_path = back_image_path
        self.created_at = created_at
        self.updated_at = updated_at
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
