"""Filter types from schema/types/filters.graphql."""

from typing import Any, Optional

import strawberry
from strawberry import ID

from .enums import (
    CircumisedEnum,
    CriterionModifier,
    FilterMode,
    GenderEnum,
    OrientationEnum,
    ResolutionEnum,
    SortDirectionEnum,
)


@strawberry.input
class FindFilterType:
    """Input for find filter."""

    q: str | None = None  # String
    page: int | None = None  # Int
    per_page: int | None = None  # Int (-1 for all, default 25)
    sort: str | None = None  # String
    direction: SortDirectionEnum | None = None  # SortDirectionEnum


@strawberry.type
class SavedFindFilterType:
    """Saved find filter type."""

    q: str | None = None  # String
    page: int | None = None  # Int
    per_page: int | None = None  # Int (-1 for all, default 25)
    sort: str | None = None  # String
    direction: SortDirectionEnum | None = None  # SortDirectionEnum


@strawberry.input
class ResolutionCriterionInput:
    """Input for resolution criterion."""

    value: ResolutionEnum  # ResolutionEnum!
    modifier: CriterionModifier  # CriterionModifier!


@strawberry.input
class OrientationCriterionInput:
    """Input for orientation criterion."""

    value: list[OrientationEnum]  # [OrientationEnum!]!


@strawberry.input
class PHashDuplicationCriterionInput:
    """Input for phash duplication criterion."""

    duplicated: bool | None = None  # Boolean
    distance: int | None = None  # Int


@strawberry.input
class StashIDCriterionInput:
    """Input for StashID criterion."""

    endpoint: str | None = None  # String
    stash_id: str | None = None  # String
    modifier: CriterionModifier  # CriterionModifier!


@strawberry.input
class CustomFieldCriterionInput:
    """Input for custom field criterion."""

    field: str  # String!
    value: list[Any] | None = None  # [Any!]
    modifier: CriterionModifier  # CriterionModifier!


@strawberry.input
class StringCriterionInput:
    """Input for string criterion."""

    value: str  # String!
    modifier: CriterionModifier  # CriterionModifier!


@strawberry.input
class IntCriterionInput:
    """Input for integer criterion."""

    value: int  # Int!
    value2: int | None = None  # Int
    modifier: CriterionModifier  # CriterionModifier!


@strawberry.input
class FloatCriterionInput:
    """Input for float criterion."""

    value: float  # Float!
    value2: float | None = None  # Float
    modifier: CriterionModifier  # CriterionModifier!


@strawberry.input
class MultiCriterionInput:
    """Input for multi criterion."""

    value: list[ID] | None = None  # [ID!]
    modifier: CriterionModifier  # CriterionModifier!
    excludes: list[ID] | None = None  # [ID!]


@strawberry.input
class GenderCriterionInput:
    """Input for gender criterion."""

    value: GenderEnum | None = None  # GenderEnum
    value_list: list[GenderEnum] | None = None  # [GenderEnum!]
    modifier: CriterionModifier  # CriterionModifier!


@strawberry.input
class CircumcisionCriterionInput:
    """Input for circumcision criterion."""

    value: list[CircumisedEnum]  # [CircumisedEnum!]!
    modifier: CriterionModifier  # CriterionModifier!


@strawberry.input
class HierarchicalMultiCriterionInput:
    """Input for hierarchical multi criterion."""

    value: list[ID]  # [ID!]!
    modifier: CriterionModifier  # CriterionModifier!
    depth: int | None = None  # Int
    excludes: list[ID] | None = None  # [ID!]


@strawberry.input
class DateCriterionInput:
    """Input for date criterion."""

    value: str  # String!
    value2: str | None = None  # String
    modifier: CriterionModifier  # CriterionModifier!


@strawberry.input
class TimestampCriterionInput:
    """Input for timestamp criterion."""

    value: str  # String!
    value2: str | None = None  # String
    modifier: CriterionModifier  # CriterionModifier!


@strawberry.input
class PhashDistanceCriterionInput:
    """Input for phash distance criterion."""

    value: str  # String!
    modifier: CriterionModifier  # CriterionModifier!
    distance: int | None = None  # Int


@strawberry.type
class SavedFilter:
    """Saved filter type."""

    id: ID  # ID!
    mode: FilterMode  # FilterMode!
    name: str  # String!
    find_filter: SavedFindFilterType | None = None  # SavedFindFilterType
    object_filter: dict[str, Any] | None = None  # Map
    ui_options: dict[str, Any] | None = None  # Map


@strawberry.input
class SaveFilterInput:
    """Input for saving filter."""

    id: ID | None = None  # ID
    mode: FilterMode  # FilterMode!
    name: str  # String!
    find_filter: FindFilterType | None = None  # FindFilterType
    object_filter: dict[str, Any] | None = None  # Map
    ui_options: dict[str, Any] | None = None  # Map


@strawberry.input
class DestroyFilterInput:
    """Input for destroying filter."""

    id: ID  # ID!


@strawberry.input
class SetDefaultFilterInput:
    """Input for setting default filter."""

    mode: FilterMode  # FilterMode!
    find_filter: FindFilterType | None = None  # FindFilterType
    object_filter: dict[str, Any] | None = None  # Map
    ui_options: dict[str, Any] | None = None  # Map


# Core filter types
@strawberry.input
class PerformerFilterType:
    """Input for performer filter."""

    AND: Optional["PerformerFilterType"] = None
    OR: Optional["PerformerFilterType"] = None
    NOT: Optional["PerformerFilterType"] = None
    name: StringCriterionInput | None = None
    disambiguation: StringCriterionInput | None = None
    details: StringCriterionInput | None = None
    filter_favorites: bool | None = None
    birth_year: IntCriterionInput | None = None
    age: IntCriterionInput | None = None
    ethnicity: StringCriterionInput | None = None
    country: StringCriterionInput | None = None
    eye_color: StringCriterionInput | None = None
    height_cm: IntCriterionInput | None = None
    measurements: StringCriterionInput | None = None
    fake_tits: StringCriterionInput | None = None
    penis_length: FloatCriterionInput | None = None
    circumcised: CircumcisionCriterionInput | None = None
    career_length: StringCriterionInput | None = None
    tattoos: StringCriterionInput | None = None
    piercings: StringCriterionInput | None = None
    aliases: StringCriterionInput | None = None
    gender: GenderCriterionInput | None = None
    is_missing: str | None = None
    tags: HierarchicalMultiCriterionInput | None = None
    tag_count: IntCriterionInput | None = None
    scene_count: IntCriterionInput | None = None
    image_count: IntCriterionInput | None = None
    gallery_count: IntCriterionInput | None = None
    play_count: IntCriterionInput | None = None
    o_counter: IntCriterionInput | None = None
    stash_id_endpoint: StashIDCriterionInput | None = None
    rating100: IntCriterionInput | None = None
    url: StringCriterionInput | None = None
    hair_color: StringCriterionInput | None = None
    weight: IntCriterionInput | None = None
    death_year: IntCriterionInput | None = None
    studios: HierarchicalMultiCriterionInput | None = None
    performers: MultiCriterionInput | None = None
    ignore_auto_tag: bool | None = None
    birthdate: DateCriterionInput | None = None
    death_date: DateCriterionInput | None = None
    scenes_filter: Optional["SceneFilterType"] = None
    images_filter: Optional["ImageFilterType"] = None
    galleries_filter: Optional["GalleryFilterType"] = None
    tags_filter: Optional["TagFilterType"] = None
    created_at: TimestampCriterionInput | None = None
    updated_at: TimestampCriterionInput | None = None
    custom_fields: list[CustomFieldCriterionInput] | None = None


@strawberry.input
class SceneMarkerFilterType:
    """Input for scene marker filter."""

    tags: HierarchicalMultiCriterionInput | None = None
    scene_tags: HierarchicalMultiCriterionInput | None = None
    performers: MultiCriterionInput | None = None
    scenes: MultiCriterionInput | None = None
    duration: FloatCriterionInput | None = None
    created_at: TimestampCriterionInput | None = None
    updated_at: TimestampCriterionInput | None = None
    scene_date: DateCriterionInput | None = None
    scene_created_at: TimestampCriterionInput | None = None
    scene_updated_at: TimestampCriterionInput | None = None
    scene_filter: Optional["SceneFilterType"] = None


@strawberry.input
class SceneFilterType:
    """Input for scene filter."""

    AND: Optional["SceneFilterType"] = None
    OR: Optional["SceneFilterType"] = None
    NOT: Optional["SceneFilterType"] = None
    id: IntCriterionInput | None = None
    title: StringCriterionInput | None = None
    code: StringCriterionInput | None = None
    details: StringCriterionInput | None = None
    director: StringCriterionInput | None = None
    oshash: StringCriterionInput | None = None
    checksum: StringCriterionInput | None = None
    phash_distance: PhashDistanceCriterionInput | None = None
    path: StringCriterionInput | None = None
    file_count: IntCriterionInput | None = None
    rating100: IntCriterionInput | None = None
    organized: bool | None = None
    o_counter: IntCriterionInput | None = None
    duplicated: PHashDuplicationCriterionInput | None = None
    resolution: ResolutionCriterionInput | None = None
    orientation: OrientationCriterionInput | None = None
    framerate: IntCriterionInput | None = None
    bitrate: IntCriterionInput | None = None
    video_codec: StringCriterionInput | None = None
    audio_codec: StringCriterionInput | None = None
    duration: IntCriterionInput | None = None
    has_markers: str | None = None
    is_missing: str | None = None
    studios: HierarchicalMultiCriterionInput | None = None
    groups: HierarchicalMultiCriterionInput | None = None
    galleries: MultiCriterionInput | None = None
    tags: HierarchicalMultiCriterionInput | None = None
    tag_count: IntCriterionInput | None = None
    performer_tags: HierarchicalMultiCriterionInput | None = None
    performer_favorite: bool | None = None
    performer_age: IntCriterionInput | None = None
    performers: MultiCriterionInput | None = None
    performer_count: IntCriterionInput | None = None
    stash_id_endpoint: StashIDCriterionInput | None = None
    url: StringCriterionInput | None = None
    interactive: bool | None = None
    interactive_speed: IntCriterionInput | None = None
    captions: StringCriterionInput | None = None
    resume_time: IntCriterionInput | None = None
    play_count: IntCriterionInput | None = None
    play_duration: IntCriterionInput | None = None
    last_played_at: TimestampCriterionInput | None = None
    date: DateCriterionInput | None = None
    created_at: TimestampCriterionInput | None = None
    updated_at: TimestampCriterionInput | None = None
    galleries_filter: Optional["GalleryFilterType"] = None
    performers_filter: Optional["PerformerFilterType"] = None
    studios_filter: Optional["StudioFilterType"] = None
    tags_filter: Optional["TagFilterType"] = None
    groups_filter: Optional["GroupFilterType"] = None
    markers_filter: Optional["SceneMarkerFilterType"] = None


@strawberry.input
class GroupFilterType:
    """Input for group filter."""

    AND: Optional["GroupFilterType"] = None
    OR: Optional["GroupFilterType"] = None
    NOT: Optional["GroupFilterType"] = None
    name: StringCriterionInput | None = None
    director: StringCriterionInput | None = None
    synopsis: StringCriterionInput | None = None
    duration: IntCriterionInput | None = None
    rating100: IntCriterionInput | None = None
    studios: HierarchicalMultiCriterionInput | None = None
    is_missing: str | None = None
    url: StringCriterionInput | None = None
    performers: MultiCriterionInput | None = None
    tags: HierarchicalMultiCriterionInput | None = None
    tag_count: IntCriterionInput | None = None
    date: DateCriterionInput | None = None
    created_at: TimestampCriterionInput | None = None
    updated_at: TimestampCriterionInput | None = None
    containing_groups: HierarchicalMultiCriterionInput | None = None
    sub_groups: HierarchicalMultiCriterionInput | None = None
    containing_group_count: IntCriterionInput | None = None
    sub_group_count: IntCriterionInput | None = None
    scenes_filter: Optional["SceneFilterType"] = None
    studios_filter: Optional["StudioFilterType"] = None


@strawberry.input
class StudioFilterType:
    """Input for studio filter."""

    AND: Optional["StudioFilterType"] = None
    OR: Optional["StudioFilterType"] = None
    NOT: Optional["StudioFilterType"] = None
    name: StringCriterionInput | None = None
    details: StringCriterionInput | None = None
    parents: MultiCriterionInput | None = None
    stash_id_endpoint: StashIDCriterionInput | None = None
    tags: HierarchicalMultiCriterionInput | None = None
    is_missing: str | None = None
    rating100: IntCriterionInput | None = None
    favorite: bool | None = None
    scene_count: IntCriterionInput | None = None
    image_count: IntCriterionInput | None = None
    gallery_count: IntCriterionInput | None = None
    tag_count: IntCriterionInput | None = None
    url: StringCriterionInput | None = None
    aliases: StringCriterionInput | None = None
    child_count: IntCriterionInput | None = None
    ignore_auto_tag: bool | None = None
    scenes_filter: Optional["SceneFilterType"] = None
    images_filter: Optional["ImageFilterType"] = None
    galleries_filter: Optional["GalleryFilterType"] = None
    created_at: TimestampCriterionInput | None = None
    updated_at: TimestampCriterionInput | None = None


@strawberry.input
class GalleryFilterType:
    """Input for gallery filter."""

    AND: Optional["GalleryFilterType"] = None
    OR: Optional["GalleryFilterType"] = None
    NOT: Optional["GalleryFilterType"] = None
    id: IntCriterionInput | None = None
    title: StringCriterionInput | None = None
    details: StringCriterionInput | None = None
    checksum: StringCriterionInput | None = None
    path: StringCriterionInput | None = None
    file_count: IntCriterionInput | None = None
    is_missing: str | None = None
    is_zip: bool | None = None
    rating100: IntCriterionInput | None = None
    organized: bool | None = None
    average_resolution: ResolutionCriterionInput | None = None
    has_chapters: str | None = None
    scenes: MultiCriterionInput | None = None
    studios: HierarchicalMultiCriterionInput | None = None
    tags: HierarchicalMultiCriterionInput | None = None
    tag_count: IntCriterionInput | None = None
    performer_tags: HierarchicalMultiCriterionInput | None = None
    performers: MultiCriterionInput | None = None
    performer_count: IntCriterionInput | None = None
    performer_favorite: bool | None = None
    performer_age: IntCriterionInput | None = None
    image_count: IntCriterionInput | None = None
    url: StringCriterionInput | None = None
    date: DateCriterionInput | None = None
    created_at: TimestampCriterionInput | None = None
    updated_at: TimestampCriterionInput | None = None
    code: StringCriterionInput | None = None
    photographer: StringCriterionInput | None = None
    scenes_filter: Optional["SceneFilterType"] = None
    images_filter: Optional["ImageFilterType"] = None
    performers_filter: Optional["PerformerFilterType"] = None
    studios_filter: Optional["StudioFilterType"] = None
    tags_filter: Optional["TagFilterType"] = None


@strawberry.input
class TagFilterType:
    """Input for tag filter."""

    AND: Optional["TagFilterType"] = None
    OR: Optional["TagFilterType"] = None
    NOT: Optional["TagFilterType"] = None
    name: StringCriterionInput | None = None
    aliases: StringCriterionInput | None = None
    favorite: bool | None = None
    description: StringCriterionInput | None = None
    is_missing: str | None = None
    scene_count: IntCriterionInput | None = None
    image_count: IntCriterionInput | None = None
    gallery_count: IntCriterionInput | None = None
    performer_count: IntCriterionInput | None = None
    studio_count: IntCriterionInput | None = None
    group_count: IntCriterionInput | None = None
    marker_count: IntCriterionInput | None = None
    parents: HierarchicalMultiCriterionInput | None = None
    children: HierarchicalMultiCriterionInput | None = None
    parent_count: IntCriterionInput | None = None
    child_count: IntCriterionInput | None = None
    ignore_auto_tag: bool | None = None
    scenes_filter: Optional["SceneFilterType"] = None
    images_filter: Optional["ImageFilterType"] = None
    galleries_filter: Optional["GalleryFilterType"] = None
    created_at: TimestampCriterionInput | None = None
    updated_at: TimestampCriterionInput | None = None


@strawberry.input
class ImageFilterType:
    """Input for image filter."""

    AND: Optional["ImageFilterType"] = None
    OR: Optional["ImageFilterType"] = None
    NOT: Optional["ImageFilterType"] = None
    title: StringCriterionInput | None = None
    details: StringCriterionInput | None = None
    id: IntCriterionInput | None = None
    checksum: StringCriterionInput | None = None
    path: StringCriterionInput | None = None
    file_count: IntCriterionInput | None = None
    rating100: IntCriterionInput | None = None
    date: DateCriterionInput | None = None
    url: StringCriterionInput | None = None
    organized: bool | None = None
    o_counter: IntCriterionInput | None = None
    resolution: ResolutionCriterionInput | None = None
    orientation: OrientationCriterionInput | None = None
    is_missing: str | None = None
    studios: HierarchicalMultiCriterionInput | None = None
    tags: HierarchicalMultiCriterionInput | None = None
    tag_count: IntCriterionInput | None = None
    performer_tags: HierarchicalMultiCriterionInput | None = None
    performers: MultiCriterionInput | None = None
    performer_count: IntCriterionInput | None = None
    performer_favorite: bool | None = None
    performer_age: IntCriterionInput | None = None
    galleries: MultiCriterionInput | None = None
    created_at: TimestampCriterionInput | None = None
    updated_at: TimestampCriterionInput | None = None
    code: StringCriterionInput | None = None
    photographer: StringCriterionInput | None = None
    galleries_filter: Optional["GalleryFilterType"] = None
    performers_filter: Optional["PerformerFilterType"] = None
    studios_filter: Optional["StudioFilterType"] = None
    tags_filter: Optional["TagFilterType"] = None
