from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .stash_context import StashQL
from .stash_interface import StashInterface
from .types import (
    StashPerformerProtocol,
    StashSceneProtocol,
    StashStudioProtocol,
    StashTagProtocol,
)


@dataclass
class VideoCaption:
    language_code: str
    caption_type: str


@dataclass
class SceneFileType:
    size: str | None = None
    duration: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    framerate: float | None = None
    bitrate: int | None = None


@dataclass
class ScenePathsType:
    screenshot: str | None = None
    preview: str | None = None
    stream: str | None = None
    webp: str | None = None
    vtt: str | None = None
    sprite: str | None = None
    funscript: str | None = None
    interactive_heatmap: str | None = None
    caption: str | None = None


@dataclass
class SceneStreamEndpoint:
    url: str
    mime_type: str | None = None
    label: str | None = None


@dataclass
class Scene(StashSceneProtocol):
    id: str
    urls: list[str] = field(default_factory=list)
    title: str | None = None
    code: str | None = None
    details: str | None = None
    director: str | None = None
    date: datetime | None = None
    rating100: int | None = None
    organized: bool = False
    o_counter: int | None = None
    interactive: bool = False
    interactive_speed: int | None = None
    captions: list[VideoCaption] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    last_played_at: datetime | None = None
    resume_time: float | None = None
    play_duration: float | None = None
    play_count: int | None = None
    play_history: list[datetime] = field(default_factory=list)
    o_history: list[datetime] = field(default_factory=list)
    files: list[SceneFileType] = field(default_factory=list)
    paths: ScenePathsType | None = None
    scene_markers: list[str] = field(default_factory=list)
    galleries: list[str] = field(default_factory=list)
    studio: StashStudioProtocol | None = None
    groups: list[str] = field(default_factory=list)
    tags: list[StashTagProtocol] = field(default_factory=list)
    performers: list[StashPerformerProtocol] = field(default_factory=list)
    stash_ids: list[str] = field(default_factory=list)
    sceneStreams: list[SceneStreamEndpoint] = field(default_factory=list)

    # Define input field configurations
    _input_fields = {
        # Field name: (attribute name, default value, transform function, required)
        "title": ("title", None, None, False),
        "code": ("code", None, None, False),
        "details": ("details", None, None, False),
        "director": ("director", None, None, False),
        "urls": ("urls", [], None, False),
        "date": ("date", None, lambda x: x.date().isoformat() if x else None, False),
        "rating100": ("rating100", None, None, False),
        "organized": ("organized", False, None, False),
        "studio_id": ("studio", None, lambda x: x.id if x else None, False),
        "gallery_ids": ("galleries", [], None, False),
        "performer_ids": ("performers", [], lambda x: [p.id for p in x], False),
        "tag_ids": ("tags", [], lambda x: [t.id for t in x], False),
        "stash_ids": ("stash_ids", [], None, False),
        "interactive": ("interactive", False, None, False),
        "interactive_speed": ("interactive_speed", None, None, False),
    }

    @staticmethod
    def find(id: str, interface: StashInterface) -> Scene | None:
        """Find a scene by ID.

        Args:
            id: The ID of the scene to find
            interface: StashInterface instance to use for querying

        Returns:
            Scene instance if found, None otherwise
        """
        data = interface.find_scene(id)
        return Scene.from_dict(data) if data else None

    @staticmethod
    def find_all(
        interface: StashInterface, filter: dict = {"per_page": -1}, q: str = ""
    ) -> list[Scene]:
        """Find all scenes matching the filter/query.

        Args:
            interface: StashInterface instance to use for querying
            filter: Filter parameters for the query
            q: Query string to search for

        Returns:
            List of Scene instances matching the criteria
        """
        data = interface.find_scenes(filter=filter, q=q)
        return [Scene.from_dict(s) for s in data]

    def save(self, interface: StashInterface) -> None:
        """Save changes to this scene in stash.

        Args:
            interface: StashInterface instance to use for updating
        """
        interface.update_scene(self.to_update_input_dict())

    @staticmethod
    def create_batch(interface: StashInterface, scenes: list[Scene]) -> list[dict]:
        """Create multiple scenes at once.

        Args:
            interface: StashInterface instance to use for creation
            scenes: List of Scene instances to create

        Returns:
            List of created scene data from stash
        """
        inputs = [s.to_create_input_dict() for s in scenes]
        return interface.create_scenes(inputs)

    @staticmethod
    def update_batch(interface: StashInterface, scenes: list[Scene]) -> list[dict]:
        """Update multiple scenes at once.

        Args:
            interface: StashInterface instance to use for updating
            scenes: List of Scene instances to update

        Returns:
            List of updated scene data from stash
        """
        updates = [s.to_update_input_dict() for s in scenes]
        return interface.update_scenes(updates)

    def to_dict(self) -> dict:
        """Convert the scene object to a dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "code": self.code,
            "details": self.details,
            "director": self.director,
            "urls": self.urls,
            "date": self.date.isoformat() if self.date else None,
            "rating100": self.rating100,
            "organized": self.organized,
            "o_counter": self.o_counter,
            "interactive": self.interactive,
            "interactive_speed": self.interactive_speed,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_played_at": (
                self.last_played_at.isoformat() if self.last_played_at else None
            ),
            "resume_time": self.resume_time,
            "play_duration": self.play_duration,
            "play_count": self.play_count,
            "play_history": [t.isoformat() for t in self.play_history],
            "o_history": [t.isoformat() for t in self.o_history],
            "files": [vars(f) for f in self.files],
            "scene_markers": self.scene_markers,
            "galleries": self.galleries,
            "studio": self.studio.to_dict() if self.studio else None,
            "groups": self.groups,
            "tags": [t.to_dict() for t in self.tags],
            "performers": [p.to_dict() for p in self.performers],
            "stash_ids": self.stash_ids,
            "sceneStreams": [vars(s) for s in self.sceneStreams],
        }

    def to_create_input_dict(self) -> dict:
        """Converts the Scene object into a dictionary matching the SceneCreateInput GraphQL definition.

        Only includes fields that have non-default values to prevent unintended overwrites.
        Uses _input_fields configuration to determine what to include.
        """
        result = {}

        for field_name, (
            attr_name,
            default_value,
            transform_func,
            required,
        ) in self._input_fields.items():
            value = getattr(self, attr_name)

            # Skip None values for non-required fields
            if value is None and not required:
                continue

            # Skip if value equals default (but still include required fields)
            if not required and value == default_value:
                continue

            # For empty lists (but still include required fields)
            if not required and isinstance(default_value, list) and not value:
                continue

            # Special handling for numeric fields that could be 0
            if isinstance(value, (int, float)) or value is not None:
                result[field_name] = transform_func(value) if transform_func else value

        return result

    def to_update_input_dict(self) -> dict:
        """Converts the Scene object into a dictionary matching the SceneUpdateInput GraphQL definition."""
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
    def from_dict(cls, data: dict) -> Scene:
        """Create a Scene instance from a dictionary.

        Args:
            data: Dictionary containing scene data from GraphQL or other sources.

        Returns:
            A new Scene instance.
        """
        # Handle both GraphQL response format and direct dictionary format
        scene_data = data.get("scene", data)

        # Handle relationships
        studio = None
        if "studio" in scene_data and scene_data["studio"]:
            from .studio import Studio

            studio = Studio.from_dict(scene_data["studio"])

        tags = []
        if "tags" in scene_data:
            from .tag import Tag

            tags = [Tag.from_dict(t) for t in scene_data["tags"]]

        performers = []
        if "performers" in scene_data:
            from .performer import Performer

            performers = [Performer.from_dict(p) for p in scene_data["performers"]]

        # Convert string dates to datetime objects using StashQL's robust datetime handling
        created_at = StashQL.sanitize_datetime(scene_data.get("created_at"))
        updated_at = StashQL.sanitize_datetime(scene_data.get("updated_at"))
        last_played_at = StashQL.sanitize_datetime(scene_data.get("last_played_at"))
        date = StashQL.sanitize_datetime(scene_data.get("date"))

        # Convert history timestamps
        play_history = [
            StashQL.sanitize_datetime(t)
            for t in scene_data.get("play_history", [])
            if StashQL.sanitize_datetime(t) is not None
        ]
        o_history = [
            StashQL.sanitize_datetime(t)
            for t in scene_data.get("o_history", [])
            if StashQL.sanitize_datetime(t) is not None
        ]

        # Create the scene instance
        scene = cls(
            id=str(scene_data.get("id", "")),
            urls=list(scene_data.get("urls", [])),
            title=scene_data.get("title"),
            code=scene_data.get("code"),
            details=scene_data.get("details"),
            director=scene_data.get("director"),
            date=date,
            rating100=scene_data.get("rating100"),
            organized=bool(scene_data.get("organized", False)),
            o_counter=scene_data.get("o_counter"),
            interactive=bool(scene_data.get("interactive", False)),
            interactive_speed=scene_data.get("interactive_speed"),
            created_at=created_at or datetime.now(),
            updated_at=updated_at or datetime.now(),
            last_played_at=last_played_at,
            resume_time=scene_data.get("resume_time"),
            play_duration=scene_data.get("play_duration"),
            play_count=scene_data.get("play_count"),
            play_history=play_history,
            o_history=o_history,
            studio=studio,
            tags=tags,
            performers=performers,
        )

        # Handle files if present
        if "files" in scene_data:
            scene.files = [SceneFileType(**f) for f in scene_data["files"]]

        # Handle galleries if present
        if "galleries" in scene_data:
            scene.galleries = [g["id"] for g in scene_data["galleries"]]

        # Handle groups if present
        if "groups" in scene_data:
            scene.groups = [g["id"] for g in scene_data["groups"]]

        # Handle stash_ids if present
        if "stash_ids" in scene_data:
            scene.stash_ids = list(scene_data["stash_ids"])

        # Handle scene_markers if present
        if "scene_markers" in scene_data:
            scene.scene_markers = list(scene_data["scene_markers"])

        # Handle sceneStreams if present
        if "sceneStreams" in scene_data:
            scene.sceneStreams = [
                SceneStreamEndpoint(**s) for s in scene_data["sceneStreams"]
            ]

        return scene
