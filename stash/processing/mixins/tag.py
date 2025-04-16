"""Tag processing mixin."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...logging import debug_print
from ...types import Image, Scene, Tag

if TYPE_CHECKING:
    pass


class TagProcessingMixin:
    """Tag processing functionality."""

    async def _process_hashtags_to_tags(
        self,
        hashtags: list[Any],
    ) -> list[Tag]:
        """Process hashtags into Stash tags.

        Args:
            hashtags: List of hashtag objects with value attribute

        Returns:
            List of Tag objects
        """
        tags = []
        for hashtag in hashtags:
            # Try to find existing tag by exact name or alias
            tag_name = hashtag.value.lower()  # Case-insensitive comparison
            found_tag = None

            # First try exact name match
            name_results = await self.context.client.find_tags(
                tag_filter={"name": {"value": tag_name, "modifier": "EQUALS"}},
            )
            if name_results.count > 0:
                # Convert dict to Tag object using unpacking
                found_tag = Tag(**name_results.tags[0])
                debug_print(
                    {
                        "method": "StashProcessing - _process_hashtags_to_tags",
                        "status": "tag_found_by_name",
                        "tag_name": tag_name,
                        "found_tag": found_tag.name,
                    }
                )
            else:
                # Then try alias match
                alias_results = await self.context.client.find_tags(
                    tag_filter={"aliases": {"value": tag_name, "modifier": "INCLUDES"}},
                )
                if alias_results.count > 0:
                    # Convert dict to Tag object using unpacking
                    found_tag = Tag(**alias_results.tags[0])
                    debug_print(
                        {
                            "method": "StashProcessing - _process_hashtags_to_tags",
                            "status": "tag_found_by_alias",
                            "tag_name": tag_name,
                            "found_tag": found_tag.name,
                        }
                    )

            if found_tag:
                tags.append(found_tag)
            else:
                # Create new tag if not found
                new_tag = Tag(name=tag_name, id="new")
                try:
                    if created_tag := await self.context.client.create_tag(new_tag):
                        tags.append(created_tag)
                        debug_print(
                            {
                                "method": "StashProcessing - _process_hashtags_to_tags",
                                "status": "tag_created",
                                "tag_name": hashtag.value,
                            }
                        )
                except Exception as e:
                    error_message = str(e)
                    # If tag already exists, it will be handled by create_tag
                    if (
                        "tag with name" in error_message
                        and "already exists" in error_message
                    ):
                        if found_tag := await self.context.client.create_tag(new_tag):
                            tags.append(found_tag)
                            debug_print(
                                {
                                    "method": "StashProcessing - _process_hashtags_to_tags",
                                    "status": "tag_found_after_create_failed",
                                    "tag_name": tag_name,
                                    "found_tag": found_tag.name,
                                }
                            )
                    else:
                        # Re-raise if it's not a "tag already exists" error
                        raise

        return tags

    async def _add_preview_tag(
        self,
        file: Scene | Image,
    ) -> None:
        """Add preview tag to file.

        Args:
            file: Scene or Image object to update
        """
        # Try to find preview tag
        tag_data = await self.context.client.find_tags(
            q="Trailer",
        )
        if tag_data.count > 0:
            preview_tag = tag_data.tags[0]
            # Check if tag already exists
            current_tag_ids = (
                {t.id for t in file.tags} if hasattr(file, "tags") else set()
            )
            if preview_tag.id not in current_tag_ids:
                debug_print(
                    {
                        "method": "StashProcessing - _add_preview_tag",
                        "status": "adding_preview_tag",
                        "file_id": file.id,
                        "tag_id": preview_tag.id,
                    }
                )
                file.tags.append(preview_tag)
            else:
                debug_print(
                    {
                        "method": "StashProcessing - _add_preview_tag",
                        "status": "preview_tag_exists",
                        "file_id": file.id,
                        "tag_id": preview_tag.id,
                    }
                )
