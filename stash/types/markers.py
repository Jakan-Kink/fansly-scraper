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

    # Fields to track for changes
    __tracked_fields__ = {
        "title",
        "seconds",
        "end_seconds",
        "scene",
        "primary_tag",
        "tags",
    }

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

    # Field definitions with their conversion functions
    __field_conversions__ = {
        "title": str,
        "seconds": float,
        "end_seconds": float,
    }

    async def _to_input_all(self) -> dict[str, Any]:
        """Convert all fields to input type.

        Returns:
            Dictionary of all input fields
        """
        # Process all fields
        data = await self._process_fields(set(self.__field_conversions__.keys()))

        # Process all relationships
        rel_data = await self._process_relationships(set(self.__relationships__.keys()))
        data.update(rel_data)

        # Convert to create input and dict
        input_class = (
            SceneMarkerCreateInput
            if not hasattr(self, "id") or self.id == "new"
            else SceneMarkerUpdateInput
        )
        input_obj = input_class(**data)
        return {
            k: v
            for k, v in vars(input_obj).items()
            if not k.startswith("_") and v is not None and k != "client_mutation_id"
        }

    async def _to_input_dirty(self) -> dict[str, Any]:
        """Convert only dirty fields to input type.

        Returns:
            Dictionary of dirty input fields plus ID
        """
        # Start with ID which is always required for updates
        data = {"id": self.id}

        # Get set of dirty fields (fields whose values have changed)
        dirty_fields = {
            field
            for field in self.__tracked_fields__
            if field in self.__original_values__
            and getattr(self, field) != self.__original_values__[field]
        }

        # Process dirty regular fields
        field_data = await self._process_fields(dirty_fields)
        data.update(field_data)

        # Process dirty relationships
        rel_data = await self._process_relationships(dirty_fields)
        data.update(rel_data)

        # Convert to update input and dict
        input_obj = SceneMarkerUpdateInput(**data)
        return {
            k: v
            for k, v in vars(input_obj).items()
            if not k.startswith("_") and v is not None and k != "client_mutation_id"
        }

    __relationships__ = {
        # Standard ID relationships
        "scene": ("scene_id", False),  # (target_field, is_list)
        "primary_tag": ("primary_tag_id", False),
        "tags": ("tag_ids", True),
    }


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
