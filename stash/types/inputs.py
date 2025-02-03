"""Input types from schema."""

from datetime import datetime
from typing import Any, List, Optional

import strawberry
from strawberry import ID, lazy

from .enums import BulkUpdateIdMode, CircumisedEnum, GenderEnum


@strawberry.input
class StashIDInput:
    """Input for StashID from schema/types/stash-box.graphql."""

    endpoint: str  # String!
    stash_id: str  # String!


@strawberry.input
class BulkUpdateStrings:
    """Input for bulk string updates from schema/types/performer.graphql."""

    values: list[str]  # [String!]!
    mode: BulkUpdateIdMode  # BulkUpdateIdMode!


@strawberry.input
class BulkUpdateIds:
    """Input for bulk ID updates from schema/types/performer.graphql."""

    ids: list[ID]  # [ID!]!
    mode: BulkUpdateIdMode  # BulkUpdateIdMode!


@strawberry.input
class CustomFieldsInput:
    """Input for custom fields from schema/types/performer.graphql."""

    values: dict[str, Any]  # Map!


@strawberry.input
class PerformerCreateInput:
    """Input for creating performers from schema/types/performer.graphql."""

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
    """Input for updating performers from schema/types/performer.graphql."""

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


@strawberry.input
class SceneMovieInput:
    """Input for scene movies from schema/types/scene.graphql.

    Note: This type is deprecated in favor of SceneGroupInput."""

    movie_id: ID  # ID! @deprecated(reason: "Use groups instead")
    scene_index: int | None = None  # Int


@strawberry.input
class SceneGroupInput:
    """Input for scene groups from schema/types/scene.graphql."""

    group_id: ID  # ID!
    scene_index: int | None = None  # Int


@strawberry.input
class SceneCreateInput:
    """Input for creating scenes from schema/types/scene.graphql."""

    # All fields optional
    title: str | None = None  # String
    code: str | None = None  # String
    details: str | None = None  # String
    director: str | None = None  # String
    url: str | None = None  # String @deprecated
    urls: list[str] | None = None  # [String!]
    date: str | None = None  # String
    rating100: int | None = None  # Int
    organized: bool | None = None  # Boolean
    studio_id: ID | None = None  # ID
    gallery_ids: list[ID] | None = None  # [ID!]
    performer_ids: list[ID] | None = None  # [ID!]
    groups: list[SceneGroupInput] | None = None  # [SceneGroupInput!]
    tag_ids: list[ID] | None = None  # [ID!]
    cover_image: str | None = None  # String (URL or base64)
    stash_ids: list[StashIDInput] | None = None  # [StashIDInput!]
    file_ids: list[ID] | None = None  # [ID!]


@strawberry.input
class SceneUpdateInput:
    """Input for updating scenes from schema/types/scene.graphql."""

    # Required fields
    id: ID  # ID!

    # Optional fields
    client_mutation_id: str | None = None  # String
    title: str | None = None  # String
    code: str | None = None  # String
    details: str | None = None  # String
    director: str | None = None  # String
    url: str | None = None  # String @deprecated
    urls: list[str] | None = None  # [String!]
    date: str | None = None  # String
    rating100: int | None = None  # Int
    organized: bool | None = None  # Boolean
    studio_id: ID | None = None  # ID
    gallery_ids: list[ID] | None = None  # [ID!]
    performer_ids: list[ID] | None = None  # [ID!]
    groups: list[SceneGroupInput] | None = None  # [SceneGroupInput!]
    tag_ids: list[ID] | None = None  # [ID!]
    cover_image: str | None = None  # String (URL or base64)
    stash_ids: list[StashIDInput] | None = None  # [StashIDInput!]
    resume_time: float | None = None  # Float
    play_duration: float | None = None  # Float
    primary_file_id: ID | None = None  # ID

    # Deprecated fields
    o_counter: int | None = None  # Int @deprecated
    play_count: int | None = None  # Int @deprecated


@strawberry.input
class BulkSceneUpdateInput:
    """Input for bulk updating scenes from schema/types/scene.graphql."""

    # Optional fields
    clientMutationId: str | None = None  # String
    ids: list[ID]  # [ID!]
    title: str | None = None  # String
    code: str | None = None  # String
    details: str | None = None  # String
    director: str | None = None  # String
    url: str | None = None  # String @deprecated(reason: "Use urls")
    urls: BulkUpdateStrings | None = None  # BulkUpdateStrings
    date: str | None = None  # String
    rating100: int | None = None  # Int (1-100)
    organized: bool | None = None  # Boolean
    studio_id: ID | None = None  # ID
    gallery_ids: BulkUpdateIds | None = None  # BulkUpdateIds
    performer_ids: BulkUpdateIds | None = None  # BulkUpdateIds
    tag_ids: BulkUpdateIds | None = None  # BulkUpdateIds
    group_ids: BulkUpdateIds | None = None  # BulkUpdateIds
    movie_ids: BulkUpdateIds | None = (
        None  # BulkUpdateIds @deprecated(reason: "Use group_ids")
    )


@strawberry.input
class SceneDestroyInput:
    """Input for destroying scenes from schema/types/scene.graphql."""

    id: ID  # ID!
    delete_file: bool | None = None  # Boolean
    delete_generated: bool | None = None  # Boolean


@strawberry.input
class ScenesDestroyInput:
    """Input for destroying multiple scenes from schema/types/scene.graphql."""

    ids: list[ID]  # [ID!]!
    delete_file: bool | None = None  # Boolean
    delete_generated: bool | None = None  # Boolean


@strawberry.input
class SceneHashInput:
    """Input for scene hash from schema/types/scene.graphql."""

    checksum: str | None = None  # String
    oshash: str | None = None  # String


@strawberry.input
class AssignSceneFileInput:
    """Input for assigning scene files from schema/types/scene.graphql."""

    scene_id: ID  # ID!
    file_id: ID  # ID!


@strawberry.input
class SceneMergeInput:
    """Input for merging scenes from schema/types/scene.graphql.

    If destination scene has no files, then the primary file of the
    first source scene will be assigned as primary."""

    source: list[ID]  # [ID!]!
    destination: ID  # ID!
    values: SceneUpdateInput | None = (
        None  # SceneUpdateInput (values defined here will override values in the destination)
    )
    play_history: bool | None = (
        None  # Boolean (if true, the source history will be combined with the destination)
    )
    o_history: bool | None = (
        None  # Boolean (if true, the source history will be combined with the destination)
    )


@strawberry.input
class GalleryCreateInput:
    """Input for creating galleries from schema/types/gallery.graphql."""

    # Required fields
    title: str  # String!

    # Optional fields
    code: str | None = None  # String
    url: str | None = None  # String @deprecated
    urls: list[str] | None = None  # [String!]
    date: str | None = None  # String
    details: str | None = None  # String
    photographer: str | None = None  # String
    rating100: int | None = None  # Int
    organized: bool | None = None  # Boolean
    scene_ids: list[ID] | None = None  # [ID!]
    studio_id: ID | None = None  # ID
    tag_ids: list[ID] | None = None  # [ID!]
    performer_ids: list[ID] | None = None  # [ID!]


@strawberry.input
class GalleryUpdateInput:
    """Input for updating galleries from schema/types/gallery.graphql."""

    # Required fields
    id: ID  # ID!

    # Optional fields
    client_mutation_id: str | None = None  # String
    title: str | None = None  # String
    code: str | None = None  # String
    url: str | None = None  # String @deprecated
    urls: list[str] | None = None  # [String!]
    date: str | None = None  # String
    details: str | None = None  # String
    photographer: str | None = None  # String
    rating100: int | None = None  # Int
    organized: bool | None = None  # Boolean
    scene_ids: list[ID] | None = None  # [ID!]
    studio_id: ID | None = None  # ID
    tag_ids: list[ID] | None = None  # [ID!]
    performer_ids: list[ID] | None = None  # [ID!]
    primary_file_id: ID | None = None  # ID


@strawberry.input
class GalleryAddInput:
    """Input for adding images to gallery from schema/types/gallery.graphql."""

    gallery_id: ID  # ID!
    image_ids: list[ID]  # [ID!]!


@strawberry.input
class GalleryRemoveInput:
    """Input for removing images from gallery from schema/types/gallery.graphql."""

    gallery_id: ID  # ID!
    image_ids: list[ID]  # [ID!]!


@strawberry.input
class GallerySetCoverInput:
    """Input for setting gallery cover from schema/types/gallery.graphql."""

    gallery_id: ID  # ID!
    cover_image_id: ID  # ID!


@strawberry.input
class GalleryResetCoverInput:
    """Input for resetting gallery cover from schema/types/gallery.graphql."""

    gallery_id: ID  # ID!


@strawberry.input
class GalleryDestroyInput:
    """Input for destroying galleries from schema/types/gallery.graphql.

    If delete_file is true, then the zip file will be deleted if the gallery is zip-file-based.
    If gallery is folder-based, then any files not associated with other galleries will be
    deleted, along with the folder, if it is not empty."""

    ids: list[ID]  # [ID!]!
    delete_file: bool | None = None  # Boolean
    delete_generated: bool | None = None  # Boolean


@strawberry.input
class BulkGalleryUpdateInput:
    """Input for bulk updating galleries from schema/types/gallery.graphql."""

    # Optional fields
    client_mutation_id: str | None = None  # String
    ids: list[ID]  # [ID!]!
    code: str | None = None  # String
    url: str | None = None  # String @deprecated
    urls: BulkUpdateStrings | None = None  # BulkUpdateStrings
    date: str | None = None  # String
    details: str | None = None  # String
    photographer: str | None = None  # String
    rating100: int | None = None  # Int (1-100)
    organized: bool | None = None  # Boolean
    scene_ids: BulkUpdateIds | None = None  # BulkUpdateIds
    studio_id: ID | None = None  # ID
    tag_ids: BulkUpdateIds | None = None  # BulkUpdateIds
    performer_ids: BulkUpdateIds | None = None  # BulkUpdateIds


@strawberry.input
class ImageUpdateInput:
    """Input for updating images from schema/types/image.graphql."""

    # Required fields
    id: ID  # ID!

    # Optional fields
    client_mutation_id: str | None = None  # String
    title: str | None = None  # String
    code: str | None = None  # String
    rating100: int | None = None  # Int (1-100)
    organized: bool | None = None  # Boolean
    url: str | None = None  # String @deprecated
    urls: list[str] | None = None  # [String!]
    date: str | None = None  # String
    details: str | None = None  # String
    photographer: str | None = None  # String
    studio_id: ID | None = None  # ID
    performer_ids: list[ID] | None = None  # [ID!]
    tag_ids: list[ID] | None = None  # [ID!]
    gallery_ids: list[ID] | None = None  # [ID!]
    primary_file_id: ID | None = None  # ID


@strawberry.input
class BulkImageUpdateInput:
    """Input for bulk updating images from schema/types/image.graphql."""

    # Optional fields
    client_mutation_id: str | None = None  # String
    ids: list[ID]  # [ID!]
    rating100: int | None = None  # Int (1-100)
    organized: bool | None = None  # Boolean
    url: str | None = None  # String @deprecated
    urls: BulkUpdateStrings | None = None  # BulkUpdateStrings
    date: str | None = None  # String
    details: str | None = None  # String
    photographer: str | None = None  # String
    studio_id: ID | None = None  # ID
    performer_ids: BulkUpdateIds | None = None  # BulkUpdateIds
    tag_ids: BulkUpdateIds | None = None  # BulkUpdateIds
    gallery_ids: BulkUpdateIds | None = None  # BulkUpdateIds


@strawberry.input
class StudioCreateInput:
    """Input for creating studios from schema/types/studio.graphql."""

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
    """Input for updating studios from schema/types/studio.graphql."""

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


@strawberry.input
class StudioDestroyInput:
    """Input for destroying studios from schema/types/studio.graphql."""

    id: ID  # ID!


@strawberry.input
class TagCreateInput:
    """Input for creating tags from schema/types/tag.graphql."""

    # Required fields
    name: str  # String!

    # Optional fields
    description: str | None = None  # String
    aliases: list[str] | None = None  # [String!]
    ignore_auto_tag: bool | None = None  # Boolean
    favorite: bool | None = None  # Boolean
    image: str | None = None  # String (URL or base64)
    parent_ids: list[ID] | None = None  # [ID!]
    child_ids: list[ID] | None = None  # [ID!]


@strawberry.input
class TagUpdateInput:
    """Input for updating tags from schema/types/tag.graphql."""

    # Required fields
    id: ID  # ID!

    # Optional fields
    name: str | None = None  # String
    description: str | None = None  # String
    aliases: list[str] | None = None  # [String!]
    ignore_auto_tag: bool | None = None  # Boolean
    favorite: bool | None = None  # Boolean
    image: str | None = None  # String (URL or base64)
    parent_ids: list[ID] | None = None  # [ID!]
    child_ids: list[ID] | None = None  # [ID!]


@strawberry.input
class TagDestroyInput:
    """Input for destroying tags from schema/types/tag.graphql."""

    id: ID  # ID!


@strawberry.input
class TagsMergeInput:
    """Input for merging tags from schema/types/tag.graphql."""

    source: list[ID]  # [ID!]!
    destination: ID  # ID!


@strawberry.input
class BulkTagUpdateInput:
    """Input for bulk updating tags from schema/types/tag.graphql."""

    ids: list[ID]  # [ID!]
    description: str | None = None  # String
    aliases: BulkUpdateStrings | None = None  # BulkUpdateStrings
    ignore_auto_tag: bool | None = None  # Boolean
    favorite: bool | None = None  # Boolean
    parent_ids: BulkUpdateIds | None = None  # BulkUpdateIds
    child_ids: BulkUpdateIds | None = None  # BulkUpdateIds
