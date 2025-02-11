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

    def to_input(self) -> dict[str, Any]:
        """Convert to GraphQL input.

        Returns:
            Dictionary of input fields for create/update
        """
        if hasattr(self, "id") and self.id != "new":
            # Update existing
            return StudioUpdateInput(
                id=self.id,
                name=self.name,
                url=self.url,
                parent_id=self.parent_studio.id if self.parent_studio else None,
                aliases=self.aliases,
                ignore_auto_tag=self.ignore_auto_tag,
                image=None,  # Set if needed
                stash_ids=[
                    StashID(endpoint=s.endpoint, stash_id=s.stash_id)
                    for s in self.stash_ids
                ],
                rating100=self.rating100,
                details=self.details,
            ).__dict__
        else:
            # Create new
            return StudioCreateInput(
                name=self.name,
                url=self.url,
                parent_id=self.parent_studio.id if self.parent_studio else None,
                aliases=self.aliases,
                ignore_auto_tag=self.ignore_auto_tag,
                image=None,  # Set if needed
                stash_ids=[
                    StashID(endpoint=s.endpoint, stash_id=s.stash_id)
                    for s in self.stash_ids
                ],
                rating100=self.rating100,
                details=self.details,
            ).__dict__


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
