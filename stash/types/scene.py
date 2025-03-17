"""Scene types from schema/types/scene.graphql."""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, List, Optional

import strawberry
from strawberry import ID, lazy

from metadata import Media, Post

from .base import BulkUpdateIds, BulkUpdateStrings, StashObject
from .files import StashID, StashIDInput, VideoFile

if TYPE_CHECKING:
    from .gallery import Gallery
    from .group import Group
    from .performer import Performer
    from .studio import Studio
    from .tag import Tag


@strawberry.type
class SceneGroup:
    """Scene group type from schema/types/scene.graphql."""

    group: Annotated["Group", lazy("stash.types.group.Group")]  # Group!
    scene_index: int | None = None  # Int


@strawberry.type
class VideoCaption:
    """Video caption type from schema/types/scene.graphql."""

    language_code: str  # String!
    caption_type: str  # String!


@strawberry.type
class SceneFileType:
    """Scene file type from schema/types/scene.graphql."""

    size: str  # String
    duration: float  # Float
    video_codec: str  # String
    audio_codec: str  # String
    width: int  # Int
    height: int  # Int
    framerate: float  # Float
    bitrate: int  # Int


@strawberry.type
class ScenePathsType:
    """Scene paths type from schema/types/scene.graphql."""

    screenshot: str | None = None  # String (Resolver)
    preview: str | None = None  # String (Resolver)
    stream: str | None = None  # String (Resolver)
    webp: str | None = None  # String (Resolver)
    vtt: str | None = None  # String (Resolver)
    sprite: str | None = None  # String (Resolver)
    funscript: str | None = None  # String (Resolver)
    interactive_heatmap: str | None = None  # String (Resolver)
    caption: str | None = None  # String (Resolver)


@strawberry.type
class SceneStreamEndpoint:
    """Scene stream endpoint type from schema/types/scene.graphql."""

    url: str  # String!
    mime_type: str | None = None  # String
    label: str | None = None  # String


@strawberry.type
class SceneMarker:
    """Scene marker type from schema/types/scene-marker.graphql."""

    id: ID  # ID!
    title: str  # String!
    seconds: float  # Float!
    stream: str | None = None  # String
    preview: str | None = None  # String
    screenshot: str | None = None  # String
    scene: Annotated["Scene", lazy("stash.types.scene.Scene")]  # Scene!
    primary_tag: Annotated["Tag", lazy("stash.types.tag.Tag")]  # Tag!
    tags: list[Annotated["Tag", lazy("stash.types.tag.Tag")]] = strawberry.field(
        default_factory=list
    )  # [Tag!]!
    # created_at and updated_at handled by Stash


@strawberry.input
class SceneGroupInput:
    """Input for scene group from schema/types/scene.graphql."""

    group_id: ID  # ID!
    scene_index: int | None = None  # Int


@strawberry.input
class SceneUpdateInput:
    """Input for updating scenes."""

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


@strawberry.type
class Scene(StashObject):
    """Scene type from schema/types/scene.graphql.

    Note: Inherits from StashObject for implementation convenience, not because
    Scene implements any interface in the schema. StashObject provides common
    functionality like find_by_id, save, and to_input methods."""

    __type_name__ = "Scene"
    __update_input_type__ = SceneUpdateInput
    # No __create_input_type__ - scenes can only be updated, they are created by the server during scanning

    # Fields to track for changes - only fields that can be written via input types
    __tracked_fields__ = {
        "title",  # SceneCreateInput/SceneUpdateInput
        "code",  # SceneCreateInput/SceneUpdateInput
        "details",  # SceneCreateInput/SceneUpdateInput
        "director",  # SceneCreateInput/SceneUpdateInput
        "date",  # SceneCreateInput/SceneUpdateInput
        "studio",  # mapped to studio_id
        "urls",  # SceneCreateInput/SceneUpdateInput
        "organized",  # SceneCreateInput/SceneUpdateInput
        "files",  # mapped to file_ids
        "galleries",  # mapped to gallery_ids
        "groups",  # SceneCreateInput/SceneUpdateInput
        "tags",  # mapped to tag_ids
        "performers",  # mapped to performer_ids
    }

    # Optional fields
    title: str | None = None  # String
    code: str | None = None  # String
    details: str | None = None  # String
    director: str | None = None  # String
    date: str | None = None  # String
    studio: Annotated["Studio", lazy("stash.types.studio.Studio")] | None = (
        None  # Studio
    )

    # Required fields
    urls: list[str] = strawberry.field(default_factory=list)  # [String!]!
    organized: bool = False  # Boolean!
    files: list[Annotated["VideoFile", lazy("stash.types.files.VideoFile")]] = (
        strawberry.field(default_factory=list)
    )  # [VideoFile!]!
    paths: ScenePathsType = strawberry.field(
        default_factory=ScenePathsType
    )  # ScenePathsType! (Resolver)
    scene_markers: list[
        Annotated["SceneMarker", lazy("stash.types.markers.SceneMarker")]
    ] = strawberry.field(
        default_factory=list
    )  # [SceneMarker!]!
    galleries: list[Annotated["Gallery", lazy("stash.types.gallery.Gallery")]] = (
        strawberry.field(default_factory=list)
    )  # [Gallery!]!
    groups: list[Annotated["SceneGroup", lazy("stash.types.scene.SceneGroup")]] = (
        strawberry.field(default_factory=list)
    )  # [SceneGroup!]!
    tags: list[Annotated["Tag", lazy("stash.types.tag.Tag")]] = strawberry.field(
        default_factory=list
    )  # [Tag!]!
    performers: list[
        Annotated["Performer", lazy("stash.types.performer.Performer")]
    ] = strawberry.field(
        default_factory=list
    )  # [Performer!]!
    stash_ids: list[StashID] = strawberry.field(default_factory=list)  # [StashID!]!
    sceneStreams: list[SceneStreamEndpoint] = strawberry.field(
        default_factory=list,
        name="sceneStreams",  # Match GraphQL field name exactly
    )  # [SceneStreamEndpoint!]! (Return valid stream paths)

    # Optional lists
    captions: list[VideoCaption] = strawberry.field(
        default_factory=list
    )  # [VideoCaption!]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scene":
        """Create scene from dictionary.

        Args:
            data: Dictionary containing scene data

        Returns:
            New scene instance

        Raises:
            ValueError: If data does not contain an ID field
        """
        # Ensure ID is present since we only update existing scenes
        if "id" not in data:
            raise ValueError("Scene data must contain an ID field")

        # No field mapping needed - using exact GraphQL names

        # Filter out fields that aren't part of our class
        valid_fields = {field.name for field in cls.__strawberry_definition__.fields}
        filtered_data = {}
        for k, v in data.items():
            if k in valid_fields:
                filtered_data[k] = v

        # created_at and updated_at handled by Stash

        # Create instance
        scene = cls(**filtered_data)

        # Convert lists
        if "files" in filtered_data:
            scene.files = [VideoFile(**f) for f in filtered_data["files"]]
        if "stash_ids" in filtered_data:
            scene.stash_ids = [StashID(**s) for s in filtered_data["stash_ids"]]

        return scene

    # Relationship definitions with their mappings
    __relationships__ = {
        # Standard ID relationships
        "studio": ("studio_id", False),  # (target_field, is_list)
        "performers": ("performer_ids", True),
        "tags": ("tag_ids", True),
        "galleries": ("gallery_ids", True),
        # Special case with custom transform
        "stash_ids": (
            "stash_ids",
            True,
            lambda s: StashID(endpoint=s.endpoint, stash_id=s.stash_id),
        ),
    }

    # Field definitions with their conversion functions
    __field_conversions__ = {
        "title": str,
        "code": str,
        "details": str,
        "director": str,
        "urls": list,
        "rating100": int,
        "organized": bool,
        "date": lambda d: (
            d.strftime("%Y-%m-%d")
            if isinstance(d, datetime)
            else (
                datetime.fromisoformat(d).strftime("%Y-%m-%d")
                if isinstance(d, str)
                else None
            )
        ),
    }


@strawberry.type
class FindScenesResultType:
    """Result type for finding scenes from schema/types/scene.graphql.

    Fields:
    count: Total number of scenes
    duration: Total duration in seconds
    filesize: Total file size in bytes
    scenes: List of scenes
    """

    count: int  # Int!
    duration: float  # Float!
    filesize: float  # Float!
    scenes: list[Annotated["Scene", lazy("stash.types.scene.Scene")]] = (
        strawberry.field(default_factory=list)
    )  # [Scene!]!


@strawberry.type
class SceneParserResult:
    """Result type for scene parser from schema/types/scene.graphql."""

    scene: Annotated["Scene", lazy("stash.types.scene.Scene")]  # Scene!
    title: str | None = None  # String
    code: str | None = None  # String
    details: str | None = None  # String
    director: str | None = None  # String
    url: str | None = None  # String
    date: str | None = None  # String
    rating100: int | None = None  # Int (1-100)
    studio_id: ID | None = None  # ID
    gallery_ids: list[ID] | None = None  # [ID!]
    performer_ids: list[ID] | None = None  # [ID!]

    tag_ids: list[ID] | None = None  # [ID!]


@strawberry.input
class SceneCreateInput:
    """Input for creating scenes."""

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
class BulkSceneUpdateInput:
    """Input for bulk updating scenes."""

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


@strawberry.type
class SceneParserResultType:
    """Result type for scene parser from schema/types/scene.graphql."""

    count: int  # Int!
    results: list[
        Annotated["SceneParserResult", lazy("stash.types.scene.SceneParserResult")]
    ] = strawberry.field(
        default_factory=list
    )  # [SceneParserResult!]!


@strawberry.input
class AssignSceneFileInput:
    """Input for assigning a file to a scene from schema/types/scene.graphql."""

    scene_id: ID  # ID!
    file_id: ID  # ID!


@strawberry.input
class SceneDestroyInput:
    """Input for destroying a scene from schema/types/scene.graphql."""

    id: ID  # ID!
    delete_file: bool | None = None  # Boolean
    delete_generated: bool | None = None  # Boolean


@strawberry.input
class SceneHashInput:
    """Input for scene hash from schema/types/scene.graphql."""

    checksum: str | None = None  # String
    oshash: str | None = None  # String


@strawberry.input
class SceneMergeInput:
    """Input for merging scenes from schema/types/scene.graphql."""

    source: list[ID]  # [ID!]!
    destination: ID  # ID!
    values: SceneUpdateInput | None = None  # SceneUpdateInput
    play_history: bool | None = None  # Boolean
    o_history: bool | None = None  # Boolean


@strawberry.input
class SceneMovieInput:
    """Input for scene movie from schema/types/scene.graphql."""

    movie_id: ID  # ID!
    scene_index: int | None = None  # Int


@strawberry.input
class ScenesDestroyInput:
    """Input for destroying multiple scenes from schema/types/scene.graphql."""

    ids: list[ID]  # [ID!]!
    delete_file: bool | None = None  # Boolean
    delete_generated: bool | None = None  # Boolean


@strawberry.type
class HistoryMutationResult:
    """Result type for history mutation from schema/types/scene.graphql."""

    count: int  # Int!
    history: list[datetime]  # [Time!]!
