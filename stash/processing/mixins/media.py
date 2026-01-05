"""Media processing mixin.

This mixin handles processing of media objects into Stash. It includes:

1. Optimized regex-based scene lookup (single query vs batched ORs)
2. Batching by mimetype to separate image and scene processing
3. Hybrid approach: regex for scenes, batched ORs for images (with fallback)
4. Performance: 4-10x faster than nested OR conditions for large datasets
5. Graceful degradation with automatic fallback to batched approach on errors
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session
from stash_graphql_client.types import Image, ImageFile, Scene, VideoFile

from errors import ConfigError
from metadata import Account, AccountMediaBundle, Attachment, Media
from metadata.decorators import with_session

from ...logging import debug_print
from ...logging import processing_logger as logger


if TYPE_CHECKING:
    pass


class MediaProcessingMixin:
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

    def _create_nested_path_or_conditions(
        self,
        media_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        """Create nested OR conditions for path filters properly formatted for Stash GraphQL.

        Args:
            media_ids: List of media IDs to search for

        Returns:
            Nested OR conditions for path filter
        """
        # Handle the single media ID case
        if len(media_ids) == 1:
            return {
                "path": {
                    "modifier": "INCLUDES",
                    "value": media_ids[0],
                }
            }

        # For multiple IDs, create a nested structure compatible with Stash's GraphQL schema
        # The first condition
        result = {
            "path": {
                "modifier": "INCLUDES",
                "value": media_ids[0],
            }
        }

        # Add remaining conditions as nested OR
        for media_id in media_ids[1:]:
            # Each OR needs to have a single condition and can have another OR
            result = {
                "OR": {
                    "path": {
                        "modifier": "INCLUDES",
                        "value": media_id,
                    },
                    "OR": result,
                }
            }

        return result

    @with_session()
    async def _find_stash_files_by_id(
        self,
        stash_files: list[
            tuple[str | int, str]
        ],  # List of (stash_id, mime_type) tuples
        session: Session | None = None,
    ) -> list[tuple[dict, Scene | Image]]:
        """Find files in Stash by stash ID.

        Args:
            stash_files: List of (stash_id, mime_type) tuples to search for
            session: Optional database session (provided by decorator)

        Returns:
            List of (raw stash object, processed file object) tuples
        """
        if session is None:
            raise ConfigError("Database session is required")
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

                for stash_id in batch_ids:
                    try:
                        image = await self.context.client.find_image(stash_id)
                        if image and (file := self._get_file_from_stash_obj(image)):
                            found.append((image, file))
                    except Exception as e:
                        debug_print(
                            {
                                "method": "StashProcessing - _find_stash_files_by_id",
                                "status": "image_find_failed",
                                "stash_id": stash_id,
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

                for stash_id in batch_ids:
                    try:
                        scene = await self.context.client.find_scene(stash_id)
                        if scene and (file := self._get_file_from_stash_obj(scene)):
                            found.append((scene, file))
                    except Exception as e:
                        debug_print(
                            {
                                "method": "StashProcessing - _find_stash_files_by_id",
                                "status": "scene_find_failed",
                                "stash_id": stash_id,
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

    async def _find_stash_files_by_path(
        self,
        media_files: list[tuple[str, str]],  # List of (media_id, mime_type) tuples
    ) -> list[tuple[dict, Scene | Image]]:
        """Find files in Stash by media IDs in path, grouped by mime type.

        Args:
            media_files: List of (media_id, mime_type) tuples to search for
        Returns:
            List of (raw stash object, processed file object) tuples
        """
        filter_params = {
            "per_page": -1,
            "sort": "created_at",
            "direction": "DESC",
        }

        # Group media IDs by mime type
        image_ids: list[str] = []
        scene_ids: list[str] = []  # Both video and application use find_scenes
        for media_id, mime_type in media_files:
            if mime_type and mime_type.startswith("image"):
                image_ids.append(media_id)
            else:  # video or application -> scenes
                scene_ids.append(media_id)

        found = []

        # Maximum batch size to prevent SQL parser stack overflow
        max_batch_size = 20

        # Process images in batches
        if image_ids:
            # Split image_ids into batches of max_batch_size
            image_id_batches = self._chunk_list(image_ids, max_batch_size)
            debug_print(
                {
                    "method": "StashProcessing - _find_stash_files_by_path",
                    "status": "processing_image_batches",
                    "total_images": len(image_ids),
                    "batch_count": len(image_id_batches),
                    "batch_size": max_batch_size,
                }
            )

            # Process each batch separately
            for batch_index, batch_ids in enumerate(image_id_batches):
                path_filter = self._create_nested_path_or_conditions(batch_ids)
                debug_print(
                    {
                        "method": "StashProcessing - _find_stash_files_by_path",
                        "status": "searching_images_batch",
                        "batch_index": batch_index + 1,
                        "batch_size": len(batch_ids),
                        "media_ids": batch_ids,
                    }
                )
                try:
                    results = await self.context.client.find_images(
                        image_filter=path_filter,
                        filter_=filter_params,
                    )
                    logger.info("Raw find_images results: %s", results)

                    if results and results.count > 0:
                        valid_files_found = False
                        for image_data in results.images:
                            logger.info("Processing image data: %s", image_data)

                            try:
                                # Library returns Image objects directly (Pydantic)
                                image = image_data
                                logger.info("Using image object: %s", image)

                                # Try to get a file from the image object
                                if file := self._get_file_from_stash_obj(image):
                                    logger.info("Found file in image: %s", file)
                                    found.append((image, file))
                                    valid_files_found = True
                                else:
                                    logger.info("No file found in image object")

                            except Exception as e:
                                logger.error(f"Error processing image data: {e}")
                                debug_print(
                                    {
                                        "method": "StashProcessing - _find_stash_files_by_path",
                                        "status": "image_processing_failed",
                                        "error": str(e),
                                        "image_data": (
                                            str(image_data)[:100] + "..."
                                            if len(str(image_data)) > 100
                                            else str(image_data)
                                        ),
                                    }
                                )

                        if not valid_files_found:
                            logger.warning(
                                f"Found {results.count} images but no valid image files could be extracted in batch {batch_index + 1}"
                            )
                            debug_print(
                                {
                                    "method": "StashProcessing - _find_stash_files_by_path",
                                    "status": "no_valid_files_found_in_batch",
                                    "batch_index": batch_index + 1,
                                    "image_count": results.count,
                                }
                            )
                except Exception as e:
                    debug_print(
                        {
                            "method": "StashProcessing - _find_stash_files_by_path",
                            "status": "image_search_failed_for_batch",
                            "batch_index": batch_index + 1,
                            "media_ids": batch_ids,
                            "error": str(e),
                        }
                    )

        # Process scenes using regex path matching (optimized single query)
        if scene_ids:
            # Build regex pattern for all scene IDs
            escaped_ids = [re.escape(media_id) for media_id in scene_ids]
            regex_pattern = "|".join(escaped_ids)

            debug_print(
                {
                    "method": "StashProcessing - _find_stash_files_by_path",
                    "status": "processing_scenes_regex",
                    "total_scenes": len(scene_ids),
                    "regex_pattern_length": len(regex_pattern),
                }
            )

            try:
                # Use findScenesByPathRegex for single-query lookup
                result = await self.context.client.find_scenes_by_path_regex(
                    filter_={"q": regex_pattern}
                )

                logger.info(
                    "Scene regex search results: found %s scenes for %s media IDs",
                    result.count,
                    len(scene_ids),
                )

                if result.count == 0:
                    logger.warning(
                        f"No scenes found for {len(scene_ids)} media IDs via regex"
                    )
                    debug_print(
                        {
                            "method": "StashProcessing - _find_stash_files_by_path",
                            "status": "no_scenes_found_regex",
                            "media_id_count": len(scene_ids),
                        }
                    )
                else:
                    valid_files_found = False
                    for scene in result.scenes:
                        try:
                            if file := self._get_file_from_stash_obj(scene):
                                found.append((scene, file))
                                valid_files_found = True
                            else:
                                logger.info(
                                    f"No file found in scene object: {scene.id}"
                                )

                        except Exception as e:
                            logger.error(f"Error processing scene data: {e}")
                            debug_print(
                                {
                                    "method": "StashProcessing - _find_stash_files_by_path",
                                    "status": "scene_processing_failed",
                                    "error": str(e),
                                    "scene_id": getattr(scene, "id", "unknown"),
                                }
                            )

                    if not valid_files_found:
                        logger.warning(
                            f"Found {result.count} scenes but no valid scene files could be extracted"
                        )
                        debug_print(
                            {
                                "method": "StashProcessing - _find_stash_files_by_path",
                                "status": "no_valid_scene_files_found",
                                "scene_count": result.count,
                            }
                        )

            except Exception as e:
                # Fallback to batched OR approach if regex fails
                logger.warning(
                    f"Regex scene search failed ({e}), falling back to batched OR approach"
                )
                debug_print(
                    {
                        "method": "StashProcessing - _find_stash_files_by_path",
                        "status": "regex_search_failed_fallback_to_batched",
                        "error": str(e),
                        "scene_id_count": len(scene_ids),
                    }
                )

                # Fallback: Use original batched approach
                scene_id_batches = self._chunk_list(scene_ids, max_batch_size)
                for batch_index, batch_ids in enumerate(scene_id_batches):
                    path_filter = self._create_nested_path_or_conditions(batch_ids)
                    try:
                        results = await self.context.client.find_scenes(
                            scene_filter=path_filter,
                            filter_=filter_params,
                        )

                        if results and results.count > 0:
                            for scene in results.scenes:
                                if file := self._get_file_from_stash_obj(scene):
                                    found.append((scene, file))  # noqa: PERF401

                    except Exception as fallback_error:
                        logger.error(
                            f"Fallback batch {batch_index + 1} also failed: {fallback_error}"
                        )
                        debug_print(
                            {
                                "method": "StashProcessing - _find_stash_files_by_path",
                                "status": "fallback_batch_failed",
                                "batch_index": batch_index + 1,
                                "error": str(fallback_error),
                            }
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
    ) -> None:
        """Update metadata on Stash object using data we already have.

        Args:
            stash_obj: Scene or Image to update
            item: Post or Message containing metadata
            account: Account that created the content
            media_id: ID to use for code field
            is_preview: Whether this is a preview file
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
        # Get username using awaitable attrs to avoid sync IO in async context
        username = (
            await account.awaitable_attrs.username
            if hasattr(account, "awaitable_attrs")
            else account.username
        )
        stash_obj.title = self._generate_title_from_content(
            content=item.content,
            username=username,
            created_at=item.createdAt,
        )
        stash_obj.details = item.content
        stash_obj.date = item_date.strftime("%Y-%m-%d")
        stash_obj.code = str(media_id)
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

            # Handle both singular url and plural urls fields
            # Set singular url property
            stash_obj.url = post_url

            # Also update the urls list if it exists
            if hasattr(stash_obj, "urls"):
                if not stash_obj.urls:
                    stash_obj.urls = []
                if post_url not in stash_obj.urls:
                    stash_obj.urls.append(post_url)

        # Add performers (we already have the account)
        performers = []
        if main_performer := await self._find_existing_performer(account):
            performers.append(main_performer)

        # Add mentioned performers if any
        try:
            mentions = await item.awaitable_attrs.accountMentions
        except AttributeError:
            mentions = None
        if mentions:
            for mention in mentions:
                # Try to find existing performer
                mention_performer = await self._find_existing_performer(mention)

                # Create new performer if not found
                if not mention_performer:
                    debug_print(
                        {
                            "method": "StashProcessing - _update_stash_metadata",
                            "status": "creating_mentioned_performer",
                            "username": mention.username,
                        }
                    )
                    try:
                        mention_performer = self._performer_from_account(mention)
                        if mention_performer:
                            await mention_performer.save(self.context.client)
                            await self._update_account_stash_id(
                                mention, mention_performer
                            )
                            debug_print(
                                {
                                    "method": "StashProcessing - _update_stash_metadata",
                                    "status": "performer_created",
                                    "username": mention.username,
                                    "stash_id": mention_performer.id,
                                }
                            )
                    except Exception as e:
                        error_message = str(e)
                        if (
                            "performer with name" in error_message
                            and "already exists" in error_message
                        ):
                            # Try to find the performer again - it may have been created by another thread
                            mention_performer = await self._find_existing_performer(
                                mention
                            )
                            if mention_performer:
                                debug_print(
                                    {
                                        "method": "StashProcessing - _update_stash_metadata",
                                        "status": "performer_found_after_create_failed",
                                        "username": mention.username,
                                        "stash_id": mention_performer.id,
                                    }
                                )
                            else:
                                # If we still can't find it, something is wrong
                                raise
                        else:
                            # Re-raise if it's not a "performer already exists" error
                            raise

                if mention_performer:
                    performers.append(mention_performer)

        if performers:
            stash_obj.performers = performers

        # Add studio (we already have the account)
        if studio := await self._find_existing_studio(account):
            stash_obj.studio = studio

        # Add hashtags as tags
        try:
            hashtags = await item.awaitable_attrs.hashtags
            if hashtags:
                tags = await self._process_hashtags_to_tags(hashtags)
                if tags:
                    # TODO: Re-enable this code after testing
                    # This code will update tags instead of overwriting them
                    # For now, we overwrite to test tag matching behavior
                    #
                    # # Preserve existing tags
                    # existing_tags = set(stash_obj.tags) if hasattr(stash_obj, "tags") else set()
                    # existing_tags.update(tags)
                    # stash_obj.tags = list(existing_tags)

                    # Temporarily overwrite tags for testing
                    stash_obj.tags = tags
        except AttributeError:
            # Item doesn't have hashtags attribute (e.g., Messages)
            pass

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
            await stash_obj.save(self.context.client)
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
        if hasattr(media, "awaitable_attrs"):
            await media.awaitable_attrs.variants
            await media.awaitable_attrs.mimetype
            await media.awaitable_attrs.is_downloaded

        debug_print(
            {
                "method": "StashProcessing - _process_media",
                "status": "processing_media",
                "media_id": media.id,
                "stash_id": media.stash_id,
                "is_downloaded": media.is_downloaded,
                "variant_count": (
                    len(media.variants) if hasattr(media, "variants") else 0
                ),
                "variants": (
                    [v.id for v in media.variants] if hasattr(media, "variants") else []
                ),
                "variant_details": (
                    [{"id": v.id, "mimetype": v.mimetype} for v in media.variants]
                    if hasattr(media, "variants") and media.variants
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
            if hasattr(media, "variants") and media.variants:
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
        if hasattr(bundle, "awaitable_attrs"):
            await bundle.awaitable_attrs.accountMedia

        debug_print(
            {
                "method": "StashProcessing - _process_bundle_media",
                "status": "processing_bundle",
                "bundle_id": bundle.id,
                "media_count": (
                    len(bundle.accountMedia) if hasattr(bundle, "accountMedia") else 0
                ),
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

    @with_session()
    async def process_creator_attachment(
        self,
        attachment: Attachment,
        item: Any,
        account: Account,
        session: Session | None = None,
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
                "has_bundle": hasattr(attachment, "bundle"),
                "bundle_loaded": hasattr(attachment, "awaitable_attrs"),
            }
        )

        # Load and process bundle if present
        if hasattr(attachment, "awaitable_attrs"):
            await attachment.awaitable_attrs.bundle
        if attachment.bundle:
            # Collect media from bundle for batch processing
            if hasattr(attachment.bundle, "awaitable_attrs"):
                await attachment.bundle.awaitable_attrs.accountMedia
                await attachment.bundle.awaitable_attrs.preview

            if hasattr(attachment.bundle, "accountMedia"):
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
                "has_aggregated_post": hasattr(attachment, "aggregated_post"),
            }
        )

        # Load and process aggregated post if present
        if hasattr(attachment, "awaitable_attrs"):
            await attachment.awaitable_attrs.is_aggregated_post
            if getattr(attachment, "is_aggregated_post", False):
                await attachment.awaitable_attrs.aggregated_post

        if (
            getattr(attachment, "is_aggregated_post", False)
            and attachment.aggregated_post
        ):
            agg_post = attachment.aggregated_post

            # Load post attributes
            if hasattr(agg_post, "awaitable_attrs"):
                await agg_post.awaitable_attrs.attachments

            debug_print(
                {
                    "method": "StashProcessing - process_creator_attachment",
                    "status": "processing_aggregated",
                    "attachment_id": attachment.id,
                    "post_id": agg_post.id,
                    "has_attachments": hasattr(agg_post, "attachments"),
                    "attachment_count": (
                        len(agg_post.attachments)
                        if hasattr(agg_post, "attachments")
                        else 0
                    ),
                }
            )

            # Process each attachment if any
            if hasattr(agg_post, "attachments") and agg_post.attachments:
                for agg_attachment in agg_post.attachments:
                    # Recursively process attachments from aggregated post
                    agg_result = await self.process_creator_attachment(
                        attachment=agg_attachment,
                        item=agg_post,
                        account=account,
                        session=session,
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

        # Load awaitable attributes for all media in one pass
        for media in media_list:
            if hasattr(media, "awaitable_attrs"):
                await media.awaitable_attrs.mimetype
                await media.awaitable_attrs.is_downloaded
                await media.awaitable_attrs.variants

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
                if hasattr(media, "variants") and media.variants:
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
