"""Performer type for Stash."""

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import strawberry
from strawberry import ID, lazy

from metadata.account import Account

from .base import StashObject
from .enums import CircumisedEnum, GenderEnum
from .files import StashID, StashIDInput
from .metadata import CustomFieldsInput


if TYPE_CHECKING:
    from ..client import StashClient
    from .group import Group
    from .scene import Scene
    from .tag import Tag


@strawberry.input
class PerformerCreateInput:
    """Input for creating performers."""

    # Required fields
    name: str  # String!

    # Optional fields
    disambiguation: str | None = None  # String
    url: str | None = None  # String @deprecated
    urls: list[str] | None = None  # [String!]
    gender: GenderEnum | None = None  # GenderEnum
    birthdate: str | None = None  # String
    ethnicity: str | None = None  # String
    country: str | None = None  # String
    eye_color: str | None = None  # String
    height_cm: int | None = None  # Int
    measurements: str | None = None  # String
    fake_tits: str | None = None  # String
    penis_length: float | None = None  # Float
    circumcised: CircumisedEnum | None = None  # CircumisedEnum
    career_length: str | None = None  # String
    tattoos: str | None = None  # String
    piercings: str | None = None  # String
    alias_list: list[str] | None = None  # [String!]
    twitter: str | None = None  # String @deprecated
    instagram: str | None = None  # String @deprecated
    favorite: bool | None = None  # Boolean
    tag_ids: list[ID] | None = None  # [ID!]
    image: str | None = None  # String (URL or base64)
    stash_ids: list[StashIDInput] | None = None  # [StashIDInput!]
    rating100: int | None = None  # Int
    details: str | None = None  # String
    death_date: str | None = None  # String
    hair_color: str | None = None  # String
    weight: int | None = None  # Int
    ignore_auto_tag: bool | None = None  # Boolean
    custom_fields: dict[str, Any] | None = None  # Map


@strawberry.input
class PerformerUpdateInput:
    """Input for updating performers."""

    # Required fields
    id: ID  # ID!

    # Optional fields
    name: str | None = None  # String
    disambiguation: str | None = None  # String
    url: str | None = None  # String @deprecated
    urls: list[str] | None = None  # [String!]
    gender: GenderEnum | None = None  # GenderEnum
    birthdate: str | None = None  # String
    ethnicity: str | None = None  # String
    country: str | None = None  # String
    eye_color: str | None = None  # String
    height_cm: int | None = None  # Int
    measurements: str | None = None  # String
    fake_tits: str | None = None  # String
    penis_length: float | None = None  # Float
    circumcised: CircumisedEnum | None = None  # CircumisedEnum
    career_length: str | None = None  # String
    tattoos: str | None = None  # String
    piercings: str | None = None  # String
    alias_list: list[str] | None = None  # [String!]
    twitter: str | None = None  # String @deprecated
    instagram: str | None = None  # String @deprecated
    favorite: bool | None = None  # Boolean
    tag_ids: list[ID] | None = None  # [ID!]
    image: str | None = None  # String (URL or base64)
    stash_ids: list[StashIDInput] | None = None  # [StashIDInput!]
    rating100: int | None = None  # Int
    details: str | None = None  # String
    death_date: str | None = None  # String
    hair_color: str | None = None  # String
    weight: int | None = None  # Int
    ignore_auto_tag: bool | None = None  # Boolean
    custom_fields: CustomFieldsInput | None = None  # CustomFieldsInput


@strawberry.type
class Performer(StashObject):
    """Performer type from schema/types/performer.graphql."""

    __type_name__ = "Performer"
    __update_input_type__ = PerformerUpdateInput
    __create_input_type__ = PerformerCreateInput

    # Fields to track for changes - only fields that can be written via input types
    __tracked_fields__ = {
        "name",  # PerformerCreateInput/PerformerUpdateInput
        "alias_list",  # PerformerCreateInput/PerformerUpdateInput
        "tags",  # mapped to tag_ids
        "disambiguation",  # PerformerCreateInput/PerformerUpdateInput
        "urls",  # PerformerCreateInput/PerformerUpdateInput
        "gender",  # PerformerCreateInput/PerformerUpdateInput
        "birthdate",  # PerformerCreateInput/PerformerUpdateInput
        "ethnicity",  # PerformerCreateInput/PerformerUpdateInput
        "country",  # PerformerCreateInput/PerformerUpdateInput
        "eye_color",  # PerformerCreateInput/PerformerUpdateInput
        "height_cm",  # PerformerCreateInput/PerformerUpdateInput
        "measurements",  # PerformerCreateInput/PerformerUpdateInput
        "fake_tits",  # PerformerCreateInput/PerformerUpdateInput
        "penis_length",  # PerformerCreateInput/PerformerUpdateInput
        "circumcised",  # PerformerCreateInput/PerformerUpdateInput
        "career_length",  # PerformerCreateInput/PerformerUpdateInput
        "tattoos",  # PerformerCreateInput/PerformerUpdateInput
        "piercings",  # PerformerCreateInput/PerformerUpdateInput
        "details",  # PerformerCreateInput/PerformerUpdateInput
        "death_date",  # PerformerCreateInput/PerformerUpdateInput
        "hair_color",  # PerformerCreateInput/PerformerUpdateInput
        "weight",  # PerformerCreateInput/PerformerUpdateInput
    }

    # Required fields from schema
    name: str  # String!
    alias_list: list[str] = strawberry.field(default_factory=list)  # [String!]!
    tags: list[Annotated["Tag", lazy("stash.types.tag.Tag")]] = strawberry.field(
        default_factory=list
    )  # [Tag!]!
    stash_ids: list[StashID] = strawberry.field(default_factory=list)  # [StashID!]!
    scenes: list[Annotated["Scene", lazy("stash.types.scene.Scene")]] = (
        strawberry.field(default_factory=list)
    )  # [Scene!]!
    groups: list[Annotated["Group", lazy("stash.types.group.Group")]] = (
        strawberry.field(default_factory=list)
    )  # [Group!]!

    # Optional fields from schema
    disambiguation: str | None = None  # String
    urls: list[str] = strawberry.field(default_factory=list)  # [String!]
    gender: GenderEnum | None = None  # GenderEnum
    birthdate: str | None = None  # String
    ethnicity: str | None = None  # String
    country: str | None = None  # String
    eye_color: str | None = None  # String
    height_cm: int | None = None  # Int
    measurements: str | None = None  # String
    fake_tits: str | None = None  # String
    penis_length: float | None = None  # Float
    circumcised: CircumisedEnum | None = None  # CircumisedEnum
    career_length: str | None = None  # String
    tattoos: str | None = None  # String
    piercings: str | None = None  # String
    image_path: str | None = None  # String (Resolver)
    details: str | None = None  # String
    death_date: str | None = None  # String
    hair_color: str | None = None  # String
    weight: int | None = None  # Int

    async def update_avatar(
        self, client: "StashClient", image_path: str | Path
    ) -> "Performer":
        """Update performer's avatar image.

        Args:
            client: StashClient instance to use for update
            image_path: Path to image file to use as avatar

        Returns:
            Updated Performer object with the new image

        Raises:
            FileNotFoundError: If image file doesn't exist
            ValueError: If image file can't be read or update fails
        """
        # Convert path to Path object
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image file not found: {path}")

        try:
            # Read and encode image (use asyncio.to_thread for blocking I/O)
            image_data = await asyncio.to_thread(path.read_bytes)
            image_b64 = base64.b64encode(image_data).decode("utf-8")
            mime = mimetypes.types_map.get(path.suffix, "image/jpeg")
            image_url = f"data:{mime};base64,{image_b64}"

            # Use client's direct method for updating image
            return await client.update_performer_image(self, image_url)

        except Exception as e:
            raise ValueError(f"Failed to update avatar: {e}") from e

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Performer":
        """Create performer from dictionary.

        Args:
            data: Dictionary containing performer data

        Returns:
            New performer instance
        """
        # Filter out fields that aren't part of our class
        try:
            valid_fields = {
                field.name
                for field in cls.__strawberry_definition__.fields  # type: ignore[attr-defined]
            }
            filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        except AttributeError:
            # Fallback if strawberry definition is not available
            filtered_data = data

        # created_at and updated_at handled by Stash

        # Create instance
        performer = cls(**filtered_data)

        # Convert lists
        if "stash_ids" in filtered_data:
            performer.stash_ids = [StashID(**s) for s in filtered_data["stash_ids"]]

        return performer

    @classmethod
    def from_account(cls, account: Account) -> "Performer":
        """Create performer from account.

        Args:
            account: Account to convert

        Returns:
            New performer instance
        """
        # Ensure we have a name (fallback to "Unknown" if all are None)
        performer_name = account.displayName or account.username or "Unknown"

        # Handle alias list with proper None checking
        alias_list = []
        if (
            account.displayName is not None
            and account.username is not None
            and account.displayName.lower() != account.username.lower()
        ):
            alias_list = [account.username]

        return cls(
            id="new",  # Will be replaced on save
            name=performer_name,
            alias_list=alias_list,  # Only add username as alias if using display_name and it's different (case-insensitive)
            urls=(
                [f"https://fansly.com/{account.username}/posts"]
                if account.username
                else []
            ),
            country="",
            details=account.about or "",
            # Required fields with defaults
            tags=[],  # Empty list of tags to start
            scenes=[],
            groups=[],  # Required relationship
            stash_ids=[],  # Required relationship
        )

    # Field definitions with their conversion functions
    __field_conversions__ = {
        "name": str,
        "disambiguation": str,
        "urls": list,
        "gender": lambda g: g.value if g else None,
        "birthdate": str,
        "ethnicity": str,
        "country": str,
        "eye_color": str,
        "height_cm": int,
        "measurements": str,
        "fake_tits": str,
        "penis_length": float,
        "circumcised": lambda c: c.value if c else None,
        "career_length": str,
        "tattoos": str,
        "piercings": str,
        "alias_list": list,
        "details": str,
        "death_date": str,
        "hair_color": str,
        "weight": int,
    }

    __relationships__ = {
        # Standard ID relationships
        "tags": ("tag_ids", True, None),  # (target_field, is_list, transform)
        # Special case with custom transform
        "stash_ids": (
            "stash_ids",
            True,
            lambda s: StashIDInput(endpoint=s.endpoint, stash_id=s.stash_id),
        ),
    }


@strawberry.type
class FindPerformersResultType:
    """Result type for finding performers from schema/types/performer.graphql."""

    count: int  # Int!
    performers: list[Performer]  # [Performer!]!
