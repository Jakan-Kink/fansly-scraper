"""Gallery types from schema/types/gallery.graphql and gallery-chapter.graphql."""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, List, Optional

import strawberry
from strawberry import ID, lazy

from metadata import Media, Message, Post

from .base import StashObject
from .enums import BulkUpdateIdMode
from .files import Folder, GalleryFile

if TYPE_CHECKING:
    from .performer import Performer
    from .scene import Scene
    from .studio import Studio
    from .tag import Tag


@strawberry.type
class GalleryPathsType:
    """Gallery paths type from schema/types/gallery.graphql."""

    cover: str = ""  # String!
    preview: str = ""  # String! # Resolver

    @classmethod
    def create_default(cls) -> "GalleryPathsType":
        """Create a default instance with empty strings."""
        return cls(cover="", preview="")


@strawberry.type
class GalleryChapter(StashObject):
    """Gallery chapter type from schema/types/gallery-chapter.graphql.

    Note: Inherits from StashObject since it has id, created_at, and updated_at
    fields in the schema, matching the common pattern."""

    __type_name__ = "GalleryChapter"

    # Fields to track for changes
    __tracked_fields__ = {
        "gallery",
        "title",
        "image_index",
    }

    # Required fields
    gallery: Annotated["Gallery", lazy("stash.types.gallery.Gallery")]  # Gallery!
    title: str  # String!
    image_index: int  # Int!

    # Field definitions with their conversion functions
    __field_conversions__ = {
        "title": str,
        "image_index": int,
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
        input_class = (
            GalleryChapterCreateInput
            if not hasattr(self, "id") or self.id == "new"
            else GalleryChapterUpdateInput
        )
        input_obj = input_class(**data)
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
        input_obj = GalleryChapterUpdateInput(**data)
        return {
            k: v
            for k, v in vars(input_obj).items()
            if not k.startswith("_") and v is not None and k != "client_mutation_id"
        }

    __relationships__ = {
        # Standard ID relationships
        "gallery": ("gallery_id", False),  # (target_field, is_list)
    }


@strawberry.input
class GalleryChapterCreateInput:
    """Input for creating gallery chapters."""

    gallery_id: ID  # ID!
    title: str  # String!
    image_index: int  # Int!


@strawberry.input
class GalleryChapterUpdateInput:
    """Input for updating gallery chapters."""

    id: ID  # ID!
    gallery_id: ID | None = None  # ID
    title: str | None = None  # String
    image_index: int | None = None  # Int


@strawberry.type
class FindGalleryChaptersResultType:
    """Result type for finding gallery chapters."""

    count: int  # Int!
    chapters: list[GalleryChapter]  # [GalleryChapter!]!


@strawberry.type
class Image:
    """Image type from schema."""

    id: str  # String!
    title: str | None = None  # String
    url: str | None = None  # String
    date: str | None = None  # String
    rating100: int | None = None  # Int
    organized: bool = False  # Boolean!
    # created_at and updated_at handled by Stash


@strawberry.type
class Gallery(StashObject):
    """Gallery type from schema/types/gallery.graphql."""

    __type_name__ = "Gallery"

    # Fields to track for changes
    __tracked_fields__ = {
        "title",
        "code",
        "date",
        "details",
        "photographer",
        "rating100",
        "urls",
        "organized",
        "files",
        "chapters",
        "scenes",
        "tags",
        "performers",
        "studio",
    }

    # Optional fields
    title: str | None = None
    code: str | None = None
    date: str | None = None
    details: str | None = None
    photographer: str | None = None
    rating100: int | None = None
    folder: Folder | None = None
    studio: Annotated["Studio", lazy("stash.types.studio.Studio")] | None = (
        None  # Forward reference
    )
    cover: Image | None = None

    # Required fields
    urls: list[str] = strawberry.field(default_factory=list)
    organized: bool = False
    files: list[Annotated["GalleryFile", lazy("stash.types.files.GalleryFile")]] = (
        strawberry.field(default_factory=list)
    )
    chapters: list[
        Annotated["GalleryChapter", lazy("stash.types.gallery.GalleryChapter")]
    ] = strawberry.field(default_factory=list)
    scenes: list[Annotated["Scene", lazy("stash.types.scene.Scene")]] = (
        strawberry.field(default_factory=list)
    )
    image_count: int = 0
    tags: list[Annotated["Tag", lazy("stash.types.tag.Tag")]] = strawberry.field(
        default_factory=list
    )
    performers: list[
        Annotated["Performer", lazy("stash.types.performer.Performer")]
    ] = strawberry.field(default_factory=list)
    paths: GalleryPathsType = strawberry.field(
        default_factory=GalleryPathsType.create_default
    )

    @strawberry.field
    async def image(self, index: int) -> Image:
        """Get image at index."""
        # TODO: Implement this resolver
        raise NotImplementedError("image resolver not implemented")

    @classmethod
    async def from_content(
        cls,
        content: Post | Message,
        performer: (
            Annotated["Performer", lazy("stash.types.performer.Performer")] | None
        ) = None,
        studio: Annotated["Studio", lazy("stash.types.studio.Studio")] | None = None,
    ) -> "Gallery":
        """Create gallery from post or message.

        Args:
            content: Post or message to convert
            performer: Optional performer to associate
            studio: Optional studio to associate

        Returns:
            New gallery instance
        """
        # Get URL based on content type
        urls = []
        if isinstance(content, Post):
            urls = [f"https://fansly.com/post/{content.id}"]
        elif isinstance(content, Message):
            urls = [f"https://fansly.com/message/{content.id}"]

        # Build gallery
        gallery = cls(
            id="new",  # Will be replaced on save
            details=content.content,
            urls=urls,
            date=content.createdAt.strftime(
                "%Y-%m-%d"
            ),  # Stash expects YYYY-MM-DD format
            # created_at and updated_at handled by Stash
            organized=True,  # Mark as organized since we have metadata
        )

        # Add relationships
        if performer:
            gallery.performers = [performer]
        if studio:
            gallery.studio = studio

        return gallery

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
        input_class = (
            GalleryCreateInput
            if not hasattr(self, "id") or self.id == "new"
            else GalleryUpdateInput
        )
        input_obj = input_class(**data)
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
        input_obj = GalleryUpdateInput(**data)
        return {
            k: v
            for k, v in vars(input_obj).items()
            if not k.startswith("_") and v is not None and k != "client_mutation_id"
        }

    __relationships__ = {
        # Standard ID relationships
        "studio": ("studio_id", False),  # (target_field, is_list)
        "performers": ("performer_ids", True),
        "tags": ("tag_ids", True),
        "scenes": ("scene_ids", True),
    }


@strawberry.input
class BulkUpdateStrings:
    """Input for bulk string updates."""

    values: list[str]  # [String!]!
    mode: BulkUpdateIdMode  # BulkUpdateIdMode!


@strawberry.input
class BulkUpdateIds:
    """Input for bulk ID updates."""

    ids: list[ID]  # [ID!]!
    mode: BulkUpdateIdMode  # BulkUpdateIdMode!


@strawberry.input
class GalleryCreateInput:
    """Input for creating galleries."""

    # Required fields
    title: str  # String!

    # Optional fields
    code: str | None = None  # String
    url: str | None = None  # String @deprecated
    urls: list[str] | None = None  # [String!]
    date: str | None = None  # String
    details: str | None = None  # String
    photographer: str | None = None  # String
    rating100: int | None = None  # Int
    organized: bool | None = None  # Boolean
    scene_ids: list[ID] | None = None  # [ID!]
    studio_id: ID | None = None  # ID
    tag_ids: list[ID] | None = None  # [ID!]
    performer_ids: list[ID] | None = None  # [ID!]


@strawberry.input
class GalleryUpdateInput:
    """Input for updating galleries."""

    # Required fields
    id: ID  # ID!

    # Optional fields
    client_mutation_id: str | None = None  # String
    title: str | None = None  # String
    code: str | None = None  # String
    url: str | None = None  # String @deprecated
    urls: list[str] | None = None  # [String!]
    date: str | None = None  # String
    details: str | None = None  # String
    photographer: str | None = None  # String
    rating100: int | None = None  # Int
    organized: bool | None = None  # Boolean
    scene_ids: list[ID] | None = None  # [ID!]
    studio_id: ID | None = None  # ID
    tag_ids: list[ID] | None = None  # [ID!]
    performer_ids: list[ID] | None = None  # [ID!]
    primary_file_id: ID | None = None  # ID


@strawberry.input
class GalleryAddInput:
    """Input for adding images to gallery."""

    gallery_id: ID  # ID!
    image_ids: list[ID]  # [ID!]!


@strawberry.input
class GalleryRemoveInput:
    """Input for removing images from gallery."""

    gallery_id: ID  # ID!
    image_ids: list[ID]  # [ID!]!


@strawberry.input
class GallerySetCoverInput:
    """Input for setting gallery cover."""

    gallery_id: ID  # ID!
    cover_image_id: ID  # ID!


@strawberry.input
class GalleryResetCoverInput:
    """Input for resetting gallery cover."""

    gallery_id: ID  # ID!


@strawberry.input
class GalleryDestroyInput:
    """Input for destroying galleries.

    If delete_file is true, then the zip file will be deleted if the gallery is zip-file-based.
    If gallery is folder-based, then any files not associated with other galleries will be
    deleted, along with the folder, if it is not empty."""

    ids: list[ID]  # [ID!]!
    delete_file: bool | None = None  # Boolean
    delete_generated: bool | None = None  # Boolean


@strawberry.input
class BulkGalleryUpdateInput:
    """Input for bulk updating galleries."""

    # Optional fields
    client_mutation_id: str | None = None  # String
    ids: list[ID]  # [ID!]!
    code: str | None = None  # String
    url: str | None = None  # String @deprecated
    urls: BulkUpdateStrings | None = None  # BulkUpdateStrings
    date: str | None = None  # String
    details: str | None = None  # String
    photographer: str | None = None  # String
    rating100: int | None = None  # Int (1-100)
    organized: bool | None = None  # Boolean
    scene_ids: BulkUpdateIds | None = None  # BulkUpdateIds
    studio_id: ID | None = None  # ID
    tag_ids: BulkUpdateIds | None = None  # BulkUpdateIds
    performer_ids: BulkUpdateIds | None = None  # BulkUpdateIds


@strawberry.type
class FindGalleriesResultType:
    """Result type for finding galleries."""

    count: int  # Int!
    galleries: list[Gallery]  # [Gallery!]!
