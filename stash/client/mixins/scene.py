"""Scene-related client functionality."""

from typing import Any

from ... import fragments
from ...client_helpers import async_lru_cache
from ...types import FindScenesResultType, Scene
from ..protocols import StashClientProtocol
from ..utils import sanitize_model_data


class SceneClientMixin(StashClientProtocol):
    """Mixin for scene-related client methods."""

    @async_lru_cache(maxsize=3096, exclude_arg_indices=[0])  # exclude self
    async def find_scene(self, id: str) -> Scene | None:
        """Find a scene by its ID.

        Args:
            id: The ID of the scene to find

        Returns:
            Scene object if found, None otherwise

        Raises:
            ValueError: If scene ID is None or empty

        Examples:
            Find a scene and check its title:
            ```python
            scene = await client.find_scene("123")
            if scene:
                print(f"Found scene: {scene.title}")
            ```

            Access scene relationships:
            ```python
            scene = await client.find_scene("123")
            if scene:
                # Get performer names
                performers = [p.name for p in scene.performers]
                # Get studio name
                studio_name = scene.studio.name if scene.studio else None
                # Get tag names
                tags = [t.name for t in scene.tags]
            ```

            Check scene paths:
            ```python
            scene = await client.find_scene("123")
            if scene:
                # Get streaming URL
                stream_url = scene.paths.stream
                # Get preview URL
                preview_url = scene.paths.preview
            ```
        """
        # Validate scene ID
        if id is None or id == "":
            raise ValueError("Scene ID cannot be empty")

        try:
            result = await self.execute(
                fragments.FIND_SCENE_QUERY,
                {"id": id},
            )
        except Exception:
            self.log.exception(f"Failed to find scene {id}")
            return None
        else:
            if result and result.get("findScene"):
                # Sanitize model data before creating Scene
                clean_data = sanitize_model_data(result["findScene"])
                return Scene(**clean_data)
            return None

    @async_lru_cache(maxsize=3096, exclude_arg_indices=[0])  # exclude self
    async def find_scenes(
        self,
        filter_: dict[str, Any] | None = None,
        scene_filter: dict[str, Any] | None = None,
        q: str | None = None,
    ) -> FindScenesResultType:
        """Find scenes matching the given filters.

        Args:
            filter_: Optional general filter parameters:
                - q: str (search query)
                - direction: SortDirectionEnum (ASC/DESC)
                - page: int
                - per_page: int
                - sort: str (field to sort by)
            q: Optional search query (alternative to filter_["q"])
            scene_filter: Optional scene-specific filter:
                - file_count: IntCriterionInput
                - is_missing: str (what data is missing)
                - organized: bool
                - path: StringCriterionInput
                - performer_count: IntCriterionInput
                - performer_tags: HierarchicalMultiCriterionInput
                - performers: MultiCriterionInput
                - rating100: IntCriterionInput
                - resolution: ResolutionEnum
                - studios: HierarchicalMultiCriterionInput
                - tag_count: IntCriterionInput
                - tags: HierarchicalMultiCriterionInput
                - title: StringCriterionInput

        Returns:
            FindScenesResultType containing:
                - count: Total number of matching scenes
                - duration: Total duration in seconds
                - filesize: Total size in bytes
                - scenes: List of Scene objects

        Examples:
            Find all organized scenes:
            ```python
            result = await client.find_scenes(
                scene_filter={"organized": True}
            )
            print(f"Found {result.count} organized scenes")
            for scene in result.scenes:
                print(f"- {scene.title}")
            ```

            Find scenes with specific performers:
            ```python
            result = await client.find_scenes(
                scene_filter={
                    "performers": {
                        "value": ["performer1", "performer2"],
                        "modifier": "INCLUDES_ALL"
                    }
                }
            )
            ```

            Find scenes with high rating and sort by date:
            ```python
            result = await client.find_scenes(
                filter_={
                    "direction": "DESC",
                    "sort": "date",
                },
                scene_filter={
                    "rating100": {
                        "value": 80,
                        "modifier": "GREATER_THAN"
                    }
                }
            )
            ```

            Paginate results:
            ```python
            result = await client.find_scenes(
                filter_={
                    "page": 1,
                    "per_page": 25,
                }
            )
            ```
        """
        if filter_ is None:
            filter_ = {"per_page": -1}
        try:
            # Add q to filter if provided
            if q is not None:
                filter_ = dict(filter_ or {})
                filter_["q"] = q

            result = await self.execute(
                fragments.FIND_SCENES_QUERY,
                {"filter": filter_, "scene_filter": scene_filter},
            )
            return FindScenesResultType(**result["findScenes"])
        except Exception:
            self.log.exception("Failed to find scenes")
            return FindScenesResultType(count=0, duration=0, filesize=0, scenes=[])

    async def create_scene(self, scene: Scene) -> Scene:
        """Create a new scene in Stash.

        Args:
            scene: Scene object with the data to create. Required fields:
                - title: Scene title
                - urls: List of URLs associated with the scene
                - organized: Whether the scene is organized

                Note: created_at and updated_at are handled by Stash

        Returns:
            Created Scene object with ID and any server-generated fields

        Raises:
            ValueError: If the scene data is invalid
            gql.TransportError: If the request fails

        Examples:
            Create a basic scene:
            ```python
            scene = Scene(
                title="My Scene",
                urls=["https://example.com/scene"],
                organized=True,  # created_at and updated_at handled by Stash
            )
            created = await client.create_scene(scene)
            print(f"Created scene with ID: {created.id}")
            ```

            Create scene with relationships:
            ```python
            scene = Scene(
                title="My Scene",
                urls=["https://example.com/scene"],
                organized=True,  # created_at and updated_at handled by Stash
                # Add relationships
                performers=[performer1, performer2],
                studio=studio,
                tags=[tag1, tag2],
            )
            created = await client.create_scene(scene)
            ```

            Create scene with metadata:
            ```python
            scene = Scene(
                title="My Scene",
                urls=["https://example.com/scene"],
                organized=True,  # created_at and updated_at handled by Stash
                # Add metadata
                details="Scene description",
                date="2024-01-31",
                rating100=85,
                code="SCENE123",
            )
            created = await client.create_scene(scene)
            ```
        """
        try:
            input_data = await scene.to_input()
            result = await self.execute(
                fragments.CREATE_SCENE_MUTATION,
                {"input": input_data},
            )
            # Clear caches since we've modified scenes
            self.find_scene.cache_clear()
            self.find_scenes.cache_clear()
            # Sanitize model data before creating Scene
            clean_data = sanitize_model_data(result["sceneCreate"])
            return Scene(**clean_data)
        except Exception:
            self.log.exception("Failed to create scene")
            raise

    async def update_scene(self, scene: Scene) -> Scene:
        """Update an existing scene in Stash.

        Args:
            scene: Scene object with updated data. Required fields:
                - id: Scene ID to update
                Any other fields that are set will be updated.
                Fields that are None will be ignored.

        Returns:
            Updated Scene object with any server-generated fields

        Raises:
            ValueError: If the scene data is invalid
            gql.TransportError: If the request fails

        Examples:
            Update scene title and rating:
            ```python
            scene = await client.find_scene("123")
            if scene:
                scene.title = "New Title"
                scene.rating100 = 90
                updated = await client.update_scene(scene)
                print(f"Updated scene: {updated.title}")
            ```

            Update scene relationships:
            ```python
            scene = await client.find_scene("123")
            if scene:
                # Add new performers
                scene.performers.extend([new_performer1, new_performer2])
                # Set new studio
                scene.studio = new_studio
                # Add new tags
                scene.tags.extend([new_tag1, new_tag2])
                updated = await client.update_scene(scene)
            ```

            Update scene metadata:
            ```python
            scene = await client.find_scene("123")
            if scene:
                # Update metadata
                scene.details = "New description"
                scene.date = "2024-01-31"
                scene.code = "NEWCODE123"
                scene.organized = True
                updated = await client.update_scene(scene)
            ```

            Update scene URLs:
            ```python
            scene = await client.find_scene("123")
            if scene:
                # Replace URLs
                scene.urls = [
                    "https://example.com/new-url",
                ]
                updated = await client.update_scene(scene)
            ```

            Remove scene relationships:
            ```python
            scene = await client.find_scene("123")
            if scene:
                # Clear studio
                scene.studio = None
                # Clear performers
                scene.performers = []
                updated = await client.update_scene(scene)
            ```
        """
        try:
            input_data = await scene.to_input()

            # Skip update if only ID is present (no actual changes)
            if set(input_data.keys()) <= {"id"}:
                self.log.debug(f"No changes to update for scene {scene.id}")
                return scene

            result = await self.execute(
                fragments.UPDATE_SCENE_MUTATION,
                {"input": input_data},
            )
        except Exception:
            self.log.exception("Failed to update scene")
            raise
        else:
            # Clear caches since we've modified a scene
            self.find_scene.cache_clear()
            self.find_scenes.cache_clear()

            # Check if the scene is in the cache and remove it
            if (
                hasattr(scene, "id")
                and scene.id is not None
                and hasattr(self, "scene_cache")
                and scene.id in self.scene_cache
            ):
                del self.scene_cache[scene.id]

            # Create a Scene instance from the result
            # Sanitize model data before creating Scene
            clean_data = sanitize_model_data(result["sceneUpdate"])
            updated_scene = Scene(**clean_data)
            # Mark as clean since we just saved
            updated_scene.mark_clean()
            return updated_scene

    async def find_duplicate_scenes(
        self,
        distance: int | None = None,
        duration_diff: float | None = None,
    ) -> list[list[Scene]]:
        """Find groups of scenes that are perceptual duplicates.

        Args:
            distance: Maximum phash distance between scenes to be considered duplicates
            duration_diff: Maximum difference in seconds between scene durations

        Returns:
            List of scene groups, where each group is a list of duplicate scenes
        """
        try:
            result = await self.execute(
                fragments.FIND_DUPLICATE_SCENES_QUERY,
                {
                    "distance": distance,
                    "duration_diff": duration_diff,
                },
            )
            return [
                [Scene(**sanitize_model_data(scene)) for scene in group]
                for group in result["findDuplicateScenes"]
            ]
        except Exception:
            self.log.exception("Failed to find duplicate scenes")
            return []

    async def parse_scene_filenames(
        self,
        filter_: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Parse scene filenames using the given configuration.

        Args:
            filter_: Optional filter to select scenes
            config: Parser configuration:
                - whitespace_separator: bool
                - field_separator: str
                - fields: list[str]

        Returns:
            Dictionary containing parse results
        """
        try:
            result = await self.execute(
                fragments.PARSE_SCENE_FILENAMES_QUERY,
                {
                    "filter": filter_,
                    "config": config,
                },
            )
            return result["parseSceneFilenames"]
        except Exception:
            self.log.exception("Failed to parse scene filenames")
            return {}

    async def scene_wall(self, q: str | None = None) -> list[Scene]:
        """Get random scenes for the wall.

        Args:
            q: Optional search query

        Returns:
            List of random Scene objects
        """
        try:
            result = await self.execute(
                fragments.SCENE_WALL_QUERY,
                {"q": q},
            )
            return [
                Scene(**sanitize_model_data(scene)) for scene in result["sceneWall"]
            ]
        except Exception:
            self.log.exception("Failed to get scene wall")
            return []

    async def bulk_scene_update(self, input_data: dict[str, Any]) -> list[Scene]:
        """Update multiple scenes at once.

        Args:
            input_data: Dictionary containing:
                - ids: List of scene IDs to update
                - Any other fields to update on all scenes

        Returns:
            List of updated Scene objects
        """
        try:
            result = await self.execute(
                fragments.BULK_SCENE_UPDATE_MUTATION,
                {"input": input_data},
            )
            # Clear caches since we've modified scenes
            self._find_scene_cache.cache_clear()
            self._find_scenes_cache.cache_clear()
            return [
                Scene(**sanitize_model_data(scene))
                for scene in result["bulkSceneUpdate"]
            ]
        except Exception:
            self.log.exception("Failed to bulk update scenes")
            raise

    async def scenes_update(self, scenes: list[Scene]) -> list[Scene]:
        """Update multiple scenes with individual data.

        Args:
            scenes: List of Scene objects to update, each must have an ID

        Returns:
            List of updated Scene objects
        """
        try:
            result = await self.execute(
                fragments.SCENES_UPDATE_MUTATION,
                {"input": [await scene.to_input() for scene in scenes]},
            )
            # Clear caches since we've modified scenes
            if hasattr(self, "find_scene") and hasattr(self.find_scene, "cache_clear"):
                self.find_scene.cache_clear()

            if hasattr(self, "find_scenes") and hasattr(
                self.find_scenes, "cache_clear"
            ):
                self.find_scenes.cache_clear()

            # Clear each scene from the scene_cache
            if hasattr(self, "scene_cache"):
                for scene in scenes:
                    if hasattr(scene, "id") and scene.id in self.scene_cache:
                        del self.scene_cache[scene.id]

            return [
                Scene(**sanitize_model_data(scene)) for scene in result["scenesUpdate"]
            ]
        except Exception:
            self.log.exception("Failed to update scenes")
            raise

    async def scene_generate_screenshot(
        self,
        id: str,
        at: float | None = None,
    ) -> str:
        """Generate a screenshot for a scene.

        Args:
            id: Scene ID
            at: Optional time in seconds to take screenshot at

        Returns:
            Path to the generated screenshot

        Raises:
            ValueError: If the scene is not found
            gql.TransportError: If the request fails
        """
        try:
            result = await self.execute(
                fragments.SCENE_GENERATE_SCREENSHOT_MUTATION,
                {"id": id, "at": at},
            )
        except Exception:
            self.log.exception(f"Failed to generate screenshot for scene {id}")
            raise
        else:
            # Clear scene cache since screenshot generation modifies the scene
            if hasattr(self, "scene_cache") and id in self.scene_cache:
                del self.scene_cache[id]

            if hasattr(self, "find_scene") and hasattr(self.find_scene, "cache_clear"):
                self.find_scene.cache_clear()

            if hasattr(self, "find_scenes") and hasattr(
                self.find_scenes, "cache_clear"
            ):
                self.find_scenes.cache_clear()

            if result and "sceneGenerateScreenshot" in result:
                return result["sceneGenerateScreenshot"]
            return ""
