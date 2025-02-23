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

    # Fields to track for changes
    __tracked_fields__ = {
        "name",
        "aliases",
        "ignore_auto_tag",
        "favorite",
        "parents",
        "children",
        "description",
    }

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

    # Field definitions with their conversion functions
    __field_conversions__ = {
        "name": str,
        "description": str,
        "aliases": list,
        "ignore_auto_tag": bool,
        "favorite": bool,
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
            TagCreateInput
            if not hasattr(self, "id") or self.id == "new"
            else TagUpdateInput
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
        input_obj = TagUpdateInput(**data)
        return {
            k: v
            for k, v in vars(input_obj).items()
            if not k.startswith("_") and v is not None and k != "client_mutation_id"
        }

    __relationships__ = {
        # Standard ID relationships
        "parents": ("parent_ids", True),  # (target_field, is_list)
        "children": ("child_ids", True),
    }


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
