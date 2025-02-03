"""Scene marker types from schema/types/scene-marker.graphql and scene-marker-tag.graphql."""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, List, Optional

import strawberry
from strawberry import ID, lazy

from .base import StashObject

if TYPE_CHECKING:
    from .scene import Scene
    from .tag import Tag


@strawberry.type
class SceneMarkerTag:
    """Scene marker tag type from schema/types/scene-marker-tag.graphql."""

    tag: Annotated[
        "Tag", lazy("stash.types.tag.Tag")
    ]  # Tag! (from schema/types/scene-marker-tag.graphql)
    scene_markers: list[
        Annotated["SceneMarker", lazy("stash.types.markers.SceneMarker")]
    ] = strawberry.field(
        default_factory=list
    )  # [SceneMarker!]! (from schema/types/scene-marker-tag.graphql)


@strawberry.type
class SceneMarker(StashObject):
    """Scene marker type from schema/types/scene-marker.graphql."""

    __type_name__ = "SceneMarker"

    # Required fields
    scene: Annotated["Scene", lazy("stash.types.scene.Scene")]  # Scene!
    title: str  # String!
    seconds: float  # Float! (The required start time of the marker (in seconds). Supports decimals.)
    primary_tag: Annotated["Tag", lazy("stash.types.tag.Tag")]  # Tag!
    tags: list[Annotated["Tag", lazy("stash.types.tag.Tag")]] = strawberry.field(
        default_factory=list
    )  # [Tag!]!
    stream: str  # String! (The path to stream this marker) (Resolver)
    preview: str  # String! (The path to the preview image for this marker) (Resolver)
    screenshot: (
        str  # String! (The path to the screenshot image for this marker) (Resolver)
    )

    # Optional fields
    end_seconds: float | None = (
        None  # Float (The optional end time of the marker (in seconds). Supports decimals.)
    )

    def to_input(self) -> dict[str, Any]:
        """Convert to GraphQL input.

        Returns:
            Dictionary of input fields for create/update
        """
        if hasattr(self, "id") and self.id != "new":
            # Update existing
            return SceneMarkerUpdateInput(
                id=self.id,
                title=self.title,
                seconds=self.seconds,
                end_seconds=self.end_seconds,
                scene_id=self.scene.id,
                primary_tag_id=self.primary_tag.id,
                tag_ids=[t.id for t in self.tags],
            ).__dict__
        else:
            # Create new
            return SceneMarkerCreateInput(
                title=self.title,
                seconds=self.seconds,
                end_seconds=self.end_seconds,
                scene_id=self.scene.id,
                primary_tag_id=self.primary_tag.id,
                tag_ids=[t.id for t in self.tags],
            ).__dict__


@strawberry.input
class SceneMarkerCreateInput:
    """Input for creating scene markers from schema/types/scene-marker.graphql."""

    title: str  # String!
    seconds: float  # Float! (The required start time of the marker (in seconds). Supports decimals.)
    end_seconds: float | None = (
        None  # Float (The optional end time of the marker (in seconds). Supports decimals.)
    )
    scene_id: ID  # ID!
    primary_tag_id: ID  # ID!
    tag_ids: list[ID] | None = None  # [ID!]


@strawberry.input
class SceneMarkerUpdateInput:
    """Input for updating scene markers from schema/types/scene-marker.graphql."""

    id: ID  # ID!
    title: str | None = None  # String
    seconds: float | None = (
        None  # Float (The start time of the marker (in seconds). Supports decimals.)
    )
    end_seconds: float | None = (
        None  # Float (The end time of the marker (in seconds). Supports decimals.)
    )
    scene_id: ID | None = None  # ID
    primary_tag_id: ID | None = None  # ID
    tag_ids: list[ID] | None = None  # [ID!]


@strawberry.type
class FindSceneMarkersResultType:
    """Result type for finding scene markers from schema/types/scene-marker.graphql."""

    count: int  # Int!
    scene_markers: list[SceneMarker]  # [SceneMarker!]!


@strawberry.type
class MarkerStringsResultType:
    """Result type for marker strings from schema/types/scene-marker.graphql."""

    count: int  # Int!
    id: ID  # ID!
    title: str  # String!
