"""Image types from schema/types/image.graphql."""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, List, Optional

import strawberry
from strawberry import ID, lazy

from metadata import Media

from .base import StashObject
from .files import ImageFile, VisualFile
from .inputs import ImageUpdateInput

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
    async def from_media(
        cls,
        media: Media,
        performer: (
            Annotated["Performer", lazy("stash.types.performer.Performer")] | None
        ) = None,
        studio: Annotated["Studio", lazy("stash.types.studio.Studio")] | None = None,
    ) -> "Image":
        """Create image from media.

        Args:
            media: Media to convert
            performer: Optional performer to associate
            studio: Optional studio to associate

        Returns:
            New image instance
        """
        # Build image
        image = cls(
            id="new",  # Will be replaced on save
            title=media.local_filename,
            date=media.createdAt,
            created_at=media.createdAt or datetime.now(),
            updated_at=datetime.now(),
            organized=True,  # Mark as organized since we have metadata
        )

        # Add relationships
        if performer:
            image.performers = [performer]
        if studio:
            image.studio = studio

        return image

    def to_input(self) -> dict[str, Any]:
        """Convert to GraphQL input.

        Returns:
            Dictionary of input fields
        """
        data = {}

        # Add optional fields if set
        if self.title:
            data["title"] = self.title
        if self.url:
            data["url"] = self.url
        if self.date:
            data["date"] = self.date.isoformat()
        if self.rating100 is not None:
            data["rating100"] = self.rating100
        if self.organized is not None:
            data["organized"] = self.organized

        # Add relationships
        if self.studio:
            data["studio_id"] = self.studio.id
        if self.performers:
            data["performer_ids"] = [p.id for p in self.performers]
        if self.tags:
            data["tag_ids"] = [t.id for t in self.tags]

        # Add ID for updates
        if hasattr(self, "id") and self.id != "new":
            data["id"] = self.id

        return data


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


@strawberry.type
class FindImagesResultType:
    """Result type for finding images from schema/types/image.graphql."""

    count: int  # Int!
    megapixels: float  # Float! (Total megapixels of the images)
    filesize: float  # Float! (Total file size in bytes)
    images: list[Annotated["Image", lazy("stash.types.image.Image")]] = (
        strawberry.field(default_factory=list)
    )  # [Image!]!
