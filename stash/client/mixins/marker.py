"""Marker-related client functionality."""

from typing import Any

from ... import fragments
from ...client_helpers import async_lru_cache
from ...types import FindSceneMarkersResultType, SceneMarker
from ..protocols import StashClientProtocol
from ..utils import sanitize_model_data


class MarkerClientMixin(StashClientProtocol):
    """Mixin for marker-related client methods."""

    @async_lru_cache(maxsize=3096, exclude_arg_indices=[0])  # exclude self
    async def find_marker(self, id: str) -> SceneMarker | None:
        """Find a scene marker by its ID.

        Args:
            id: The ID of the marker to find

        Returns:
            SceneMarker object if found, None otherwise
        """
        try:
            result = await self.execute(
                fragments.FIND_MARKER_QUERY,
                {"id": id},
            )
            if result and result.get("findSceneMarker"):
                # Sanitize model data before creating SceneMarker
                clean_data = sanitize_model_data(result["findSceneMarker"])
                return SceneMarker(**clean_data)
            return None
        except Exception:
            self.log.exception(f"Failed to find marker {id}")
            return None

    @async_lru_cache(maxsize=3096, exclude_arg_indices=[0])  # exclude self
    async def find_markers(
        self,
        filter_: dict[str, Any] = {"per_page": -1},
        marker_filter: dict[str, Any] | None = None,
        q: str | None = None,
    ) -> FindSceneMarkersResultType:
        """Find scene markers matching the given filters.

        Args:
            filter_: Optional general filter parameters:
                - q: str (search query)
                - direction: SortDirectionEnum (ASC/DESC)
                - page: int
                - per_page: int
                - sort: str (field to sort by)
            marker_filter: Optional marker-specific filter
            q: Optional search query (alternative to filter_["q"])

        Returns:
            FindSceneMarkersResultType containing:
                - count: Total number of matching markers
                - scene_markers: List of SceneMarker objects
        """
        try:
            # Add q to filter if provided
            if q is not None:
                filter_ = dict(filter_ or {})
                filter_["q"] = q

            result = await self.execute(
                fragments.FIND_MARKERS_QUERY,
                {"filter": filter_, "marker_filter": marker_filter},
            )
            return FindSceneMarkersResultType(**result["findSceneMarkers"])
        except Exception:
            self.log.exception("Failed to find markers")
            return FindSceneMarkersResultType(count=0, scene_markers=[])

    async def create_marker(self, marker: SceneMarker) -> SceneMarker:
        """Create a new scene marker in Stash.

        Args:
            marker: SceneMarker object with the data to create. Required fields:
                - title: Marker title
                - scene_id: ID of the scene this marker belongs to
                - seconds: Time in seconds where the marker occurs

        Returns:
            Created SceneMarker object with ID and any server-generated fields

        Raises:
            ValueError: If the marker data is invalid
            gql.TransportError: If the request fails
        """
        try:
            input_data = await marker.to_input()
            result = await self.execute(
                fragments.CREATE_MARKER_MUTATION,
                {"input": input_data},
            )
            # Clear caches since we've modified markers
            self.find_marker.cache_clear()
            self.find_markers.cache_clear()
            # Sanitize model data before creating SceneMarker
            clean_data = sanitize_model_data(result["sceneMarkerCreate"])
            return SceneMarker(**clean_data)
        except Exception:
            self.log.exception("Failed to create marker")
            raise

    async def scene_marker_tags(self, scene_id: str) -> list[dict[str, Any]]:
        """Get scene marker tags for a scene.

        Args:
            scene_id: Scene ID

        Returns:
            List of scene marker tags, each containing:
                - tag: Tag object
                - scene_markers: List of SceneMarker objects
        """
        try:
            result = await self.execute(
                fragments.SCENE_MARKER_TAG_QUERY,
                {"scene_id": scene_id},
            )
            return result["sceneMarkerTags"]
        except Exception:
            self.log.exception(f"Failed to get scene marker tags for scene {scene_id}")
            return []

    async def update_marker(self, marker: SceneMarker) -> SceneMarker:
        """Update an existing scene marker in Stash.

        Args:
            marker: SceneMarker object with updated data. Required fields:
                - id: Marker ID to update
                Any other fields that are set will be updated.
                Fields that are None will be ignored.

        Returns:
            Updated SceneMarker object with any server-generated fields

        Raises:
            ValueError: If the marker data is invalid
            gql.TransportError: If the request fails
        """
        try:
            input_data = await marker.to_input()

            # Skip update if only ID is present (no actual changes)
            if set(input_data.keys()) <= {"id"}:
                self.log.debug(f"No changes to update for marker {marker.id}")
                return marker

            result = await self.execute(
                fragments.UPDATE_MARKER_MUTATION,
                {"input": input_data},
            )
            # Clear caches since we've modified a marker
            self.find_marker.cache_clear()
            self.find_markers.cache_clear()
            # Sanitize model data before creating SceneMarker
            clean_data = sanitize_model_data(result["sceneMarkerUpdate"])
            updated_marker = SceneMarker(**clean_data)
            # Mark as clean since we just saved
            updated_marker.mark_clean()
            return updated_marker
        except Exception:
            self.log.exception("Failed to update marker")
            raise
