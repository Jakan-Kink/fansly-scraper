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

    # Required fields
    gallery: Annotated["Gallery", lazy("stash.types.gallery.Gallery")]  # Gallery!
    title: str  # String!
    image_index: int  # Int!

    def to_input(self) -> dict[str, Any]:
        """Convert to GraphQL input.

        Returns:
            Dictionary of input fields for create/update
        """
        if hasattr(self, "id") and self.id != "new":
            # Update existing
            return GalleryChapterUpdateInput(
                id=self.id,
                gallery_id=self.gallery.id,
                title=self.title,
                image_index=self.image_index,
            ).__dict__
        else:
            # Create new
            return GalleryChapterCreateInput(
                gallery_id=self.gallery.id,
                title=self.title,
                image_index=self.image_index,
            ).__dict__


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

    async def to_input(self) -> dict[str, Any]:
        """Convert to GraphQL input.

        Returns:
            Dictionary of input fields for create/update
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
                        pass

        # Add ID if this is an update
        if hasattr(self, "id") and self.id != "new":
            data["id"] = self.id

        # Helper function to get ID from object or dict
        async def get_id(obj: Any) -> str | None:
            if isinstance(obj, dict):
                return obj.get("id")
            if hasattr(obj, "awaitable_attrs"):
                await obj.awaitable_attrs.id
                return obj.id
            return getattr(obj, "id", None)

        # Process relationships
        relationships = {
            # Standard ID relationships
            "studio": ("studio_id", False),  # (target_field, is_list)
            "performers": ("performer_ids", True),
            "tags": ("tag_ids", True),
            "scenes": ("scene_ids", True),
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

        # Create input object based on operation type
        input_class = GalleryUpdateInput if "id" in data else GalleryCreateInput
        input_obj = input_class(**data)
        return {
            k: v
            for k, v in vars(input_obj).items()
            if not k.startswith("_") and v is not None and k != "client_mutation_id"
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
