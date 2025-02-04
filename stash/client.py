"""GraphQL client for Stash."""

import asyncio
import logging
import re
import time
from typing import Any, TypeVar

import httpx
import strawberry

from . import fragments
from .client_helpers import str_compare
from .types import (
    AutoTagMetadataOptions,
    ConfigDefaultSettingsResult,
    CriterionModifier,
    FindGalleriesResultType,
    FindImagesResultType,
    FindJobInput,
    FindPerformersResultType,
    FindSceneMarkersResultType,
    FindScenesResultType,
    FindStudiosResultType,
    FindTagsResultType,
    Gallery,
    GenerateMetadataOptions,
    Image,
    Job,
    JobStatus,
    OnMultipleMatch,
    Performer,
    ScanMetadataInput,
    ScanMetadataOptions,
    Scene,
    SceneMarker,
    Studio,
    Tag,
)

T = TypeVar("T")


@strawberry.type
class StashClient:
    """GraphQL client for Stash."""

    def __init__(
        self,
        conn: dict[str, Any] = None,
        verify_ssl: bool = True,
    ) -> None:
        """Initialize client.

        Args:
            conn: Connection details dictionary with:
                - Scheme: Protocol (default: "http")
                - Host: Hostname (default: "localhost")
                - Port: Port number (default: 9999)
                - ApiKey: Optional API key
                - Logger: Optional logger instance
            verify_ssl: Whether to verify SSL certificates
        """
        conn = conn or {}

        # Set up logging
        self.log = conn.get("Logger", logging.getLogger(__name__))

        # Build URL
        scheme = conn.get("Scheme", "http")
        host = conn.get("Host", "localhost")
        if host == "0.0.0.0":  # nosec B104 - Converting all-interfaces to localhost
            host = "127.0.0.1"
        port = conn.get("Port", 9999)
        self.url = f"{scheme}://{host}:{port}/graphql"

        # Set up HTTP client
        self.client = httpx.AsyncClient(
            verify=verify_ssl,
            headers=(
                {
                    "ApiKey": conn.get("ApiKey", ""),
                }
                if conn.get("ApiKey")
                else {}
            ),
        )

        self.log.debug(f"Using Stash endpoint at {self.url}")

    async def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL query or mutation.

        This is a low-level method that executes raw GraphQL queries.
        You should prefer using the high-level methods like find_scene,
        create_performer, etc. instead of this method directly.

        Args:
            query: GraphQL query or mutation string
            variables: Optional query variables dictionary

        Returns:
            Query response data dictionary containing the "data" field
            from the GraphQL response.

        Raises:
            httpx.HTTPError: If the HTTP request fails
            ValueError: If the response contains GraphQL errors
            Exception: If any other error occurs during execution

        Examples:
            Execute a custom query:
            ```python
            result = await client.execute(
                \"\"\"
                query FindScene($id: ID!) {
                    findScene(id: $id) {
                        id
                        title
                    }
                }
                \"\"\",
                {"id": "123"},
            )
            if result and result.get("findScene"):
                print(f"Found scene: {result['findScene']['title']}")
            ```

            Execute a custom mutation:
            ```python
            result = await client.execute(
                \"\"\"
                mutation UpdateScene($input: SceneUpdateInput!) {
                    sceneUpdate(input: $input) {
                        id
                        title
                    }
                }
                \"\"\",
                {
                    "input": {
                        "id": "123",
                        "title": "New Title",
                    }
                },
            )
            ```

            Handle errors:
            ```python
            try:
                result = await client.execute(query, variables)
            except httpx.HTTPError as e:
                print(f"HTTP error: {e}")
            except ValueError as e:
                print(f"GraphQL error: {e}")
            except Exception as e:
                print(f"Other error: {e}")
            ```
        """
        try:
            response = await self.client.post(
                self.url,
                json={
                    "query": query,
                    "variables": variables or {},
                },
            )
            response.raise_for_status()
            result = response.json()

            if "errors" in result:
                self.log.error(f"GraphQL errors: {result['errors']}")
                raise ValueError(f"GraphQL errors: {result['errors']}")

            return result["data"]

        except Exception as e:
            self.log.error(f"Failed to execute query: {e}")
            raise

    def _parse_obj_for_ID(self, param, str_key="name"):
        if isinstance(param, str):
            try:
                return int(param)
            except ValueError:
                return {str_key: param.strip()}
        elif isinstance(param, dict):
            if param.get("stored_id"):
                return int(param["stored_id"])
            if param.get("id"):
                return int(param["id"])
        return param

    # Scene methods
    async def find_scene(self, id: str) -> Scene | None:
        """Find a scene by its ID.

        Args:
            id: The ID of the scene to find

        Returns:
            Scene object if found, None otherwise

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
        try:
            result = await self.execute(
                fragments.FIND_SCENE_QUERY,
                {"id": id},
            )
            if result and result.get("findScene"):
                return Scene(**result["findScene"])
            return None
        except Exception as e:
            self.log.error(f"Failed to find scene {id}: {e}")
            return None

    async def find_scenes(
        self,
        filter_: dict[str, Any] | None = None,
        scene_filter: dict[str, Any] | None = None,
    ) -> FindScenesResultType:
        """Find scenes matching the given filters.

        Args:
            filter_: Optional general filter parameters:
                - direction: SortDirectionEnum (ASC/DESC)
                - page: int
                - per_page: int
                - sort: str (field to sort by)
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
        try:
            result = await self.execute(
                fragments.FIND_SCENES_QUERY,
                {"filter": filter_, "scene_filter": scene_filter},
            )
            return FindScenesResultType(**result["findScenes"])
        except Exception as e:
            self.log.error(f"Failed to find scenes: {e}")
            return FindScenesResultType(count=0, duration=0, filesize=0, scenes=[])

    async def create_scene(self, scene: Scene) -> Scene:
        """Create a new scene in Stash.

        Args:
            scene: Scene object with the data to create. Required fields:
                - title: Scene title
                - urls: List of URLs associated with the scene
                - organized: Whether the scene is organized
                - created_at: Creation timestamp
                - updated_at: Last update timestamp

        Returns:
            Created Scene object with ID and any server-generated fields

        Raises:
            ValueError: If the scene data is invalid
            httpx.HTTPError: If the request fails

        Examples:
            Create a basic scene:
            ```python
            scene = Scene(
                title="My Scene",
                urls=["https://example.com/scene"],
                organized=True,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            created = await client.create_scene(scene)
            print(f"Created scene with ID: {created.id}")
            ```

            Create scene with relationships:
            ```python
            scene = Scene(
                title="My Scene",
                urls=["https://example.com/scene"],
                organized=True,
                created_at=datetime.now(),
                updated_at=datetime.now(),
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
                organized=True,
                created_at=datetime.now(),
                updated_at=datetime.now(),
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
            result = await self.execute(
                fragments.CREATE_SCENE_MUTATION,
                {"input": scene.to_input()},
            )
            return Scene(**result["sceneCreate"])
        except Exception as e:
            self.log.error(f"Failed to create scene: {e}")
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
            httpx.HTTPError: If the request fails

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
                scene.urls = ["https://example.com/new-url"]
                # Or add new URL
                scene.urls.append("https://example.com/another-url")
                updated = await client.update_scene(scene)
            ```
        """
        try:
            result = await self.execute(
                fragments.UPDATE_SCENE_MUTATION,
                {"input": scene.to_input()},
            )
            return Scene(**result["sceneUpdate"])
        except Exception as e:
            self.log.error(f"Failed to update scene: {e}")
            raise

    # Performer methods
    async def find_performer(
        self,
        performer: int | str | dict,
        on_multiple: OnMultipleMatch = OnMultipleMatch.RETURN_FIRST,
    ) -> list[Performer] | Performer | None:
        """Find a performer by their ID.

        Args:
            performer (int, str, dict): The ID of the performer to find

        Returns:
            Performer object if found, None otherwise

        Examples:
            Find a performer and check their details:
            ```python
            performer = await client.find_performer("123")
            if performer:
                print(f"Found performer: {performer.name}")
                if performer.disambiguation:
                    print(f"Also known as: {performer.disambiguation}")
            ```

            Access performer stats:
            ```python
            performer = await client.find_performer("123")
            if performer:
                print(f"Scenes: {performer.scene_count}")
                print(f"Images: {performer.image_count}")
                print(f"Galleries: {performer.gallery_count}")
                print(f"Rating: {performer.rating100}/100")
            ```

            Check performer metadata:
            ```python
            performer = await client.find_performer("123")
            if performer:
                # Get URLs
                urls = performer.urls
                # Get demographics
                country = performer.country
                ethnicity = performer.ethnicity
                # Get measurements
                height = performer.height_cm
                measurements = performer.measurements
            ```

            Access performer relationships:
            ```python
            performer = await client.find_performer("123")
            if performer:
                # Get scenes
                scene_titles = [s.title for s in performer.scenes]
                # Get tags
                tag_names = [t.name for t in performer.tags]
                # Get custom fields
                custom_data = performer.custom_fields
            ```
        """
        performer = self._parse_obj_for_ID(performer)
        if isinstance(performer, int):
            try:
                result = await self.execute(
                    fragments.FIND_PERFORMER_QUERY,
                    {"id": performer},
                )
                if result and result.get("findPerformer"):
                    return Performer(**result["findPerformer"])
                return None
            except Exception as e:
                self.log.error(f"Failed to find performer {id}: {e}")
                return None
        if not performer:
            return None

        performer_filter = {}
        if performer.get("disambiguation"):
            performer_filter["disambiguation"] = {
                "value": performer["disambiguation"],
                "modifier": CriterionModifier.INCLUDES,
            }
            performer_filter["OR"] = {
                "aliases": {
                    "value": performer["disambiguation"],
                    "modifier": CriterionModifier.INCLUDES,
                }
            }
        performer_search = await self.find_performers(
            q=performer["name"],
            performer_filter=performer_filter,
        )
        performer_matches = self.__match_performer_alias(
            performer,
            performer_search,
        )
        if len(performer_matches) > 1:
            warn_msg = f"Matched multiple Performers to '{performer['name']}'"
            if on_multiple == OnMultipleMatch.RETURN_NONE:
                self.log.warning(f"{warn_msg} returning None")
                return None
            if on_multiple == OnMultipleMatch.RETURN_LIST:
                self.log.warning(f"{warn_msg} returning all matches")
                return [self.find_performer(p.id) for p in performer_matches]
            if on_multiple == OnMultipleMatch.RETURN_FIRST:
                self.log.warning(f"{warn_msg} returning first match")
        if len(performer_matches) > 0:
            return self.find_performer(performer_matches[0].id)

    def __match_performer_alias(
        self,
        search: dict,
        performers: FindPerformersResultType,
    ) -> list[Performer | None]:
        performer_matches = {}

        # attempt to match exclusively to primary name
        for p_dict in performers.performers:
            # Convert dict to Performer object
            p = Performer(**p_dict) if isinstance(p_dict, dict) else p_dict

            # Handle disambiguation
            search_disambiguation = search.get("disambiguation")
            if search_disambiguation and getattr(p, "disambiguation", None):
                # ignore disambiguation if it does not match search
                if search_disambiguation not in p.disambiguation:
                    continue

            if str_compare(search["name"], p.name):
                self.log.debug(
                    f'matched performer "{search["name"]}" to "{p.name}" ({p.id}) using primary name'
                )
                performer_matches[p.id] = p
                return list(performer_matches.values())

        # no match on primary name attempt aliases
        for p in performers.performers:
            aliases = []
            # new versions of stash NOTE: wont be needed after performer alias matching
            if p.alias_list:
                aliases = p.alias_list

            if not aliases:
                continue
            for alias in aliases:
                alias_search = search["name"]
                if search.get("disambiguation"):
                    alias_search += f' ({search["disambiguation"]})'
                parsed_alias = alias.strip()
                if str_compare(alias_search, parsed_alias):
                    self.log.info(
                        f'matched performer "{alias_search}" to "{p["name"]}" ({p["id"]}) using alias'
                    )
                    performer_matches[p.id] = p
        return list(performer_matches.values())

    async def find_performers(
        self,
        filter_: dict[str, Any] = {"per_page": -1},
        performer_filter: dict[str, Any] = {},
        q: str = "",
    ) -> FindPerformersResultType:
        """Find performers matching the given filters.

        Args:
            filter_: Optional general filter parameters:
                - direction: SortDirectionEnum (ASC/DESC)
                - page: int
                - per_page: int
                - sort: str (field to sort by)
            performer_filter: Optional performer-specific filter:
                - birth_year: IntCriterionInput
                - circumcised: CircumcisedEnum
                - country: StringCriterionInput
                - created_at: TimestampCriterionInput
                - ethnicity: StringCriterionInput
                - eye_color: StringCriterionInput
                - favorite: bool
                - filter_favorites: bool
                - gender: GenderEnum
                - hair_color: StringCriterionInput
                - height_cm: IntCriterionInput
                - ignore_auto_tag: bool
                - name: StringCriterionInput
                - rating100: IntCriterionInput
                - scene_count: IntCriterionInput
                - studios: HierarchicalMultiCriterionInput
                - tag_count: IntCriterionInput
                - tags: HierarchicalMultiCriterionInput
                - updated_at: TimestampCriterionInput

        Returns:
            FindPerformersResultType containing:
                - count: Total number of matching performers
                - performers: List of Performer objects

        Examples:
            Find all favorite performers:
            ```python
            result = await client.find_performers(
                performer_filter={"favorite": True}
            )
            print(f"Found {result.count} favorite performers")
            for performer in result.performers:
                print(f"- {performer.name}")
            ```

            Find performers by gender and country:
            ```python
            result = await client.find_performers(
                performer_filter={
                    "gender": "FEMALE",
                    "country": {
                        "value": "USA",
                        "modifier": "EQUALS"
                    }
                }
            )
            ```

            Find performers with high rating and sort by name:
            ```python
            result = await client.find_performers(
                filter_={
                    "direction": "ASC",
                    "sort": "name",
                },
                performer_filter={
                    "rating100": {
                        "value": 80,
                        "modifier": "GREATER_THAN"
                    }
                }
            )
            ```

            Find performers with specific tags:
            ```python
            result = await client.find_performers(
                performer_filter={
                    "tags": {
                        "value": ["tag1", "tag2"],
                        "modifier": "INCLUDES_ALL",
                        "depth": 0  # Direct tags only
                    }
                }
            )
            ```
        """
        if q:
            filter_["q"] = q
        try:
            result = await self.execute(
                fragments.FIND_PERFORMERS_QUERY,
                {"filter": filter_, "performer_filter": performer_filter},
            )
            return FindPerformersResultType(**result["findPerformers"])
        except Exception as e:
            self.log.error(f"Failed to find performers: {e}")
            return FindPerformersResultType(count=0, performers=[])

    async def create_performer(self, performer: Performer) -> Performer:
        """Create a new performer in Stash.

        Args:
            performer: Performer object with the data to create. Required fields:
                - name: Performer name
                - created_at: Creation timestamp
                - updated_at: Last update timestamp

        Returns:
            Created Performer object with ID and any server-generated fields

        Raises:
            ValueError: If the performer data is invalid
            httpx.HTTPError: If the request fails

        Examples:
            Create a basic performer:
            ```python
            performer = Performer(
                name="Performer Name",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            created = await client.create_performer(performer)
            print(f"Created performer with ID: {created.id}")
            ```

            Create performer with demographics:
            ```python
            performer = Performer(
                name="Performer Name",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add demographics
                gender=GenderEnum.FEMALE,
                birthdate="1990-01-01",
                ethnicity="Caucasian",
                country="USA",
                height_cm=170,
            )
            created = await client.create_performer(performer)
            ```

            Create performer with URLs and social media:
            ```python
            performer = Performer(
                name="Performer Name",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add URLs
                urls=[
                    "https://example.com/performer",
                    "https://twitter.com/performer",
                    "https://instagram.com/performer",
                ],
            )
            created = await client.create_performer(performer)
            ```

            Create performer with tags and custom fields:
            ```python
            performer = Performer(
                name="Performer Name",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add tags
                tags=[tag1, tag2],
                # Add custom fields
                custom_fields={
                    "field1": "value1",
                    "field2": "value2",
                },
            )
            created = await client.create_performer(performer)
            ```
        """
        try:
            result = await self.execute(
                fragments.CREATE_PERFORMER_MUTATION,
                {"input": performer.to_input()},
            )
            return Performer(**result["performerCreate"])
        except Exception as e:
            self.log.error(f"Failed to create performer: {e}")
            raise

    async def update_performer(self, performer: Performer) -> Performer:
        """Update an existing performer in Stash.

        Args:
            performer: Performer object with updated data. Required fields:
                - id: Performer ID to update
                Any other fields that are set will be updated.
                Fields that are None will be ignored.

        Returns:
            Updated Performer object with any server-generated fields

        Raises:
            ValueError: If the performer data is invalid
            httpx.HTTPError: If the request fails

        Examples:
            Update performer name and rating:
            ```python
            performer = await client.find_performer("123")
            if performer:
                performer.name = "New Name"
                performer.rating100 = 90
                updated = await client.update_performer(performer)
                print(f"Updated performer: {updated.name}")
            ```

            Update performer demographics:
            ```python
            performer = await client.find_performer("123")
            if performer:
                # Update demographics
                performer.country = "New Country"
                performer.ethnicity = "New Ethnicity"
                performer.height_cm = 175
                performer.measurements = "New Measurements"
                updated = await client.update_performer(performer)
            ```

            Update performer URLs:
            ```python
            performer = await client.find_performer("123")
            if performer:
                # Replace URLs
                performer.urls = [
                    "https://example.com/new-url",
                    "https://twitter.com/new-handle",
                ]
                # Or add new URL
                performer.urls.append("https://instagram.com/new-handle")
                updated = await client.update_performer(performer)
            ```

            Update performer tags and custom fields:
            ```python
            performer = await client.find_performer("123")
            if performer:
                # Update tags
                performer.tags = [new_tag1, new_tag2]
                # Update custom fields
                performer.custom_fields = {
                    "new_field1": "new_value1",
                    "new_field2": "new_value2",
                }
                updated = await client.update_performer(performer)
            ```

            Update performer flags:
            ```python
            performer = await client.find_performer("123")
            if performer:
                # Update flags
                performer.favorite = True
                performer.ignore_auto_tag = False
                updated = await client.update_performer(performer)
            ```
        """
        try:
            result = await self.execute(
                fragments.UPDATE_PERFORMER_MUTATION,
                {"input": performer.to_input()},
            )
            return Performer(**result["performerUpdate"])
        except Exception as e:
            self.log.error(f"Failed to update performer: {e}")
            raise

    # Studio methods
    async def find_studio(self, id: str) -> Studio | None:
        """Find a studio by its ID.

        Args:
            id: The ID of the studio to find

        Returns:
            Studio object if found, None otherwise

        Examples:
            Find a studio and check its details:
            ```python
            studio = await client.find_studio("123")
            if studio:
                print(f"Found studio: {studio.name}")
                if studio.url:
                    print(f"Website: {studio.url}")
            ```

            Access studio stats:
            ```python
            studio = await client.find_studio("123")
            if studio:
                print(f"Scenes: {studio.scene_count}")
                print(f"Images: {studio.image_count}")
                print(f"Galleries: {studio.gallery_count}")
                print(f"Performers: {studio.performer_count}")
                print(f"Rating: {studio.rating100}/100")
            ```

            Check studio hierarchy:
            ```python
            studio = await client.find_studio("123")
            if studio:
                # Get parent studio
                if studio.parent_studio:
                    print(f"Parent: {studio.parent_studio.name}")
                # Get child studios
                for child in studio.child_studios:
                    print(f"Child: {child.name}")
            ```

            Access studio relationships:
            ```python
            studio = await client.find_studio("123")
            if studio:
                # Get tags
                tag_names = [t.name for t in studio.tags]
                # Get aliases
                aliases = studio.aliases
                # Get StashIDs
                stash_ids = [
                    f"{s.endpoint}: {s.stash_id}"
                    for s in studio.stash_ids
                ]
            ```
        """
        try:
            result = await self.execute(
                fragments.FIND_STUDIO_QUERY,
                {"id": id},
            )
            if result and result.get("findStudio"):
                return Studio(**result["findStudio"])
            return None
        except Exception as e:
            self.log.error(f"Failed to find studio {id}: {e}")
            return None

    async def find_studios(
        self,
        filter_: dict[str, Any] = {"per_page": -1},
        studio_filter: dict[str, Any] = {},
        q: str = "",
    ) -> FindStudiosResultType:
        """Find studios matching the given filters.

        Args:
            filter_: Optional general filter parameters:
                - direction: SortDirectionEnum (ASC/DESC)
                - page: int
                - per_page: int
                - sort: str (field to sort by)
            studio_filter: Optional studio-specific filter:
                - created_at: TimestampCriterionInput
                - favorite: bool
                - filter_favorites: bool
                - ignore_auto_tag: bool
                - is_missing: str (what data is missing)
                - name: StringCriterionInput
                - parent_id: IntCriterionInput
                - rating100: IntCriterionInput
                - scene_count: IntCriterionInput
                - stash_id: StringCriterionInput
                - tag_count: IntCriterionInput
                - tags: HierarchicalMultiCriterionInput
                - updated_at: TimestampCriterionInput
                - url: StringCriterionInput

        Returns:
            FindStudiosResultType containing:
                - count: Total number of matching studios
                - studios: List of Studio objects

        Examples:
            Find all favorite studios:
            ```python
            result = await client.find_studios(
                studio_filter={"favorite": True}
            )
            print(f"Found {result.count} favorite studios")
            for studio in result.studios:
                print(f"- {studio.name}")
            ```

            Find studios by name pattern:
            ```python
            result = await client.find_studios(
                studio_filter={
                    "name": {
                        "value": "Studio",
                        "modifier": "INCLUDES"
                    }
                }
            )
            ```

            Find studios with high rating and sort by name:
            ```python
            result = await client.find_studios(
                filter_={
                    "direction": "ASC",
                    "sort": "name",
                },
                studio_filter={
                    "rating100": {
                        "value": 80,
                        "modifier": "GREATER_THAN"
                    }
                }
            )
            ```

            Find studios with specific tags:
            ```python
            result = await client.find_studios(
                studio_filter={
                    "tags": {
                        "value": ["tag1", "tag2"],
                        "modifier": "INCLUDES_ALL",
                        "depth": 0  # Direct tags only
                    }
                }
            )
            ```

            Find studios by parent:
            ```python
            result = await client.find_studios(
                studio_filter={
                    "parent_id": {
                        "value": parent_id,
                        "modifier": "EQUALS"
                    }
                }
            )
            ```
        """
        if q:
            filter_["q"] = q
        try:
            result = await self.execute(
                fragments.FIND_STUDIOS_QUERY,
                {"filter": filter_, "studio_filter": studio_filter},
            )
            return FindStudiosResultType(**result["findStudios"])
        except Exception as e:
            self.log.error(f"Failed to find studios: {e}")
            return FindStudiosResultType(count=0, studios=[])

    async def create_studio(self, studio: Studio) -> Studio:
        """Create a new studio in Stash.

        Args:
            studio: Studio object with the data to create. Required fields:
                - name: Studio name
                - created_at: Creation timestamp
                - updated_at: Last update timestamp

        Returns:
            Created Studio object with ID and any server-generated fields

        Raises:
            ValueError: If the studio data is invalid
            httpx.HTTPError: If the request fails

        Examples:
            Create a basic studio:
            ```python
            studio = Studio(
                name="Studio Name",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            created = await client.create_studio(studio)
            print(f"Created studio with ID: {created.id}")
            ```

            Create studio with details:
            ```python
            studio = Studio(
                name="Studio Name",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add details
                url="https://example.com/studio",
                details="Studio description",
                rating100=85,
                favorite=True,
            )
            created = await client.create_studio(studio)
            ```

            Create studio with parent:
            ```python
            studio = Studio(
                name="Studio Name",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Set parent studio
                parent_studio=parent_studio,
            )
            created = await client.create_studio(studio)
            ```

            Create studio with tags and StashIDs:
            ```python
            studio = Studio(
                name="Studio Name",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add tags
                tags=[tag1, tag2],
                # Add StashIDs
                stash_ids=[
                    StashID(endpoint="stash-box", stash_id="123"),
                ],
            )
            created = await client.create_studio(studio)
            ```
        """
        try:
            result = await self.execute(
                fragments.CREATE_STUDIO_MUTATION,
                {"input": studio.to_input()},
            )
            return Studio(**result["studioCreate"])
        except Exception as e:
            self.log.error(f"Failed to create studio: {e}")
            raise

    async def update_studio(self, studio: Studio) -> Studio:
        """Update an existing studio in Stash.

        Args:
            studio: Studio object with updated data. Required fields:
                - id: Studio ID to update
                Any other fields that are set will be updated.
                Fields that are None will be ignored.

        Returns:
            Updated Studio object with any server-generated fields

        Raises:
            ValueError: If the studio data is invalid
            httpx.HTTPError: If the request fails

        Examples:
            Update studio name and rating:
            ```python
            studio = await client.find_studio("123")
            if studio:
                studio.name = "New Name"
                studio.rating100 = 90
                updated = await client.update_studio(studio)
                print(f"Updated studio: {updated.name}")
            ```

            Update studio details:
            ```python
            studio = await client.find_studio("123")
            if studio:
                # Update details
                studio.url = "https://example.com/new-url"
                studio.details = "New description"
                studio.favorite = True
                updated = await client.update_studio(studio)
            ```

            Update studio hierarchy:
            ```python
            studio = await client.find_studio("123")
            if studio:
                # Set new parent studio
                studio.parent_studio = new_parent
                # Update aliases
                studio.aliases = ["alias1", "alias2"]
                updated = await client.update_studio(studio)
            ```

            Update studio tags and StashIDs:
            ```python
            studio = await client.find_studio("123")
            if studio:
                # Update tags
                studio.tags = [new_tag1, new_tag2]
                # Update StashIDs
                studio.stash_ids = [
                    StashID(endpoint="stash-box", stash_id="456"),
                ]
                updated = await client.update_studio(studio)
            ```

            Update studio flags:
            ```python
            studio = await client.find_studio("123")
            if studio:
                # Update flags
                studio.favorite = True
                studio.ignore_auto_tag = False
                updated = await client.update_studio(studio)
            ```
        """
        try:
            result = await self.execute(
                fragments.UPDATE_STUDIO_MUTATION,
                {"input": studio.to_input()},
            )
            return Studio(**result["studioUpdate"])
        except Exception as e:
            self.log.error(f"Failed to update studio: {e}")
            raise

    # Tag methods
    async def find_tag(self, id: str) -> Tag | None:
        """Find a tag by its ID.

        Args:
            id: The ID of the tag to find

        Returns:
            Tag object if found, None otherwise

        Examples:
            Find a tag and check its details:
            ```python
            tag = await client.find_tag("123")
            if tag:
                print(f"Found tag: {tag.name}")
                if tag.description:
                    print(f"Description: {tag.description}")
            ```

            Access tag stats:
            ```python
            tag = await client.find_tag("123")
            if tag:
                print(f"Scenes: {tag.scene_count}")
                print(f"Scene Markers: {tag.scene_marker_count}")
                print(f"Images: {tag.image_count}")
                print(f"Galleries: {tag.gallery_count}")
                print(f"Performers: {tag.performer_count}")
                print(f"Studios: {tag.studio_count}")
            ```

            Check tag hierarchy:
            ```python
            tag = await client.find_tag("123")
            if tag:
                # Get parent tags
                print(f"Parents: {tag.parent_count}")
                for parent in tag.parents:
                    print(f"- {parent.name}")
                # Get child tags
                print(f"Children: {tag.child_count}")
                for child in tag.children:
                    print(f"- {child.name}")
            ```

            Access tag metadata:
            ```python
            tag = await client.find_tag("123")
            if tag:
                # Get aliases
                aliases = tag.aliases
                # Get image path
                image = tag.image_path
                # Check flags
                is_favorite = tag.favorite
                ignore_auto = tag.ignore_auto_tag
            ```
        """
        try:
            result = await self.execute(
                fragments.FIND_TAG_QUERY,
                {"id": id},
            )
            if result and result.get("findTag"):
                return Tag(**result["findTag"])
            return None
        except Exception as e:
            self.log.error(f"Failed to find tag {id}: {e}")
            return None

    async def find_tags(
        self,
        filter_: dict[str, Any] | None = None,
        tag_filter: dict[str, Any] | None = None,
    ) -> FindTagsResultType:
        """Find tags matching the given filters.

        Args:
            filter_: Optional general filter parameters:
                - direction: SortDirectionEnum (ASC/DESC)
                - page: int
                - per_page: int
                - sort: str (field to sort by)
            tag_filter: Optional tag-specific filter:
                - count: IntCriterionInput
                - created_at: TimestampCriterionInput
                - description: StringCriterionInput
                - favorite: bool
                - filter_favorites: bool
                - ignore_auto_tag: bool
                - is_missing: str (what data is missing)
                - name: StringCriterionInput
                - parent_count: IntCriterionInput
                - child_count: IntCriterionInput
                - scene_count: IntCriterionInput
                - scene_marker_count: IntCriterionInput
                - image_count: IntCriterionInput
                - gallery_count: IntCriterionInput
                - performer_count: IntCriterionInput
                - studio_count: IntCriterionInput

        Returns:
            FindTagsResultType containing:
                - count: Total number of matching tags
                - tags: List of Tag objects

        Examples:
            Find all favorite tags:
            ```python
            result = await client.find_tags(
                tag_filter={"favorite": True}
            )
            print(f"Found {result.count} favorite tags")
            for tag in result.tags:
                print(f"- {tag.name}")
            ```

            Find tags by name pattern:
            ```python
            result = await client.find_tags(
                tag_filter={
                    "name": {
                        "value": "tag",
                        "modifier": "INCLUDES"
                    }
                }
            )
            ```

            Find tags with high usage:
            ```python
            result = await client.find_tags(
                filter_={
                    "direction": "DESC",
                    "sort": "scene_count",
                },
                tag_filter={
                    "scene_count": {
                        "value": 10,
                        "modifier": "GREATER_THAN"
                    }
                }
            )
            ```

            Find tags with specific hierarchy:
            ```python
            result = await client.find_tags(
                tag_filter={
                    "parent_count": {
                        "value": 0,
                        "modifier": "EQUALS"
                    },
                    "child_count": {
                        "value": 0,
                        "modifier": "GREATER_THAN"
                    }
                }
            )
            ```

            Find tags with description:
            ```python
            result = await client.find_tags(
                tag_filter={
                    "description": {
                        "value": "",
                        "modifier": "NOT_NULL"
                    }
                }
            )
            ```
        """
        try:
            result = await self.execute(
                fragments.FIND_TAGS_QUERY,
                {"filter": filter_, "tag_filter": tag_filter},
            )
            return FindTagsResultType(**result["findTags"])
        except Exception as e:
            self.log.error(f"Failed to find tags: {e}")
            return FindTagsResultType(count=0, tags=[])

    async def create_tag(self, tag: Tag) -> Tag:
        """Create a new tag in Stash.

        Args:
            tag: Tag object with the data to create. Required fields:
                - name: Tag name
                - created_at: Creation timestamp
                - updated_at: Last update timestamp

        Returns:
            Created Tag object with ID and any server-generated fields

        Raises:
            ValueError: If the tag data is invalid
            httpx.HTTPError: If the request fails

        Examples:
            Create a basic tag:
            ```python
            tag = Tag(
                name="Tag Name",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            created = await client.create_tag(tag)
            print(f"Created tag with ID: {created.id}")
            ```

            Create tag with description and aliases:
            ```python
            tag = Tag(
                name="Tag Name",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add details
                description="Tag description",
                aliases=["alias1", "alias2"],
                favorite=True,
            )
            created = await client.create_tag(tag)
            ```

            Create tag with hierarchy:
            ```python
            tag = Tag(
                name="Tag Name",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Set parent and child tags
                parents=[parent_tag1, parent_tag2],
                children=[child_tag1, child_tag2],
            )
            created = await client.create_tag(tag)
            ```

            Create tag with image and flags:
            ```python
            tag = Tag(
                name="Tag Name",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add image
                image="https://example.com/tag.jpg",  # URL or base64
                # Set flags
                favorite=True,
                ignore_auto_tag=False,
            )
            created = await client.create_tag(tag)
            ```
        """
        try:
            result = await self.execute(
                fragments.CREATE_TAG_MUTATION,
                {"input": tag.to_input()},
            )
            return Tag(**result["tagCreate"])
        except Exception as e:
            self.log.error(f"Failed to create tag: {e}")
            raise

    async def update_tag(self, tag: Tag) -> Tag:
        """Update an existing tag in Stash.

        Args:
            tag: Tag object with updated data. Required fields:
                - id: Tag ID to update
                Any other fields that are set will be updated.
                Fields that are None will be ignored.

        Returns:
            Updated Tag object with any server-generated fields

        Raises:
            ValueError: If the tag data is invalid
            httpx.HTTPError: If the request fails

        Examples:
            Update tag name and description:
            ```python
            tag = await client.find_tag("123")
            if tag:
                tag.name = "New Name"
                tag.description = "New description"
                updated = await client.update_tag(tag)
                print(f"Updated tag: {updated.name}")
            ```

            Update tag aliases:
            ```python
            tag = await client.find_tag("123")
            if tag:
                # Replace aliases
                tag.aliases = ["new_alias1", "new_alias2"]
                # Or add new alias
                tag.aliases.append("new_alias3")
                updated = await client.update_tag(tag)
            ```

            Update tag hierarchy:
            ```python
            tag = await client.find_tag("123")
            if tag:
                # Update parent tags
                tag.parents = [new_parent1, new_parent2]
                # Update child tags
                tag.children = [new_child1, new_child2]
                updated = await client.update_tag(tag)
            ```

            Update tag image and flags:
            ```python
            tag = await client.find_tag("123")
            if tag:
                # Update image
                tag.image = "https://example.com/new-image.jpg"  # URL or base64
                # Update flags
                tag.favorite = True
                tag.ignore_auto_tag = False
                updated = await client.update_tag(tag)
            ```

            Remove tag relationships:
            ```python
            tag = await client.find_tag("123")
            if tag:
                # Clear parents
                tag.parents = []
                # Clear children
                tag.children = []
                # Clear aliases
                tag.aliases = []
                updated = await client.update_tag(tag)
            ```
        """
        try:
            result = await self.execute(
                fragments.UPDATE_TAG_MUTATION,
                {"input": tag.to_input()},
            )
            return Tag(**result["tagUpdate"])
        except Exception as e:
            self.log.error(f"Failed to update tag: {e}")
            raise

    # Gallery methods
    async def find_gallery(self, id: str) -> Gallery | None:
        """Find a gallery by its ID.

        Args:
            id: The ID of the gallery to find

        Returns:
            Gallery object if found, None otherwise

        Examples:
            Find a gallery and check its details:
            ```python
            gallery = await client.find_gallery("123")
            if gallery:
                print(f"Found gallery: {gallery.title}")
                if gallery.details:
                    print(f"Details: {gallery.details}")
            ```

            Access gallery metadata:
            ```python
            gallery = await client.find_gallery("123")
            if gallery:
                # Get basic info
                print(f"Code: {gallery.code}")
                print(f"Date: {gallery.date}")
                print(f"Rating: {gallery.rating100}/100")
                print(f"Photographer: {gallery.photographer}")
                print(f"Image Count: {gallery.image_count}")
                # Get URLs
                for url in gallery.urls:
                    print(f"URL: {url}")
            ```

            Access gallery relationships:
            ```python
            gallery = await client.find_gallery("123")
            if gallery:
                # Get studio
                if gallery.studio:
                    print(f"Studio: {gallery.studio.name}")
                # Get performers
                for performer in gallery.performers:
                    print(f"Performer: {performer.name}")
                # Get tags
                for tag in gallery.tags:
                    print(f"Tag: {tag.name}")
            ```

            Access gallery files:
            ```python
            gallery = await client.find_gallery("123")
            if gallery:
                # Get files
                for file in gallery.files:
                    print(f"File: {file.path}")
                    print(f"Size: {file.size}")
                    print(f"Modified: {file.mod_time}")
            ```

            Access linked scenes:
            ```python
            gallery = await client.find_gallery("123")
            if gallery:
                # Get linked scenes
                for scene in gallery.scenes:
                    print(f"Scene: {scene.title}")
                    print(f"Preview: {scene.paths.preview}")
            ```
        """
        try:
            result = await self.execute(
                fragments.FIND_GALLERY_QUERY,
                {"id": id},
            )
            if result and result.get("findGallery"):
                return Gallery(**result["findGallery"])
            return None
        except Exception as e:
            self.log.error(f"Failed to find gallery {id}: {e}")
            return None

    async def find_galleries(
        self,
        filter_: dict[str, Any] | None = None,
        gallery_filter: dict[str, Any] | None = None,
    ) -> FindGalleriesResultType:
        """Find galleries matching the given filters.

        Args:
            filter_: Optional general filter parameters:
                - direction: SortDirectionEnum (ASC/DESC)
                - page: int
                - per_page: int
                - sort: str (field to sort by)
            gallery_filter: Optional gallery-specific filter:
                - average_resolution: ResolutionEnum
                - created_at: TimestampCriterionInput
                - file_count: IntCriterionInput
                - has_chapters: bool
                - image_count: IntCriterionInput
                - is_missing: str (what data is missing)
                - is_zip: bool
                - organized: bool
                - path: StringCriterionInput
                - performer_count: IntCriterionInput
                - performer_tags: HierarchicalMultiCriterionInput
                - performers: MultiCriterionInput
                - rating100: IntCriterionInput
                - studios: HierarchicalMultiCriterionInput
                - tag_count: IntCriterionInput
                - tags: HierarchicalMultiCriterionInput
                - title: StringCriterionInput
                - updated_at: TimestampCriterionInput

        Returns:
            FindGalleriesResultType containing:
                - count: Total number of matching galleries
                - galleries: List of Gallery objects

        Examples:
            Find all organized galleries:
            ```python
            result = await client.find_galleries(
                gallery_filter={"organized": True}
            )
            print(f"Found {result.count} organized galleries")
            for gallery in result.galleries:
                print(f"- {gallery.title}")
            ```

            Find galleries by title pattern:
            ```python
            result = await client.find_galleries(
                gallery_filter={
                    "title": {
                        "value": "gallery",
                        "modifier": "INCLUDES"
                    }
                }
            )
            ```

            Find galleries with specific performers:
            ```python
            result = await client.find_galleries(
                gallery_filter={
                    "performers": {
                        "value": ["performer1", "performer2"],
                        "modifier": "INCLUDES_ALL"
                    }
                }
            )
            ```

            Find galleries with high rating and sort by date:
            ```python
            result = await client.find_galleries(
                filter_={
                    "direction": "DESC",
                    "sort": "date",
                },
                gallery_filter={
                    "rating100": {
                        "value": 80,
                        "modifier": "GREATER_THAN"
                    }
                }
            )
            ```

            Find galleries by file type:
            ```python
            result = await client.find_galleries(
                gallery_filter={
                    "is_zip": True,  # Only zip galleries
                    "file_count": {
                        "value": 10,
                        "modifier": "GREATER_THAN"
                    }
                }
            )
            ```
        """
        try:
            result = await self.execute(
                fragments.FIND_GALLERIES_QUERY,
                {"filter": filter_, "gallery_filter": gallery_filter},
            )
            return FindGalleriesResultType(**result["findGalleries"])
        except Exception as e:
            self.log.error(f"Failed to find galleries: {e}")
            return FindGalleriesResultType(count=0, galleries=[])

    async def create_gallery(self, gallery: Gallery) -> Gallery:
        """Create a new gallery in Stash.

        Args:
            gallery: Gallery object with the data to create. Required fields:
                - title: Gallery title
                - created_at: Creation timestamp
                - updated_at: Last update timestamp

        Returns:
            Created Gallery object with ID and any server-generated fields

        Raises:
            ValueError: If the gallery data is invalid
            httpx.HTTPError: If the request fails

        Examples:
            Create a basic gallery:
            ```python
            gallery = Gallery(
                title="Gallery Title",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            created = await client.create_gallery(gallery)
            print(f"Created gallery with ID: {created.id}")
            ```

            Create gallery with details:
            ```python
            gallery = Gallery(
                title="Gallery Title",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add details
                code="GAL123",
                details="Gallery description",
                photographer="Photographer Name",
                rating100=85,
                organized=True,
            )
            created = await client.create_gallery(gallery)
            ```

            Create gallery with URLs:
            ```python
            gallery = Gallery(
                title="Gallery Title",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add URLs
                urls=[
                    "https://example.com/gallery",
                    "https://example.com/gallery/page2",
                ],
            )
            created = await client.create_gallery(gallery)
            ```

            Create gallery with relationships:
            ```python
            gallery = Gallery(
                title="Gallery Title",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add relationships
                studio=studio,
                performers=[performer1, performer2],
                tags=[tag1, tag2],
                scenes=[scene1, scene2],
            )
            created = await client.create_gallery(gallery)
            ```

            Create gallery with files:
            ```python
            gallery = Gallery(
                title="Gallery Title",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add files
                files=[
                    GalleryFile(
                        path="/path/to/gallery.zip",
                        basename="gallery.zip",
                    ),
                ],
            )
            created = await client.create_gallery(gallery)
            ```
        """
        try:
            result = await self.execute(
                fragments.CREATE_GALLERY_MUTATION,
                {"input": gallery.to_input()},
            )
            return Gallery(**result["galleryCreate"])
        except Exception as e:
            self.log.error(f"Failed to create gallery: {e}")
            raise

    async def update_gallery(self, gallery: Gallery) -> Gallery:
        """Update an existing gallery in Stash.

        Args:
            gallery: Gallery object with updated data. Required fields:
                - id: Gallery ID to update
                Any other fields that are set will be updated.
                Fields that are None will be ignored.

        Returns:
            Updated Gallery object with any server-generated fields

        Raises:
            ValueError: If the gallery data is invalid
            httpx.HTTPError: If the request fails

        Examples:
            Update gallery title and details:
            ```python
            gallery = await client.find_gallery("123")
            if gallery:
                gallery.title = "New Title"
                gallery.details = "New description"
                gallery.code = "NEWGAL123"
                updated = await client.update_gallery(gallery)
                print(f"Updated gallery: {updated.title}")
            ```

            Update gallery URLs:
            ```python
            gallery = await client.find_gallery("123")
            if gallery:
                # Replace URLs
                gallery.urls = [
                    "https://example.com/new-url",
                    "https://example.com/new-url/page2",
                ]
                # Or add new URL
                gallery.urls.append("https://example.com/new-url/page3")
                updated = await client.update_gallery(gallery)
            ```

            Update gallery relationships:
            ```python
            gallery = await client.find_gallery("123")
            if gallery:
                # Update studio
                gallery.studio = new_studio
                # Update performers
                gallery.performers = [new_performer1, new_performer2]
                # Update tags
                gallery.tags = [new_tag1, new_tag2]
                # Update scenes
                gallery.scenes = [new_scene1, new_scene2]
                updated = await client.update_gallery(gallery)
            ```

            Update gallery metadata:
            ```python
            gallery = await client.find_gallery("123")
            if gallery:
                # Update metadata
                gallery.photographer = "New Photographer"
                gallery.rating100 = 90
                gallery.organized = True
                gallery.date = "2024-01-31"
                updated = await client.update_gallery(gallery)
            ```

            Remove gallery relationships:
            ```python
            gallery = await client.find_gallery("123")
            if gallery:
                # Clear studio
                gallery.studio = None
                # Clear performers
                gallery.performers = []
                # Clear tags
                gallery.tags = []
                # Clear scenes
                gallery.scenes = []
                updated = await client.update_gallery(gallery)
            ```
        """
        try:
            result = await self.execute(
                fragments.UPDATE_GALLERY_MUTATION,
                {"input": gallery.to_input()},
            )
            return Gallery(**result["galleryUpdate"])
        except Exception as e:
            self.log.error(f"Failed to update gallery: {e}")
            raise

    # Image methods
    async def find_image(self, id: str) -> Image | None:
        """Find an image by its ID.

        Args:
            id: The ID of the image to find

        Returns:
            Image object if found, None otherwise

        Examples:
            Find an image and check its details:
            ```python
            image = await client.find_image("123")
            if image:
                print(f"Found image: {image.title}")
                if image.details:
                    print(f"Details: {image.details}")
            ```

            Access image metadata:
            ```python
            image = await client.find_image("123")
            if image:
                # Get basic info
                print(f"Code: {image.code}")
                print(f"Date: {image.date}")
                print(f"Rating: {image.rating100}/100")
                print(f"Photographer: {image.photographer}")
                print(f"O-Counter: {image.o_counter}")
                # Get URLs
                for url in image.urls:
                    print(f"URL: {url}")
            ```

            Access image relationships:
            ```python
            image = await client.find_image("123")
            if image:
                # Get studio
                if image.studio:
                    print(f"Studio: {image.studio.name}")
                # Get performers
                for performer in image.performers:
                    print(f"Performer: {performer.name}")
                # Get tags
                for tag in image.tags:
                    print(f"Tag: {tag.name}")
                # Get galleries
                for gallery in image.galleries:
                    print(f"Gallery: {gallery.title}")
            ```

            Access image files:
            ```python
            image = await client.find_image("123")
            if image:
                # Get files
                for file in image.files:
                    print(f"File: {file.path}")
                    print(f"Size: {file.size}")
                    print(f"Width: {file.width}")
                    print(f"Height: {file.height}")
            ```

            Access image paths:
            ```python
            image = await client.find_image("123")
            if image:
                # Get paths
                print(f"Thumbnail: {image.paths.thumbnail}")
                print(f"Preview: {image.paths.preview}")
                print(f"Image: {image.paths.image}")
            ```
        """
        try:
            result = await self.execute(
                fragments.FIND_IMAGE_QUERY,
                {"id": id},
            )
            if result and result.get("findImage"):
                return Image(**result["findImage"])
            return None
        except Exception as e:
            self.log.error(f"Failed to find image {id}: {e}")
            return None

    async def find_images(
        self,
        filter_: dict[str, Any] | None = None,
        image_filter: dict[str, Any] | None = None,
    ) -> FindImagesResultType:
        """Find images matching the given filters.

        Args:
            filter_: Optional general filter parameters:
                - direction: SortDirectionEnum (ASC/DESC)
                - page: int
                - per_page: int
                - sort: str (field to sort by)
            image_filter: Optional image-specific filter:
                - aspect_ratio: FloatCriterionInput
                - average_resolution: ResolutionEnum
                - created_at: TimestampCriterionInput
                - file_count: IntCriterionInput
                - has_galleries: bool
                - is_missing: str (what data is missing)
                - o_counter: IntCriterionInput
                - organized: bool
                - orientation: OrientationEnum
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
                - updated_at: TimestampCriterionInput

        Returns:
            FindImagesResultType containing:
                - count: Total number of matching images
                - megapixels: Total megapixels of all images
                - filesize: Total size in bytes
                - images: List of Image objects

        Examples:
            Find all organized images:
            ```python
            result = await client.find_images(
                image_filter={"organized": True}
            )
            print(f"Found {result.count} organized images")
            for image in result.images:
                print(f"- {image.title}")
            ```

            Find images by resolution:
            ```python
            result = await client.find_images(
                image_filter={
                    "resolution": "VERY_HIGH",  # 4K or higher
                    "orientation": "LANDSCAPE"
                }
            )
            ```

            Find images with specific performers:
            ```python
            result = await client.find_images(
                image_filter={
                    "performers": {
                        "value": ["performer1", "performer2"],
                        "modifier": "INCLUDES_ALL"
                    }
                }
            )
            ```

            Find images with high rating and sort by date:
            ```python
            result = await client.find_images(
                filter_={
                    "direction": "DESC",
                    "sort": "date",
                },
                image_filter={
                    "rating100": {
                        "value": 80,
                        "modifier": "GREATER_THAN"
                    }
                }
            )
            ```

            Find images by aspect ratio:
            ```python
            result = await client.find_images(
                image_filter={
                    "aspect_ratio": {
                        "value": 1.78,  # 16:9
                        "modifier": "EQUALS"
                    }
                }
            )
            ```
        """
        try:
            result = await self.execute(
                fragments.FIND_IMAGES_QUERY,
                {"filter": filter_, "image_filter": image_filter},
            )
            return FindImagesResultType(**result["findImages"])
        except Exception as e:
            self.log.error(f"Failed to find images: {e}")
            return FindImagesResultType(count=0, megapixels=0, filesize=0, images=[])

    async def create_image(self, image: Image) -> Image:
        """Create a new image in Stash.

        Args:
            image: Image object with the data to create. Required fields:
                - title: Image title
                - created_at: Creation timestamp
                - updated_at: Last update timestamp

        Returns:
            Created Image object with ID and any server-generated fields

        Raises:
            ValueError: If the image data is invalid
            httpx.HTTPError: If the request fails

        Examples:
            Create a basic image:
            ```python
            image = Image(
                title="Image Title",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            created = await client.create_image(image)
            print(f"Created image with ID: {created.id}")
            ```

            Create image with details:
            ```python
            image = Image(
                title="Image Title",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add details
                code="IMG123",
                details="Image description",
                photographer="Photographer Name",
                rating100=85,
                organized=True,
            )
            created = await client.create_image(image)
            ```

            Create image with URLs:
            ```python
            image = Image(
                title="Image Title",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add URLs
                urls=[
                    "https://example.com/image",
                    "https://example.com/image/full",
                ],
            )
            created = await client.create_image(image)
            ```

            Create image with relationships:
            ```python
            image = Image(
                title="Image Title",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add relationships
                studio=studio,
                performers=[performer1, performer2],
                tags=[tag1, tag2],
                galleries=[gallery1, gallery2],
            )
            created = await client.create_image(image)
            ```

            Create image with files:
            ```python
            image = Image(
                title="Image Title",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add files
                files=[
                    ImageFile(
                        path="/path/to/image.jpg",
                        basename="image.jpg",
                        width=1920,
                        height=1080,
                    ),
                ],
            )
            created = await client.create_image(image)
            ```
        """
        try:
            result = await self.execute(
                fragments.CREATE_IMAGE_MUTATION,
                {"input": image.to_input()},
            )
            return Image(**result["imageCreate"])
        except Exception as e:
            self.log.error(f"Failed to create image: {e}")
            raise

    async def update_image(self, image: Image) -> Image:
        """Update an existing image in Stash.

        Args:
            image: Image object with updated data. Required fields:
                - id: Image ID to update
                Any other fields that are set will be updated.
                Fields that are None will be ignored.

        Returns:
            Updated Image object with any server-generated fields

        Raises:
            ValueError: If the image data is invalid
            httpx.HTTPError: If the request fails

        Examples:
            Update image title and details:
            ```python
            image = await client.find_image("123")
            if image:
                image.title = "New Title"
                image.details = "New description"
                image.code = "NEWIMG123"
                updated = await client.update_image(image)
                print(f"Updated image: {updated.title}")
            ```

            Update image URLs:
            ```python
            image = await client.find_image("123")
            if image:
                # Replace URLs
                image.urls = [
                    "https://example.com/new-url",
                    "https://example.com/new-url/full",
                ]
                # Or add new URL
                image.urls.append("https://example.com/new-url/original")
                updated = await client.update_image(image)
            ```

            Update image relationships:
            ```python
            image = await client.find_image("123")
            if image:
                # Update studio
                image.studio = new_studio
                # Update performers
                image.performers = [new_performer1, new_performer2]
                # Update tags
                image.tags = [new_tag1, new_tag2]
                # Update galleries
                image.galleries = [new_gallery1, new_gallery2]
                updated = await client.update_image(image)
            ```

            Update image metadata:
            ```python
            image = await client.find_image("123")
            if image:
                # Update metadata
                image.photographer = "New Photographer"
                image.rating100 = 90
                image.organized = True
                image.date = "2024-01-31"
                updated = await client.update_image(image)
            ```

            Remove image relationships:
            ```python
            image = await client.find_image("123")
            if image:
                # Clear studio
                image.studio = None
                # Clear performers
                image.performers = []
                # Clear tags
                image.tags = []
                # Clear galleries
                image.galleries = []
                updated = await client.update_image(image)
            ```
        """
        try:
            result = await self.execute(
                fragments.UPDATE_IMAGE_MUTATION,
                {"input": image.to_input()},
            )
            return Image(**result["imageUpdate"])
        except Exception as e:
            self.log.error(f"Failed to update image: {e}")
            raise

    # Marker methods
    async def find_marker(self, id: str) -> SceneMarker | None:
        """Find a scene marker by its ID.

        Args:
            id: The ID of the scene marker to find

        Returns:
            SceneMarker object if found, None otherwise

        Examples:
            Find a marker and check its details:
            ```python
            marker = await client.find_marker("123")
            if marker:
                print(f"Found marker: {marker.title}")
                print(f"At: {marker.seconds} seconds")
            ```

            Access marker scene:
            ```python
            marker = await client.find_marker("123")
            if marker:
                # Get scene info
                print(f"Scene: {marker.scene.title}")
                print(f"Stream: {marker.scene.paths.stream}")
                print(f"Preview: {marker.scene.paths.preview}")
            ```

            Access marker tags:
            ```python
            marker = await client.find_marker("123")
            if marker:
                # Get primary tag
                print(f"Primary: {marker.primary_tag.name}")
                # Get other tags
                for tag in marker.tags:
                    print(f"Tag: {tag.name}")
            ```

            Access marker paths:
            ```python
            marker = await client.find_marker("123")
            if marker:
                # Get paths
                print(f"Stream: {marker.stream}")
                print(f"Preview: {marker.preview}")
                print(f"Screenshot: {marker.screenshot}")
            ```
        """
        try:
            result = await self.execute(
                fragments.FIND_MARKER_QUERY,
                {"id": id},
            )
            if result and result.get("findSceneMarker"):
                return SceneMarker(**result["findSceneMarker"])
            return None
        except Exception as e:
            self.log.error(f"Failed to find marker {id}: {e}")
            return None

    async def find_markers(
        self,
        filter_: dict[str, Any] | None = None,
        marker_filter: dict[str, Any] | None = None,
    ) -> FindSceneMarkersResultType:
        """Find scene markers matching the given filters.

        Args:
            filter_: Optional general filter parameters:
                - direction: SortDirectionEnum (ASC/DESC)
                - page: int
                - per_page: int
                - sort: str (field to sort by)
            marker_filter: Optional marker-specific filter:
                - created_at: TimestampCriterionInput
                - scene_tags: HierarchicalMultiCriterionInput
                - scenes: MultiCriterionInput
                - tag_count: IntCriterionInput
                - tags: HierarchicalMultiCriterionInput
                - title: StringCriterionInput
                - updated_at: TimestampCriterionInput

        Returns:
            FindSceneMarkersResultType containing:
                - count: Total number of matching markers
                - scene_markers: List of SceneMarker objects

        Examples:
            Find markers by title:
            ```python
            result = await client.find_markers(
                marker_filter={
                    "title": {
                        "value": "action",
                        "modifier": "INCLUDES"
                    }
                }
            )
            print(f"Found {result.count} markers")
            for marker in result.scene_markers:
                print(f"- {marker.title} at {marker.seconds}s")
            ```

            Find markers with specific tags:
            ```python
            result = await client.find_markers(
                marker_filter={
                    "tags": {
                        "value": ["tag1", "tag2"],
                        "modifier": "INCLUDES_ALL"
                    }
                }
            )
            ```

            Find markers in specific scenes:
            ```python
            result = await client.find_markers(
                marker_filter={
                    "scenes": {
                        "value": ["scene1", "scene2"],
                        "modifier": "INCLUDES"
                    }
                }
            )
            ```

            Find markers with scene tags:
            ```python
            result = await client.find_markers(
                marker_filter={
                    "scene_tags": {
                        "value": ["tag1", "tag2"],
                        "modifier": "INCLUDES_ALL",
                        "depth": 1  # Include child tags
                    }
                }
            )
            ```

            Sort markers by timestamp:
            ```python
            result = await client.find_markers(
                filter_={
                    "direction": "ASC",
                    "sort": "seconds",
                }
            )
            ```
        """
        try:
            result = await self.execute(
                fragments.FIND_MARKERS_QUERY,
                {"filter": filter_, "marker_filter": marker_filter},
            )
            return FindSceneMarkersResultType(**result["findSceneMarkers"])
        except Exception as e:
            self.log.error(f"Failed to find markers: {e}")
            return FindSceneMarkersResultType(count=0, scene_markers=[])

    async def create_marker(self, marker: SceneMarker) -> SceneMarker:
        """Create a new scene marker in Stash.

        Args:
            marker: SceneMarker object with the data to create. Required fields:
                - title: Marker title
                - seconds: Timestamp in seconds
                - scene: Parent Scene object
                - primary_tag: Primary Tag object
                - created_at: Creation timestamp
                - updated_at: Last update timestamp

        Returns:
            Created SceneMarker object with ID and any server-generated fields

        Raises:
            ValueError: If the marker data is invalid
            httpx.HTTPError: If the request fails

        Examples:
            Create a basic marker:
            ```python
            marker = SceneMarker(
                title="Action Scene",
                seconds=120.5,  # At 2:00.5
                scene=scene,
                primary_tag=tag,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            created = await client.create_marker(marker)
            print(f"Created marker with ID: {created.id}")
            ```

            Create marker with additional tags:
            ```python
            marker = SceneMarker(
                title="Action Scene",
                seconds=120.5,
                scene=scene,
                primary_tag=primary_tag,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add additional tags
                tags=[tag1, tag2],
            )
            created = await client.create_marker(marker)
            ```

            Create marker with paths:
            ```python
            marker = SceneMarker(
                title="Action Scene",
                seconds=120.5,
                scene=scene,
                primary_tag=primary_tag,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                # Add paths
                stream="https://example.com/marker.mp4",
                preview="https://example.com/marker.jpg",
                screenshot="https://example.com/screenshot.jpg",
            )
            created = await client.create_marker(marker)
            ```
        """
        try:
            result = await self.execute(
                fragments.CREATE_MARKER_MUTATION,
                {"input": marker.to_input()},
            )
            return SceneMarker(**result["sceneMarkerCreate"])
        except Exception as e:
            self.log.error(f"Failed to create marker: {e}")
            raise

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
            httpx.HTTPError: If the request fails

        Examples:
            Update marker title and timestamp:
            ```python
            marker = await client.find_marker("123")
            if marker:
                marker.title = "New Title"
                marker.seconds = 135.0  # Move to 2:15.0
                updated = await client.update_marker(marker)
                print(f"Updated marker: {updated.title}")
            ```

            Update marker tags:
            ```python
            marker = await client.find_marker("123")
            if marker:
                # Update primary tag
                marker.primary_tag = new_primary_tag
                # Update additional tags
                marker.tags = [new_tag1, new_tag2]
                updated = await client.update_marker(marker)
            ```

            Update marker paths:
            ```python
            marker = await client.find_marker("123")
            if marker:
                # Update paths
                marker.stream = "https://example.com/new-marker.mp4"
                marker.preview = "https://example.com/new-marker.jpg"
                marker.screenshot = "https://example.com/new-screenshot.jpg"
                updated = await client.update_marker(marker)
            ```

            Remove marker tags:
            ```python
            marker = await client.find_marker("123")
            if marker:
                # Clear additional tags
                marker.tags = []
                updated = await client.update_marker(marker)
            ```
        """
        try:
            result = await self.execute(
                fragments.UPDATE_MARKER_MUTATION,
                {"input": marker.to_input()},
            )
            return SceneMarker(**result["sceneMarkerUpdate"])
        except Exception as e:
            self.log.error(f"Failed to update marker: {e}")
            raise

    async def __aenter__(self) -> "StashClient":
        """Enter async context manager.

        Returns:
            The client instance.
        """
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context manager.

        Args:
            exc_type: Exception type if an error occurred
            exc_val: Exception value if an error occurred
            exc_tb: Exception traceback if an error occurred
        """
        await self.close()

    async def get_configuration_defaults(self) -> ConfigDefaultSettingsResult:
        """Get configuration defaults from Stash.

        Returns:
            ConfigDefaultSettingsResult containing:
                - scan: ScanMetadataOptions
                - autoTag: AutoTagMetadataOptions
                - generate: GenerateMetadataOptions
                - deleteFile: bool
                - deleteGenerated: bool

        Raises:
            ValueError: If query fails
        """
        result = await self.execute(fragments.CONFIG_DEFAULTS_QUERY)
        if defaults := result.get("configuration", {}).get("defaults"):
            return ConfigDefaultSettingsResult(**defaults)
        return ConfigDefaultSettingsResult(
            scan=ScanMetadataOptions(),
            autoTag=AutoTagMetadataOptions(),
            generate=GenerateMetadataOptions(),
            deleteFile=False,
            deleteGenerated=False,
        )

    async def metadata_scan(
        self,
        paths: list[str] = [],
        flags: dict[str, Any] = {},
    ) -> str:
        """Start a metadata scan job.

        Args:
            paths: List of paths to scan (empty for all paths)
            flags: Optional scan flags matching ScanMetadataInput schema:
                - rescan: bool
                - scanGenerateCovers: bool
                - scanGeneratePreviews: bool
                - scanGenerateImagePreviews: bool
                - scanGenerateSprites: bool
                - scanGeneratePhashes: bool
                - scanGenerateThumbnails: bool
                - scanGenerateClipPreviews: bool
                - filter: ScanMetaDataFilterInput

        Returns:
            Job ID string

        Raises:
            ValueError: If scan fails to start
        """
        # Use fragment for mutation
        # Start with defaults from configuration
        # Start with defaults from configuration
        try:
            defaults = await self.get_configuration_defaults()
            scan_input = ScanMetadataInput(
                paths=paths,
                rescan=getattr(defaults.scan, "rescan", False),
                scanGenerateCovers=getattr(defaults.scan, "scanGenerateCovers", True),
                scanGeneratePreviews=getattr(
                    defaults.scan, "scanGeneratePreviews", True
                ),
                scanGenerateImagePreviews=getattr(
                    defaults.scan, "scanGenerateImagePreviews", True
                ),
                scanGenerateSprites=getattr(defaults.scan, "scanGenerateSprites", True),
                scanGeneratePhashes=getattr(defaults.scan, "scanGeneratePhashes", True),
                scanGenerateThumbnails=getattr(
                    defaults.scan, "scanGenerateThumbnails", True
                ),
                scanGenerateClipPreviews=getattr(
                    defaults.scan, "scanGenerateClipPreviews", True
                ),
            )
        except Exception as e:
            self.log.warning(
                f"Failed to get scan defaults: {e}, using hardcoded defaults"
            )
            scan_input = ScanMetadataInput(
                paths=paths,
                rescan=False,
                scanGenerateCovers=True,
                scanGeneratePreviews=True,
                scanGenerateImagePreviews=True,
                scanGenerateSprites=True,
                scanGeneratePhashes=True,
                scanGenerateThumbnails=True,
                scanGenerateClipPreviews=True,
            )

        # Override with any provided flags
        if flags:
            for key, value in flags.items():
                setattr(scan_input, key, value)

        # Convert to dict for GraphQL
        variables = {"input": scan_input.__dict__}
        result = await self.execute(fragments.METADATA_SCAN_MUTATION, variables)
        job_id = result.get("metadataScan")
        if not job_id:
            raise ValueError("Failed to start metadata scan")
        return job_id

    async def find_job(self, job_id: str) -> Job | None:
        """Find a job by ID.

        Args:
            job_id: Job ID to find

        Returns:
            Job object if found, None otherwise
        """
        # Use fragment for query
        result = await self.execute(
            fragments.FIND_JOB_QUERY,
            {"input": FindJobInput(id=job_id).__dict__},
        )
        if job_data := result.get("findJob"):
            return Job(**job_data)
        return None

    async def wait_for_job(
        self,
        job_id: str,
        status: JobStatus = JobStatus.FINISHED,
        period: float = 1.5,
        timeout: float = 120,
    ) -> bool | None:
        """Wait for a job to reach a specific status.

        Args:
            job_id: Job ID to wait for
            status: Status to wait for (default: JobStatus.FINISHED)
            period: Time between checks in seconds (default: 1.5)
            timeout: Maximum time to wait in seconds (default: 120)

        Returns:
            True if job reached desired status
            False if job finished with different status
            None if job not found

        Raises:
            TimeoutError: If timeout is reached
        """
        timeout_value = time.time() + timeout
        while time.time() < timeout_value:
            job = await self.find_job(job_id)
            if not job:
                return None

            self.log.debug(
                f"Waiting for Job:{job_id} Status:{job.status} Progress:{job.progress}"
            )

            if job.status == status:
                return True
            if job.status in [JobStatus.FINISHED, JobStatus.CANCELLED]:
                return False

            await asyncio.sleep(period)

        raise TimeoutError("Hit timeout waiting for Job to complete")

    async def close(self) -> None:
        """Close the HTTP client and clean up resources.

        This method should be called when you're done with the client
        to properly clean up resources. You can also use the client
        as an async context manager to automatically handle cleanup.

        Examples:
            Manual cleanup:
            ```python
            client = StashClient("http://localhost:9999/graphql")
            try:
                # Use client...
                scene = await client.find_scene("123")
            finally:
                await client.close()
            ```

            Using async context manager:
            ```python
            async with StashClient("http://localhost:9999/graphql") as client:
                # Client will be automatically closed after this block
                scene = await client.find_scene("123")
            ```
        """
        await self.client.aclose()
