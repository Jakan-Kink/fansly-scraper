from datetime import datetime

from stashapi.stashapp import StashInterface

from .types import StashSceneProtocol

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


class StashScene(StashSceneProtocol):
    @staticmethod
    def find(id: str, interface: StashInterface) -> "StashScene":
        """Find a scene by ID.

        Args:
            id: The ID of the scene to find
            interface: StashInterface instance to use for querying

        Returns:
            StashScene instance if found, None otherwise
        """
        data = interface.find_scene(id)
        return StashScene.from_dict(data) if data else None

    @staticmethod
    def find_all(
        interface: StashInterface, filter: dict = {"per_page": -1}, q: str = ""
    ) -> list["StashScene"]:
        """Find all scenes matching the filter/query.

        Args:
            interface: StashInterface instance to use for querying
            filter: Filter parameters for the query
            q: Query string to search for

        Returns:
            List of StashScene instances matching the criteria
        """
        data = interface.find_scenes(filter=filter, q=q)
        return [StashScene.from_dict(s) for s in data]

    def save(self, interface: StashInterface) -> None:
        """Save changes to this scene in stash.

        Args:
            interface: StashInterface instance to use for updating
        """
        interface.update_scene(self.to_dict())

    @staticmethod
    def create_batch(
        interface: StashInterface, scenes: list["StashScene"]
    ) -> list[dict]:
        """Create multiple scenes at once.

        Args:
            interface: StashInterface instance to use for creation
            scenes: List of StashScene instances to create

        Returns:
            List of created scene data from stash
        """
        inputs = [s.to_create_input_dict() for s in scenes]
        return interface.create_scenes(inputs)

    @staticmethod
    def update_batch(
        interface: StashInterface, scenes: list["StashScene"]
    ) -> list[dict]:
        """Update multiple scenes at once.

        Args:
            interface: StashInterface instance to use for updating
            scenes: List of StashScene instances to update

        Returns:
            List of updated scene data from stash
        """
        updates = [s.to_update_input_dict() for s in scenes]
        return interface.update_scenes(updates)

    def to_dict(self) -> dict:
        scene_dict = {
            "id": self.id,
            "urls": self.urls,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
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
        return scene_dict

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
        StashSceneProtocol.__init__(
            self=self, id=id, urls=urls, created_at=created_at, updated_at=updated_at
        )
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

    def to_create_input_dict(self) -> dict:
        """Converts the StashScene object into a dictionary matching the SceneCreateInput GraphQL definition."""
        return {
            "title": self.title,
            "code": self.code,
            "urls": self.urls,
            "date": self.date.isoformat() if self.date else None,
            "details": self.details,
            "director": self.director,
            "rating100": self.rating100,
            "organized": self.organized,
            "studio_id": self.studio.id if self.studio else None,
            "gallery_ids": [gallery.id for gallery in self.galleries],
            "tag_ids": [tag.id for tag in self.tags],
            "performer_ids": [performer.id for performer in self.performers],
            "stash_ids": self.stash_ids,
            "interactive": self.interactive,
            "interactive_speed": self.interactive_speed,
        }

    def to_update_input_dict(self) -> dict:
        """Converts the StashScene object into a dictionary matching the SceneUpdateInput GraphQL definition."""
        return {"id": self.id, **self.to_create_input_dict()}

    def stash_create(self, interface: StashInterface) -> dict:
        """Creates the scene in stash using the interface.

        Args:
            interface: StashInterface instance to use for creation

        Returns:
            dict: Response from stash containing the created scene data
        """
        return interface.create_scene(self.to_create_input_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "StashScene":
        """Create a StashScene instance from a dictionary.

        Args:
            data: Dictionary containing scene data from GraphQL or other sources.

        Returns:
            A new StashScene instance.
        """
        # Handle both GraphQL response format and direct dictionary format
        scene_data = data.get("scene", data)

        # Create the base scene object
        scene = cls(
            id=str(scene_data.get("id", "")),
            urls=list(scene_data.get("urls", [])),
            title=scene_data.get("title"),
            code=scene_data.get("code"),
            details=scene_data.get("details"),
            director=scene_data.get("director"),
            date=scene_data.get("date"),
            rating100=scene_data.get("rating100"),
            organized=bool(scene_data.get("organized", False)),
            o_counter=scene_data.get("o_counter"),
            interactive=bool(scene_data.get("interactive", False)),
            interactive_speed=scene_data.get("interactive_speed"),
            created_at=scene_data.get("created_at"),
            updated_at=scene_data.get("updated_at"),
        )

        # Handle files if present
        if "files" in scene_data:
            from .stash_scene_file import StashSceneFile

            scene.files = [StashSceneFile.from_dict(f) for f in scene_data["files"]]

        # Handle galleries if present
        if "galleries" in scene_data:
            from .stash_gallery import StashGallery

            scene.galleries = [
                StashGallery.from_dict(g) for g in scene_data["galleries"]
            ]

        # Handle studio if present
        if "studio" in scene_data and scene_data["studio"]:
            from .stash_studio import StashStudio

            scene.studio = StashStudio.from_dict(scene_data["studio"])

        # Handle groups if present
        if "groups" in scene_data:
            from .stash_group import StashGroup

            scene.groups = [StashGroup.from_dict(g) for g in scene_data["groups"]]

        # Handle tags if present
        if "tags" in scene_data:
            from .stash_tag import StashTag

            scene.tags = [StashTag.from_dict(t) for t in scene_data["tags"]]

        # Handle performers if present
        if "performers" in scene_data:
            from .stash_performer import StashPerformer

            scene.performers = [
                StashPerformer.from_dict(p) for p in scene_data["performers"]
            ]

        # Handle stash_ids if present
        if "stash_ids" in scene_data:
            scene.stash_ids = list(scene_data["stash_ids"])

        # Handle scene_markers if present
        if "scene_markers" in scene_data:
            scene.scene_markers = list(scene_data["scene_markers"])

        # Handle sceneStreams if present
        if "sceneStreams" in scene_data:
            scene.sceneStreams = list(scene_data["sceneStreams"])

        return scene
