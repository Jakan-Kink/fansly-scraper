"""Tag type from schema/types/tag.graphql."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional

import strawberry
from strawberry import ID, lazy

from metadata import Hashtag

from .base import StashObject


@strawberry.type
class Tag(StashObject):
    """Tag type from schema/types/tag.graphql."""

    __type_name__ = "Tag"

    # Required fields
    name: str  # String!
    aliases: list[str] = strawberry.field(default_factory=list)  # [String!]!
    ignore_auto_tag: bool = False  # Boolean!
    favorite: bool = False  # Boolean!
    scene_count: int = 0  # Int! (Resolver)
    scene_marker_count: int = 0  # Int! (Resolver)
    image_count: int = 0  # Int! (Resolver)
    gallery_count: int = 0  # Int! (Resolver)
    performer_count: int = 0  # Int! (Resolver)
    studio_count: int = 0  # Int! (Resolver)
    group_count: int = 0  # Int! (Resolver)
    movie_count: int = (
        0  # Int! @deprecated(reason: "use group_count instead") (Resolver)
    )
    parents: list["Tag"] = strawberry.field(default_factory=list)  # [Tag!]!
    children: list["Tag"] = strawberry.field(default_factory=list)  # [Tag!]!
    parent_count: int = 0  # Int! (Resolver)
    child_count: int = 0  # Int! (Resolver)

    # Optional fields
    description: str | None = None  # String
    image_path: str | None = None  # String (Resolver)

    @classmethod
    async def from_hashtag(cls, hashtag: Hashtag) -> "Tag":
        """Create tag from hashtag.

        Args:
            hashtag: Hashtag to convert

        Returns:
            New tag instance
        """
        return cls(
            id="new",  # Will be replaced on save
            name=hashtag.name,
            createdAt=datetime.now(),
            updatedAt=datetime.now(),
            # Set reasonable defaults
            ignore_auto_tag=False,  # Allow auto-tagging
            scene_count=0,
            scene_marker_count=0,
            image_count=0,
            gallery_count=0,
            performer_count=0,
            studio_count=0,
            parent_count=0,
            child_count=0,
        )

    def to_input(self) -> dict[str, Any]:
        """Convert to GraphQL input.

        Returns:
            Dictionary of input fields for create/update
        """
        if hasattr(self, "id") and self.id != "new":
            # Update existing
            return TagUpdateInput(
                id=self.id,
                name=self.name,
                description=self.description,
                aliases=self.aliases,
                ignore_auto_tag=self.ignore_auto_tag,
                image=None,  # Set if needed
                parent_ids=[p.id for p in self.parents],
                child_ids=[c.id for c in self.children],
            ).__dict__
        else:
            # Create new
            return TagCreateInput(
                name=self.name,
                description=self.description,
                aliases=self.aliases,
                ignore_auto_tag=self.ignore_auto_tag,
                image=None,  # Set if needed
                parent_ids=[p.id for p in self.parents],
                child_ids=[c.id for c in self.children],
            ).__dict__


@strawberry.input
class TagDestroyInput:
    """Input for destroying a tag from schema/types/tag.graphql."""

    id: ID  # ID!


@strawberry.input
class TagsMergeInput:
    """Input for merging tags from schema/types/tag.graphql."""

    source: list[ID]  # [ID!]!
    destination: ID  # ID!


@strawberry.input
class BulkTagUpdateInput:
    """Input for bulk updating tags from schema/types/tag.graphql."""

    ids: list[ID]  # [ID!]!
    description: str | None = None  # String
    aliases: list[str] | None = None  # [String!]
    ignore_auto_tag: bool | None = None  # Boolean
    favorite: bool | None = None  # Boolean
    parent_ids: list[ID] | None = None  # [ID!]
    child_ids: list[ID] | None = None  # [ID!]


@strawberry.input
class TagCreateInput:
    """Input for creating tags."""

    # Required fields
    name: str  # String!

    # Optional fields
    description: str | None = None  # String
    aliases: list[str] | None = None  # [String!]
    ignore_auto_tag: bool | None = None  # Boolean
    image: str | None = None  # String (URL or base64)
    parent_ids: list[ID] | None = None  # [ID!]
    child_ids: list[ID] | None = None  # [ID!]


@strawberry.input
class TagUpdateInput:
    """Input for updating tags."""

    # Required fields
    id: ID  # ID!

    # Optional fields
    name: str | None = None  # String
    description: str | None = None  # String
    aliases: list[str] | None = None  # [String!]
    ignore_auto_tag: bool | None = None  # Boolean
    image: str | None = None  # String (URL or base64)
    parent_ids: list[ID] | None = None  # [ID!]
    child_ids: list[ID] | None = None  # [ID!]


@strawberry.type
class FindTagsResultType:
    """Result type for finding tags from schema/types/tag.graphql."""

    count: int  # Int!
    tags: list[Tag]  # [Tag!]!
