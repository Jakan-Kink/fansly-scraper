"""Gallery processing mixin."""

from __future__ import annotations

import asyncio
import contextlib
import traceback
from collections.abc import Callable
from pprint import pformat
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import inspect
from sqlalchemy.orm import Session
from stash_graphql_client.types import Gallery, GalleryChapter, Studio, is_set

from metadata import Account, Post
from metadata.attachment import ContentType
from textio import print_error

from ...logging import debug_print
from ...logging import processing_logger as logger


if TYPE_CHECKING:
    from datetime import datetime


class HasMetadata(Protocol):
    """Protocol for models that have metadata for Stash."""

    id: int
    content: str | None
    createdAt: datetime
    attachments: list[Any]
    # Messages don't have accountMentions, only Posts do
    accountMentions: list[Account] | None = None
    stash_id: int | None = None
    awaitable_attrs: Callable | None = None


class GalleryProcessingMixin:
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

        # Use store.get() for identity map caching (instant if cached)
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
        # Use store.find() for identity map caching
        galleries = await self.store.find(Gallery, title__exact=title)
        if not galleries:
            return None

        # Filter for matching date and studio
        target_date = item.createdAt.strftime("%Y-%m-%d")
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
        """
        # Use store.find_one() for identity map caching (code is unique)
        gallery = await self.store.find_one(Gallery, code=str(item.id))
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
        # Use store.find() for identity map caching
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
                await gallery.save(self.context.client)
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
        return Gallery(
            id="new",  # Will be replaced on save
            title=title,
            details=item.content,
            code=str(item.id),  # Use post/message ID as code for uniqueness
            date=item.createdAt.strftime("%Y-%m-%d"),
            # created_at and updated_at handled by Stash
            organized=True,  # Mark as organized since we have metadata
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
        # Get username
        username = (
            await account.awaitable_attrs.username
            if hasattr(account, "awaitable_attrs")
            else account.username
        )

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
        try:
            mentions = await item.awaitable_attrs.accountMentions
            if mentions:
                mention_tasks = [
                    self._find_existing_performer(mention) for mention in mentions
                ]
                mention_results = await asyncio.gather(*mention_tasks)
                performers.extend([p for p in mention_results if p is not None])
        except AttributeError:
            # Item doesn't have accountMentions attribute (e.g., Messages)
            pass

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

        # Add chapters for aggregated posts
        if hasattr(item, "attachments"):
            image_index = 0
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
                    chapter = GalleryChapter(
                        id="new",
                        gallery=gallery,
                        title=title,
                        image_index=image_index,
                    )
                    gallery.chapters.append(chapter)
                    image_index += 1  # Increment for next chapter

        # Save gallery with chapters
        await gallery.save(self.context.client)
        return gallery

    async def _process_item_gallery(
        self,
        item: HasMetadata,
        account: Account,
        performer: Any,
        studio: Studio | None,
        item_type: str,
        url_pattern: str,
        session: Session | None = None,
    ) -> None:
        """Process a single item's gallery.

        Args:
            item: Item to process
            account: Account that owns the item
            performer: Performer to associate with gallery
            studio: Optional studio to associate with gallery
            item_type: Type of item ("post" or "message")
            url_pattern: URL pattern for the item
            session: Optional database session
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

        async with contextlib.AsyncExitStack() as stack:
            if session is None:
                session = await stack.enter_async_context(
                    self.database.get_async_session()
                )

            attachments = await item.awaitable_attrs.attachments or []
            debug_print(
                {
                    "method": "StashProcessing - _process_item_gallery",
                    "status": "got_attachments",
                    "item_id": item.id,
                    "attachment_count": len(attachments),
                    "attachment_ids": (
                        [a.id for a in attachments] if attachments else []
                    ),
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
                    "account_id": inspect(account).identity[0] if account else None,
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
            try:
                hashtags = await item.awaitable_attrs.hashtags
                if hashtags:
                    tags = await self._process_hashtags_to_tags(hashtags)
                    for tag in tags:
                        await gallery.add_tag(tag)
            except AttributeError:
                # Item doesn't have hashtags attribute (e.g., Messages)
                pass

            # Process media batch
            all_images = []
            all_scenes = []

            # Only process media if we have a batch
            if media_batch:
                try:
                    # Create batches by mimetype group for more efficient processing
                    # Group media by mimetype group (image, video, application)
                    image_media = []
                    video_media = []
                    app_media = []

                    for media in media_batch:
                        if hasattr(media, "awaitable_attrs"):
                            await media.awaitable_attrs.mimetype

                        mimetype = getattr(media, "mimetype", "")
                        if mimetype and mimetype.startswith("image/"):
                            image_media.append(media)
                        elif mimetype and mimetype.startswith("video/"):
                            video_media.append(media)
                        elif mimetype and mimetype.startswith("application/"):
                            app_media.append(media)

                    # Process each batch separately
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
                if gallery.id == "new":
                    debug_print(
                        {
                            "method": "StashProcessing - _process_item_gallery",
                            "status": "deleting_empty_gallery",
                            "item_id": item.id,
                            "gallery_id": gallery.id,
                        }
                    )
                    await gallery.destroy(self.context.client)
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

            # Link images and scenes to gallery
            # Link images using the special API endpoint
            # Note: Manual retry logic removed - HTTPXAsyncTransport has built-in retry
            if all_images:
                try:
                    success = await self.context.client.add_gallery_images(
                        gallery_id=gallery.id,
                        image_ids=[img.id for img in all_images],
                    )
                    if success:
                        debug_print(
                            {
                                "method": "StashProcessing - _process_item_gallery",
                                "status": "gallery_images_added",
                                "item_id": item.id,
                                "gallery_id": gallery.id,
                                "image_count": len(all_images),
                            }
                        )
                    else:
                        logger.error(
                            f"Failed to add {len(all_images)} images to gallery {gallery.id}"
                        )
                        debug_print(
                            {
                                "method": "StashProcessing - _process_item_gallery",
                                "status": "gallery_images_add_failed",
                                "item_id": item.id,
                                "gallery_id": gallery.id,
                                "image_count": len(all_images),
                            }
                        )
                except Exception as e:
                    logger.exception(
                        f"Error adding images to gallery for {item_type} {item.id}: {e}"
                    )
                    debug_print(
                        {
                            "method": "StashProcessing - _process_item_gallery",
                            "status": "gallery_images_add_error",
                            "item_id": item.id,
                            "gallery_id": gallery.id,
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                        }
                    )
                    print_error(
                        f"Failed to add {len(all_images)} images to gallery {gallery.id}. "
                        f"Continuing with scenes..."
                    )

            # Link scenes using relationship helper
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
                await gallery.save(self.context.client)
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
