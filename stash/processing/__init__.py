"""Processing module for Stash integration."""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from metadata import Account, Database
from metadata.decorators import with_session
from textio import print_error, print_info, print_warning

from ..context import StashContext
from ..logging import debug_print
from ..types import Performer, Studio
from .base import HasMetadata, StashProcessingBase
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
        try:
            if not account or not performer:
                raise ValueError("Missing account or performer data")
            # Convert dict to Performer if needed
            if isinstance(performer, dict):
                performer = Performer.from_dict(performer)
            elif not isinstance(performer, Performer):
                raise TypeError("performer must be a Stash Performer object or dict")

            # Ensure we have a fresh account instance bound to the session
            from sqlalchemy.sql import select

            stmt = select(Account).where(Account.id == account.id)
            result = await session.execute(stmt)
            account = result.scalar_one()
            if not isinstance(account, Account):
                raise TypeError("account must be a SQLAlchemy Account model")

            if account.stash_id != performer.id:
                await self._update_account_stash_id(
                    account=account,
                    performer=performer,
                )

            # Process creator studio
            print_info("Processing creator Studio...")
            studio = await self.process_creator_studio(
                account=account,
                performer=performer,
                session=session,
            )

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

            # Refresh account again before processing messages
            await session.refresh(account)
            print_info("Processing creator messages...")
            await self.process_creator_messages(
                account=account,
                performer=performer,
                studio=studio,
                session=session,
            )

        except Exception as e:
            print_error(f"Error in Stash processing: {e}")
            logging.getLogger(__name__).exception(
                "Error in Stash processing", exc_info=e
            )
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
            print_info(f"Finished Stash processing for {performer.name}")


# Export main class
__all__ = ["StashProcessing"]
