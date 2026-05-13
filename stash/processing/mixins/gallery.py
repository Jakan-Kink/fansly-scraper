"""Gallery processing mixin."""

from __future__ import annotations

import asyncio
import contextlib
import traceback
from pprint import pformat
from typing import Any

from stash_graphql_client.types import Gallery, GalleryChapter, Studio, is_set

from metadata import Account, ContentType, Post
from textio import print_error

from ...logging import debug_print
from ...logging import processing_logger as logger
from ..protocols import HasMetadata, StashProcessingProtocol


class GalleryProcessingMixin(StashProcessingProtocol):
    """Gallery processing functionality."""

    async def _get_gallery_by_stash_id(
        self,
        item: HasMetadata,
    ) -> Gallery | None:
        """Try to find gallery by stash_id using identity map.

        Note:
            Migrated to use store.get() for O(1) cached lookups.
        """
        if not hasattr(item, "stash_id") or not item.stash_id:
            return None

        # Cache-first: try sync get_cached() (zero-cost after preload),
        # fall back to async get() only if cache misses
        gallery = self.store.get_cached(Gallery, str(item.stash_id))
        if not gallery:
            gallery = await self.store.get(Gallery, str(item.stash_id))
        if gallery:
            debug_print(
                {
                    "method": "StashProcessing - _get_gallery_by_stash_id",
                    "status": "found",
                    "item_id": item.id,
                    "gallery_id": gallery.id,
                    "cached": "identity_map",
                }
            )
        return gallery

    async def _get_gallery_by_title(
        self,
        item: HasMetadata,
        title: str,
        studio: Studio | None,
    ) -> Gallery | None:
        """Try to find gallery by title and metadata.

        Pattern 5: Migrated to use store.find() with Django-style filtering.
        Additional filtering (date, studio) done in-memory for complex conditions.
        """
        # Cache-first: try sync filter() (zero-cost after preload),
        # fall back to async find() only if cache misses
        target_date = item.createdAt.strftime("%Y-%m-%d")
        galleries = self.store.filter(
            Gallery,
            lambda g: (
                g.title == title
                and g.date == target_date
                and (not studio or (g.studio and g.studio.id == studio.id))
            ),
        )
        gallery = galleries[0] if galleries else None

        if not gallery:
            # Fallback: query GraphQL and filter in-memory
            galleries = await self.store.find(Gallery, title__exact=title)
            gallery = next(
                (
                    g
                    for g in galleries
                    if g.date == target_date
                    and (not studio or (g.studio and g.studio.id == studio.id))
                ),
                None,
            )

        if gallery:
            debug_print(
                {
                    "method": "StashProcessing - _get_gallery_by_title",
                    "status": "found",
                    "item_id": item.id,
                    "gallery_id": gallery.id,
                }
            )
            if hasattr(item, "stash_id"):
                item.stash_id = int(gallery.id)
        return gallery

    async def _get_gallery_by_code(
        self,
        item: HasMetadata,
    ) -> Gallery | None:
        """Try to find gallery by code (post/message ID).

        Pattern 5: Migrated to use store.find_one() with Django-style filtering.
        Since code should be unique, use find_one() instead of find().

        Validates that returned gallery actually has the expected code to protect
        against server bugs or data corruption.
        """
        expected_code = str(item.id)
        # Cache-first: try sync filter() (zero-cost after preload),
        # fall back to async find_one() only if cache misses
        results = self.store.filter(Gallery, lambda g: g.code == expected_code)
        gallery = results[0] if results else None
        if not gallery:
            gallery = await self.store.find_one(Gallery, code=expected_code)

        # Validate the returned gallery has the expected code
        if gallery and gallery.code != expected_code:
            debug_print(
                {
                    "method": "StashProcessing - _get_gallery_by_code",
                    "status": "validation_failed",
                    "item_id": item.id,
                    "gallery_id": gallery.id,
                    "expected_code": expected_code,
                    "actual_code": gallery.code,
                }
            )
            return None

        if gallery:
            debug_print(
                {
                    "method": "StashProcessing - _get_gallery_by_code",
                    "status": "found",
                    "item_id": item.id,
                    "gallery_id": gallery.id,
                }
            )
            if hasattr(item, "stash_id"):
                item.stash_id = int(gallery.id)
        return gallery

    async def _get_gallery_by_url(
        self,
        item: HasMetadata,
        url: str,
    ) -> Gallery | None:
        """Try to find gallery by URL.

        Pattern 5: Migrated to use store.find() with Django-style filtering.
        Uses url__contains to search, then filters in-memory for exact match in urls list.
        """
        # Cache-first: try sync filter() (zero-cost after preload),
        # fall back to async find() only if cache misses
        galleries = self.store.filter(
            Gallery,
            lambda g: is_set(g.urls) and url in g.urls,
        )
        if not galleries:
            galleries = await self.store.find(Gallery, url__contains=url)
        if not galleries:
            return None

        # Filter results in-memory for exact URL match in list
        for gallery in galleries:
            # Check if url matches in urls list (url field is deprecated)
            if is_set(gallery.urls) and url in gallery.urls:
                debug_print(
                    {
                        "method": "StashProcessing - _get_gallery_by_url",
                        "status": "found",
                        "item_id": item.id,
                        "gallery_id": gallery.id,
                    }
                )
                if hasattr(item, "stash_id"):
                    item.stash_id = int(gallery.id)
                gallery.code = str(item.id)
                await self.store.save(gallery)
                return gallery
        return None

    async def _create_new_gallery(
        self,
        item: HasMetadata,
        title: str,
    ) -> Gallery:
        """Create a new gallery with basic fields."""
        debug_print(
            {
                "method": "StashProcessing - _create_new_gallery",
                "status": "creating",
                "item_id": item.id,
            }
        )
        return Gallery.new(
            title=title,
            details=item.content,
            code=str(item.id),  # Use post/message ID as code for uniqueness
            date=item.createdAt.strftime("%Y-%m-%d"),
            # created_at and updated_at handled by Stash
            organized=True,  # Mark as organized since we have metadata
            performers=[],  # Initialize as empty list to avoid UnsetType
            tags=[],  # Initialize as empty list to avoid UnsetType
        )

    async def _get_gallery_metadata(
        self,
        item: HasMetadata,
        account: Account,
        url_pattern: str,
    ) -> tuple[str, str, str]:
        """Get metadata needed for gallery operations.

        Args:
            item: The item to process
            account: The Account object
            url_pattern: URL pattern for the item

        Returns:
            Tuple of (username, title, url)
        """
        username = account.username

        # Generate title
        title = self._generate_title_from_content(
            content=item.content,
            username=username,
            created_at=item.createdAt,
        )

        # Generate URL
        url = url_pattern.format(username=username, id=item.id)

        return username, title, url

    async def _setup_gallery_performers(
        self,
        gallery: Gallery,
        item: HasMetadata,
        performer: Any,
    ) -> None:
        """Set up performers for a gallery.

        Args:
            gallery: Gallery to set up
            item: Source item with mentions
            performer: Main performer
        """
        performers = []

        # Add main performer (id is always loaded in library objects)
        if performer:
            performers.append(performer)

        # Add mentioned accounts as performers
        mentions = getattr(item, "mentions", None)
        if mentions:
            mention_tasks = [
                self._find_existing_performer(mention) for mention in mentions
            ]
            mention_results = await asyncio.gather(*mention_tasks)
            performers.extend([p for p in mention_results if p is not None])

        # Add performers using relationship helper for bidirectional sync
        for perf in performers:
            await gallery.add_performer(perf)

    async def _check_aggregated_posts(self, posts: list[Post]) -> bool:
        """Check if any aggregated posts have media content.

        Args:
            posts: List of posts to check

        Returns:
            True if any post has media content, False otherwise
        """
        for post in posts:
            if await self._has_media_content(post):
                return True
        return False

    async def _has_media_content(self, item: HasMetadata) -> bool:
        """Check if an item has media content that needs a gallery.

        Args:
            item: The item to check

        Returns:
            True if the item has media content, False otherwise
        """
        # Check for attachments
        if hasattr(item, "attachments") and item.attachments:
            for attachment in item.attachments:
                # Direct media content
                if hasattr(attachment, "contentType") and attachment.contentType in (
                    ContentType.ACCOUNT_MEDIA,
                    ContentType.ACCOUNT_MEDIA_BUNDLE,
                ):
                    debug_print(
                        {
                            "method": "StashProcessing - _has_media_content",
                            "status": "has_media",
                            "item_id": item.id,
                            "content_type": attachment.contentType,
                        }
                    )
                    return True

                # Aggregated posts (which might contain media)
                if (
                    hasattr(attachment, "contentType")
                    and attachment.contentType == ContentType.AGGREGATED_POSTS
                    and hasattr(attachment, "resolve_content")
                    and (post := await attachment.resolve_content())
                    and await self._check_aggregated_posts([post])
                ):
                    debug_print(
                        {
                            "method": "StashProcessing - _has_media_content",
                            "status": "has_aggregated_media",
                            "item_id": item.id,
                            "post_id": post.id,
                        }
                    )
                    return True

        debug_print(
            {
                "method": "StashProcessing - _has_media_content",
                "status": "no_media",
                "item_id": item.id,
            }
        )
        return False

    async def _get_or_create_gallery(
        self,
        item: HasMetadata,
        account: Account,
        performer: Any,
        studio: Studio | None,
        item_type: str,  # noqa: ARG002
        url_pattern: str,
    ) -> Gallery | None:
        """Get or create a gallery for an item.

        Args:
            item: The item to process
            account: The Account object
            performer: The Performer object
            studio: The Studio object
            _item_type: Type of item ("post" or "message") - reserved for future use
            url_pattern: URL pattern for the item

        Returns:
            Gallery object or None if creation fails or item has no media
        """
        # Only create/get gallery if there's media content
        if not await self._has_media_content(item):
            debug_print(
                {
                    "method": "StashProcessing - _get_or_create_gallery",
                    "status": "skipped_no_media",
                    "item_id": item.id,
                }
            )
            return None
        # Get metadata needed for all operations
        username, title, url = await self._get_gallery_metadata(
            item, account, url_pattern
        )

        # Try each search method in order
        for method in [
            lambda: self._get_gallery_by_stash_id(item),
            lambda: self._get_gallery_by_code(item),
            lambda: self._get_gallery_by_title(item, title, studio),
            lambda: self._get_gallery_by_url(item, url),
        ]:
            if gallery := await method():
                return gallery

        # Create new gallery if none found
        gallery = await self._create_new_gallery(item, title)

        # Set up performers
        await self._setup_gallery_performers(gallery, item, performer)

        # Set studio if provided (id is always loaded in library objects)
        if studio:
            gallery.studio = studio

        # Set URLs (url field is deprecated, use urls)
        gallery.urls = [url]

        # Save gallery so it gets a real Stash ID (required for chapter gallery_id)
        await self.store.save(gallery)

        # Persist stash_id back on the item so subsequent lookups use
        # _get_gallery_by_stash_id() (O(1) cache hit) instead of re-searching
        if hasattr(item, "stash_id") and gallery.id is not None:
            with contextlib.suppress(ValueError, TypeError):
                item.stash_id = int(gallery.id)

        # Add chapters for aggregated posts
        if hasattr(item, "attachments"):
            image_index = 1
            for attachment in item.attachments:
                if (
                    hasattr(attachment, "contentType")
                    and attachment.contentType == ContentType.AGGREGATED_POSTS
                    and hasattr(attachment, "resolve_content")
                    and (post := await attachment.resolve_content())
                    and await self._has_media_content(post)
                ):
                    # Only create chapter if post has media
                    # Generate chapter title using same method as gallery title
                    title = self._generate_title_from_content(
                        content=post.content,
                        username=username,  # Use same username as parent
                        created_at=post.createdAt,
                    )

                    # Create chapter
                    chapter = GalleryChapter.new(
                        gallery=gallery,
                        title=title,
                        image_index=image_index,
                    )
                    await self.store.save(chapter)
                    image_index += 1  # Increment for next chapter
        return gallery

    async def _process_item_gallery(
        self,
        item: HasMetadata,
        account: Account,
        performer: Any,
        studio: Studio | None,
        item_type: str,
        url_pattern: str,
    ) -> None:
        """Process a single item's gallery.

        Args:
            item: Item to process
            account: Account that owns the item
            performer: Performer to associate with gallery
            studio: Optional studio to associate with gallery
            item_type: Type of item ("post" or "message")
            url_pattern: URL pattern for the item
        """
        debug_print(
            {
                "method": "StashProcessing - _process_item_gallery",
                "status": "entry",
                "item_id": item.id,
                "item_type": item_type,
                "attachment_count": (
                    len(item.attachments) if hasattr(item, "attachments") else 0
                ),
            }
        )

        attachments = item.attachments or []
        debug_print(
            {
                "method": "StashProcessing - _process_item_gallery",
                "status": "got_attachments",
                "item_id": item.id,
                "attachment_count": len(attachments),
                "attachment_ids": ([a.id for a in attachments] if attachments else []),
            }
        )
        if not attachments:
            debug_print(
                {
                    "method": "StashProcessing - _process_item_gallery",
                    "status": "no_attachments",
                    "item_id": item.id,
                }
            )
            return

        debug_print(
            {
                "method": "StashProcessing - _process_item_gallery",
                "status": "processing_attachments",
                "item_id": item.id,
                "attachment_count": len(attachments),
                "attachment_ids": [a.id for a in attachments],
            }
        )

        # Collect all media from attachments for batch processing
        media_batch = await self._collect_media_from_attachments(attachments)

        debug_print(
            {
                "method": "StashProcessing - _process_item_gallery",
                "status": "collected_media_batch",
                "item_id": item.id,
                "media_count": len(media_batch),
                "account_id": account.id if account else None,
            }
        )

        debug_print(
            {
                "method": "StashProcessing - _process_item_gallery",
                "status": "creating_gallery",
                "item_id": item.id,
            }
        )
        gallery = await self._get_or_create_gallery(
            item=item,
            account=account,
            performer=performer,
            studio=studio,
            item_type=item_type,
            url_pattern=url_pattern,
        )
        if not gallery:
            debug_print(
                {
                    "method": "StashProcessing - _process_item_gallery",
                    "status": "gallery_creation_failed",
                    "item_id": item.id,
                }
            )
            return
        debug_print(
            {
                "method": "StashProcessing - _process_item_gallery",
                "status": "gallery_created",
                "item_id": item.id,
                "gallery_id": gallery.id if gallery else None,
            }
        )

        # Add hashtags as tags using relationship helper
        hashtags = getattr(item, "hashtags", None)
        if hashtags:
            tags = await self._process_hashtags_to_tags(hashtags)
            for tag in tags:
                await gallery.add_tag(tag)

        # Process media batch
        all_images = []
        all_scenes = []

        # Only process media if we have a batch
        if media_batch:
            try:
                # Group media by mimetype group (image, video, application)
                image_media = []
                video_media = []
                app_media = []

                for media in media_batch:
                    mimetype = getattr(media, "mimetype", "")
                    if mimetype and mimetype.startswith("image/"):
                        image_media.append(media)
                    elif mimetype and mimetype.startswith("video/"):
                        video_media.append(media)
                    elif mimetype and mimetype.startswith("application/"):
                        app_media.append(media)

                debug_print(
                    {
                        "method": "StashProcessing - _process_item_gallery",
                        "status": "processing_media_by_mimetype",
                        "item_id": item.id,
                        "image_count": len(image_media),
                        "video_count": len(video_media),
                        "application_count": len(app_media),
                    }
                )

                # Process images batch
                if image_media:
                    image_result = await self._process_media_batch_by_mimetype(
                        media_list=image_media,
                        item=item,
                        account=account,
                    )
                    all_images.extend(image_result["images"])

                # Process videos batch
                if video_media:
                    video_result = await self._process_media_batch_by_mimetype(
                        media_list=video_media,
                        item=item,
                        account=account,
                    )
                    all_scenes.extend(video_result["scenes"])

                # Process application batch
                if app_media:
                    app_result = await self._process_media_batch_by_mimetype(
                        media_list=app_media,
                        item=item,
                        account=account,
                    )
                    all_scenes.extend(app_result["scenes"])

                debug_print(
                    {
                        "method": "StashProcessing - _process_item_gallery",
                        "status": "media_batch_processed",
                        "item_id": item.id,
                        "images_processed": len(all_images),
                        "scenes_processed": len(all_scenes),
                    }
                )
            except Exception as e:
                debug_print(
                    {
                        "method": "StashProcessing - _process_item_gallery",
                        "status": "media_batch_failed",
                        "item_id": item.id,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
                )

        if not all_images and not all_scenes:
            # No content was processed, delete the gallery if we just created it
            if gallery.is_new():
                debug_print(
                    {
                        "method": "StashProcessing - _process_item_gallery",
                        "status": "deleting_empty_gallery",
                        "item_id": item.id,
                        "gallery_id": gallery.id,
                    }
                )
                await self.store.delete(gallery)
            return

        debug_print(
            {
                "method": "StashProcessing - _process_item_gallery",
                "status": "content_summary",
                "item_id": item.id,
                "gallery_id": gallery.id,
                "image_count": len(all_images),
                "scene_count": len(all_scenes),
            }
        )

        # Gallery.__side_mutations__["images"] fires addGalleryImages at
        # gallery.save(), diffing against _snapshot.
        if all_images:
            gallery.images = list(gallery.images or []) + list(all_images)
        for scene in all_scenes:
            await gallery.add_scene(scene)
        if all_scenes:
            debug_print(
                {
                    "method": "StashProcessing - _process_item_gallery",
                    "status": "gallery_scenes_added",
                    "item_id": item.id,
                    "gallery_id": gallery.id,
                    "scene_count": len(all_scenes),
                    "scenes": pformat(all_scenes),
                }
            )

        # Save gallery
        try:
            await self.store.save(gallery)
        except Exception as e:
            logger.exception(
                f"Failed to save gallery for {item_type} {item.id}",
                exc_info=e,
            )
            debug_print(
                {
                    "method": "StashProcessing - _process_item_gallery",
                    "status": "gallery_save_error",
                    "item_id": item.id,
                    "gallery_id": gallery.id,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }
            )
            print_error(f"Failed to save gallery for {item_type} {item.id}: {e}")
