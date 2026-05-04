"""Base class for Stash processing module."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import traceback
import warnings
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from typing import TYPE_CHECKING, Any

from stash_graphql_client import ServerCapabilities, StashContext
from stash_graphql_client.errors import (
    StashCapabilityError,
    StashUnmappedFieldWarning,
    StashVersionError,
)
from stash_graphql_client.store import StashEntityStore
from stash_graphql_client.types import (
    Gallery,
    Image,
    Performer,
    Scene,
    Studio,
    Tag,
    is_set,
)

from metadata import Account, Database
from pathio import get_stash_path, set_create_directory_for_download
from textio import print_error, print_info, print_warning

from ..logging import debug_print
from ..logging import processing_logger as logger
from .protocols import StashProcessingProtocol


if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState


class StashProcessingBase(StashProcessingProtocol):
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

    # Class-level declarations for Pylance — values set in __init__
    config: FanslyConfig
    state: DownloadState
    context: StashContext
    database: Database
    _account: Account | None
    _performer: Performer | None
    _studio: Studio | None
    _stash_parent_task: str | None
    _scene_code_index: dict[str, list[Scene]]
    _image_code_index: dict[str, list[Image]]

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

        # Per-creator cached lookups — set/cleared in continue_stash_processing()
        self._account: Account | None = None
        self._performer: Performer | None = None
        self._studio: Studio | None = None
        self._stash_parent_task: str | None = None

        # Media code indexes — filename pattern: {date}_at_{time}_UTC_id_{media_id}.{ext}
        self._scene_code_index: dict[str, list[Scene]] = defaultdict(list)
        self._image_code_index: dict[str, list[Image]] = defaultdict(list)

    @property
    def store(self) -> StashEntityStore:
        """Convenient access to Stash entity store.

        Returns:
            StashEntityStore from context for ORM-style operations
        """
        return self.context.store

    @property
    def capabilities(self) -> ServerCapabilities:
        """Convenient access to server capabilities."""
        return self.context.capabilities

    async def _preload_stash_entities(self) -> None:
        """Configure cache TTLs for Stash entity types.

        Pins TTL to None (no expiration) since the script is the sole writer
        to Stash during processing — cached entities stay valid for the run.
        Mixin call sites use the ``store.filter(...) → store.find_one(...)``
        pattern, so the cache populates lazily as entities are actually
        looked up; no upfront fetch is needed.

        Per-creator entities (Gallery, Image, Scene) also get TTL=None but
        are invalidated per-creator (see processing/__init__.py).
        """
        for entity_type in (Performer, Tag, Studio, Gallery, Image, Scene):
            self.store.set_ttl(entity_type, None)

    async def _preload_creator_media(self) -> None:
        """Preload Galleries, Images, and Scenes for current creator into identity map.

        Uses self.state.base_path as path filter to load only this creator's media.
        Dramatically speeds up subsequent lookups by avoiding per-item GraphQL queries.

        Also builds media code indexes for O(1) lookups by media_id extracted from
        filenames. Pattern from pyofscraperstash: preload per-performer, build index,
        invalidate after processing.
        """
        if not self.state.base_path:
            logger.debug("No base_path set, skipping creator media preload")
            return

        path_filter = get_stash_path(self.state.base_path, self.config).rstrip("/")
        logger.info(f"Preloading creator media from: {path_filter}")

        self._scene_code_index.clear()
        self._image_code_index.clear()

        try:
            scene_count = 0
            async for scene in self.store.find_iter(
                Scene, query_batch=500, path__contains=path_filter
            ):
                scene_count += 1
                self._index_scene_files(scene)
            logger.info(f"Preloaded {scene_count} scenes")

            image_count = 0
            async for image in self.store.find_iter(
                Image, query_batch=500, path__contains=path_filter
            ):
                image_count += 1
                self._index_image_files(image)
            logger.info(f"Preloaded {image_count} images")

            # Galleries have no file path — looked up by code on demand.
            # Preloading all galleries pulls in scene references via the fragment.

        except Exception as e:
            logger.warning(f"Failed to preload creator media (continuing anyway): {e}")

        logger.info(
            f"Built media code indexes: {len(self._scene_code_index)} scene codes, "
            f"{len(self._image_code_index)} image codes"
        )

        stats = self.store.cache_stats()
        logger.info(
            f"Cache after creator preload: {stats.total_entries} entries "
            f"({', '.join(f'{k}: {v}' for k, v in sorted(stats.by_type.items()))})"
        )

    def _index_scene_files(self, scene: Scene) -> None:
        """Index a scene's files by media code for O(1) lookups."""
        if not is_set(scene.files) or not scene.files:
            return
        for f in scene.files:
            if is_set(f.path) and f.path:
                # Extract media_id from fansly filename pattern:
                # {date}_at_{time}_UTC_id_{media_id}.{ext}
                # OR: {date}_at_{time}_UTC_preview_id_{media_id}.{ext}
                for marker in ("_id_", "_preview_id_"):
                    if marker in f.path:
                        after_marker = f.path.split(marker)[-1]
                        media_code = after_marker.split(".")[0]
                        if media_code:
                            self._scene_code_index[media_code].append(scene)

    def _index_image_files(self, image: Image) -> None:
        """Index an image's visual files by media code for O(1) lookups."""
        if not is_set(image.visual_files) or not image.visual_files:
            return
        for f in image.visual_files:
            if is_set(f.path) and f.path:
                for marker in ("_id_", "_preview_id_"):
                    if marker in f.path:
                        after_marker = f.path.split(marker)[-1]
                        media_code = after_marker.split(".")[0]
                        if media_code:
                            self._image_code_index[media_code].append(image)

    def find_scenes_by_media_codes(
        self, media_codes: list[str]
    ) -> dict[str, list[Scene]]:
        """Find scenes by media codes using the pre-built index. O(1) per lookup."""
        result: dict[str, list[Scene]] = {}
        for code in media_codes:
            candidates = self._scene_code_index.get(code, [])
            if candidates:
                seen_ids: set[str] = set()
                unique = [
                    s
                    for s in candidates
                    if s.id not in seen_ids and not seen_ids.add(s.id)
                ]
                result[code] = unique
        return result

    def find_images_by_media_codes(
        self, media_codes: list[str]
    ) -> dict[str, list[Image]]:
        """Find images by media codes using the pre-built index. O(1) per lookup."""
        result: dict[str, list[Image]] = {}
        for code in media_codes:
            candidates = self._image_code_index.get(code, [])
            if candidates:
                seen_ids: set[str] = set()
                unique = [
                    i
                    for i in candidates
                    if i.id not in seen_ids and not seen_ids.add(i.id)
                ]
                result[code] = unique
        return result

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
            database=config._database,
            _background_task=None,
            _cleanup_event=asyncio.Event(),
            _owns_db=False,
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
                self.state.base_path = self.state.download_path
                print_info(f"Created download path: {self.state.download_path}")
            except Exception as e:
                print_error(f"Failed to create download path: {e}")
                return

        # Log scan path capability (v0.11 gates this via __safe_to_eat__)
        if self.capabilities.input_has_field("GenerateMetadataInput", "paths"):
            logger.debug("Server supports targeted metadata scan paths")

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
                paths=[get_stash_path(self.state.base_path, self.config)],
                flags=flags,
            )
            print_info(f"Metadata scan job ID: {job_id}")

            finished_job = False
            while not finished_job:
                try:
                    finished_job = await self.context.client.wait_for_job(job_id)
                except Exception:
                    finished_job = False
        except (RuntimeError, ValueError) as e:
            # ValueError catches the lib's own failure shape:
            # stash_graphql_client's ``metadata_scan`` raises
            # ``ValueError("Failed to start metadata scan: ...")``
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
        try:
            await self.context.get_client()
        except StashVersionError as e:
            print_error(f"Stash server too old: {e}")
            print_warning("Minimum required: Stash v0.30.0 (appSchema 75)")
            return
        except StashCapabilityError as e:
            # SGC 0.12.2+ raises this distinct from StashVersionError when a
            # per-feature appSchema gate fails at use time. get_client() itself
            # only does floor-version checking today, so this catch is defensive
            # against future SGC versions that may surface capability checks
            # earlier in the connect path.
            print_error(f"Stash server missing required capability: {e}")
            return
        except RuntimeError as e:
            print_error(f"Failed to initialize Stash client: {e}")
            return
        logger.debug("Client initialized, proceeding with scan")

        # Surface v0.11 deprecation/unmapped field warnings in logs
        warnings.filterwarnings(
            "always", category=DeprecationWarning, module="stash_graphql_client"
        )
        warnings.filterwarnings("always", category=StashUnmappedFieldWarning)

        await self._preload_stash_entities()

        # _preload_creator_media() must run AFTER scan_creator_folder()
        # so it can index the files the scan discovers.
        await self.scan_creator_folder()
        await self._preload_creator_media()
        account, performer = await self.process_creator()

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
            # Get performer name (library returns Pydantic objects directly)
            perf_name = performer.name if performer else "unknown performer"
            print_info(f"Stash processing completed successfully for {perf_name}")
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
            # Remove this task from config's background tasks if it's there
            if hasattr(self, "config") and hasattr(self.config, "get_background_tasks"):
                background_tasks = self.config.get_background_tasks()
                current_task = asyncio.current_task()
                if current_task in background_tasks:
                    try:
                        background_tasks.remove(current_task)
                        logger.debug(
                            f"Removed completed task {current_task} from background tasks"
                        )
                    except ValueError:
                        pass  # Task was already removed

            # Always set cleanup event so waiting code can proceed
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

        # Log final cache state before cleanup
        try:
            stats = self.store.cache_stats()
            logger.info(
                f"Cache at cleanup: {stats.total_entries} entries "
                f"({', '.join(f'{k}: {v}' for k, v in sorted(stats.by_type.items()))})"
            )
        except Exception:
            logger.debug("Failed to collect cache stats during cleanup")

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
                own_tasks = [
                    task
                    for task in background_tasks
                    if task.get_coro().__qualname__.startswith(
                        self.__class__.__module__
                    )
                ]

                # Cancel own tasks
                for task in own_tasks:
                    if not task.done():
                        logger.debug(f"Cancelling additional task: {task}")
                        task.cancel()
                    with contextlib.suppress(ValueError):
                        background_tasks.remove(task)

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
