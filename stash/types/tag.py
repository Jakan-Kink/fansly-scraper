"""Tag type from schema/types/tag.graphql."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional

import strawberry
from strawberry import ID, lazy

from metadata import Hashtag

from .base import StashObject


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
class Tag(StashObject):
    """Tag type from schema/types/tag.graphql."""

    __type_name__ = "Tag"
    __update_input_type__ = TagUpdateInput
    __create_input_type__ = TagCreateInput

    # Fields to track for changes - only fields that can be written via input types
    __tracked_fields__ = {
        "name",  # TagCreateInput/TagUpdateInput
        "aliases",  # TagCreateInput/TagUpdateInput
        "description",  # TagCreateInput/TagUpdateInput
        "parents",  # mapped to parent_ids
        "children",  # mapped to child_ids
    }

    # Required fields
    name: str  # String!
    aliases: list[str] = strawberry.field(default_factory=list)  # [String!]!
    parents: list["Tag"] = strawberry.field(default_factory=list)  # [Tag!]!
    children: list["Tag"] = strawberry.field(default_factory=list)  # [Tag!]!

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
            name=hashtag.value,
            # Set reasonable defaults
        )

    # Field definitions with their conversion functions
    __field_conversions__ = {
        "name": str,
        "description": str,
        "aliases": list,
    }

    __relationships__ = {
        # Standard ID relationships
        "parents": ("parent_ids", True, None),  # (target_field, is_list, transform)
        "children": ("child_ids", True, None),
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


@strawberry.type
class FindTagsResultType:
    """Result type for finding tags from schema/types/tag.graphql."""

    count: int  # Int!
    tags: list[Tag]  # [Tag!]!
