"""Studio type from schema/types/studio.graphql."""

from typing import TYPE_CHECKING, Annotated

import strawberry
from strawberry import ID, lazy

from metadata import Account

from .base import StashObject
from .files import StashID, StashIDInput

if TYPE_CHECKING:
    from .tag import Tag


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
class Studio(StashObject):
    """Studio type from schema/types/studio.graphql."""

    __type_name__ = "Studio"
    __update_input_type__ = StudioUpdateInput
    __create_input_type__ = StudioCreateInput

    # Fields to track for changes - only fields that can be written via input types
    __tracked_fields__ = {
        "name",  # StudioCreateInput/StudioUpdateInput
        "aliases",  # StudioCreateInput/StudioUpdateInput
        "tags",  # mapped to tag_ids
        "stash_ids",  # StudioCreateInput/StudioUpdateInput
        "url",  # StudioCreateInput/StudioUpdateInput
        "parent_studio",  # mapped to parent_id
        "details",  # StudioCreateInput/StudioUpdateInput
    }

    # Required fields
    name: str  # String!
    aliases: list[str] = strawberry.field(default_factory=list)  # [String!]!
    tags: list[Annotated["Tag", lazy("stash.types.tag.Tag")]] = strawberry.field(
        default_factory=list
    )  # [Tag!]!
    stash_ids: list[StashID] = strawberry.field(default_factory=list)  # [StashID!]!

    # Optional fields
    url: str | None = None  # String
    parent_studio: Annotated["Studio", lazy("stash.types.studio.Studio")] | None = (
        None  # Studio
    )
    details: str | None = None  # String
    image_path: str | None = None  # String (Resolver)

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
            name=account.displayName or account.username or "Unknown",
            url=f"https://fansly.com/{account.username}",
            details=account.about,
        )

    # Field definitions with their conversion functions
    __field_conversions__ = {
        "name": str,
        "url": str,
        "aliases": list,
        "details": str,
    }

    __relationships__ = {
        # Standard ID relationships
        "parent_studio": (
            "parent_id",
            False,
            None,
        ),  # (target_field, is_list, transform)
        "tags": ("tag_ids", True, None),
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


@strawberry.type
class FindStudiosResultType:
    """Result type for finding studios from schema/types/studio.graphql."""

    count: int  # Int!
    studios: list[Studio]  # [Studio!]!
