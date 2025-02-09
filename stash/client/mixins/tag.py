"""Tag-related client functionality."""

from typing import Any

from ... import fragments
from ...types import FindTagsResultType, Tag
from ..protocols import StashClientProtocol


class TagClientMixin(StashClientProtocol):
    """Mixin for tag-related client methods."""

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
            if result and result.get("findTag"):
                return Tag(**result["findTag"])
            return None
        except Exception as e:
            self.log.error(f"Failed to find tag {id}: {e}")
            return None

    async def find_tags(
        self,
        filter_: dict[str, Any] = {"per_page": -1},
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
        """
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
        except Exception as e:
            self.log.error(f"Failed to find tags: {e}")
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
            httpx.HTTPError: If the request fails
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
            httpx.HTTPError: If the request fails
        """
        try:
            result = await self.execute(
                fragments.TAGS_MERGE_MUTATION,
                {"input": {"source": source, "destination": destination}},
            )
            return Tag(**result["tagsMerge"])
        except Exception as e:
            self.log.error(f"Failed to merge tags {source} into {destination}: {e}")
            raise

    async def bulk_tag_update(
        self,
        ids: list[str],
        description: str | None = None,
        aliases: list[str] | None = None,
        ignore_auto_tag: bool | None = None,
        favorite: bool | None = None,
        parent_ids: list[str] | None = None,
        child_ids: list[str] | None = None,
    ) -> list[Tag]:
        """Update multiple tags at once.

        Args:
            ids: List of tag IDs to update
            description: Optional description to set
            aliases: Optional list of aliases to set
            ignore_auto_tag: Optional ignore_auto_tag flag to set
            favorite: Optional favorite flag to set
            parent_ids: Optional list of parent tag IDs to set
            child_ids: Optional list of child tag IDs to set

        Returns:
            List of updated Tag objects

        Raises:
            ValueError: If the tag data is invalid
            httpx.HTTPError: If the request fails
        """
        try:
            input_data = {"ids": ids}
            if description is not None:
                input_data["description"] = description
            if aliases is not None:
                input_data["aliases"] = aliases
            if ignore_auto_tag is not None:
                input_data["ignore_auto_tag"] = ignore_auto_tag
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
            return [Tag(**tag) for tag in result["bulkTagUpdate"]]
        except Exception as e:
            self.log.error(f"Failed to bulk update tags {ids}: {e}")
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
