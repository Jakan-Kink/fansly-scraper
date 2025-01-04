from datetime import datetime

from stashapi.stashapp import StashInterface

from .stash_context import StashQL
from .stash_group import StashGroup
from .stash_tag import StashTag


class StashStudio(StashQL):
    @staticmethod
    def find(id: str, interface: StashInterface) -> "StashStudio":
        data = interface.find_studio(id)
        return StashStudio.from_dict(data) if data else None

    def save(self, interface: StashInterface) -> None:
        interface.update_studio(self.to_dict())

    name: str
    url: str | None
    parent_studio: "StashStudio" | None
    child_studios: list["StashStudio"]
    aliases: list[str]
    tags: list[StashTag]
    ignore_auto_tag: bool
    image_path: str | None
    rating100: int | None
    favorite: bool
    details: str | None
    groups: list[StashGroup]
    stash_ids: list[str]

    def __init__(
        self,
        id: str,
        name: str,
        url: str | None = None,
        ignore_auto_tag: bool = False,
        image_path: str | None = None,
        rating100: int | None = None,
        favorite: bool = False,
        details: str | None = None,
        created_at: datetime = datetime.now(),
        updated_at: datetime = datetime.now(),
    ):
        super().__init__(id=id, urls=[], created_at=created_at, updated_at=updated_at)
        self.name = name
        self.url = url
        self.parent_studio = None
        self.child_studios = []
        self.aliases = []
        self.tags = []
        self.ignore_auto_tag = ignore_auto_tag
        self.image_path = image_path
        self.rating100 = rating100
        self.favorite = favorite
        self.details = details
        self.groups = []
        self.stash_ids = []

    def scene_count(self, depth: int | None = None) -> int:
        # Implement logic to count scenes
        return 0

    def image_count(self, depth: int | None = None) -> int:
        # Implement logic to count images
        return 0

    def gallery_count(self, depth: int | None = None) -> int:
        # Implement logic to count galleries
        return 0

    def performer_count(self, depth: int | None = None) -> int:
        # Implement logic to count performers
        return 0

    def group_count(self, depth: int | None = None) -> int:
        # Implement logic to count groups
        return 0
