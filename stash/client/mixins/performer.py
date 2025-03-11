"""Performer-related client functionality."""

from typing import Any

from ... import fragments
from ...client_helpers import async_lru_cache
from ...types import FindPerformersResultType, Performer
from ..protocols import StashClientProtocol


class PerformerClientMixin(StashClientProtocol):
    """Mixin for performer-related client methods."""

    @async_lru_cache(maxsize=3096, exclude_arg_indices=[0])  # exclude self
    async def find_performer(
        self,
        performer: int | str | dict,
    ) -> Performer | None:
        """Find a performer by ID, name, or filter.

        Args:
            performer: Can be:
                - ID (int/str): Find by ID
                - Name (str): Find by name
                - Dict: Find by filter criteria

        Returns:
            Performer object if found, None otherwise

        Examples:
            Find by ID:
            ```python
            performer = await client.find_performer("123")
            if performer:
                print(f"Found performer: {performer.name}")
            ```

            Find by name:
            ```python
            performer = await client.find_performer("Performer Name")
            if performer:
                print(f"Found performer with ID: {performer.id}")
            ```

            Find by filter:
            ```python
            performer = await client.find_performer({
                "name": "Performer Name",
                "disambiguation": "2000s"
            })
            ```

            Access performer relationships:
            ```python
            performer = await client.find_performer("123")
            if performer:
                # Get scene titles
                scene_titles = [s.title for s in performer.scenes]
                # Get studio name
                studio_name = performer.studio.name if performer.studio else None
                # Get tag names
                tags = [t.name for t in performer.tags]
            ```
        """
        try:
            # Parse input to handle different types
            parsed_input = self._parse_obj_for_ID(performer)

            if isinstance(parsed_input, dict):
                # If it's a name filter, try name then alias
                name = parsed_input.get("name")
                if name:
                    # Try by name first
                    result = await self.find_performers(
                        performer_filter={"name": {"value": name, "modifier": "EQUALS"}}
                    )
                    if result.count > 0:
                        return result.performers[0]

                    # Try by alias
                    result = await self.find_performers(
                        performer_filter={
                            "aliases": {"value": name, "modifier": "INCLUDES"}
                        }
                    )
                    if result.count > 0:
                        return result.performers[0]
                    return None
            else:
                # If it's an ID, use direct lookup
                result = await self.execute(
                    fragments.FIND_PERFORMER_QUERY,
                    {"id": str(parsed_input)},
                )
            if result and result.get("findPerformer"):
                return Performer(**result["findPerformer"])
            return None
        except Exception as e:
            self.log.error(f"Failed to find performer {performer}: {e}")
            return None

    @async_lru_cache(maxsize=3096, exclude_arg_indices=[0])  # exclude self
    async def find_performers(
        self,
        filter_: dict[str, Any] = {"per_page": -1},
        performer_filter: dict[str, Any] | None = None,
        q: str | None = None,
    ) -> FindPerformersResultType:
        """Find performers matching the given filters.

        Args:
            filter_: Optional general filter parameters:
                - q: str (search query)
                - direction: SortDirectionEnum (ASC/DESC)
                - page: int
                - per_page: int
                - sort: str (field to sort by)
            q: Optional search query (alternative to filter_["q"])
            performer_filter: Optional performer-specific filter:
                - birth_year: IntCriterionInput
                - age: IntCriterionInput
                - ethnicity: StringCriterionInput
                - country: StringCriterionInput
                - eye_color: StringCriterionInput
                - height: StringCriterionInput
                - measurements: StringCriterionInput
                - fake_tits: StringCriterionInput
                - career_length: StringCriterionInput
                - tattoos: StringCriterionInput
                - piercings: StringCriterionInput
                - favorite: bool
                - rating100: IntCriterionInput
                - gender: GenderEnum
                - is_missing: str (what data is missing)
                - name: StringCriterionInput
                - studios: HierarchicalMultiCriterionInput
                - tags: HierarchicalMultiCriterionInput

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

            Find performers with specific tags:
            ```python
            result = await client.find_performers(
                performer_filter={
                    "tags": {
                        "value": ["tag1", "tag2"],
                        "modifier": "INCLUDES_ALL"
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

            Paginate results:
            ```python
            result = await client.find_performers(
                filter_={
                    "page": 1,
                    "per_page": 25,
                }
            )
            ```
        """
        try:
            # Add q to filter if provided
            if q is not None:
                filter_ = dict(filter_)  # Copy since we have a default
                filter_["q"] = q

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

        Returns:
            Created Performer object with ID and any server-generated fields

        Raises:
            ValueError: If the performer data is invalid
            gql.TransportError: If the request fails

        Examples:
            Create a basic performer:
            ```python
            performer = Performer(
                name="Performer Name",
            )
            created = await client.create_performer(performer)
            print(f"Created performer with ID: {created.id}")
            ```

            Create performer with metadata:
            ```python
            performer = Performer(
                name="Performer Name",
                # Add metadata
                gender="FEMALE",
                birthdate="1990-01-01",
                ethnicity="Caucasian",
                country="USA",
                eye_color="Blue",
                height_cm=170,
                measurements="34B-24-36",
                fake_tits="No",
                career_length="2010-2020",
                tattoos="None",
                piercings="Ears",
                url="https://example.com/performer",
                twitter="@performer",
                instagram="@performer",
                details="Performer details",
            )
            created = await client.create_performer(performer)
            ```

            Create performer with relationships:
            ```python
            performer = Performer(
                name="Performer Name",
                # Add relationships
                tags=[tag1, tag2],
                image="https://example.com/image.jpg",
                stash_ids=[stash_id1, stash_id2],
            )
            created = await client.create_performer(performer)
            ```
        """
        try:
            input_data = await performer.to_input()
            result = await self.execute(
                fragments.CREATE_PERFORMER_MUTATION,
                {"input": input_data},
            )
            return Performer(**result["performerCreate"])
        except Exception as e:
            error_message = str(e)
            if (
                "performer with name" in error_message
                and "already exists" in error_message
            ):
                self.log.info(
                    f"Performer '{performer.name}' already exists. Fetching existing performer."
                )
                # Clear both performer caches since we have a new performer
                self.find_performer.cache_clear()
                self.find_performers.cache_clear()
                # Try to find the existing performer with exact name match
                result = await self.find_performers(
                    performer_filter={
                        "name": {"value": performer.name, "modifier": "EQUALS"}
                    },
                )
                if result.count > 0:
                    return result.performers[0]
                raise  # Re-raise if we couldn't find the performer

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
            gql.TransportError: If the request fails

        Examples:
            Update performer name and metadata:
            ```python
            performer = await client.find_performer("123")
            if performer:
                performer.name = "New Name"
                performer.gender = "FEMALE"
                performer.birthdate = "1990-01-01"
                updated = await client.update_performer(performer)
                print(f"Updated performer: {updated.name}")
            ```

            Update performer relationships:
            ```python
            performer = await client.find_performer("123")
            if performer:
                # Add new tags
                performer.tags.extend([new_tag1, new_tag2])
                # Update image
                performer.image = "https://example.com/new-image.jpg"
                updated = await client.update_performer(performer)
            ```

            Update performer URLs:
            ```python
            performer = await client.find_performer("123")
            if performer:
                # Replace URLs
                performer.url = "https://example.com/new-url"
                performer.twitter = "@new_twitter"
                performer.instagram = "@new_instagram"
                updated = await client.update_performer(performer)
            ```

            Remove performer relationships:
            ```python
            performer = await client.find_performer("123")
            if performer:
                # Clear tags
                performer.tags = []
                # Clear image
                performer.image = None
                updated = await client.update_performer(performer)
            ```
        """
        try:
            input_data = await performer.to_input()
            result = await self.execute(
                fragments.UPDATE_PERFORMER_MUTATION,
                {"input": input_data},
            )
            return Performer(**result["performerUpdate"])
        except Exception as e:
            self.log.error(f"Failed to update performer: {e}")
            raise

    async def update_performer_image(
        self, performer: Performer, image_url: str
    ) -> Performer:
        """Update a performer's image.

        Args:
            performer: Performer object with at least the ID set
            image_url: URL or data URI of the image to set

        Returns:
            Updated Performer object with the new image

        Raises:
            ValueError: If the performer data is invalid
            gql.TransportError: If the request fails

        Examples:
            Update performer image with a data URI:
            ```python
            performer = await client.find_performer("123")
            if performer:
                # Update with data URI
                image_url = "data:image/jpeg;base64,..."
                updated = await client.update_performer_image(performer, image_url)
            ```

            Update performer image from a file:
            ```python
            performer = await client.find_performer("123")
            if performer:
                # Use the performer's update_avatar method
                updated = await performer.update_avatar(client, "/path/to/image.jpg")
            ```
        """
        try:
            # Create a minimal input with just ID and image
            input_data = {"id": performer.id, "image": image_url}

            result = await self.execute(
                fragments.UPDATE_PERFORMER_MUTATION,
                {"input": input_data},
            )
            return Performer(**result["performerUpdate"])
        except Exception as e:
            self.log.error(f"Failed to update performer image: {e}")
            raise
