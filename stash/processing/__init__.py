"""Processing module for Stash integration."""

from __future__ import annotations

import asyncio
import traceback
from typing import TYPE_CHECKING

from stash_graphql_client import StashContext
from stash_graphql_client.types import (
    Gallery,
    GalleryChapter,
    Image,
    ImageFile,
    Performer,
    Scene,
    VideoFile,
)

from helpers.rich_progress import get_progress_manager
from metadata import Account, Database
from textio import print_error, print_info

from ..logging import debug_print
from ..logging import processing_logger as logger
from .base import StashProcessingBase
from .mixins import (
    AccountProcessingMixin,
    BatchProcessingMixin,
    ContentProcessingMixin,
    GalleryProcessingMixin,
    MediaProcessingMixin,
    StudioProcessingMixin,
    TagProcessingMixin,
)


if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState


class StashProcessing(
    StashProcessingBase,
    AccountProcessingMixin,
    StudioProcessingMixin,
    GalleryProcessingMixin,
    MediaProcessingMixin,
    ContentProcessingMixin,
    BatchProcessingMixin,
    TagProcessingMixin,
):
    """Process metadata into Stash.

    This class handles:
    - Converting metadata to Stash types
    - Creating/updating Stash objects
    - Background processing
    - Resource cleanup

    Example:
        ```python
        processor = StashProcessing.from_config(config, state)
        await processor.start_creator_processing()
        await processor.cleanup()
        ```
    """

    def __init__(
        self,
        config: FanslyConfig,
        state: DownloadState,
        context: StashContext,
        database: Database,
        _background_task: asyncio.Task | None = None,
        _cleanup_event: asyncio.Event | None = None,
        _owns_db: bool = False,
    ) -> None:
        """Initialize StashProcessing.

        Args:
            config: Configuration object
            state: Download state object
            context: Stash context object
            database: Database object
            _background_task: Background task for processing
            _cleanup_event: Event for cleanup signaling
            _owns_db: Whether this instance owns the database connection
        """
        super().__init__(
            config, state, context, database, _background_task, _cleanup_event, _owns_db
        )
        AccountProcessingMixin.__init__(self)
        StudioProcessingMixin.__init__(self)
        GalleryProcessingMixin.__init__(self)
        MediaProcessingMixin.__init__(self)
        ContentProcessingMixin.__init__(self)
        BatchProcessingMixin.__init__(self)
        TagProcessingMixin.__init__(self)

    async def continue_stash_processing(
        self,
        account: Account | None,
        performer: Performer | None,
    ) -> None:
        """Continue processing in background.

        Args:
            account: Account to process
            performer: Performer created from account
        """
        progress_mgr = get_progress_manager()

        try:
            if not account or not performer:
                raise ValueError("Missing account or performer data")
            # Validate performer type (library returns Pydantic objects directly)
            if not isinstance(performer, Performer):
                raise TypeError("performer must be a Stash Performer object")

            self._account = account
            self._performer = performer

            # Convert performer.id (string) to int for comparison with account.stash_id (int)
            if account.stash_id != int(performer.id):
                await self._update_account_stash_id(
                    account=account,
                    performer=performer,
                )

            # 3 phases: studio, posts, messages
            performer_label = performer.name or "creator"
            with progress_mgr.session():
                self._stash_parent_task = progress_mgr.add_task(
                    name="stash_creator",
                    description=f"Stash: {performer_label}",
                    total=3,
                    show_elapsed=True,
                )

                # Process creator studio
                print_info("Processing creator Studio...")
                studio = await self.process_creator_studio(account=account)
                self._studio = studio
                progress_mgr.update_task(self._stash_parent_task, advance=1)

                # Process creator content
                print_info("Processing creator posts...")
                await self.process_creator_posts(
                    account=account,
                    performer=performer,
                    studio=studio,
                )
                progress_mgr.update_task(self._stash_parent_task, advance=1)

                print_info("Processing creator messages...")
                await self.process_creator_messages(
                    account=account,
                    performer=performer,
                    studio=studio,
                )
                progress_mgr.update_task(self._stash_parent_task, advance=1)

        except Exception as e:
            print_error(f"Error in Stash processing: {e}")
            logger.exception("Error in Stash processing", exc_info=e)
            debug_print(
                {
                    "method": "StashProcessing - continue_stash_processing",
                    "status": "processing_failed",
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }
            )
            raise
        finally:
            self._stash_parent_task = None
            self._account = None
            self._performer = None
            self._studio = None
            self._scene_code_index.clear()
            self._image_code_index.clear()
            # Drop both parents and their file leaves — Scene/Image preload
            # via GraphQL fragment caches VideoFile/ImageFile as separate
            # entries; orphaning them across creators leaks ~3.9k objects
            # per pass on a typical fan with ~3k images and ~800 scenes.
            for entity_type in (
                Gallery,
                GalleryChapter,
                Scene,
                Image,
                VideoFile,
                ImageFile,
            ):
                self.store.invalidate_type(entity_type)
            logger.debug("Invalidated per-creator entity caches and media code indexes")

            performer_name = (
                performer.name if isinstance(performer, Performer) else repr(performer)
            )
            stats = self.store.cache_stats()
            by_type = ", ".join(f"{k}={v}" for k, v in sorted(stats.by_type.items()))
            print_info(
                f"Finished Stash processing for {performer_name} "
                f"(cache: {stats.total_entries} entries — {by_type})"
            )


# Export main class
__all__ = ["StashProcessing"]
