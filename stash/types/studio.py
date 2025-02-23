"""Studio type from schema/types/studio.graphql."""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, List, Optional

import strawberry
from strawberry import ID, lazy

from metadata import Account

from .base import StashObject
from .files import StashID, StashIDInput

if TYPE_CHECKING:
    from .group import Group
    from .tag import Tag


@strawberry.type
class Studio(StashObject):
    """Studio type from schema/types/studio.graphql."""

    __type_name__ = "Studio"

    # Fields to track for changes
    __tracked_fields__ = {
        "name",
        "aliases",
        "tags",
        "ignore_auto_tag",
        "child_studios",
        "stash_ids",
        "favorite",
        "groups",
        "url",
        "parent_studio",
        "rating100",
        "details",
    }

    # Required fields
    name: str  # String!
    aliases: list[str] = strawberry.field(default_factory=list)  # [String!]!
    tags: list[Annotated["Tag", lazy("stash.types.tag.Tag")]] = strawberry.field(
        default_factory=list
    )  # [Tag!]!
    ignore_auto_tag: bool = False  # Boolean!
    child_studios: list[Annotated["Studio", lazy("stash.types.studio.Studio")]] = (
        strawberry.field(default_factory=list)
    )  # [Studio!]!
    scene_count: int = 0  # Int! (Resolver)
    image_count: int = 0  # Int! (Resolver)
    gallery_count: int = 0  # Int! (Resolver)
    performer_count: int = 0  # Int! (Resolver)
    group_count: int = 0  # Int! (Resolver)
    stash_ids: list[StashID] = strawberry.field(default_factory=list)  # [StashID!]!
    favorite: bool = False  # Boolean!
    groups: list[Annotated["Group", lazy("stash.types.group.Group")]] = (
        strawberry.field(default_factory=list)
    )  # [Group!]!

    # Optional fields
    url: str | None = None  # String
    parent_studio: Annotated["Studio", lazy("stash.types.studio.Studio")] | None = (
        None  # Studio
    )
    image_path: str | None = None  # String (Resolver)
    rating100: int | None = None  # Int (1-100)
    details: str | None = None  # String

    @classmethod
    async def from_account(cls, account: Account) -> "Studio":
        """Create studio from account.

        Args:
            account: Account to convert

        Returns:
            New studio instance
        """
        return cls(
            id="new",  # Will be replaced on save
            name=account.displayName or account.username,
            url=f"https://fansly.com/{account.username}",
            details=account.about,
            # created_at and updated_at handled by Stash
            # Set reasonable defaults
            ignore_auto_tag=False,  # Allow auto-tagging
            favorite=True,  # Mark as favorite since it's a followed account
            scene_count=0,
            image_count=0,
            gallery_count=0,
            performer_count=0,
            group_count=0,
        )

    # Field definitions with their conversion functions
    __field_conversions__ = {
        "name": str,
        "url": str,
        "aliases": list,
        "ignore_auto_tag": bool,
        "favorite": bool,
        "rating100": int,
        "details": str,
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
            StudioCreateInput
            if not hasattr(self, "id") or self.id == "new"
            else StudioUpdateInput
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
        input_obj = StudioUpdateInput(**data)
        return {
            k: v
            for k, v in vars(input_obj).items()
            if not k.startswith("_") and v is not None and k != "client_mutation_id"
        }

    __relationships__ = {
        # Standard ID relationships
        "parent_studio": ("parent_id", False),  # (target_field, is_list)
        "tags": ("tag_ids", True),
        # Special case with custom transform
        "stash_ids": (
            "stash_ids",
            True,
            lambda s: StashIDInput(endpoint=s.endpoint, stash_id=s.stash_id),
        ),
    }


@strawberry.input
class StudioDestroyInput:
    """Input for destroying a studio from schema/types/studio.graphql."""

    id: ID  # ID!


@strawberry.input
class StudioCreateInput:
    """Input for creating studios."""

    # Required fields
    name: str  # String!

    # Optional fields
    url: str | None = None  # String
    parent_id: ID | None = None  # ID
    image: str | None = None  # String (URL or base64)
    stash_ids: list[StashIDInput] | None = None  # [StashIDInput!]
    rating100: int | None = None  # Int
    favorite: bool | None = None  # Boolean
    details: str | None = None  # String
    aliases: list[str] | None = None  # [String!]
    tag_ids: list[ID] | None = None  # [ID!]
    ignore_auto_tag: bool | None = None  # Boolean


@strawberry.input
class StudioUpdateInput:
    """Input for updating studios."""

    # Required fields
    id: ID  # ID!

    # Optional fields
    name: str | None = None  # String
    url: str | None = None  # String
    parent_id: ID | None = None  # ID
    image: str | None = None  # String (URL or base64)
    stash_ids: list[StashIDInput] | None = None  # [StashIDInput!]
    rating100: int | None = None  # Int
    favorite: bool | None = None  # Boolean
    details: str | None = None  # String
    aliases: list[str] | None = None  # [String!]
    tag_ids: list[ID] | None = None  # [ID!]
    ignore_auto_tag: bool | None = None  # Boolean


@strawberry.type
class FindStudiosResultType:
    """Result type for finding studios from schema/types/studio.graphql."""

    count: int  # Int!
    studios: list[Studio]  # [Studio!]!
