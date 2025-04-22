"""Base class for Stash processing module."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import traceback
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from tqdm import tqdm

from metadata import Account, Database
from metadata.decorators import with_session
from pathio import set_create_directory_for_download
from textio import print_error, print_info, print_warning

from ..client import StashClient
from ..context import StashContext
from ..logging import debug_print
from ..logging import processing_logger as logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from config import FanslyConfig
    from download.core import DownloadState


@runtime_checkable
class HasMetadata(Protocol):
    """Protocol for models that have metadata for Stash."""

    id: int
    content: str | None
    createdAt: datetime
    attachments: list[Any]
    # Messages don't have accountMentions, only Posts do
    accountMentions: list[Account] | None = None


class StashProcessingBase:
    """Base class for StashProcessing functionality.

    This class handles:
    - Basic initialization and resource management
    - Database connection handling
    - Common utilities like file scanning
    - Cleanup and resource management

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
        """Initialize StashProcessingBase.

        Args:
            config: Configuration instance
            state: State instance
            context: StashContext instance
            database: Database instance
            _background_task: Optional background task
            _cleanup_event: Optional cleanup event
            _owns_db: Whether this instance owns the database connection
        """
        self.config = config
        self.state = state
        self.context = context
        self.database = database
        self._background_task = _background_task
        self._cleanup_event = _cleanup_event or asyncio.Event()
        self._owns_db = _owns_db
        self.log = logging.getLogger(__name__)

    @classmethod
    def from_config(
        cls,
        config: FanslyConfig,
        state: DownloadState,
    ) -> Any:  # Return type will be the derived class
        """Create processor from config.

        Args:
            config: FanslyConfig instance
            state: Current download state

        Returns:
            New processor instance

        Raises:
            RuntimeError: If no StashContext connection data available
        """
        state_copy = deepcopy(state)
        context = config.get_stash_context()
        instance = cls(
            config=config,
            state=state_copy,
            context=context,
            database=config._database,  # Use existing database instance
            _background_task=None,
            _cleanup_event=asyncio.Event(),
            _owns_db=False,  # We don't own the database
        )
        return instance

    async def scan_creator_folder(self) -> None:
        """Scan the creator's folder for media files."""
        if not self.state.base_path:
            print_info("No download path set, attempting to create one...")
            try:
                self.state.download_path = set_create_directory_for_download(
                    self.config, self.state
                )
                print_info(f"Created download path: {self.state.download_path}")
            except Exception as e:
                print_error(f"Failed to create download path: {e}")
                return

        # Start metadata scan with all generation flags enabled
        flags = {
            "scanGenerateCovers": True,
            "scanGeneratePreviews": True,
            "scanGenerateImagePreviews": True,
            "scanGenerateSprites": True,
            "scanGeneratePhashes": True,
            "scanGenerateThumbnails": True,
            "scanGenerateClipPreviews": True,
        }
        try:
            job_id = await self.context.client.metadata_scan(
                paths=[str(self.state.base_path)],
                flags=flags,
            )
            print_info(f"Metadata scan job ID: {job_id}")

            finished_job = False
            while not finished_job:
                try:
                    finished_job = await self.context.client.wait_for_job(job_id)
                except Exception:
                    finished_job = False
        except RuntimeError as e:
            raise RuntimeError(f"Failed to process metadata: {e}") from e

    async def start_creator_processing(self) -> None:
        """Start processing creator metadata.

        This method:
        1. Checks if StashContext is configured
        2. Scans the creator folder
        3. Processes the creator metadata
        4. Continues processing in the background
        """
        if self.config.stash_context_conn is None:
            print_warning(
                "StashContext is not configured. Skipping metadata processing."
            )
            return

        # Initialize Stash client
        logger.debug(f"Initializing client on context {id(self.context)}")
        await self.context.get_client()
        logger.debug("Client initialized, proceeding with scan")

        await self.scan_creator_folder()
        account, performer = await self.process_creator()

        # Continue processing in background with proper task management
        loop = asyncio.get_running_loop()
        self._background_task = loop.create_task(
            self._safe_background_processing(account, performer)
        )
        self.config.get_background_tasks().append(self._background_task)

    async def _safe_background_processing(
        self,
        account: Account | None,
        performer: Any | None,
    ) -> None:
        """Safely handle background processing with cleanup.

        Args:
            account: Account to process
            performer: Performer created from account
        """
        try:
            await self.continue_stash_processing(account, performer)
        except asyncio.CancelledError:
            logger.debug("Background task cancelled")
            # Handle task cancellation
            debug_print({"status": "background_task_cancelled"})
            raise
        except Exception as e:
            logger.exception(
                f"Background task failed: {e}",
                traceback=True,
                exc_info=e,
                stack_info=True,
            )
            debug_print(
                {
                    "error": f"background_task_failed: {e}",
                    "traceback": traceback.format_exc(),
                }
            )
            raise
        finally:
            if self._cleanup_event:
                self._cleanup_event.set()

    async def cleanup(self) -> None:
        """Safely cleanup resources.

        This method:
        1. Cancels any background processing
        2. Waits for cleanup event with timeout
        3. Closes client connection
        4. Cleans up any tracked tasks
        """

        logger.debug(f"Starting cleanup for {self.__class__.__name__}")

        try:
            # Cancel and wait for background task with timeout
            if self._background_task and not self._background_task.done():
                logger.debug(f"Cancelling background task {self._background_task}")
                self._background_task.cancel()
                if self._cleanup_event:
                    try:
                        # Wait for cleanup event with timeout
                        await asyncio.wait_for(self._cleanup_event.wait(), timeout=10)
                        logger.debug("Cleanup event was set")
                    except TimeoutError:
                        logger.warning(
                            "Timeout waiting for cleanup event, continuing anyway"
                        )

            # Force-set the cleanup event to ensure we don't block
            if self._cleanup_event and not self._cleanup_event.is_set():
                logger.debug("Forcing cleanup event to be set")
                self._cleanup_event.set()

            # Cancel any other tasks registered in config
            if hasattr(self, "config") and hasattr(self.config, "get_background_tasks"):
                background_tasks = self.config.get_background_tasks()
                # Find tasks created by this instance
                own_tasks = []
                for task in background_tasks:
                    # If task was created in our module
                    if task.get_coro().__qualname__.startswith(
                        self.__class__.__module__
                    ):
                        own_tasks.append(task)

                # Cancel own tasks
                for task in own_tasks:
                    if not task.done():
                        logger.debug(f"Cancelling additional task: {task}")
                        task.cancel()
                    try:
                        background_tasks.remove(task)
                    except ValueError:
                        pass  # Task was already removed

        except Exception as e:
            logger.error(f"Error during cleanup task cancellation: {e}")

        finally:
            # Always close client with timeout
            try:
                logger.debug("Closing Stash client connection")
                await asyncio.wait_for(self.context.close(), timeout=5)
                logger.debug("Stash client closed successfully")
            except TimeoutError:
                logger.warning("Timeout closing Stash client connection")
            except Exception as e:
                logger.error(f"Error closing Stash client: {e}")

            logger.debug(f"Cleanup completed for {self.__class__.__name__}")

    def _generate_title_from_content(
        self,
        content: str | None,
        username: str,
        created_at: datetime,
        current_pos: int | None = None,
        total_media: int | None = None,
    ) -> str:
        """Generate title from content with fallback to date format.

        Args:
            content: Content to generate title from
            username: Username for fallback title
            created_at: Creation date for fallback title
            current_pos: Current media position (optional)
            total_media: Total media count (optional)

        Returns:
            Generated title
        """
        title = None
        if content:
            # Try to get first line as title
            first_line = content.split("\n")[0].strip()
            if len(first_line) >= 10 and len(first_line) <= 128:
                title = first_line
            elif len(first_line) > 128:
                title = first_line[:125] + "..."

        # If no suitable title from content, use date format
        if not title:
            title = f"{username} - {created_at.strftime('%Y/%m/%d')}"

        # Append position if multiple media
        if total_media and total_media > 1 and current_pos:
            title = f"{title} - {current_pos}/{total_media}"

        return title
