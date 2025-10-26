"""Group types from schema/types/group.graphql."""

from typing import TYPE_CHECKING, Annotated

import strawberry
from strawberry import ID, lazy

from .base import StashObject
from .enums import BulkUpdateIdMode

if TYPE_CHECKING:
    from .scene import Scene
    from .studio import Studio
    from .tag import Tag


@strawberry.type
class GroupDescription:
    """Group description type from schema."""

    group: "Group"  # Group!
    description: str | None = None  # String


@strawberry.input
class GroupCreateInput:
    """Input for creating groups from schema/types/group.graphql."""

    # Required fields
    name: str  # String!

    # Optional fields
    aliases: str | None = None  # String
    duration: int | None = None  # Int (in seconds)
    date: str | None = None  # String
    rating100: int | None = None  # Int (1-100)
    studio_id: ID | None = None  # ID
    director: str | None = None  # String
    synopsis: str | None = None  # String
    urls: list[str] | None = None  # [String!]
    tag_ids: list[ID] | None = None  # [ID!]
    containing_groups: list["GroupDescriptionInput"] | None = (
        None  # [GroupDescriptionInput!]
    )
    sub_groups: list["GroupDescriptionInput"] | None = None  # [GroupDescriptionInput!]
    front_image: str | None = None  # String (URL or base64 encoded data URL)
    back_image: str | None = None  # String (URL or base64)


@strawberry.input
class GroupUpdateInput:
    """Input for updating groups from schema/types/group.graphql."""

    # Required fields
    id: ID  # ID!

    # Optional fields
    name: str | None = None  # String
    aliases: str | None = None  # String
    duration: int | None = None  # Int (in seconds)
    date: str | None = None  # String
    rating100: int | None = None  # Int (1-100)
    studio_id: ID | None = None  # ID
    director: str | None = None  # String
    synopsis: str | None = None  # String
    urls: list[str] | None = None  # [String!]
    tag_ids: list[ID] | None = None  # [ID!]
    containing_groups: list["GroupDescriptionInput"] | None = (
        None  # [GroupDescriptionInput!]
    )
    sub_groups: list["GroupDescriptionInput"] | None = None  # [GroupDescriptionInput!]
    front_image: str | None = None  # String (URL or base64 encoded data URL)
    back_image: str | None = None  # String (URL or base64 encoded data URL)


@strawberry.type
class Group(StashObject):
    """Group type from schema."""

    __type_name__ = "Group"
    __update_input_type__ = GroupUpdateInput
    __create_input_type__ = GroupCreateInput

    # Fields to track for changes - only fields that can be written via input types
    __tracked_fields__ = {
        "name",  # GroupCreateInput/GroupUpdateInput
        "urls",  # GroupCreateInput/GroupUpdateInput
        "tags",  # mapped to tag_ids
        "containing_groups",  # GroupCreateInput/GroupUpdateInput
        "sub_groups",  # GroupCreateInput/GroupUpdateInput
        "aliases",  # GroupCreateInput/GroupUpdateInput
        "duration",  # GroupCreateInput/GroupUpdateInput
        "date",  # GroupCreateInput/GroupUpdateInput
        "studio",  # mapped to studio_id
        "director",  # GroupCreateInput/GroupUpdateInput
        "synopsis",  # GroupCreateInput/GroupUpdateInput
    }

    # Required fields
    name: str  # String!
    urls: list[str] = strawberry.field(default_factory=list)  # [String!]!
    tags: list[Annotated["Tag", lazy("stash.types.tag.Tag")]] = strawberry.field(
        default_factory=list
    )  # [Tag!]!
    containing_groups: list[GroupDescription] = strawberry.field(
        default_factory=list
    )  # [GroupDescription!]!
    sub_groups: list[GroupDescription] = strawberry.field(
        default_factory=list
    )  # [GroupDescription!]!
    scenes: list[Annotated["Scene", lazy("stash.types.scene.Scene")]] = (
        strawberry.field(default_factory=list)
    )  # [Scene!]!

    # Optional fields
    aliases: str | None = None  # String
    duration: int | None = None  # Int (in seconds)
    date: str | None = None  # String
    studio: Annotated["Studio", lazy("stash.types.studio.Studio")] | None = (
        None  # Studio
    )
    director: str | None = None  # String
    synopsis: str | None = None  # String
    front_image_path: str | None = None  # String (Resolver)
    back_image_path: str | None = None  # String (Resolver)

    # Field definitions with their conversion functions
    __field_conversions__ = {
        "name": str,
        "urls": list,
        "aliases": str,
        "duration": int,
        "date": str,
        "rating100": int,
        "director": str,
        "synopsis": str,
    }

    __relationships__ = {
        # Standard ID relationships
        "studio": ("studio_id", False, None),  # (target_field, is_list, transform)
        "tags": ("tag_ids", True, None),
        # Special case with custom transform for group descriptions
        "containing_groups": (
            "containing_groups",
            True,
            lambda g: GroupDescriptionInput(
                group_id=g.group.id,
                description=g.description,
            ),
        ),
        "sub_groups": (
            "sub_groups",
            True,
            lambda g: GroupDescriptionInput(
                group_id=g.group.id,
                description=g.description,
            ),
        ),
    }


@strawberry.input
class GroupDescriptionInput:
    """Input for group description."""

    group_id: ID  # ID!
    description: str | None = None  # String


@strawberry.input
class BulkUpdateGroupDescriptionsInput:
    """Input for bulk updating group descriptions."""

    groups: list[GroupDescriptionInput]  # [GroupDescriptionInput!]!
    mode: BulkUpdateIdMode  # BulkUpdateIdMode!


@strawberry.input
class BulkGroupUpdateInput:
    """Input for bulk updating groups."""

    client_mutation_id: str | None = None  # String
    ids: list[ID]  # [ID!]
    rating100: int | None = None  # Int
    studio_id: ID | None = None  # ID
    director: str | None = None  # String
    urls: list[str] | None = None  # BulkUpdateStrings
    tag_ids: list[ID] | None = None  # BulkUpdateIds
    containing_groups: BulkUpdateGroupDescriptionsInput | None = (
        None  # BulkUpdateGroupDescriptionsInput
    )
    sub_groups: BulkUpdateGroupDescriptionsInput | None = (
        None  # BulkUpdateGroupDescriptionsInput
    )


@strawberry.input
class GroupDestroyInput:
    """Input for destroying groups."""

    id: ID  # ID!


@strawberry.input
class ReorderSubGroupsInput:
    """Input for reordering sub groups from schema/types/group.graphql.

    Fields:
        group_id: ID of the group to reorder sub groups for
        sub_group_ids: IDs of the sub groups to reorder. These must be a subset of the current sub groups.
            Sub groups will be inserted in this order at the insert_index.
        insert_at_id: The sub-group ID at which to insert the sub groups
        insert_after: If true, the sub groups will be inserted after the insert_index,
            otherwise they will be inserted before"""

    group_id: ID  # ID!
    sub_group_ids: list[ID]  # [ID!]!
    insert_at_id: ID  # ID!
    insert_after: bool  # Boolean


@strawberry.type
class FindGroupsResultType:
    """Result type for finding groups."""

    count: int  # Int!
    groups: list[Group]  # [Group!]!


@strawberry.input
class GroupSubGroupAddInput:
    """Input for adding sub groups from schema/types/group.graphql.

    Fields:
        containing_group_id: ID of the group to add sub groups to
        sub_groups: List of sub groups to add
        insert_index: The index at which to insert the sub groups. If not provided,
            the sub groups will be appended to the end"""

    containing_group_id: ID  # ID!
    sub_groups: list[GroupDescriptionInput]  # [GroupDescriptionInput!]!
    insert_index: int | None = None  # Int


@strawberry.input
class GroupSubGroupRemoveInput:
    """Input for removing sub groups."""

    containing_group_id: ID  # ID!
    sub_group_ids: list[ID]  # [ID!]!
