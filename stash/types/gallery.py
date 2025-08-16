"""Gallery types from schema/types/gallery.graphql and gallery-chapter.graphql."""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, List, Optional

import strawberry
from strawberry import ID, lazy

from metadata import Media, Message, Post

from .base import StashObject
from .enums import BulkUpdateIdMode
from .files import Folder, GalleryFile
from .image import Image

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
class GalleryChapter(StashObject):
    """Gallery chapter type from schema/types/gallery-chapter.graphql.

    Note: Inherits from StashObject since it has id, created_at, and updated_at
    fields in the schema, matching the common pattern."""

    __type_name__ = "GalleryChapter"
    __update_input_type__ = GalleryChapterUpdateInput
    __create_input_type__ = GalleryChapterCreateInput

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

    __relationships__ = {
        # Standard ID relationships
        "gallery": ("gallery_id", False, None),  # (target_field, is_list, transform)
    }


@strawberry.type
class FindGalleryChaptersResultType:
    """Result type for finding gallery chapters."""

    count: int  # Int!
    chapters: list[GalleryChapter]  # [GalleryChapter!]!


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


@strawberry.type
class Gallery(StashObject):
    """Gallery type from schema/types/gallery.graphql."""

    __type_name__ = "Gallery"
    __update_input_type__ = GalleryUpdateInput
    __create_input_type__ = GalleryCreateInput

    # Fields to track for changes
    __tracked_fields__ = {
        "title",
        "code",
        "date",
        "details",
        "photographer",
        "rating100",
        "url",  # Deprecated but still needed
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
    url: str | None = None  # Deprecated, but needed for compatibility

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
        url = ""
        urls = []
        if isinstance(content, Post):
            url = f"https://fansly.com/post/{content.id}"
            urls = [url]
        elif isinstance(content, Message):
            url = f"https://fansly.com/message/{content.id}"
            urls = [url]

        # Build gallery
        gallery = cls(
            id="new",  # Will be replaced on save
            title=f"{studio.name} - {content.id}" if studio else str(content.id),
            details=content.content,
            url=url,  # Set both url and urls for compatibility
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
        "url": str,  # Deprecated but still needed
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

    __relationships__ = {
        # Standard ID relationships
        "studio": ("studio_id", False, None),  # (target_field, is_list)
        "performers": ("performer_ids", True, None),
        "tags": ("tag_ids", True, None),
        "scenes": ("scene_ids", True, None),
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
