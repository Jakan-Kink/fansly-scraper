"""Image types from schema/types/image.graphql."""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, List, Optional

import strawberry
from strawberry import ID, lazy

from metadata import Media

from .base import BulkUpdateIds, BulkUpdateStrings, StashObject
from .files import ImageFile, VisualFile

if TYPE_CHECKING:
    from .gallery import Gallery
    from .performer import Performer
    from .studio import Studio
    from .tag import Tag


@strawberry.type
class ImageFileType:
    """Image file type from schema/types/image.graphql."""

    mod_time: datetime  # Time!
    size: int  # Int!
    width: int  # Int!
    height: int  # Int!


@strawberry.type
class ImagePathsType:
    """Image paths type from schema/types/image.graphql."""

    thumbnail: str | None = None  # String (Resolver)
    preview: str | None = None  # String (Resolver)
    image: str | None = None  # String (Resolver)


@strawberry.type
class Image(StashObject):
    """Image type from schema."""

    __type_name__ = "Image"

    # Fields to track for changes
    __tracked_fields__ = {
        "title",
        "code",
        "urls",
        "date",
        "details",
        "photographer",
        "organized",
        "visual_files",
        "paths",
        "galleries",
        "tags",
        "performers",
        "files",  # Deprecated but still tracked
    }

    # Optional fields
    title: str | None = None  # String
    code: str | None = None  # String
    urls: list[str] = strawberry.field(default_factory=list)  # [String!]
    rating100: int | None = None  # Int (1-100)
    date: str | None = None  # String
    details: str | None = None  # String
    photographer: str | None = None  # String
    o_counter: int | None = None  # Int
    studio: Annotated["Studio", lazy("stash.types.studio.Studio")] | None = (
        None  # Studio
    )

    # Required fields
    urls: list[str] = strawberry.field(default_factory=list)  # [String!]!
    organized: bool = False  # Boolean!
    visual_files: list[VisualFile] = strawberry.field(
        default_factory=list
    )  # [VisualFile!]!
    paths: ImagePathsType = strawberry.field(
        default_factory=lambda: ImagePathsType(thumbnail="", preview="", image="")
    )  # ImagePathsType! (Resolver)
    galleries: list[Annotated["Gallery", lazy("stash.types.gallery.Gallery")]] = (
        strawberry.field(default_factory=list)
    )  # [Gallery!]!
    tags: list[Annotated["Tag", lazy("stash.types.tag.Tag")]] = strawberry.field(
        default_factory=list
    )  # [Tag!]!
    performers: list[
        Annotated["Performer", lazy("stash.types.performer.Performer")]
    ] = strawberry.field(
        default_factory=list
    )  # [Performer!]!

    # Deprecated fields (use visual_files instead)
    files: list[ImageFile] = strawberry.field(default_factory=list)  # [ImageFile!]!

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Image":
        """Create image from dictionary.

        Args:
            data: Dictionary containing image data

        Returns:
            New image instance
        """
        # Filter out fields that aren't part of our class
        valid_fields = {field.name for field in cls.__strawberry_definition__.fields}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}

        # created_at and updated_at handled by Stash

        # Create instance
        image = cls(**filtered_data)

        # Convert lists
        if "files" in filtered_data:
            image.files = [ImageFile(**f) for f in filtered_data["files"]]
        if "visual_files" in filtered_data:
            image.visual_files = [
                VisualFile(**f) for f in filtered_data["visual_files"]
            ]

        return image

    # Relationship definitions with their mappings
    __relationships__ = {
        "studio": ("studio_id", False),  # (target_field, is_list)
        "performers": ("performer_ids", True),
        "tags": ("tag_ids", True),
        "galleries": ("gallery_ids", True),
    }

    # Field definitions with their conversion functions
    __field_conversions__ = {
        "title": str,
        "code": str,
        "urls": list,
        "details": str,
        "photographer": str,
        "rating100": int,
        "organized": bool,
        "date": lambda d: (
            d.strftime("%Y-%m-%d")
            if isinstance(d, datetime)
            else (
                datetime.fromisoformat(d).strftime("%Y-%m-%d")
                if isinstance(d, str)
                else None
            )
        ),
    }

    async def _to_input_all(self) -> dict[str, Any]:
        """Convert all fields to input type.

        Returns:
            Dictionary of all input fields
        """
        # Process all fields
        data = await self._process_fields(set(self.__field_conversions__.keys()))

        # Process all relationships
        rel_data = await self._process_relationships(set(self.__relationships__.keys()))
        data.update(rel_data)

        # Convert to create input and dict
        input_obj = ImageUpdateInput(**data)
        return {
            k: v
            for k, v in vars(input_obj).items()
            if not k.startswith("_") and v is not None and k != "client_mutation_id"
        }

    async def _to_input_dirty(self) -> dict[str, Any]:
        """Convert only dirty fields to input type.

        Returns:
            Dictionary of dirty input fields plus ID
        """
        # Start with ID which is always required for updates
        data = {"id": self.id}

        # Get set of dirty fields (fields whose values have changed)
        dirty_fields = {
            field
            for field in self.__tracked_fields__
            if field in self.__original_values__
            and getattr(self, field) != self.__original_values__[field]
        }

        # Process dirty regular fields
        field_data = await self._process_fields(dirty_fields)
        data.update(field_data)

        # Process dirty relationships
        rel_data = await self._process_relationships(dirty_fields)
        data.update(rel_data)

        # Convert to update input and dict
        input_obj = ImageUpdateInput(**data)
        return {
            k: v
            for k, v in vars(input_obj).items()
            if not k.startswith("_") and v is not None and k != "client_mutation_id"
        }


@strawberry.input
class ImageDestroyInput:
    """Input for destroying images from schema/types/image.graphql."""

    id: ID  # ID!
    delete_file: bool | None = None  # Boolean
    delete_generated: bool | None = None  # Boolean


@strawberry.input
class ImagesDestroyInput:
    """Input for destroying multiple images from schema/types/image.graphql."""

    ids: list[ID]  # [ID!]!
    delete_file: bool | None = None  # Boolean
    delete_generated: bool | None = None  # Boolean


@strawberry.input
class ImageUpdateInput:
    """Input for updating images."""

    # Required fields
    id: ID  # ID!

    # Optional fields
    client_mutation_id: str | None = None  # String
    title: str | None = None  # String
    code: str | None = None  # String
    rating100: int | None = None  # Int (1-100)
    organized: bool | None = None  # Boolean
    url: str | None = None  # String @deprecated
    urls: list[str] | None = None  # [String!]
    date: str | None = None  # String
    details: str | None = None  # String
    photographer: str | None = None  # String
    studio_id: ID | None = None  # ID
    performer_ids: list[ID] | None = None  # [ID!]
    tag_ids: list[ID] | None = None  # [ID!]
    gallery_ids: list[ID] | None = None  # [ID!]
    primary_file_id: ID | None = None  # ID


@strawberry.input
class BulkImageUpdateInput:
    """Input for bulk updating images."""

    # Optional fields
    client_mutation_id: str | None = None  # String
    ids: list[ID]  # [ID!]
    rating100: int | None = None  # Int (1-100)
    organized: bool | None = None  # Boolean
    url: str | None = None  # String @deprecated
    urls: BulkUpdateStrings | None = None  # BulkUpdateStrings
    date: str | None = None  # String
    details: str | None = None  # String
    photographer: str | None = None  # String
    studio_id: ID | None = None  # ID
    performer_ids: BulkUpdateIds | None = None  # BulkUpdateIds
    tag_ids: BulkUpdateIds | None = None  # BulkUpdateIds
    gallery_ids: BulkUpdateIds | None = None  # BulkUpdateIds


@strawberry.type
class FindImagesResultType:
    """Result type for finding images from schema/types/image.graphql."""

    count: int  # Int!
    megapixels: float  # Float! (Total megapixels of the images)
    filesize: float  # Float! (Total file size in bytes)
    images: list[Annotated["Image", lazy("stash.types.image.Image")]] = (
        strawberry.field(default_factory=list)
    )  # [Image!]!
