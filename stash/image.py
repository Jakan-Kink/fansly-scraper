from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from stashapi.stashapp import StashInterface

from .image_paths import ImagePathsType
from .stash_context import StashQL
from .types import (
    StashGalleryProtocol,
    StashImageProtocol,
    StashPerformerProtocol,
    StashStudioProtocol,
    StashTagProtocol,
    VisualFileProtocol,
)


@dataclass
class Image(StashImageProtocol):
    """Represents an image in the Stash database."""

    id: str
    title: str | None = None
    code: str | None = None
    rating100: int | None = None
    date: datetime | None = None
    details: str | None = None
    photographer: str | None = None
    o_counter: int | None = None
    organized: bool = False
    visual_files: list[VisualFileProtocol] = field(default_factory=list)
    paths: ImagePathsType | None = None
    galleries: list[StashGalleryProtocol] = field(default_factory=list)
    studio: StashStudioProtocol | None = None
    tags: list[StashTagProtocol] = field(default_factory=list)
    performers: list[StashPerformerProtocol] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @staticmethod
    def find(id: str, interface: StashInterface) -> Image | None:
        """Find an image by ID.

        Args:
            id: The ID of the image to find
            interface: StashInterface instance to use for querying

        Returns:
            Image instance if found, None otherwise
        """
        data = interface.find_image(id)
        return Image.from_dict(data) if data else None

    @staticmethod
    def find_all(
        interface: StashInterface, filter: dict = {"per_page": -1}, q: str = ""
    ) -> list[Image]:
        """Find all images matching the filter/query.

        Args:
            interface: StashInterface instance to use for querying
            filter: Filter parameters for the query
            q: Query string to search for

        Returns:
            List of Image instances matching the criteria
        """
        data = interface.find_images(filter=filter, q=q)
        return [Image.from_dict(i) for i in data]

    def save(self, interface: StashInterface) -> None:
        """Save changes to this image in stash.

        Args:
            interface: StashInterface instance to use for updating
        """
        interface.update_image(self.to_dict())

    @staticmethod
    def create_batch(interface: StashInterface, images: list[Image]) -> list[dict]:
        """Create multiple images at once.

        Args:
            interface: StashInterface instance to use for creation
            images: List of Image instances to create

        Returns:
            List of created image data from stash
        """
        inputs = [i.to_create_input_dict() for i in images]
        return interface.create_images(inputs)

    @staticmethod
    def update_batch(interface: StashInterface, images: list[Image]) -> list[dict]:
        """Update multiple images at once.

        Args:
            interface: StashInterface instance to use for updating
            images: List of Image instances to update

        Returns:
            List of updated image data from stash
        """
        updates = [i.to_update_input_dict() for i in images]
        return interface.update_images(updates)

    def to_dict(self) -> dict:
        """Convert the image object to a dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "code": self.code,
            "rating100": self.rating100,
            "date": self.date.isoformat() if self.date else None,
            "details": self.details,
            "photographer": self.photographer,
            "o_counter": self.o_counter,
            "organized": self.organized,
            "visual_files": [f.to_dict() for f in self.visual_files],
            "paths": vars(self.paths) if self.paths else None,
            "galleries": [g.to_dict() for g in self.galleries],
            "studio": self.studio.to_dict() if self.studio else None,
            "tags": [t.to_dict() for t in self.tags],
            "performers": [p.to_dict() for p in self.performers],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_create_input_dict(self) -> dict:
        """Converts the Image object into a dictionary matching the ImageCreateInput GraphQL definition."""
        return {
            "title": self.title,
            "code": self.code,
            "rating100": self.rating100,
            "date": self.date.isoformat() if self.date else None,
            "details": self.details,
            "photographer": self.photographer,
            "organized": self.organized,
            "gallery_ids": [g.id for g in self.galleries],
            "studio_id": self.studio.id if self.studio else None,
            "tag_ids": [t.id for t in self.tags],
            "performer_ids": [p.id for p in self.performers],
        }

    def to_update_input_dict(self) -> dict:
        """Converts the Image object into a dictionary matching the ImageUpdateInput GraphQL definition."""
        return {"id": self.id, **self.to_create_input_dict()}

    def stash_create(self, interface: StashInterface) -> dict:
        """Creates the image in stash using the interface.

        Args:
            interface: StashInterface instance to use for creation

        Returns:
            dict: Response from stash containing the created image data
        """
        return interface.create_image(self.to_create_input_dict())

    @classmethod
    def from_dict(cls, data: dict) -> Image:
        """Create an Image instance from a dictionary.

        Args:
            data: Dictionary containing image data from GraphQL or other sources.

        Returns:
            A new Image instance.
        """
        # Handle both GraphQL response format and direct dictionary format
        image_data = data.get("image", data)

        # Convert string dates to datetime objects using StashQL's robust datetime handling
        date = StashQL.sanitize_datetime(image_data.get("date"))
        created_at = StashQL.sanitize_datetime(image_data.get("created_at"))
        updated_at = StashQL.sanitize_datetime(image_data.get("updated_at"))

        # Handle relationships
        studio = None
        if image_data.get("studio"):
            from .studio import Studio

            studio = Studio.from_dict(image_data["studio"])

        tags = []
        if "tags" in image_data:
            from .tag import Tag

            tags = [Tag.from_dict(t) for t in image_data["tags"]]

        performers = []
        if "performers" in image_data:
            from .performer import Performer

            performers = [Performer.from_dict(p) for p in image_data["performers"]]

        galleries = []
        if "galleries" in image_data:
            from .gallery import Gallery

            galleries = [Gallery.from_dict(g) for g in image_data["galleries"]]

        # Handle visual files
        visual_files = []
        if "visual_files" in image_data:
            from .file import VisualFile

            visual_files = [VisualFile.from_dict(f) for f in image_data["visual_files"]]

        # Handle paths
        paths = None
        if "paths" in image_data and image_data["paths"]:
            paths = ImagePathsType(**image_data["paths"])

        # Create the image instance
        image = cls(
            id=str(image_data.get("id", "")),
            title=image_data.get("title"),
            code=image_data.get("code"),
            rating100=image_data.get("rating100"),
            date=date,
            details=image_data.get("details"),
            photographer=image_data.get("photographer"),
            o_counter=image_data.get("o_counter"),
            organized=bool(image_data.get("organized", False)),
            visual_files=visual_files,
            paths=paths,
            galleries=galleries,
            studio=studio,
            tags=tags,
            performers=performers,
            created_at=created_at or datetime.now(),
            updated_at=updated_at or datetime.now(),
        )

        return image
