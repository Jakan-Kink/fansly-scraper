from datetime import datetime

from stashapi.stashapp import StashInterface

from .image_paths_type import ImagePathsType
from .stash_context import StashQL
from .stash_gallery import StashGallery
from .stash_performer import StashPerformer
from .stash_studio import StashStudio
from .stash_tag import StashTag
from .visual_file import VisualFile


class StashImage(StashQL):
    @staticmethod
    def find(id: str, interface: StashInterface) -> "StashImage":
        data = interface.find_image(id)
        return StashImage.from_dict(data) if data else None

    def save(self, interface: StashInterface) -> None:
        interface.update_image(self.to_dict())

    title: str | None
    code: str | None
    rating100: int | None
    date: datetime | str | None
    details: str | None
    photographer: str | None
    o_counter: int | None
    organized: bool
    visual_files: list[VisualFile]
    paths: list[ImagePathsType] | None
    galleries: list[StashGallery]
    studio: StashStudio | None
    tags: list[StashTag]
    performers: list[StashPerformer]

    def __init__(
        self,
        id: str,
        title: str | None = None,
        code: str | None = None,
        rating100: int | None = None,
        urls: list[str] = [],
        date: datetime | str | None = None,
        details: str | None = None,
        photographer: str | None = None,
        o_counter: int | None = None,
        organized: bool = False,
        created_at: datetime = datetime.now(),
        updated_at: datetime = datetime.now(),
    ):
        super().__init__(id=id, urls=urls, created_at=created_at, updated_at=updated_at)
        self.title = title
        self.code = code
        self.rating100 = rating100
        self.date: datetime = date
        self.details: str = details
        self.photographer: str = photographer
        self.o_counter = o_counter
        self.organized: bool = organized
        self.visual_files: list[VisualFile] = []
        self.paths: list[ImagePathsType] | None = None
        self.galleries: list[StashGallery] = []
        self.studio: StashStudio | None = None
        self.tags: list[StashTag] = []
        self.performers: list[StashPerformer] = []
