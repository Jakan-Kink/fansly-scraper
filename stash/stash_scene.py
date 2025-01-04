from datetime import datetime


class StashScene:
    def __init__(
        self,
        id: str,
        title: str | None = None,
        code: str | None = None,
        details: str | None = None,
        director: str | None = None,
        urls: list[str] = [],
        date: str | None = None,
        rating100: int | None = None,
        organized: bool = False,
        o_counter: int | None = None,
        interactive: bool = False,
        interactive_speed: int | None = None,
        created_at: datetime = datetime.now(),
        updated_at: datetime = datetime.now(),
    ):
        self.id = id
        self.title = title
        self.code = code
        self.details = details
        self.director = director
        self.urls = urls
        self.date = date
        self.rating100 = rating100
        self.organized = organized
        self.o_counter = o_counter
        self.interactive = interactive
        self.interactive_speed = interactive_speed
        self.created_at = created_at
        self.updated_at = updated_at
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
