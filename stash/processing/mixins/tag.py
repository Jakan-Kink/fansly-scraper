"""Tag processing mixin."""

from __future__ import annotations

import asyncio
from typing import Any

from stash_graphql_client.types import Image, Scene, Tag, is_set

from ...logging import debug_print
from ...logging import processing_logger as logger
from ..protocols import StashProcessingProtocol


class TagProcessingMixin(StashProcessingProtocol):
    """Tag processing functionality."""

    async def _preload_tags(self) -> None:
        """Fetch every Stash tag into the store, then index by name and alias.

        A store that already holds tags (the daemon keeps one for its lifetime)
        is indexed without another fetch.
        """
        if not self.store.all_cached(Tag):
            async for _tag in self.store.find_iter(
                Tag, query_batch=self.config.stash_tags_batch
            ):
                pass
        self._tags_by_name = {}
        for tag in self.store.all_cached(Tag):
            self._index_tag(tag)

    def _index_tag(self, tag: Tag) -> None:
        """Record a tag under its lowercased name and aliases; a name wins."""
        if is_set(tag.aliases) and tag.aliases:
            for alias in tag.aliases:
                self._tags_by_name.setdefault(alias.lower(), tag)
        if is_set(tag.name) and tag.name:
            self._tags_by_name[tag.name.lower()] = tag

    def _find_tag_in_cache(self, name: str) -> Tag | None:
        """Find a tag by name or alias among the tags the store holds.

        Checks both tag names and aliases (case-insensitive) to prevent
        creating duplicate tags that collide with existing aliases. The
        name/alias index answers first; a tag cached since it was built is
        found by scanning the store.

        Args:
            name: Tag name to search for

        Returns:
            Tag if found by name or alias, None otherwise
        """
        name_lower = name.lower()
        indexed = self._tags_by_name.get(name_lower)
        if indexed is not None:
            return indexed
        results = self.store.filter(
            Tag,
            lambda t: bool(
                (is_set(t.name) and t.name and t.name.lower() == name_lower)
                or (
                    is_set(t.aliases)
                    and t.aliases
                    and any(a.lower() == name_lower for a in t.aliases)
                )
            ),
        )
        if not results:
            return None
        self._index_tag(results[0])
        return results[0]

    async def _get_or_create_tag(self, name: str) -> Tag:
        """Get existing tag or create new one, checking aliases.

        Stash rejects tag creation when the name collides with an existing
        alias (case-insensitive). This method checks cache (name + aliases),
        falls back to GraphQL name search, then alias search, before creating.

        Args:
            name: Tag name to find or create

        Returns:
            Tag object (existing or newly created and saved)
        """
        # 1. Cache-first: check both name and aliases (tags are preloaded)
        tag = self._find_tag_in_cache(name)
        if tag:
            return tag

        # 2. GraphQL fallback: search by name
        tag = await self.store.find_one(Tag, name=name)
        # 3. GraphQL fallback: search by alias
        if not tag:
            tag = await self.store.find_one(Tag, aliases__contains=name)
        # 4. Not found anywhere — create and save
        if not tag:
            tag = Tag.new(name=name)
            await self.store.save(tag)
        self._index_tag(tag)
        return tag

    async def _process_hashtags_to_tags(
        self,
        hashtags: list[Any],
    ) -> list[Tag]:
        """Process hashtags into Stash tags using batch operations.

        Uses cache-first lookup with alias checking to prevent duplicate
        tag creation errors from Stash.

        Args:
            hashtags: List of hashtag objects with value attribute

        Returns:
            List of Tag objects
        """
        if not hashtags:
            return []

        tag_names = [h.value.lower() for h in hashtags]
        logger.debug(f"Processing {len(tag_names)} hashtags into tags")

        # Process all tags (cache hits are instant, GraphQL only for misses)
        tag_tasks = [self._get_or_create_tag(name) for name in tag_names]

        try:
            tags = await asyncio.gather(*tag_tasks, return_exceptions=True)

            valid_tags = []
            for i, tag_or_exc in enumerate(tags):
                if isinstance(tag_or_exc, BaseException):
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
                f"Processed {len(valid_tags)}/{len(tag_names)} tags successfully"
            )

        except Exception as e:
            logger.exception(f"Batch tag processing failed: {e}")
            logger.warning("Falling back to sequential tag processing")
            valid_tags = []
            for tag_name in tag_names:
                try:
                    tag = await self._get_or_create_tag(tag_name)
                    valid_tags.append(tag)
                except Exception as tag_error:
                    logger.warning(f"Failed to process tag '{tag_name}': {tag_error}")

        return valid_tags

    async def _add_preview_tag(
        self,
        file: Scene | Image,
    ) -> None:
        """Add preview tag to file.

        Args:
            file: Scene or Image object to update
        """
        # Cache-first: try sync filter() (zero-cost, tags preloaded with TTL=None),
        # fall back to async find_one() only if cache misses
        results = self.store.filter(Tag, lambda t: t.name == "Trailer")
        preview_tag = results[0] if results else None
        if not preview_tag:
            preview_tag = await self.store.find_one(Tag, name="Trailer")
        if preview_tag:
            await file.add_tag(preview_tag)
