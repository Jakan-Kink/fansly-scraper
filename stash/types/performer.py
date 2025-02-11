"""Performer type for Stash."""

import base64
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, List, Optional

import strawberry
from strawberry import ID, lazy

from metadata import Account

from .base import StashObject
from .enums import CircumisedEnum, GenderEnum
from .files import StashID, StashIDInput
from .metadata import CustomFieldsInput

if TYPE_CHECKING:
    from ..client import StashClient
    from .group import Group
    from .scene import Scene
    from .tag import Tag


@strawberry.type
class Performer(StashObject):
    """Performer type from schema/types/performer.graphql."""

    __type_name__ = "Performer"

    # Required fields from schema
    name: str  # String!
    alias_list: list[str] = strawberry.field(default_factory=list)  # [String!]!
    favorite: bool = False  # Boolean!
    tags: list[Annotated["Tag", lazy("stash.types.tag.Tag")]] = strawberry.field(
        default_factory=list
    )  # [Tag!]!
    ignore_auto_tag: bool = False  # Boolean!
    scene_count: int = 0  # Int! (Resolver)
    image_count: int = 0  # Int! (Resolver)
    gallery_count: int = 0  # Int! (Resolver)
    group_count: int = 0  # Int! (Resolver)
    performer_count: int = 0  # Int! (Resolver)
    scenes: list[Annotated["Scene", lazy("stash.types.scene.Scene")]] = (
        strawberry.field(default_factory=list)
    )  # [Scene!]!
    stash_ids: list[StashID] = strawberry.field(default_factory=list)  # [StashID!]!

    # Optional fields from schema
    disambiguation: str | None = None  # String
    url: str | None = None  # String @deprecated(reason: "Use urls")
    urls: list[str] = strawberry.field(default_factory=list)  # [String!]
    gender: GenderEnum | None = None  # GenderEnum
    twitter: str | None = None  # String @deprecated(reason: "Use urls")
    instagram: str | None = None  # String @deprecated(reason: "Use urls")
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
    o_counter: int | None = None  # Int (Resolver)
    rating100: int | None = None  # Int (1-100)
    details: str | None = None  # String
    death_date: str | None = None  # String
    hair_color: str | None = None  # String
    weight: int | None = None  # Int
    groups: list["Group"] = strawberry.field(default_factory=list)  # [Group!]!

    # Custom fields (Map type in schema)
    custom_fields: dict[str, Any] = strawberry.field(default_factory=dict)  # Map!

    async def update_avatar(
        self, client: "StashClient", image_path: str | Path
    ) -> None:
        """Update performer's avatar image.

        Args:
            client: StashClient instance to use for update
            image_path: Path to image file to use as avatar

        Raises:
            FileNotFoundError: If image file doesn't exist
            ValueError: If image file can't be read or update fails
        """
        # Convert path to Path object
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image file not found: {path}")

        try:
            # Read and encode image
            with open(path, "rb") as f:
                image_data = f.read()
            image_b64 = base64.b64encode(image_data).decode("utf-8")
            mime = mimetypes.types_map.get(path.suffix, "image/jpeg")
            image_url = f"data:{mime};base64,{image_b64}"

            # Create update input with just ID and image
            update_input = {"id": self.id, "image": image_url}

            # Update performer
            await client.update_performer(self.__class__(**update_input))

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
        valid_fields = {field.name for field in cls.__strawberry_definition__.fields}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}

        # created_at and updated_at handled by Stash

        # Create instance
        performer = cls(**filtered_data)

        # Convert lists
        if "stash_ids" in filtered_data:
            performer.stash_ids = [StashID(**s) for s in filtered_data["stash_ids"]]

        return performer

    @classmethod
    async def from_account(cls, account: Account) -> "Performer":
        """Create performer from account.

        Args:
            account: Account to convert

        Returns:
            New performer instance
        """
        return cls(
            id="new",  # Will be replaced on save
            name=account.displayName or account.username,
            alias_list=(
                [account.username]
                if (
                    account.displayName
                    and account.displayName.lower() != account.username.lower()
                )
                else []
            ),  # Only add username as alias if using displayName and it's different (case-insensitive)
            urls=[f"https://fansly.com/{account.username}/posts"],
            country=account.location,
            details=account.about,
            # created_at and updated_at handled by Stash
            # Required fields with defaults
            favorite=False,  # Default to not favorite
            tags=[],  # Empty list of tags to start
            ignore_auto_tag=False,  # Allow auto-tagging
            scene_count=0,
            image_count=0,
            gallery_count=0,
            group_count=0,
            performer_count=0,
            scenes=[],
        )

    def to_input(self) -> dict[str, Any]:
        """Convert to GraphQL input.

        Returns:
            Dictionary of input fields for create/update
        """
        if hasattr(self, "id") and self.id != "new":
            # Update existing
            return PerformerUpdateInput(
                id=self.id,
                name=self.name,
                disambiguation=self.disambiguation,
                urls=self.urls,
                gender=self.gender,
                birthdate=self.birthdate,
                ethnicity=self.ethnicity,
                country=self.country,
                eye_color=self.eye_color,
                height_cm=self.height_cm,
                measurements=self.measurements,
                fake_tits=self.fake_tits,
                penis_length=self.penis_length,
                circumcised=self.circumcised,
                career_length=self.career_length,
                tattoos=self.tattoos,
                piercings=self.piercings,
                alias_list=self.alias_list,
                favorite=self.favorite,
                tag_ids=[t.id for t in self.tags],
                rating100=self.rating100,
                details=self.details,
                death_date=self.death_date,
                hair_color=self.hair_color,
                weight=self.weight,
                ignore_auto_tag=self.ignore_auto_tag,
                stash_ids=[
                    StashID(endpoint=s.endpoint, stash_id=s.stash_id)
                    for s in self.stash_ids
                ],
                custom_fields=self.custom_fields,
            ).__dict__
        else:
            # Create new
            return PerformerCreateInput(
                name=self.name,
                disambiguation=self.disambiguation,
                urls=self.urls,
                gender=self.gender,
                birthdate=self.birthdate,
                ethnicity=self.ethnicity,
                country=self.country,
                eye_color=self.eye_color,
                height_cm=self.height_cm,
                measurements=self.measurements,
                fake_tits=self.fake_tits,
                penis_length=self.penis_length,
                circumcised=self.circumcised,
                career_length=self.career_length,
                tattoos=self.tattoos,
                piercings=self.piercings,
                alias_list=self.alias_list,
                favorite=self.favorite,
                tag_ids=[t.id for t in self.tags],
                rating100=self.rating100,
                details=self.details,
                death_date=self.death_date,
                hair_color=self.hair_color,
                weight=self.weight,
                ignore_auto_tag=self.ignore_auto_tag,
                stash_ids=[
                    StashID(endpoint=s.endpoint, stash_id=s.stash_id)
                    for s in self.stash_ids
                ],
                custom_fields=self.custom_fields,
            ).__dict__


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
class FindPerformersResultType:
    """Result type for finding performers from schema/types/performer.graphql."""

    count: int  # Int!
    performers: list[Performer]  # [Performer!]!
