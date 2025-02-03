"""Gallery types from schema/types/gallery.graphql and gallery-chapter.graphql."""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, List, Optional

import strawberry
from strawberry import ID, lazy

from metadata import Media, Message, Post

from .base import StashObject
from .files import Folder, GalleryFile
from .inputs import GalleryCreateInput, GalleryUpdateInput

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
    created_at: datetime  # Time!
    updated_at: datetime  # Time!


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
            title=(
                content.content[:100] if content.content else None
            ),  # Truncate long content
            details=content.content,
            urls=urls,
            date=content.createdAt.isoformat(),
            created_at=content.createdAt or datetime.now(),
            updated_at=datetime.now(),
            organized=True,  # Mark as organized since we have metadata
        )

        # Add relationships
        if performer:
            gallery.performers = [performer]
        if studio:
            gallery.studio = studio

        return gallery

    def to_input(self) -> dict[str, Any]:
        """Convert to GraphQL input.

        Returns:
            Dictionary of input fields for create/update
        """
        if hasattr(self, "id") and self.id != "new":
            # Update existing
            return GalleryUpdateInput(
                id=self.id,
                title=self.title,
                code=self.code,
                urls=self.urls,
                date=self.date,
                details=self.details,
                photographer=self.photographer,
                rating100=self.rating100,
                organized=self.organized,
                scene_ids=[s.id for s in self.scenes],
                studio_id=self.studio.id if self.studio else None,
                tag_ids=[t.id for t in self.tags],
                performer_ids=[p.id for p in self.performers],
            ).__dict__
        else:
            # Create new
            return GalleryCreateInput(
                title=self.title or "",  # Required by schema
                code=self.code,
                urls=self.urls,
                date=self.date,
                details=self.details,
                photographer=self.photographer,
                rating100=self.rating100,
                organized=self.organized,
                scene_ids=[s.id for s in self.scenes],
                studio_id=self.studio.id if self.studio else None,
                tag_ids=[t.id for t in self.tags],
                performer_ids=[p.id for p in self.performers],
            ).__dict__


@strawberry.type
class FindGalleriesResultType:
    """Result type for finding galleries."""

    count: int  # Int!
    galleries: list[Gallery]  # [Gallery!]!
