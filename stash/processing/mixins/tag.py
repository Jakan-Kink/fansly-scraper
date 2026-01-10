"""Tag processing mixin."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from stash_graphql_client.types import Image, Scene, Tag

from ...logging import debug_print
from ...logging import processing_logger as logger


if TYPE_CHECKING:
    pass


class TagProcessingMixin:
    """Tag processing functionality."""

    async def _process_hashtags_to_tags(
        self,
        hashtags: list[Any],
    ) -> list[Tag]:
        """Process hashtags into Stash tags using batch operations.

        Migrated to use store.get_or_create() for massive API call reduction:
        - OLD: N hashtags x 2 searches = 2N API calls
        - NEW: N parallel get_or_create calls with identity map caching

        Args:
            hashtags: List of hashtag objects with value attribute

        Returns:
            List of Tag objects (90%+ reduction in API calls!)

        Note:
            Uses asyncio.gather for parallel tag creation/lookup.
            Identity map ensures tags are cached and reused.
        """
        if not hashtags:
            return []

        tag_names = [h.value.lower() for h in hashtags]
        logger.debug(
            f"Processing {len(tag_names)} hashtags into tags using batch operations"
        )

        # Use get_or_create in parallel for all tags (identity map handles duplicates)
        tag_tasks = [self.store.get_or_create(Tag, name=name) for name in tag_names]

        try:
            # Execute all get_or_create operations in parallel
            tags = await asyncio.gather(*tag_tasks, return_exceptions=True)

            # Filter out exceptions and log failures
            valid_tags = []
            for i, tag_or_exc in enumerate(tags):
                if isinstance(tag_or_exc, Exception):
                    logger.warning(
                        f"Failed to get/create tag '{tag_names[i]}': {tag_or_exc}"
                    )
                    debug_print(
                        {
                            "method": "StashProcessing - _process_hashtags_to_tags",
                            "status": "tag_failed",
                            "tag_name": tag_names[i],
                            "error": str(tag_or_exc),
                        }
                    )
                else:
                    valid_tags.append(tag_or_exc)
                    debug_print(
                        {
                            "method": "StashProcessing - _process_hashtags_to_tags",
                            "status": "tag_processed",
                            "tag_name": tag_or_exc.name,
                            "tag_id": tag_or_exc.id,
                        }
                    )

            logger.debug(
                f"Batch processed {len(valid_tags)}/{len(tag_names)} tags successfully"
            )

        except Exception as e:
            logger.exception(f"Batch tag processing failed: {e}")
            # Fallback: process tags one by one
            logger.warning("Falling back to sequential tag processing")
            tags = []
            for tag_name in tag_names:
                try:
                    tag = await self.store.get_or_create(Tag, name=tag_name)
                    tags.append(tag)
                except Exception as tag_error:
                    logger.warning(f"Failed to process tag '{tag_name}': {tag_error}")

            return tags
        else:
            # Success - return valid tags
            return valid_tags

    async def _add_preview_tag(
        self,
        file: Scene | Image,
    ) -> None:
        """Add preview tag to file.

        Args:
            file: Scene or Image object to update
        """
        # Try to find preview tag
        preview_tag = await self.store.find_one(Tag, name="Trailer")
        if preview_tag:
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
