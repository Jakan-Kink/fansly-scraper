"""Media processing mixin.

This mixin handles processing of media objects into Stash. It includes:

1. Optimized regex-based scene lookup (single query vs batched ORs)
2. Batching by mimetype to separate image and scene processing
3. Django-style filtering with targeted regex patterns
4. Performance: 4-10x faster than nested OR conditions
5. Lazy iteration with find_iter for memory efficiency

Pattern 5 Migration Complete:
- Replaced 40-line _create_nested_path_or_conditions with Django-style regex
- Uses targeted regex: base_path.*(code1|code2|code3) for precision
- Leverages find_iter for lazy iteration (doesn't fetch all pages upfront)
- Dramatic code reduction and improved maintainability
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from stash_graphql_client.types import Image, ImageFile, Scene, Studio, VideoFile
from stash_graphql_client.types.base import is_set

from metadata import Account, AccountMediaBundle, Attachment, Media
from pathio import get_stash_path

from ...logging import debug_print
from ...logging import processing_logger as logger
from ..protocols import StashProcessingProtocol


class MediaProcessingMixin(StashProcessingProtocol):
    """Media processing functionality."""

    def _get_file_from_stash_obj(
        self,
        stash_obj: Scene | Image,
    ) -> ImageFile | VideoFile | None:
        """Get ImageFile or VideoFile from Scene or Image object.

        Args:
            stash_obj: Scene or Image object from Stash

        Returns:
            ImageFile or VideoFile object, or None if no files found
        """
        if isinstance(stash_obj, Image):
            return self._get_image_file_from_stash_obj(stash_obj)
        if isinstance(stash_obj, Scene):
            return self._get_video_file_from_stash_obj(stash_obj)
        return None

    def _get_image_file_from_stash_obj(
        self, stash_obj: Image
    ) -> ImageFile | VideoFile | None:
        """Extract ImageFile or VideoFile from Stash Image object.

        Args:
            stash_obj: Image object from Stash

        Returns:
            ImageFile or VideoFile object, or None if no valid file found.
            Note: Animated GIFs are stored as VideoFile in Stash, so Images
            can have either ImageFile or VideoFile in their visual_files.
        """
        if not stash_obj.visual_files:
            logger.debug("Image has no visual_files")
            return None

        # Library returns ImageFile/VideoFile objects directly (Pydantic auto-deserialization)
        file_data = stash_obj.visual_files[0]
        logger.debug(f"Found visual file: {file_data}")
        return file_data

    def _get_video_file_from_stash_obj(self, stash_obj: Scene) -> VideoFile | None:
        """Extract VideoFile from Stash Scene object.

        The library handles file deserialization automatically via Pydantic.
        No manual type checking needed - Pydantic ensures correct types.

        Args:
            stash_obj: Scene object from Stash

        Returns:
            VideoFile object or None if no files
        """
        if not stash_obj.files:
            logger.debug(f"Scene {stash_obj.id} has no files")
            return None

        # Library returns VideoFile objects directly (Pydantic auto-deserialization)
        file_data = stash_obj.files[0]
        logger.debug(f"Found VideoFile in scene {stash_obj.id}: {file_data}")
        return file_data

    def _create_targeted_regex_pattern(
        self,
        media_ids: Sequence[str],
    ) -> str:
        """Create targeted regex pattern for path filtering.

        Pattern 5: Replaces 40-line nested OR construction with simple regex.
        Uses base_path for precision: /creator/path/.*(code1|code2|code3)

        Args:
            media_ids: List of media IDs to search for

        Returns:
            Regex pattern string for Django-style path__regex filter
        """
        # Escape codes for regex safety
        escaped_codes = [re.escape(code) for code in media_ids]

        # Build targeted regex with base path if available
        if (
            hasattr(self, "state")
            and hasattr(self.state, "base_path")
            and self.state.base_path
        ):
            base_path_str = get_stash_path(self.state.base_path, self.config)
            # Match: /base/path/.*(code1|code2|code3)
            return f"{re.escape(base_path_str)}.*({'|'.join(escaped_codes)})"

        # Fallback: match any of the codes without path constraint
        return "|".join(escaped_codes)

    async def _find_stash_files_by_id(
        self,
        stash_files: list[
            tuple[str | int, str]
        ],  # List of (stash_id, mime_type) tuples
    ) -> list[tuple[dict, Scene | Image]]:
        """Find files in Stash by stash ID.

        Args:
            stash_files: List of (stash_id, mime_type) tuples to search for

        Returns:
            List of (raw stash object, processed file object) tuples
        """
        found = []

        # Group by mime type
        image_ids: list[str] = []
        scene_ids: list[str] = []
        image_id_map: dict[str, str] = {}  # stash_id -> mime_type mapping
        scene_id_map: dict[str, str] = {}  # stash_id -> mime_type mapping

        for stash_id, mime_type in stash_files:
            stash_id_str = str(stash_id)
            if mime_type and mime_type.startswith("image"):
                image_ids.append(stash_id_str)
                image_id_map[stash_id_str] = mime_type
            else:  # video or application -> scenes
                scene_ids.append(stash_id_str)
                scene_id_map[stash_id_str] = mime_type

        # Maximum batch size to prevent API overload
        max_batch_size = 20

        # Process images in batches
        if image_ids:
            # Split into batches
            image_id_batches = self._chunk_list(image_ids, max_batch_size)
            debug_print(
                {
                    "method": "StashProcessing - _find_stash_files_by_id",
                    "status": "finding_images_in_batches",
                    "total_images": len(image_ids),
                    "batch_count": len(image_id_batches),
                }
            )

            for batch_index, batch_ids in enumerate(image_id_batches):
                debug_print(
                    {
                        "method": "StashProcessing - _find_stash_files_by_id",
                        "status": "processing_image_batch",
                        "batch_index": batch_index + 1,
                        "batch_size": len(batch_ids),
                        "stash_ids": batch_ids,
                    }
                )

                try:
                    # Use get_many() for batch lookup — checks all cached IDs under
                    # a single lock acquisition, then fetches any misses
                    images = await self.store.get_many(Image, list(batch_ids))
                    found.extend(
                        (image, file)
                        for image in images
                        if (file := self._get_file_from_stash_obj(image))
                    )
                except Exception as e:
                    debug_print(
                        {
                            "method": "StashProcessing - _find_stash_files_by_id",
                            "status": "image_batch_find_failed",
                            "batch_index": batch_index + 1,
                            "error": str(e),
                        }
                    )

        # Process scenes in batches
        if scene_ids:
            # Split into batches
            scene_id_batches = self._chunk_list(scene_ids, max_batch_size)
            debug_print(
                {
                    "method": "StashProcessing - _find_stash_files_by_id",
                    "status": "finding_scenes_in_batches",
                    "total_scenes": len(scene_ids),
                    "batch_count": len(scene_id_batches),
                }
            )

            for batch_index, batch_ids in enumerate(scene_id_batches):
                debug_print(
                    {
                        "method": "StashProcessing - _find_stash_files_by_id",
                        "status": "processing_scene_batch",
                        "batch_index": batch_index + 1,
                        "batch_size": len(batch_ids),
                        "stash_ids": batch_ids,
                    }
                )

                try:
                    # Use get_many() for batch lookup — checks all cached IDs under
                    # a single lock acquisition, then fetches any misses
                    scenes = await self.store.get_many(Scene, list(batch_ids))
                    found.extend(
                        (scene, file)
                        for scene in scenes
                        if (file := self._get_file_from_stash_obj(scene))
                    )
                except Exception as e:
                    debug_print(
                        {
                            "method": "StashProcessing - _find_stash_files_by_id",
                            "status": "scene_batch_find_failed",
                            "batch_index": batch_index + 1,
                            "error": str(e),
                        }
                    )

        logger.debug(
            {
                "method": "StashProcessing - _find_stash_files_by_id",
                "status": "found_files",
                "found_count": len(found),
                "found_files": [f[0] for f in found],
            }
        )
        return found

    def _chunk_list(self, items: Sequence[str], chunk_size: int) -> list[Sequence[str]]:
        """Split a list into chunks of specified size.

        Args:
            items: List to split into chunks
            chunk_size: Maximum size of each chunk

        Returns:
            List of chunks
        """
        return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]

    def _match_files_by_regex(
        self,
        stash_obj: Scene | Image,
        pattern: re.Pattern,
    ) -> bool:
        """Check if any file path in a Stash object matches a compiled regex.

        Args:
            stash_obj: Scene or Image object
            pattern: Compiled regex pattern

        Returns:
            True if any file path matches
        """
        if isinstance(stash_obj, Image):
            files = stash_obj.visual_files or []
        elif isinstance(stash_obj, Scene):
            files = stash_obj.files or []
        else:
            return False
        return any(
            hasattr(f, "path") and f.path and pattern.search(f.path) for f in files
        )

    async def _find_stash_files_by_path(
        self,
        media_files: list[tuple[str, str]],  # List of (media_id, mime_type) tuples
    ) -> list[tuple[dict, Scene | Image]]:
        """Find files in Stash by media IDs in path, grouped by mime type.

        Primary path: O(1) dict lookup using media code indexes built during
        _preload_creator_media(). Falls back to regex GraphQL queries only when
        the index is empty (e.g., no preload ran, or in tests).

        Args:
            media_files: List of (media_id, mime_type) tuples to search for
        Returns:
            List of (raw stash object, processed file object) tuples
        """
        # Group media IDs by mime type
        image_ids: list[str] = []
        scene_ids: list[str] = []  # Both video and application use find_scenes
        for media_id, mime_type in media_files:
            if mime_type and mime_type.startswith("image"):
                image_ids.append(media_id)
            else:  # video or application -> scenes
                scene_ids.append(media_id)

        found = []

        # Primary path: O(1) index lookup (built during _preload_creator_media)
        if self._scene_code_index or self._image_code_index:
            if image_ids:
                images_by_code = self.find_images_by_media_codes(image_ids)
                found.extend(
                    (image, file)
                    for images in images_by_code.values()
                    for image in images
                    if (file := self._get_file_from_stash_obj(image))
                )

            if scene_ids:
                scenes_by_code = self.find_scenes_by_media_codes(scene_ids)
                found.extend(
                    (scene, file)
                    for scenes in scenes_by_code.values()
                    for scene in scenes
                    if (file := self._get_file_from_stash_obj(scene))
                )

            if found:
                logger.debug(
                    f"Index lookup found {len(found)} files for "
                    f"{len(image_ids)} images + {len(scene_ids)} scenes"
                )
            return found

        # Fallback: index is empty (no preload), use regex GraphQL search
        logger.debug("Media code index empty, falling back to regex search")

        # Maximum batch size to prevent SQL parser stack overflow
        max_batch_size = 20

        # Fallback: GraphQL path search via find_iter()
        # Process images in batches
        if image_ids:
            image_id_batches = self._chunk_list(image_ids, max_batch_size)
            for batch_index, batch_ids in enumerate(image_id_batches):
                regex_pattern = self._create_targeted_regex_pattern(batch_ids)
                try:
                    async for image in self.store.find_iter(
                        Image,
                        path__regex=regex_pattern,
                    ):
                        if file := self._get_file_from_stash_obj(image):
                            found.append((image, file))  # noqa: PERF401
                except Exception as e:
                    debug_print(
                        {
                            "method": "StashProcessing - _find_stash_files_by_path",
                            "status": "image_search_failed_for_batch",
                            "batch_index": batch_index + 1,
                            "error": str(e),
                        }
                    )

        # Process scenes using targeted regex with find_iter
        if scene_ids:
            regex_pattern = self._create_targeted_regex_pattern(scene_ids)
            try:
                async for scene in self.store.find_iter(
                    Scene,
                    path__regex=regex_pattern,
                ):
                    if file := self._get_file_from_stash_obj(scene):
                        found.append((scene, file))  # noqa: PERF401
            except Exception as e:
                logger.warning(
                    f"Regex scene search failed ({e}), falling back to batched approach"
                )
                scene_id_batches = self._chunk_list(scene_ids, max_batch_size)
                for batch_index, batch_ids in enumerate(scene_id_batches):
                    batch_regex = self._create_targeted_regex_pattern(batch_ids)
                    try:
                        async for scene in self.store.find_iter(
                            Scene,
                            path__regex=batch_regex,
                        ):
                            if file := self._get_file_from_stash_obj(scene):
                                found.append((scene, file))  # noqa: PERF401
                    except Exception as fallback_error:
                        logger.error(
                            f"Fallback batch {batch_index + 1} failed: {fallback_error}"
                        )

        logger.debug(
            {
                "method": "StashProcessing - _find_stash_files_by_path",
                "status": "found_files",
                "found_count": len(found),
                "found_files": [f[0] for f in found],
            }
        )
        return found

    async def _update_stash_metadata(
        self,
        stash_obj: Scene | Image,
        item: Any,  # Post or Message
        account: Account,
        media_id: str,
        is_preview: bool = False,
        studio: Studio | None = None,
    ) -> None:
        """Update metadata on Stash object using data we already have.

        Args:
            stash_obj: Scene or Image to update
            item: Post or Message containing metadata
            account: Account that created the content
            media_id: ID to use for code field
            is_preview: Whether this is a preview file
            studio: Pre-resolved studio (avoids repeated lookups per media item)
        """
        # Only update metadata if this is the earliest instance we've seen
        item_date = item.createdAt.date()  # Get date part of datetime
        current_date_str = getattr(stash_obj, "date", None)
        current_date = None  # Initialize to None, will be parsed from current_date_str if it exists
        is_organized = getattr(stash_obj, "organized", False)
        current_title = getattr(stash_obj, "title", None) or ""

        # Log full object details for debugging
        logger.debug(
            "\nFull stash object details:\n"
            f"Object Type: {stash_obj.__class__.__name__}\n"
            f"ID: {stash_obj.id}\n"
            f"Title: {current_title}\n"
            f"Date: {current_date_str}\n"
            f"Code: {getattr(stash_obj, 'code', None)}\n"
            f"Organized: {is_organized}\n"
            f"Item date: {item_date}\n"
            f"Item ID: {item.id}\n"
            f"Media ID: {media_id}\n"
        )

        # Check if title needs fixing (from old batch processing bug)
        has_bad_title = "Media from" in current_title

        if has_bad_title:
            logger.debug(
                {
                    "method": "StashProcessing - _update_stash_metadata",
                    "status": "forcing_update",
                    "reason": "bad_title_detected",
                    "current_title": current_title,
                    "media_id": media_id,
                }
            )
            # Force update by skipping all date/organized checks below

        elif is_organized:
            logger.debug(
                {
                    "method": "StashProcessing - _update_stash_metadata",
                    "status": "skipping_metadata",
                    "reason": "already_organized",
                    "media_id": media_id,
                    "item_id": item.id,
                    "stash_id": stash_obj.id,
                    "object_title": current_title,
                    "object_date": current_date_str,
                    "object_code": getattr(stash_obj, "code", None),
                }
            )
            return

        elif current_date_str:
            # Parse current date if we have one
            current_date = None
            try:
                current_date = datetime.strptime(current_date_str, "%Y-%m-%d").date()  # noqa: DTZ007
            except ValueError:
                logger.warning(
                    f"Invalid date format in stash object: {current_date_str}"
                )

            # If we have a valid current date and this item is from later, skip the update
            if current_date and item_date > current_date:
                debug_print(
                    {
                        "method": "StashProcessing - _update_stash_metadata",
                        "status": "skipping_metadata",
                        "reason": "later_date",
                        "current_date": current_date.isoformat(),
                        "new_date": item_date.isoformat(),
                        "media_id": media_id,
                    }
                )
                return

        # This is either the first instance or an earlier one - update the metadata
        username = account.username

        # Pattern 6: Only update fields we explicitly want to change
        # Use UNSET for fields we don't touch to preserve server values
        stash_obj.title = self._generate_title_from_content(
            content=item.content,
            username=username,
            created_at=item.createdAt,
        )
        stash_obj.details = item.content
        stash_obj.date = item_date.strftime("%Y-%m-%d")
        stash_obj.code = str(media_id)
        # organized field left as-is (UNSET if not loaded)
        debug_print(
            {
                "method": "StashProcessing - _update_stash_metadata",
                "status": "updating_metadata",
                "reason": "earlier_date",
                "current_date": current_date.isoformat() if current_date else None,
                "new_date": item_date.isoformat(),
                "media_id": media_id,
            }
        )

        # Add URL only for posts since message URLs won't work for other users
        if hasattr(item, "id") and item.__class__.__name__ == "Post":
            post_url = f"https://fansly.com/post/{item.id}"

            # Add URL to the urls list (singular url was removed in 0.11.0)
            if not is_set(stash_obj.urls) or not stash_obj.urls:
                stash_obj.urls = []
            if post_url not in stash_obj.urls:
                stash_obj.urls.append(post_url)

        # Add main performer — use cached self._performer for the primary creator
        # to avoid a redundant GraphQL lookup per media item
        if self._account and account.id == self._account.id and self._performer:
            main_performer = self._performer
        else:
            main_performer = await self._find_existing_performer(account)
        if main_performer:
            await stash_obj.add_performer(main_performer)

        # Add mentioned performers with simplified creation
        # Uses field name 'mentions' (not alias 'accountMentions') for Pydantic access
        mentions = getattr(item, "mentions", None)

        if mentions:
            for mention in mentions:
                # Use get_or_create for automatic conflict handling
                mention_performer = await self._get_or_create_performer(mention)
                if mention_performer:
                    # Update stash_id in database (skip for PostMention — no stash_id)
                    await self._update_account_stash_id(mention, mention_performer)

                    # Use relationship helper for bidirectional sync
                    await stash_obj.add_performer(mention_performer)

                    mention_name = getattr(mention, "username", None) or getattr(
                        mention, "handle", None
                    )
                    debug_print(
                        {
                            "method": "StashProcessing - _update_stash_metadata",
                            "status": "mention_performer_added",
                            "username": mention_name,
                            "stash_id": mention_performer.id,
                        }
                    )

        # Add studio (use pre-resolved studio, or resolve once if not provided)
        if studio is None:
            studio = self._studio
        if studio is None:
            studio = await self._find_existing_studio(account)
        if studio:
            # Scene has set_studio() helper, Image doesn't
            if hasattr(stash_obj, "set_studio"):
                stash_obj.set_studio(studio)
            else:
                stash_obj.studio = studio

        # Add hashtags as tags using relationship helper
        hashtags = getattr(item, "hashtags", None)
        if hashtags:
            tags = await self._process_hashtags_to_tags(hashtags)
            for tag in tags:
                await stash_obj.add_tag(tag)

        # Mark as preview if needed
        if is_preview:
            await self._add_preview_tag(stash_obj)

        logger.debug(
            f"Method: StashProcessing - _update_stash_metadata, "
            f"Status: update_metadata--before_save, "
            f"Object type: {stash_obj.__class__.__name__}, "
            f"Object ID: {stash_obj.id}"
        )

        # Save changes to Stash (save() handles dirty check internally)
        try:
            await self.store.save(stash_obj)
            logger.debug("Successfully saved changes to Stash")
        except Exception as e:
            logger.error(f"Error saving changes to Stash: {e}")
            debug_print(
                {
                    "method": "StashProcessing - _update_stash_metadata",
                    "status": "save_error",
                    "object_type": stash_obj.__class__.__name__,
                    "object_id": stash_obj.id,
                    "error": str(e),
                }
            )
            raise

    async def _process_media(
        self,
        media: Media,
        item: Any,
        account: Account,
        result: dict[str, list[Image | Scene]],
    ) -> None:
        """Process a media object and add its Stash objects to the result.

        Args:
            media: Media object to process
            item: Post or Message containing the media
            account: Account that created the content
            result: Dictionary to add results to
        """
        debug_print(
            {
                "method": "StashProcessing - _process_media",
                "status": "processing_media",
                "media_id": media.id,
                "stash_id": media.stash_id,
                "is_downloaded": media.is_downloaded,
                "variant_count": len(media.variants) if media.variants else 0,
                "variants": ([v.id for v in media.variants] if media.variants else []),
                "variant_details": (
                    [{"id": v.id, "mimetype": v.mimetype} for v in media.variants]
                    if media.variants
                    else []
                ),
            }
        )

        # Try to find in Stash and update metadata
        stash_result = None

        # First try by stash_id if available
        if media.stash_id:
            stash_result = await self._find_stash_files_by_id(
                [(media.stash_id, media.mimetype)]
            )
        else:
            # Collect all media IDs (original + variants)
            media_files = [(str(media.id), media.mimetype)]
            if media.variants:
                # Log variant relationships in detail
                debug_print(
                    {
                        "method": "StashProcessing - _process_media",
                        "status": "media_variant_details",
                        "media_id": str(media.id),
                        "media_mimetype": media.mimetype,
                        "variant_ids": [str(v.id) for v in media.variants],
                        "variant_mimetypes": [v.mimetype for v in media.variants],
                    }
                )
                media_files.extend((str(v.id), v.mimetype) for v in media.variants)

            debug_print(
                {
                    "method": "StashProcessing - _process_media",
                    "status": "searching_media_files",
                    "media_files": media_files,
                }
            )
            stash_result = await self._find_stash_files_by_path(media_files)

        # Update metadata and collect objects
        for stash_obj, _ in stash_result:
            await self._update_stash_metadata(
                stash_obj=stash_obj,
                item=item,
                account=account,
                media_id=str(media.id),
            )
            if isinstance(stash_obj, Image):
                result["images"].append(stash_obj)
            elif isinstance(stash_obj, Scene):
                result["scenes"].append(stash_obj)

    async def _process_bundle_media(
        self,
        bundle: AccountMediaBundle,
        item: Any,
        account: Account,
        result: dict[str, list[Image | Scene]],
    ) -> None:
        """Process a media bundle and add its Stash objects to the result.

        Args:
            bundle: AccountMediaBundle to process
            item: Post or Message containing the bundle
            account: Account that created the content
            result: Dictionary to add results to
        """
        debug_print(
            {
                "method": "StashProcessing - _process_bundle_media",
                "status": "processing_bundle",
                "bundle_id": bundle.id,
                "media_count": (len(bundle.accountMedia) if bundle.accountMedia else 0),
            }
        )

        # Collect media for batch processing
        media_batch = []

        # Collect media from bundle for batch processing
        for account_media in bundle.accountMedia:
            if account_media.media:
                media_batch.append(account_media.media)
            if account_media.preview:
                media_batch.append(account_media.preview)

        # Add bundle preview if any
        if bundle.preview:
            media_batch.append(bundle.preview)

        # Process the collected media batch if any
        if media_batch:
            debug_print(
                {
                    "method": "StashProcessing - _process_bundle_media",
                    "status": "processing_media_batch",
                    "bundle_id": bundle.id,
                    "media_count": len(media_batch),
                }
            )

            # Process media in batches by mimetype using the account passed to the method
            batch_result = await self._process_media_batch_by_mimetype(
                media_list=media_batch, item=item, account=account
            )

            # Add batch results to the overall result
            result["images"].extend(batch_result["images"])
            result["scenes"].extend(batch_result["scenes"])

    async def process_creator_attachment(
        self,
        attachment: Attachment,
        item: Any,
        account: Account,
    ) -> dict[str, list[Image | Scene]]:
        """Process attachment into Image and Scene objects.

        Args:
            attachment: Attachment object to process
            session: Optional database session to use
            item: Post or Message containing the attachment
            account: Account that created the content

        Returns:
            Dictionary containing lists of Image and Scene objects:
            {
                "images": list[Image],
                "scenes": list[Scene]
            }
        """
        result = {"images": [], "scenes": []}

        # Handle direct media
        debug_print(
            {
                "method": "StashProcessing - process_creator_attachment",
                "status": "checking_media",
                "attachment_id": attachment.id,
                "has_media": bool(attachment.media),
                "has_media_media": (
                    bool(attachment.media.media) if attachment.media else False
                ),
                "media_type": (
                    type(attachment.media).__name__ if attachment.media else None
                ),
            }
        )

        # Collect media for batch processing
        media_batch = []

        # Add direct media and preview to batch
        if attachment.media:
            if attachment.media.media:
                media_batch.append(attachment.media.media)
            if attachment.media.preview:
                media_batch.append(attachment.media.preview)

        # Handle media bundles
        debug_print(
            {
                "method": "StashProcessing - process_creator_attachment",
                "status": "checking_bundle",
                "attachment_id": attachment.id,
                "has_bundle": bool(attachment.bundle),
            }
        )

        if attachment.bundle:
            if attachment.bundle.accountMedia:
                for account_media in attachment.bundle.accountMedia:
                    if account_media.media:
                        media_batch.append(account_media.media)
                    if account_media.preview:
                        media_batch.append(account_media.preview)

            if attachment.bundle.preview:
                media_batch.append(attachment.bundle.preview)

        # Handle aggregated posts
        debug_print(
            {
                "method": "StashProcessing - process_creator_attachment",
                "status": "checking_aggregated",
                "attachment_id": attachment.id,
                "is_aggregated_post": getattr(attachment, "is_aggregated_post", False),
            }
        )

        if (
            getattr(attachment, "is_aggregated_post", False)
            and attachment.aggregated_post
        ):
            agg_post = attachment.aggregated_post

            debug_print(
                {
                    "method": "StashProcessing - process_creator_attachment",
                    "status": "processing_aggregated",
                    "attachment_id": attachment.id,
                    "post_id": agg_post.id,
                    "attachment_count": (
                        len(agg_post.attachments) if agg_post.attachments else 0
                    ),
                }
            )

            # Process each attachment if any
            if agg_post.attachments:
                for agg_attachment in agg_post.attachments:
                    # Recursively process attachments from aggregated post
                    agg_result = await self.process_creator_attachment(
                        attachment=agg_attachment,
                        item=agg_post,
                        account=account,
                    )
                    result["images"].extend(agg_result["images"])
                    result["scenes"].extend(agg_result["scenes"])

        # Process the collected media batch if any
        if media_batch:
            debug_print(
                {
                    "method": "StashProcessing - process_creator_attachment",
                    "status": "processing_media_batch",
                    "attachment_id": attachment.id,
                    "media_count": len(media_batch),
                }
            )

            # Process media in batches by mimetype using the account passed to the method
            batch_result = await self._process_media_batch_by_mimetype(
                media_list=media_batch, item=item, account=account
            )

            # Add batch results to the overall result
            result["images"].extend(batch_result["images"])
            result["scenes"].extend(batch_result["scenes"])

        return result

    async def _process_media_batch_by_mimetype(
        self,
        media_list: Sequence[Media],
        item: Any,
        account: Account,
    ) -> dict[str, list[Image | Scene]]:
        """Process a batch of media objects grouped by mimetype.

        This optimized method processes multiple media objects in batches grouped
        by mimetype to reduce API calls to Stash. Since StashProcessing is designed
        to work with a single account/creator at a time, all media is processed using
        the provided account object.

        Args:
            media_list: List of Media objects to process
            item: Post or Message containing the media
            account: Account that created the content (the creator being processed)

        Returns:
            Dictionary containing lists of Image and Scene objects
        """
        result = {"images": [], "scenes": []}
        if not media_list:
            return result

        # Maximum batch size to avoid SQL parser overflow
        max_batch_size = 20

        # Split into batches of max_batch_size for processing
        if len(media_list) > max_batch_size:
            # Process in batches
            batched_results = {"images": [], "scenes": []}
            media_batches = self._chunk_list(media_list, max_batch_size)

            debug_print(
                {
                    "method": "StashProcessing - _process_media_batch_by_mimetype",
                    "status": "splitting_into_batches",
                    "total_media": len(media_list),
                    "batch_count": len(media_batches),
                    "batch_size": max_batch_size,
                }
            )

            # Process each batch
            for batch_index, batch in enumerate(media_batches):
                debug_print(
                    {
                        "method": "StashProcessing - _process_media_batch_by_mimetype",
                        "status": "processing_batch",
                        "batch_index": batch_index + 1,
                        "batch_size": len(batch),
                    }
                )

                # Process this batch
                batch_result = await self._process_batch_internal(
                    media_list=batch,
                    item=item,
                    account=account,
                )

                # Add results to overall results
                batched_results["images"].extend(batch_result["images"])
                batched_results["scenes"].extend(batch_result["scenes"])

            # Return combined results from all batches
            return batched_results

        # Process as a single batch if under max_batch_size
        return await self._process_batch_internal(media_list, item, account)

    async def _process_batch_internal(
        self,
        media_list: list[Media],
        item: Any,
        account: Account,
    ) -> dict[str, list[Image | Scene]]:
        """Process a small batch of media objects internally.

        This internal method handles the actual processing of media objects.
        It's designed to work with batches small enough to avoid SQL parser overflows.

        Args:
            media_list: List of Media objects to process (should be limited in size)
            item: Post or Message containing the media
            account: Account that created the content

        Returns:
            Dictionary containing lists of Image and Scene objects
        """
        result = {"images": [], "scenes": []}
        if not media_list:
            return result

        # Use cached studio from self (set in continue_stash_processing),
        # fall back to lookup for direct callers (tests, standalone use)
        studio = self._studio
        if studio is None:
            studio = await self._find_existing_studio(account)

        # Group media by stash_id vs path-based lookup
        stash_id_media = []
        path_media = []

        # First pass: separate media with stash_id from those without
        for media in media_list:
            if media.stash_id:
                stash_id_media.append((media.stash_id, media.mimetype, media.id))
            else:
                # Start with the media itself
                path_media.append((str(media.id), media.mimetype, media.id))
                # Add variants
                if media.variants:
                    path_media.extend(
                        (str(variant.id), variant.mimetype, media.id)
                        for variant in media.variants
                    )

        debug_print(
            {
                "method": "StashProcessing - _process_media_batch_by_mimetype",
                "status": "batch_processing",
                "stash_id_count": len(stash_id_media),
                "path_media_count": len(path_media),
                "total_media": len(media_list),
            }
        )

        # Process stash_id batch
        if stash_id_media:
            # Convert to format expected by _find_stash_files_by_id
            lookup_data = [
                (stash_id, mimetype) for stash_id, mimetype, _ in stash_id_media
            ]
            # Use str keys since GraphQL returns string IDs
            stash_id_map = {
                str(stash_id): media_id for stash_id, _, media_id in stash_id_media
            }

            debug_print(
                {
                    "method": "StashProcessing - _process_media_batch_by_mimetype",
                    "status": "processing_stash_id_batch",
                    "lookup_count": len(lookup_data),
                }
            )

            stash_results = await self._find_stash_files_by_id(lookup_data)

            # Process results and update metadata
            for stash_obj, _ in stash_results:
                # Find the original media_id that corresponds to this stash object
                media_id = stash_id_map.get(stash_obj.id)
                if media_id:
                    await self._update_stash_metadata(
                        stash_obj=stash_obj,
                        item=item,
                        account=account,
                        media_id=str(media_id),
                        studio=studio,
                    )

                    # Add to appropriate result list
                    if isinstance(stash_obj, Image):
                        result["images"].append(stash_obj)
                    elif isinstance(stash_obj, Scene):
                        result["scenes"].append(stash_obj)

        # Process path-based batch
        if path_media:
            # Group by mimetype for more efficient lookup
            image_media = []
            scene_media = []

            for path, mimetype, media_id in path_media:
                if mimetype and mimetype.startswith("image"):
                    image_media.append((path, mimetype, media_id))
                else:  # video, application, etc. go to scenes
                    scene_media.append((path, mimetype, media_id))

            # Process images batch
            if image_media:
                debug_print(
                    {
                        "method": "StashProcessing - _process_media_batch_by_mimetype",
                        "status": "processing_image_batch",
                        "count": len(image_media),
                    }
                )

                # Prepare path filter with all image paths
                lookup_data = [(path, mimetype) for path, mimetype, _ in image_media]
                media_id_map = {path: media_id for path, _, media_id in image_media}

                stash_results = await self._find_stash_files_by_path(lookup_data)

                # Find corresponding media_id for each result
                for stash_obj, file_obj in stash_results:
                    # Look for media_id in path
                    for path, media_id in media_id_map.items():
                        if (
                            path in file_obj.path
                        ):  # Check if media_id is in the file path
                            await self._update_stash_metadata(
                                stash_obj=stash_obj,
                                item=item,
                                account=account,
                                media_id=str(media_id),
                                studio=studio,
                            )

                            if isinstance(stash_obj, Image):
                                result["images"].append(stash_obj)
                                break

            # Process scenes batch
            if scene_media:
                debug_print(
                    {
                        "method": "StashProcessing - _process_media_batch_by_mimetype",
                        "status": "processing_scene_batch",
                        "count": len(scene_media),
                    }
                )

                # Prepare path filter with all scene paths
                lookup_data = [(path, mimetype) for path, mimetype, _ in scene_media]
                media_id_map = {path: media_id for path, _, media_id in scene_media}

                stash_results = await self._find_stash_files_by_path(lookup_data)

                # Find corresponding media_id for each result
                for stash_obj, file_obj in stash_results:
                    # Look for media_id in path
                    for path, media_id in media_id_map.items():
                        if (
                            path in file_obj.path
                        ):  # Check if media_id is in the file path
                            await self._update_stash_metadata(
                                stash_obj=stash_obj,
                                item=item,
                                account=account,
                                media_id=str(media_id),
                                studio=studio,
                            )

                            if isinstance(stash_obj, Scene):
                                result["scenes"].append(stash_obj)
                                break

        # Log completion summary
        debug_print(
            {
                "method": "StashProcessing - _process_media_batch_by_mimetype",
                "status": "batch_processing_complete",
                "images_found": len(result["images"]),
                "scenes_found": len(result["scenes"]),
                "total_media_processed": len(media_list),
            }
        )

        return result
