from datetime import datetime

from stashapi.stashapp import StashInterface

from .stash_context import StashQL
from .stash_gallery import StashGallery
from .stash_group import StashGroup
from .stash_performer import StashPerformer
from .stash_tag import StashTag

scene_fragment = (
    "id "
    "title "
    "code "
    "details "
    "director "
    "urls "
    "date "
    "organized "
    "created_at "
    "updated_at "
    "files { "
    "id "
    "path "
    "basename "
    "parent_folder_id "
    "size "
    "format "
    "width "
    "height "
    "duration "
    "created_at "
    "updated_at "
    "} "
    "galleries { "
    "id "
    "} "
    "studio { "
    "id "
    "} "
    "tags { "
    "id "
    "} "
    "performers { "
    "id "
    "} "
)


class StashScene(StashQL):
    @staticmethod
    def find(id: str, interface: StashInterface) -> "StashScene":
        data = interface.find_scene(id)
        return StashScene.from_dict(data) if data else None

    def save(self, interface: StashInterface) -> None:
        interface.update_scene(self.to_dict())

    title: str | None
    code: str | None
    details: str | None
    director: str | None
    urls: list[str]
    date: str | None
    rating100: int | None
    organized: bool
    o_counter: int | None
    interactive: bool
    interactive_speed: int | None
    files: list[str]
    paths: None
    scene_markers: list[str]
    galleries: list[StashGallery]
    studio: StashGroup | None
    groups: list[StashGroup]
    tags: list[StashTag]
    performers: list[StashPerformer]
    stash_ids: list[str]
    sceneStreams: list[str]

    def to_dict(self) -> dict:
        base_dict = super().to_dict()
        scene_dict = {
            "title": self.title,
            "code": self.code,
            "details": self.details,
            "director": self.director,
            "date": self.date.isoformat() if self.date else None,
            "rating100": self.rating100,
            "organized": self.organized,
            "o_counter": self.o_counter,
            "interactive": self.interactive,
            "interactive_speed": self.interactive_speed,
            "files": self.files,
            "scene_markers": self.scene_markers,
            "galleries": [gallery.id for gallery in self.galleries],
            "studio": self.studio.id if self.studio else None,
            "groups": [group.id for group in self.groups],
            "tags": [tag.id for tag in self.tags],
            "performers": [performer.id for performer in self.performers],
            "stash_ids": self.stash_ids,
            "sceneStreams": self.sceneStreams,
        }
        return {**base_dict, **scene_dict}

    def __init__(
        self,
        id: str,
        urls: list[str] = [],
        title: str | None = None,
        code: str | None = None,
        details: str | None = None,
        director: str | None = None,
        date: str | None = None,
        rating100: int | None = None,
        organized: bool = False,
        o_counter: int | None = None,
        interactive: bool = False,
        interactive_speed: int | None = None,
        created_at: datetime = datetime.now(),
        updated_at: datetime = datetime.now(),
    ) -> None:
        super().__init__(id=id, urls=urls, created_at=created_at, updated_at=updated_at)
        self.title = title
        self.code = code
        self.details = details
        self.director = director
        self.date = self.sanitize_datetime(date)
        self.rating100 = rating100
        self.organized = organized
        self.o_counter = o_counter
        self.interactive = interactive
        self.interactive_speed = interactive_speed
        self.created_at = self.sanitize_datetime(created_at)
        self.updated_at = self.sanitize_datetime(updated_at)
        self.files = []
        self.paths = None
        self.scene_markers = []
        self.galleries = []
        self.studio = None
        self.groups = []
        self.tags = []
        self.performers = []
        self.stash_ids = []
        self.sceneStreams = []
