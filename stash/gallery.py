from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .stash_context import StashQL
from .stash_interface import StashInterface
from .types import (
    StashGalleryProtocol,
    StashSceneProtocol,
    StashStudioProtocol,
    StashTagProtocol,
    VisualFileProtocol,
)


@dataclass
class GalleryChapter:
    """Represents a chapter within a gallery."""

    id: str
    title: str
    image_index: int
    gallery_id: str
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Gallery(StashGalleryProtocol):
    """Represents a gallery in the Stash database."""

    id: str
    title: str | None = None
    code: str | None = None
    urls: list[str] = field(default_factory=list)
    date: datetime | None = None
    details: str | None = None
    photographer: str | None = None
    rating100: int | None = None
    organized: bool = False
    files: list[VisualFileProtocol | StashGalleryProtocol] = field(default_factory=list)
    folder: str | None = None
    chapters: list[GalleryChapter] = field(default_factory=list)
    scenes: list[StashSceneProtocol] = field(default_factory=list)
    studio: StashStudioProtocol | None = None
    image_count: int = 0
    tags: list[StashTagProtocol] = field(default_factory=list)
    performers: list[StashTagProtocol] = field(default_factory=list)
    cover: str | None = None
    paths: dict | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # Define input field configurations
    _input_fields = {
        # Field name: (attribute name, default value, transform function, required)
        "title": ("title", None, None, False),
        "code": ("code", None, None, False),
        "urls": ("urls", [], None, False),
        "date": ("date", None, lambda x: x.date().isoformat() if x else None, False),
        "details": ("details", None, None, False),
        "photographer": ("photographer", None, None, False),
        "rating100": ("rating100", None, None, False),
        "organized": ("organized", False, None, False),
        "scene_ids": ("scenes", [], lambda x: [s.id for s in x], False),
        "studio_id": ("studio", None, lambda x: x.id if x else None, False),
        "tag_ids": ("tags", [], lambda x: [t.id for t in x], False),
        "performer_ids": ("performers", [], lambda x: [p.id for p in x], False),
    }

    @staticmethod
    def find(id: str, interface: StashInterface) -> Gallery | None:
        """Find a gallery by ID.

        Args:
            id: The ID of the gallery to find
            interface: StashInterface instance to use for querying

        Returns:
            Gallery instance if found, None otherwise
        """
        data = interface.find_gallery(id)
        return Gallery.from_dict(data) if data else None

    @staticmethod
    def find_all(
        interface: StashInterface, filter: dict = {"per_page": -1}, q: str = ""
    ) -> list[Gallery]:
        """Find all galleries matching the filter/query.

        Args:
            interface: StashInterface instance to use for querying
            filter: Filter parameters for the query
            q: Query string to search for

        Returns:
            List of Gallery instances matching the criteria
        """
        data = interface.find_galleries(filter=filter, q=q)
        return [Gallery.from_dict(g) for g in data]

    def save(self, interface: StashInterface) -> None:
        """Save changes to this gallery in stash.

        Args:
            interface: StashInterface instance to use for updating
        """
        interface.update_gallery(self.to_update_input_dict())

    @staticmethod
    def create_batch(interface: StashInterface, galleries: list[Gallery]) -> list[dict]:
        """Create multiple galleries at once.

        Args:
            interface: StashInterface instance to use for creation
            galleries: List of Gallery instances to create

        Returns:
            List of created gallery data from stash
        """
        inputs = [g.to_create_input_dict() for g in galleries]
        return interface.create_galleries(inputs)

    @staticmethod
    def update_batch(interface: StashInterface, galleries: list[Gallery]) -> list[dict]:
        """Update multiple galleries at once.

        Args:
            interface: StashInterface instance to use for updating
            galleries: List of Gallery instances to update

        Returns:
            List of updated gallery data from stash
        """
        updates = [g.to_update_input_dict() for g in galleries]
        return interface.update_galleries(updates)

    def to_dict(self) -> dict:
        """Convert the gallery object to a dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "code": self.code,
            "urls": self.urls,
            "date": self.date.isoformat() if self.date else None,
            "details": self.details,
            "photographer": self.photographer,
            "rating100": self.rating100,
            "organized": self.organized,
            "files": [f.to_dict() for f in self.files],
            "folder": self.folder,
            "chapters": [
                {
                    "id": c.id,
                    "title": c.title,
                    "image_index": c.image_index,
                    "gallery_id": c.gallery_id,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                }
                for c in self.chapters
            ],
            "scenes": [s.to_dict() for s in self.scenes],
            "studio": self.studio.to_dict() if self.studio else None,
            "image_count": self.image_count,
            "tags": [t.to_dict() for t in self.tags],
            "performers": [p.to_dict() for p in self.performers],
            "cover": self.cover,
            "paths": self.paths,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_create_input_dict(self) -> dict:
        """Converts the Gallery object into a dictionary matching the GalleryCreateInput GraphQL definition.

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
        """Converts the Gallery object into a dictionary matching the GalleryUpdateInput GraphQL definition."""
        return {"id": self.id, **self.to_create_input_dict()}

    def stash_create(self, interface: StashInterface) -> dict:
        """Creates the gallery in stash using the interface.

        Args:
            interface: StashInterface instance to use for creation

        Returns:
            dict: Response from stash containing the created gallery data
        """
        return interface.create_gallery(self.to_create_input_dict())

    @classmethod
    def from_dict(cls, data: dict) -> Gallery:
        """Create a Gallery instance from a dictionary.

        Args:
            data: Dictionary containing gallery data from GraphQL or other sources.

        Returns:
            A new Gallery instance.
        """
        # Handle both GraphQL response format and direct dictionary format
        gallery_data = data.get("gallery", data)

        # Convert string dates to datetime objects using StashQL's robust datetime handling
        date = StashQL.sanitize_datetime(gallery_data.get("date"))
        created_at = StashQL.sanitize_datetime(gallery_data.get("created_at"))
        updated_at = StashQL.sanitize_datetime(gallery_data.get("updated_at"))

        # Handle relationships
        studio = None
        if gallery_data.get("studio"):
            from .studio import Studio

            studio = Studio.from_dict(gallery_data["studio"])

        tags = []
        if "tags" in gallery_data:
            from .tag import Tag

            tags = [Tag.from_dict(t) for t in gallery_data["tags"]]

        performers = []
        if "performers" in gallery_data:
            from .performer import Performer

            performers = [Performer.from_dict(p) for p in gallery_data["performers"]]

        scenes = []
        if "scenes" in gallery_data:
            from .scene import Scene

            scenes = [Scene.from_dict(s) for s in gallery_data["scenes"]]

        # Handle files
        files = []
        if "files" in gallery_data:
            from .file import VisualFile

            files = [VisualFile.from_dict(f) for f in gallery_data["files"]]

        # Handle chapters
        chapters = []
        if "chapters" in gallery_data:
            for chapter_data in gallery_data["chapters"]:
                chapter_created_at = StashQL.sanitize_datetime(
                    chapter_data.get("created_at")
                )
                chapter_updated_at = StashQL.sanitize_datetime(
                    chapter_data.get("updated_at")
                )
                chapters.append(
                    GalleryChapter(
                        id=chapter_data["id"],
                        title=chapter_data["title"],
                        image_index=chapter_data["image_index"],
                        gallery_id=chapter_data["gallery_id"],
                        created_at=chapter_created_at or datetime.now(),
                        updated_at=chapter_updated_at or datetime.now(),
                    )
                )

        # Create the gallery instance
        gallery = cls(
            id=str(gallery_data.get("id", "")),
            title=gallery_data.get("title"),
            code=gallery_data.get("code"),
            urls=list(gallery_data.get("urls", [])),
            date=date,
            details=gallery_data.get("details"),
            photographer=gallery_data.get("photographer"),
            rating100=gallery_data.get("rating100"),
            organized=bool(gallery_data.get("organized", False)),
            files=files,
            folder=gallery_data.get("folder"),
            chapters=chapters,
            scenes=scenes,
            studio=studio,
            image_count=gallery_data.get("image_count", 0),
            tags=tags,
            performers=performers,
            cover=gallery_data.get("cover"),
            paths=gallery_data.get("paths"),
            created_at=created_at or datetime.now(),
            updated_at=updated_at or datetime.now(),
        )

        return gallery
