"""Scene types from schema/types/scene.graphql."""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, List, Optional

import strawberry
from strawberry import ID, lazy

from metadata import Media, Post

from .base import StashObject
from .files import StashID, VideoFile
from .inputs import SceneCreateInput, SceneUpdateInput

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
    created_at: datetime  # Time!
    updated_at: datetime  # Time!


@strawberry.type
class Scene(StashObject):
    """Scene type from schema/types/scene.graphql.

    Note: Inherits from StashObject for implementation convenience, not because
    Scene implements any interface in the schema. StashObject provides common
    functionality like find_by_id, save, and to_input methods."""

    __type_name__ = "Scene"

    # Optional fields
    title: str | None = None  # String
    code: str | None = None  # String
    details: str | None = None  # String
    director: str | None = None  # String
    date: str | None = None  # String
    rating100: int | None = None  # Int (1-100)
    o_counter: int | None = None  # Int
    interactive_speed: int | None = None  # Int
    last_played_at: datetime | None = (
        None  # Time (The last time play count was updated)
    )
    resume_time: float | None = None  # Float (The time index a scene was left at)
    play_duration: float | None = (
        None  # Float (The total time a scene has spent playing)
    )
    play_count: int | None = None  # Int (The number of times a scene has been played)
    studio: Annotated["Studio", lazy("stash.types.studio.Studio")] | None = (
        None  # Studio
    )

    # Required fields
    urls: list[str] = strawberry.field(default_factory=list)  # [String!]!
    organized: bool = False  # Boolean!
    interactive: bool = False  # Boolean!
    created_at: datetime  # Time!
    updated_at: datetime  # Time!
    play_history: list[datetime] = strawberry.field(
        default_factory=list
    )  # [Time!]! (Times a scene was played)
    o_history: list[datetime] = strawberry.field(
        default_factory=list
    )  # [Time!]! (Times the o counter was incremented)
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
    scene_streams: list[SceneStreamEndpoint] = strawberry.field(
        default_factory=list
    )  # [SceneStreamEndpoint!]! (Return valid stream paths)

    # Optional lists
    captions: list[VideoCaption] = strawberry.field(
        default_factory=list
    )  # [VideoCaption!]

    @classmethod
    async def from_media(
        cls,
        media: Media,
        post: Post | None = None,
        performer: (
            Annotated["Performer", lazy("stash.types.performer.Performer")] | None
        ) = None,
        studio: Annotated["Studio", lazy("stash.types.studio.Studio")] | None = None,
    ) -> "Scene":
        """Create scene from media.

        Args:
            media: Media to convert
            post: Optional post containing the media
            performer: Optional performer to associate
            studio: Optional studio to associate

        Returns:
            New scene instance
        """
        # Get title from post content or media filename
        title = None
        if post and post.content:
            title = post.content[:100]  # Truncate long content
        elif media.local_filename:
            title = media.local_filename

        # Build scene
        scene = cls(
            id="new",  # Will be replaced on save
            title=title,
            details=post.content if post else None,
            date=post.createdAt.isoformat() if post else media.createdAt.isoformat(),
            urls=[f"https://fansly.com/post/{post.id}"] if post else [],
            created_at=media.createdAt or datetime.now(),
            updated_at=datetime.now(),
            organized=True,  # Mark as organized since we have metadata
        )

        # Add relationships
        if performer:
            scene.performers = [performer]
        if studio:
            scene.studio = studio

        return scene

    def to_input(self) -> dict[str, Any]:
        """Convert to GraphQL input.

        Returns:
            Dictionary of input fields for create/update
        """
        if hasattr(self, "id") and self.id != "new":
            # Update existing
            return SceneUpdateInput(
                id=self.id,
                title=self.title,
                code=self.code,
                details=self.details,
                director=self.director,
                urls=self.urls,
                date=self.date,
                rating100=self.rating100,
                organized=self.organized,
                studio_id=self.studio.id if self.studio else None,
                performer_ids=[p.id for p in self.performers],
                tag_ids=[t.id for t in self.tags],
                gallery_ids=[g.id for g in self.galleries],
                stashIds=[
                    StashID(endpoint=s.endpoint, stash_id=s.stash_id)
                    for s in self.stashIds
                ],
            ).__dict__
        else:
            # Create new
            return SceneCreateInput(
                title=self.title,
                code=self.code,
                details=self.details,
                director=self.director,
                urls=self.urls,
                date=self.date,
                rating100=self.rating100,
                organized=self.organized,
                studio_id=self.studio.id if self.studio else None,
                performer_ids=[p.id for p in self.performers],
                tag_ids=[t.id for t in self.tags],
                gallery_ids=[g.id for g in self.galleries],
                stashIds=[
                    StashID(endpoint=s.endpoint, stash_id=s.stash_id)
                    for s in self.stashIds
                ],
            ).__dict__


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
class SceneGroupInput:
    """Input for scene group from schema/types/scene.graphql."""

    group_id: ID  # ID!
    scene_index: int | None = None  # Int


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
