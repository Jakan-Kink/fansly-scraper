from datetime import datetime

from stashapi.stashapp import StashInterface

from .stash_context import StashQL
from .stash_scene import StashScene
from .visual_file import VisualFile


class StashGallery(StashQL):
    @staticmethod
    def find(id: str, interface: StashInterface) -> "StashGallery":
        data = interface.find_gallery(id)
        return StashGallery.from_dict(data) if data else None

    def save(self, interface: StashInterface) -> None:
        interface.update_gallery(self.to_dict())

    title: str | None
    code: str | None
    urls: list[str]
    date: datetime | str | None
    details: str | None
    photographer: str | None
    rating100: int | None
    organized: bool
    files: list[VisualFile]
    folder: None
    chapters: list
    scenes: list[StashScene]
    studio: None
    image_count: int
    tags: list
    performers: list
    cover: None
    paths: None

    def to_dict(self) -> dict:
        base_dict = super().to_dict()
        gallery_dict = {
            "title": self.title,
            "code": self.code,
            "date": self.date.isoformat() if self.date else None,
            "details": self.details,
            "photographer": self.photographer,
            "rating100": self.rating100,
            "organized": self.organized,
            "files": [file.get_path() for file in self.files],
            "folder": self.folder,
            "chapters": self.chapters,
            "scenes": [scene.id for scene in self.scenes],
            "studio": self.studio.id if self.studio else None,
            "image_count": self.image_count,
            "tags": self.tags,
            "performers": [performer.id for performer in self.performers],
            "cover": self.cover,
            "paths": self.paths,
        }
        return {**base_dict, **gallery_dict}

    def __init__(
        self,
        id: str,
        urls: list[str] = [],
        title: str | None = None,
        code: str | None = None,
        date: datetime | str | None = None,
        details: str | None = None,
        photographer: str | None = None,
        rating100: int | None = None,
        organized: bool = False,
        created_at: datetime | str | None = None,
        updated_at: datetime | str | None = None,
    ) -> None:
        super().__init__(id=id, urls=urls, created_at=created_at, updated_at=updated_at)
        self.title = title
        self.code = code
        self.date = self.sanitize_datetime(date)
        self.details = details
        self.photographer = photographer
        self.rating100 = rating100
        self.organized = organized
        self.created_at = self.sanitize_datetime(created_at)
        self.updated_at = self.sanitize_datetime(updated_at)
        self.files = []
        self.folder = None
        self.chapters = []
        self.scenes = []
        self.studio = None
        self.image_count = 0
        self.tags = []
        self.performers = []
        self.cover = None
        self.paths = None

    def image(self, index: int):
        # Implement logic to retrieve image by index
        pass
