"""Processing module for Stash integration."""

from __future__ import annotations

import asyncio
import traceback
from typing import TYPE_CHECKING

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from stash_graphql_client import StashContext
from stash_graphql_client.types import Gallery, GalleryChapter, Image, Performer, Scene

from helpers.rich_progress import get_progress_manager
from metadata import Account, Database
from metadata.decorators import with_session
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

    @with_session()
    async def continue_stash_processing(
        self,
        account: Account | None,
        performer: Performer | None,
        session: AsyncSession | None = None,
    ) -> None:
        """Continue processing in background.

        Args:
            account: Account to process
            performer: Performer created from account
            session: Optional database session to use

        Note:
            This method requires a session and will ensure the account is properly bound to it.
            The performer object is a Stash GraphQL type, not a SQLAlchemy model.
        """
        if session is None:
            raise RuntimeError("Session should be provided by @with_session decorator")
        progress_mgr = get_progress_manager()

        try:
            if not account or not performer:
                raise ValueError("Missing account or performer data")
            # Validate performer type (library returns Pydantic objects directly)
            if not isinstance(performer, Performer):
                raise TypeError("performer must be a Stash Performer object")

            # Get account ID safely without triggering lazy loading
            try:
                identity = inspect(account).identity
                account_id = account.id if identity is None else identity[0]
            except Exception:
                # Invalid account type - try to access id attribute directly
                # This will raise AttributeError if account doesn't have an id attribute
                account_id = account.id

            # Ensure we have a fresh account instance bound to the session
            stmt = select(Account).where(Account.id == account_id)
            result = await session.execute(stmt)
            account = result.scalar_one()
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
                studio = await self.process_creator_studio(
                    account=account,
                    session=session,
                )
                self._studio = studio
                progress_mgr.update_task(self._stash_parent_task, advance=1)

                # Process creator content
                # Refresh account to ensure it's still bound
                await session.refresh(account)
                print_info("Processing creator posts...")
                await self.process_creator_posts(
                    account=account,
                    performer=performer,
                    studio=studio,
                    session=session,
                )
                progress_mgr.update_task(self._stash_parent_task, advance=1)

                # Refresh account again before processing messages
                await session.refresh(account)
                print_info("Processing creator messages...")
                await self.process_creator_messages(
                    account=account,
                    performer=performer,
                    studio=studio,
                    session=session,
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
            for entity_type in (Gallery, GalleryChapter, Scene, Image):
                self.store.invalidate_type(entity_type)
            logger.debug("Invalidated per-creator entity caches and media code indexes")

            performer_name = (
                performer.name if isinstance(performer, Performer) else repr(performer)
            )
            print_info(f"Finished Stash processing for {performer_name}")


# Export main class
__all__ = ["StashProcessing"]
