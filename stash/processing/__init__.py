"""Processing module for Stash integration."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from stash_graphql_client import StashContext

from metadata import Database

from .base import StashProcessingBase
from .mixins import (
    AccountProcessingMixin,
    ContentProcessingMixin,
    FileFirstProcessingMixin,
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
    TagProcessingMixin,
    FileFirstProcessingMixin,
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
        TagProcessingMixin.__init__(self)


# Export main class
__all__ = ["StashProcessing"]
