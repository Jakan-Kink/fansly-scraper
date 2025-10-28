"""Tag-related client functionality."""

from typing import Any

from ... import fragments
from ...client_helpers import async_lru_cache
from ...types import FindTagsResultType, Tag
from ..protocols import StashClientProtocol
from ..utils import sanitize_model_data


class TagClientMixin(StashClientProtocol):
    """Mixin for tag-related client methods."""

    @async_lru_cache(maxsize=3096, exclude_arg_indices=[0])  # exclude self
    async def find_tag(self, id: str) -> Tag | None:
        """Find a tag by its ID.

        Args:
            id: The ID of the tag to find

        Returns:
            Tag object if found, None otherwise
        """
        try:
            result = await self.execute(
                fragments.FIND_TAG_QUERY,
                {"id": id},
            )
        except Exception:
            self.log.exception(f"Failed to find tag {id}")
            return None
        else:
            if result and result.get("findTag"):
                # Sanitize model data before creating Tag
                clean_data = sanitize_model_data(result["findTag"])
                return Tag(**clean_data)
            return None

    @async_lru_cache(maxsize=3096, exclude_arg_indices=[0])  # exclude self
    async def find_tags(
        self,
        filter_: dict[str, Any] | None = None,
        tag_filter: dict[str, Any] | None = None,
        q: str | None = None,
    ) -> FindTagsResultType:
        """Find tags matching the given filters.

        Args:
            filter_: Optional general filter parameters:
                - q: str (search query)
                - direction: SortDirectionEnum (ASC/DESC)
                - page: int
                - per_page: int
                - sort: str (field to sort by)
            tag_filter: Optional tag-specific filter
            q: Optional search query (alternative to filter_["q"])

        Returns:
            FindTagsResultType containing:
                - count: Total number of matching tags
                - tags: List of Tag objects

        Note:
            Results are cached using async_lru_cache with the function arguments as key.
        """
        if filter_ is None:
            filter_ = {"per_page": -1}
        try:
            # Add q to filter if provided
            if q is not None:
                filter_ = dict(filter_ or {})
                filter_["q"] = q

            result = await self.execute(
                fragments.FIND_TAGS_QUERY,
                {"filter": filter_, "tag_filter": tag_filter},
            )
            return FindTagsResultType(**result["findTags"])
        except Exception:
            self.log.exception("Failed to find tags")
            return FindTagsResultType(count=0, tags=[])

    async def create_tag(self, tag: Tag) -> Tag:
        """Create a new tag in Stash.

        Args:
            tag: Tag object with the data to create. Required fields:
                - name: Tag name

        Returns:
            Created Tag object with ID and any server-generated fields

        Raises:
            ValueError: If the tag data is invalid
            gql.TransportError: If the request fails
        """
        try:
            input_data = await tag.to_input()
            result = await self.execute(
                fragments.CREATE_TAG_MUTATION,
                {"input": input_data},
            )
            return Tag(**sanitize_model_data(result["tagCreate"]))
        except Exception as e:
            error_message = str(e)
            if "tag with name" in error_message and "already exists" in error_message:
                self.log.info(
                    f"Tag '{tag.name}' already exists. Fetching existing tag."
                )
                # Clear both tag caches
                self.find_tag.cache_clear()
                self.find_tags.cache_clear()
                # Try to find the existing tag with exact name match
                results = await self.find_tags(
                    tag_filter={"name": {"value": tag.name, "modifier": "EQUALS"}},
                )
                if results.count > 0:
                    return Tag(**sanitize_model_data(results.tags[0]))
                raise  # Re-raise if we couldn't find the tag
            self.log.exception("Failed to create tag")
            raise

    async def tags_merge(
        self,
        source: list[str],
        destination: str,
    ) -> Tag:
        """Merge multiple tags into one.

        Args:
            source: List of source tag IDs to merge
            destination: Destination tag ID

        Returns:
            Updated destination Tag object

        Raises:
            ValueError: If the tag data is invalid
            gql.TransportError: If the request fails
        """
        try:
            result = await self.execute(
                fragments.TAGS_MERGE_MUTATION,
                {"input": {"source": source, "destination": destination}},
            )
            # Clear caches since we've modified tags
            self.find_tag.cache_clear()
            self.find_tags.cache_clear()
            return Tag(**sanitize_model_data(result["tagsMerge"]))
        except Exception:
            self.log.exception(f"Failed to merge tags {source} into {destination}")
            raise

    async def bulk_tag_update(
        self,
        ids: list[str],
        description: str | None = None,
        aliases: list[str] | None = None,
        favorite: bool | None = None,
        parent_ids: list[str] | None = None,
        child_ids: list[str] | None = None,
    ) -> list[Tag]:
        """Update multiple tags at once.

        Args:
            ids: List of tag IDs to update
            description: Optional description to set
            aliases: Optional list of aliases to set
            favorite: Optional favorite flag to set
            parent_ids: Optional list of parent tag IDs to set
            child_ids: Optional list of child tag IDs to set

        Returns:
            List of updated Tag objects

        Raises:
            ValueError: If the tag data is invalid
            gql.TransportError: If the request fails
        """
        try:
            input_data = {"ids": ids}
            if description is not None:
                input_data["description"] = description
            if aliases is not None:
                input_data["aliases"] = aliases
            if favorite is not None:
                input_data["favorite"] = favorite
            if parent_ids is not None:
                input_data["parent_ids"] = parent_ids
            if child_ids is not None:
                input_data["child_ids"] = child_ids

            result = await self.execute(
                fragments.BULK_TAG_UPDATE_MUTATION,
                {"input": input_data},
            )
            # Clear caches since we've modified tags
            self.find_tag.cache_clear()
            self.find_tags.cache_clear()
            return [Tag(**sanitize_model_data(tag)) for tag in result["bulkTagUpdate"]]
        except Exception:
            self.log.exception(f"Failed to bulk update tags {ids}")
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
            gql.TransportError: If the request fails
        """
        try:
            input_data = await tag.to_input()

            # Skip update if only ID is present (no actual changes)
            if set(input_data.keys()) <= {"id"}:
                self.log.debug(f"No changes to update for tag {tag.id}")
                return tag

            result = await self.execute(
                fragments.UPDATE_TAG_MUTATION,
                {"input": input_data},
            )
        except Exception:
            self.log.exception("Failed to update tag")
            raise
        else:
            # Clear caches since we've modified a tag
            self.find_tag.cache_clear()
            self.find_tags.cache_clear()
            updated_tag = Tag(**sanitize_model_data(result["tagUpdate"]))
            # Mark as clean since we just saved
            updated_tag.mark_clean()
            return updated_tag
