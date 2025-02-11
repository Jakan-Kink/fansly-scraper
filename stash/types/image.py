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

    async def to_input(self) -> dict[str, Any]:
        """Convert to GraphQL input.

        Returns:
            Dictionary of input fields
        """
        # Field definitions with their conversion functions
        field_conversions = {
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

        # Process regular fields
        data = {}
        for field, converter in field_conversions.items():
            if hasattr(self, field):
                value = getattr(self, field)
                if value is not None:
                    try:
                        converted = converter(value)
                        if converted is not None:
                            data[field] = converted
                    except (ValueError, TypeError):
                        # Skip fields that can't be converted
                        pass

        # ID is required - we only update existing objects
        if not hasattr(self, "id") or self.id == "new":
            raise ValueError(
                f"Image must have an ID for updates, got: {getattr(self, 'id', None)}"
            )
        data["id"] = self.id

        # Helper function to get ID from object or dict
        async def get_id(obj: Any) -> str | None:
            if isinstance(obj, dict):
                return obj.get("id")
            if hasattr(obj, "awaitable_attrs"):
                await obj.awaitable_attrs.id
            return getattr(obj, "id", None)

        # Process relationships
        relationships = {
            "studio": ("studio_id", False),  # (target_field, is_list)
            "performers": ("performer_ids", True),
            "tags": ("tag_ids", True),
            "galleries": ("gallery_ids", True),
        }

        for rel_field, (target_field, is_list) in relationships.items():
            if hasattr(self, rel_field):
                value = getattr(self, rel_field)
                if not value:
                    continue

                if is_list:
                    # Handle list relationships
                    ids = []
                    for item in value:
                        if item_id := await get_id(item):
                            ids.append(item_id)
                    if ids:
                        data[target_field] = ids
                else:
                    # Handle single relationships
                    if item_id := await get_id(value):
                        data[target_field] = item_id

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
