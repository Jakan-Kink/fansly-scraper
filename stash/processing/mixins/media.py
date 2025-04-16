"""Media processing mixin."""

from __future__ import annotations

import asyncio
import contextlib
import traceback
from typing import TYPE_CHECKING, Any, Dict, List, Tuple

from sqlalchemy.orm import Session

from metadata import Account, AccountMedia, AccountMediaBundle, Attachment, Media
from metadata.decorators import with_session
from textio import print_error

from ...logging import debug_print
from ...logging import processing_logger as logger
from ...types import Image, ImageFile, Scene, Tag, VideoFile

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


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
            # Get the primary ImageFile
            logger.debug(f"Getting file from Image object: {stash_obj}")
            if stash_obj.visual_files:
                logger.debug(f"Image has {len(stash_obj.visual_files)} visual files")
                for file_data in stash_obj.visual_files:
                    logger.debug(f"Checking visual file: {file_data}")
                    # Convert dict to ImageFile if needed
                    if isinstance(file_data, dict):
                        # Ensure fingerprints exists
                        if "fingerprints" not in file_data:
                            file_data["fingerprints"] = []
                        # Ensure mod_time exists
                        if "mod_time" not in file_data:
                            file_data["mod_time"] = None
                        file = ImageFile(**file_data)
                    else:
                        file = file_data
                    logger.debug(f"Converted to file object: {file}")
                    if isinstance(file, ImageFile):
                        logger.debug(f"Found ImageFile: {file}")
                        return file
            else:
                logger.debug("Image has no visual_files")
        elif isinstance(stash_obj, Scene):
            # Get the primary VideoFile
            if stash_obj.files:
                return stash_obj.files[0]
        return None

    def _create_nested_path_or_conditions(
        self,
        media_ids: list[str],
    ) -> dict[str, dict]:
        """Create nested OR conditions for path filters.

        Args:
            media_ids: List of media IDs to search for

        Returns:
            Nested OR conditions for path filter
        """
        # Create individual path conditions
        conditions = [
            {
                "path": {
                    "modifier": "INCLUDES",
                    "value": media_id,
                }
            }
            for media_id in media_ids
        ]

        if len(conditions) == 1:
            return conditions[0]

        # Build nested OR conditions from right to left
        result = conditions[-1]  # Start with the last condition
        for condition in reversed(
            conditions[:-1]
        ):  # Process remaining conditions right to left
            result = {
                "OR": {
                    "path": condition["path"],
                    "OR": result,
                }
            }
        return result

    async def _find_stash_files_by_id(
        self,
        stash_files: list[tuple[str, str]],  # List of (stash_id, mime_type) tuples
    ) -> list[tuple[dict, Scene | Image]]:
        """Find files in Stash by stash ID.

        Args:
            stash_files: List of (stash_id, mime_type) tuples to search for

        Returns:
            List of (raw stash object, processed file object) tuples
        """
        found = []

        # Group by mime type
        image_ids = []
        scene_ids = []
        for stash_id, mime_type in stash_files:
            if mime_type.startswith("image"):
                image_ids.append(stash_id)
            else:  # video or application -> scenes
                scene_ids.append(stash_id)

        # Find images
        if image_ids:
            debug_print(
                {
                    "method": "StashProcessing - _find_stash_files_by_id",
                    "status": "finding_images",
                    "stash_ids": image_ids,
                }
            )
            for stash_id in image_ids:
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

        # Find scenes
        if scene_ids:
            debug_print(
                {
                    "method": "StashProcessing - _find_stash_files_by_id",
                    "status": "finding_scenes",
                    "stash_ids": scene_ids,
                }
            )
            for stash_id in scene_ids:
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
        image_ids = []
        scene_ids = []  # Both video and application use find_scenes
        for media_id, mime_type in media_files:
            if mime_type.startswith("image"):
                image_ids.append(media_id)
            else:  # video or application -> scenes
                scene_ids.append(media_id)

        found = []

        # Find images
        if image_ids:
            path_filter = self._create_nested_path_or_conditions(image_ids)
            debug_print(
                {
                    "method": "StashProcessing - _find_stash_files_by_path",
                    "status": "searching_images",
                    "media_ids": image_ids,
                    "filter": path_filter,
                }
            )
            try:
                results = await self.context.client.find_images(
                    image_filter=path_filter,
                    filter_=filter_params,
                )
                logger.info(f"Raw find_images results: {results}")
                if results.count > 0:
                    for image_data in results.images:
                        logger.info(f"Processing image data: {image_data}")
                        image = (
                            Image(**image_data)
                            if isinstance(image_data, dict)
                            else image_data
                        )
                        logger.info(f"Created image object: {image}")
                        if file := self._get_file_from_stash_obj(image):
                            logger.info(f"Found file in image: {file}")
                            found.append((image, file))
                        else:
                            logger.info("No file found in image object")
            except Exception as e:
                debug_print(
                    {
                        "method": "StashProcessing - _find_stash_files_by_path",
                        "status": "image_search_failed",
                        "media_ids": image_ids,
                        "error": str(e),
                    }
                )

        # Find scenes (both video and application)
        if scene_ids:
            path_filter = self._create_nested_path_or_conditions(scene_ids)
            debug_print(
                {
                    "method": "StashProcessing - _find_stash_files_by_path",
                    "status": "searching_scenes",
                    "media_ids": scene_ids,
                    "filter": path_filter,
                }
            )
            try:
                results = await self.context.client.find_scenes(
                    scene_filter=path_filter,
                    filter_=filter_params,
                )
                if results.count > 0:
                    for scene_data in results.scenes:
                        scene = (
                            Scene(**scene_data)
                            if isinstance(scene_data, dict)
                            else scene_data
                        )
                        if file := self._get_file_from_stash_obj(scene):
                            found.append((scene, file))
            except Exception as e:
                debug_print(
                    {
                        "method": "StashProcessing - _find_stash_files_by_path",
                        "status": "scene_search_failed",
                        "media_ids": scene_ids,
                        "error": str(e),
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
        is_organized = getattr(stash_obj, "organized", False)
        if is_organized:
            logger.debug(
                {
                    "method": "StashProcessing - _update_stash_metadata",
                    "status": "skipping_metadata",
                    "reason": "already_organized",
                    "media_id": media_id,
                    "item_id": item.id,
                    "stash_id": stash_obj.id,
                }
            )
            return

        # Parse current date if we have one
        current_date = None
        if current_date_str:
            try:
                from datetime import datetime

                current_date = datetime.strptime(current_date_str, "%Y-%m-%d").date()
            except ValueError:
                logger.warning(
                    f"Invalid date format in stash object: {current_date_str}"
                )

        # If we have a valid current date and this item is from later, skip the update
        if current_date and item_date >= current_date:
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
        stash_obj.title = self._generate_title_from_content(
            content=item.content,
            username=account.username,
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
        if hasattr(item, "id") and getattr(item, "__class__", None).__name__ == "Post":
            if not hasattr(stash_obj, "urls"):
                stash_obj.urls = []
            post_url = f"https://fansly.com/post/{item.id}"
            if post_url not in stash_obj.urls:
                stash_obj.urls.append(post_url)

        # Add performers (we already have the account)
        performers = []
        if main_performer := await self._find_existing_performer(account):
            performers.append(main_performer)

        # Add mentioned performers if any
        if hasattr(item, "accountMentions") and item.accountMentions:
            for mention in item.accountMentions:
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
                        from ...types import Performer

                        mention_performer = Performer.from_account(mention)
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
        if hasattr(item, "hashtags"):
            await item.awaitable_attrs.hashtags
            if item.hashtags:
                tags = await self._process_hashtags_to_tags(item.hashtags)
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

        # Mark as preview if needed
        if is_preview:
            await self._add_preview_tag(stash_obj)

        logger.debug(
            f"Method: StashProcessing - _update_stash_metadata, "
            f"Status: update_metadata--before_save, "
            f"Object type: {stash_obj.__type_name__}, "
            f"Object ID: {stash_obj.id}"
        )

        # Save changes to Stash only if object is dirty
        if stash_obj.is_dirty():
            await stash_obj.save(self.context.client)
        else:
            debug_print(
                {
                    "method": "StashProcessing - _update_stash_metadata",
                    "status": "no_changes",
                    "object_type": stash_obj.__type_name__,
                    "object_id": stash_obj.id,
                }
            )

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
                "stash_id": media.stash_id if hasattr(media, "stash_id") else None,
                "is_downloaded": media.is_downloaded,
                "variant_count": (
                    len(media.variants) if hasattr(media, "variants") else 0
                ),
                "variants": (
                    [v.id for v in media.variants] if hasattr(media, "variants") else []
                ),
            }
        )

        # Try to find in Stash and update metadata
        stash_result = None

        # First try by stash_id if available
        if hasattr(media, "stash_id") and media.stash_id:
            stash_result = await self._find_stash_files_by_id(
                [(media.stash_id, media.mimetype)]
            )
        else:
            # Collect all media IDs (original + variants)
            media_files = [(str(media.id), media.mimetype)]
            if hasattr(media, "variants") and media.variants:
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

        # Process each media item in the bundle
        for account_media in bundle.accountMedia:
            if account_media.media:
                await self._process_media(account_media.media, item, account, result)
            if account_media.preview:
                await self._process_media(account_media.preview, item, account, result)

        # Process bundle preview if any
        if bundle.preview:
            await self._process_media(bundle.preview, item, account, result)

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

        # Process direct media and its preview
        if attachment.media:
            if attachment.media.media:
                await self._process_media(attachment.media.media, item, account, result)
            if attachment.media.preview:
                await self._process_media(
                    attachment.media.preview, item, account, result
                )

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
            await self._process_bundle_media(attachment.bundle, item, account, result)

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

        return result
