from datetime import datetime

from .image_paths_type import ImagePathsType
from .stash_gallery import StashGallery
from .stash_performer import StashPerformer
from .stash_studio import StashStudio
from .stash_tag import StashTag
from .visual_file import VisualFile


class StashImage:
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
        self.id = id
        self.title = title
        self.code = code
        self.rating100 = rating100
        self.urls = urls
        self.date: datetime = date
        self.details: str = details
        self.photographer: str = photographer
        self.o_counter = o_counter
        self.organized: bool = organized
        self.created_at = created_at
        self.updated_at = updated_at
        self.visual_files: list[VisualFile] = []
        self.paths: list[ImagePathsType] | None = None
        self.galleries: list[StashGallery] = []
        self.studio: StashStudio | None = None
        self.tags: list[StashTag] = []
        self.performers: list[StashPerformer] = []
