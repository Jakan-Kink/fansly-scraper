"""Studio-related client functionality."""

from typing import Any

from ... import fragments
from ...client_helpers import async_lru_cache
from ...types import FindStudiosResultType, Studio
from ..protocols import StashClientProtocol
from ..utils import sanitize_model_data


class StudioClientMixin(StashClientProtocol):
    """Mixin for studio-related client methods."""

    @async_lru_cache(maxsize=3096, exclude_arg_indices=[0])  # exclude self
    async def find_studio(self, id: str) -> Studio | None:
        """Find a studio by its ID.

        Args:
            id: The ID of the studio to find

        Returns:
            Studio object if found, None otherwise
        """
        try:
            result = await self.execute(
                fragments.FIND_STUDIO_QUERY,
                {"id": id},
            )
            if result and result.get("findStudio"):
                # Sanitize model data before creating Studio
                clean_data = sanitize_model_data(result["findStudio"])
                return Studio(**clean_data)
            return None
        except Exception:
            self.log.exception(f"Failed to find studio {id}")
            return None

    @async_lru_cache(maxsize=3096, exclude_arg_indices=[0])  # exclude self
    async def find_studios(
        self,
        filter_: dict[str, Any] = {"per_page": -1},
        studio_filter: dict[str, Any] | None = None,
        q: str | None = None,
    ) -> FindStudiosResultType:
        """Find studios matching the given filters.

        Args:
            filter_: Optional general filter parameters:
                - q: str (search query)
                - direction: SortDirectionEnum (ASC/DESC)
                - page: int
                - per_page: int
                - sort: str (field to sort by)
            studio_filter: Optional studio-specific filter
            q: Optional search query (alternative to filter_["q"])

        Returns:
            FindStudiosResultType containing:
                - count: Total number of matching studios
                - studios: List of Studio objects
        """
        try:
            # Add q to filter if provided
            if q is not None:
                filter_ = dict(filter_ or {})
                filter_["q"] = q

            result = await self.execute(
                fragments.FIND_STUDIOS_QUERY,
                {"filter": filter_, "studio_filter": studio_filter},
            )
            return FindStudiosResultType(**result["findStudios"])
        except Exception:
            self.log.exception("Failed to find studios")
            return FindStudiosResultType(count=0, studios=[])

    async def create_studio(self, studio: Studio) -> Studio:
        """Create a new studio in Stash.

        Args:
            studio: Studio object with the data to create. Required fields:
                - name: Studio name

        Returns:
            Created Studio object with ID and any server-generated fields

        Raises:
            ValueError: If the studio data is invalid
            gql.TransportError: If the request fails
        """
        try:
            input_data = await studio.to_input()
            result = await self.execute(
                fragments.CREATE_STUDIO_MUTATION,
                {"input": input_data},
            )
            # Clear caches since we've modified studios
            self.find_studio.cache_clear()
            self.find_studios.cache_clear()
            # Sanitize model data before creating Studio
            clean_data = sanitize_model_data(result["studioCreate"])
            return Studio(**clean_data)
        except Exception:
            self.log.exception("Failed to create studio")
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
            gql.TransportError: If the request fails
        """
        try:
            input_data = await studio.to_input()

            # Skip update if only ID is present (no actual changes)
            if set(input_data.keys()) <= {"id"}:
                self.log.debug(f"No changes to update for studio {studio.id}")
                return studio

            result = await self.execute(
                fragments.UPDATE_STUDIO_MUTATION,
                {"input": input_data},
            )
            # Clear caches since we've modified a studio
            self.find_studio.cache_clear()
            self.find_studios.cache_clear()
            # Sanitize model data before creating Studio
            clean_data = sanitize_model_data(result["studioUpdate"])
            updated_studio = Studio(**clean_data)
            # Mark as clean since we just saved
            updated_studio.mark_clean()
            return updated_studio
        except Exception:
            self.log.exception("Failed to update studio")
            raise
