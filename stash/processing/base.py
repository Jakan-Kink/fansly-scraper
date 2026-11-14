"""Base class for Stash processing module."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import traceback
import warnings
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import PurePath
from typing import TYPE_CHECKING, Self

from stash_graphql_client import ServerCapabilities, StashContext
from stash_graphql_client.errors import (
    StashCapabilityError,
    StashUnmappedFieldWarning,
    StashVersionError,
)
from stash_graphql_client.store import StashEntityStore
from stash_graphql_client.types import (
    BaseFile,
    Gallery,
    Image,
    Job,
    JobStatus,
    Performer,
    Scene,
    SceneCreateInput,
    Studio,
    Tag,
)

from helpers.rich_progress import get_progress_manager
from metadata import Account, Database, Media, Message
from metadata.models import get_store
from pathio import get_stash_path, set_create_directory_for_download
from textio import print_error, print_info, print_warning

from ..logging import debug_print
from ..logging import processing_logger as logger
from .protocols import StashProcessingProtocol


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from config import FanslyConfig
    from download.core import DownloadState
    from metadata import Post


_SCAN_POLL_S = 1.5
# Consecutive polls a scan job may be absent before it is treated as gone.
_SCAN_MISSING_POLLS = 3
# A READY scan with nothing running in Stash for this long will never start
# (stashapp/stash#7347).
_SCAN_FROZEN_S = 15.0
_ACTIVE_JOB_STATUSES = (JobStatus.RUNNING, JobStatus.STOPPING)


@dataclass
class _ScanWatch:
    """What the scan wait last saw of one job, and when."""

    seen: tuple[object, object] | None = None
    changed_at: float = field(default_factory=time.monotonic)
    idle_since: float | None = None
    requeue: bool = False


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
    database: Database | None
    _account: Account | None
    _performer: Performer | None
    _studio: Studio | None
    _stash_parent_task: str | None

    def __init__(
        self,
        config: FanslyConfig,
        state: DownloadState,
        context: StashContext,
        database: Database | None,
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

        # (lowercased username, name) -> matched performers; None = ambiguous
        self._performer_matches: dict[tuple[str, str], list[Performer] | None] = {}
        # lowercased tag name or alias -> tag
        self._tags_by_name: dict[str, Tag] = {}
        # True once the galleries the current items could have are in the store
        self._galleries_preloaded = False

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

    @staticmethod
    async def _link_tag(obj: Scene | Image | Gallery, tag: Tag) -> None:
        """Add a tag to a scene, image or gallery without loading the tag's reverse side.

        ``obj.add_tag`` also syncs the tag's own list of scenes/images/galleries,
        which makes the store fetch every one in the library that carries the
        tag. Appending to the loaded ``tags`` list is all the save needs;
        ``add_tag`` is only used when that list has not been loaded.
        """
        if isinstance(obj.tags, list):
            if all(existing.id != tag.id for existing in obj.tags):
                obj.tags.append(tag)
        else:
            await obj.add_tag(tag)

    @staticmethod
    async def _link_performer(
        obj: Scene | Image | Gallery, performer: Performer
    ) -> None:
        """Add a performer without loading the performer's reverse side.

        Same reasoning as ``_link_tag``: ``obj.add_performer`` would fetch every
        scene, image or gallery in the library that the performer is on.
        """
        if isinstance(obj.performers, list):
            if all(existing.id != performer.id for existing in obj.performers):
                obj.performers.append(performer)
        else:
            await obj.add_performer(performer)

    async def _preload_stash_entities(self) -> None:
        """Configure cache TTLs for Stash entity types and preload the tags.

        Pins TTL to None (no expiration) since the script is the sole writer
        to Stash during processing — cached entities stay valid for the run.
        Tags are fetched up front (hashtags resolve against all of them);
        performers and studios populate lazily through the mixins'
        ``store.filter(...) → store.find_one(...)`` lookups.

        Per-creator entities (Gallery, Image, Scene) also get TTL=None but
        are invalidated per-creator (see processing/__init__.py).
        """
        for entity_type in (Performer, Tag, Studio, Gallery, Image, Scene):
            self.store.set_ttl(entity_type, None)
        await self._preload_tags()

    @classmethod
    def from_config(
        cls,
        config: FanslyConfig,
        state: DownloadState,
    ) -> Self:
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

    async def scan_creator_folder(self, paths: list[str] | None = None) -> None:
        """Scan creator media into Stash, then settle before reads.

        Args:
            paths: Stash-visible paths to scan. Defaults to the creator's
                whole folder; the incremental path passes the exact
                just-downloaded file paths so Stash only re-indexes those.
        """
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
        scan_paths = paths or [get_stash_path(self.state.base_path, self.config)]
        try:
            # A scan cancelled because Stash would never start it is queued once more.
            for _ in range(2):
                job_id = await self.context.client.metadata_scan(
                    paths=scan_paths,
                    flags=flags,
                )
                print_info(f"Metadata scan job ID: {job_id}")
                if not await self._wait_for_scan_job(job_id):
                    break
            else:
                logger.warning(
                    "Stash did not start the scan after it was queued again; "
                    "continuing with what Stash has indexed"
                )

            # The job-FINISHED signal can precede Stash's index commit; settle
            # before any File/Scene/Image read-back.
            if self.config.stash_scan_settle_s:
                await asyncio.sleep(self.config.stash_scan_settle_s)
        except (RuntimeError, ValueError) as e:
            # ValueError catches the lib's own failure shape:
            # stash_graphql_client's ``metadata_scan`` raises
            # ``ValueError("Failed to start metadata scan: ...")``
            raise RuntimeError(f"Failed to process metadata: {e}") from e

    async def _wait_for_scan_job(self, job_id: str) -> bool:
        """Poll a scan job until Stash reports it over, showing its progress.

        A job that is cancelled, fails, drops out of Stash's job queue, or does
        not change for the stall timeout is logged and treated as over; the
        caller continues with what is indexed. A job left READY while Stash
        runs nothing is cancelled. A shutdown request ends the wait, cancelling
        the job in Stash if it has not started.

        Args:
            job_id: Scan job ID returned by ``metadata_scan``.

        Returns:
            True when the job was cancelled as frozen and should be queued again.
        """
        progress = get_progress_manager()
        with progress.session():
            task = progress.add_task(
                name="stash_scan",
                description=f"Stash scan job {job_id}",
                total=100,
                show_elapsed=True,
            )
            try:
                return await self._poll_scan_job(job_id, task)
            finally:
                progress.remove_task(task)

    async def _poll_scan_job(self, job_id: str, task: str) -> bool:
        misses = 0
        watch = _ScanWatch()
        while True:
            job = await self.context.client.find_job(job_id)
            if job is None:
                misses += 1
                if misses >= _SCAN_MISSING_POLLS:
                    logger.warning(
                        f"Scan job {job_id} is not in Stash's job queue; "
                        "continuing with what Stash has indexed"
                    )
                    return False
            elif job.status == JobStatus.FINISHED:
                return False
            elif job.status in (JobStatus.CANCELLED, JobStatus.FAILED):
                detail = f": {job.error}" if isinstance(job.error, str) else ""
                logger.warning(
                    f"Scan job {job_id} ended as {job.status}{detail}; "
                    "continuing with what Stash has indexed"
                )
                return False
            else:
                misses = 0
                if await self._scan_wait_over(job_id, job, task, watch):
                    return watch.requeue
            await asyncio.sleep(_SCAN_POLL_S)

    async def _scan_wait_over(
        self, job_id: str, job: Job, task: str, watch: _ScanWatch
    ) -> bool:
        """Show a queued or running scan's state; True when the wait should end."""
        stall_timeout = self.config.stash_scan_stall_timeout_s
        shutdown = self.config._shutdown_event
        now = time.monotonic()
        seen = (job.status, job.progress)
        if seen != watch.seen:
            logger.debug(f"Scan job {job_id}: {job.status}, progress {job.progress}")
            watch.seen = seen
            watch.changed_at = now
            done = job.progress if isinstance(job.progress, float) else 0.0
            get_progress_manager().update_task(
                task,
                advance=0,
                description=f"Stash scan job {job_id}: {job.status}",
                completed=int(done * 100),
            )
        elif now - watch.changed_at >= stall_timeout:
            logger.warning(
                f"Scan job {job_id} has not changed from {job.status} in "
                f"{stall_timeout:g} s; continuing with what Stash has indexed"
            )
            return True
        if shutdown is not None and shutdown.is_set():
            await self._abandon_scan_job(job_id, job.status)
            return True
        if job.status != JobStatus.READY or await self._other_job_active(job_id):
            watch.idle_since = None
        elif watch.idle_since is None:
            watch.idle_since = now
        elif now - watch.idle_since >= _SCAN_FROZEN_S:
            watch.requeue = await self.context.client.stop_job(job_id)
            outcome = "cancelled it" if watch.requeue else "Stash did not cancel it"
            logger.warning(
                f"Scan job {job_id} stayed READY for {_SCAN_FROZEN_S:g} s with "
                f"nothing running in Stash; {outcome}"
            )
            return True
        return False

    async def _other_job_active(self, job_id: str) -> bool:
        """Whether Stash is running any job other than this one."""
        jobs = await self.context.client.job_queue()
        return any(
            job.id != job_id and job.status in _ACTIVE_JOB_STATUSES for job in jobs
        )

    async def _abandon_scan_job(self, job_id: str, status: object) -> None:
        """Stop waiting on a scan at shutdown; a job still queued is cancelled."""
        if status == JobStatus.READY:
            cancelled = await self.context.client.stop_job(job_id)
            outcome = (
                "cancelled it in Stash" if cancelled else "Stash did not cancel it"
            )
            logger.warning(f"Scan job {job_id} was still queued at shutdown; {outcome}")
        else:
            logger.warning(
                f"Scan job {job_id} is {status} at shutdown; no longer waiting on it"
            )

    def _configure_scene_creation_guard(self) -> None:
        """Enable Scene creation (the split's create path) only when the user
        explicitly opted in via ``stash_enable_scene_split is True``.

        SGC blocks ``Scene`` creation unless ``Scene.__create_input_type__`` is
        set. We set it process-globally for the True mode only; ``"dry-run"`` and
        ``False`` leave it unset, so a stray new-Scene save raises — a backstop
        against accidental writes in non-split modes.
        """
        if self.config.stash_enable_scene_split is True:
            Scene.__create_input_type__ = SceneCreateInput

    async def _connect_stash(self) -> bool:
        """Connect the Stash client and prepare guards + preload.

        Returns False (after logging) when Stash is unconfigured or the server
        is too old / missing a capability — the caller then skips processing.
        """
        if self.config.stash_context_conn is None:
            print_warning(
                "StashContext is not configured. Skipping metadata processing."
            )
            return False

        logger.debug(f"Initializing client on context {id(self.context)}")
        try:
            await self.context.get_client()
        except StashVersionError as e:
            print_error(f"Stash server too old: {e}")
            print_warning("Minimum required: Stash v0.30.0 (appSchema 75)")
            return False
        except StashCapabilityError as e:
            # SGC 0.12.2+ raises this distinct from StashVersionError when a
            # per-feature appSchema gate fails at use time. get_client() itself
            # only does floor-version checking today, so this catch is defensive
            # against future SGC versions that may surface capability checks
            # earlier in the connect path.
            print_error(f"Stash server missing required capability: {e}")
            return False
        except RuntimeError as e:
            print_error(f"Failed to initialize Stash client: {e}")
            return False
        logger.debug("Client initialized, proceeding with scan")

        # Surface v0.11 deprecation/unmapped field warnings in logs
        warnings.filterwarnings(
            "always", category=DeprecationWarning, module="stash_graphql_client"
        )
        warnings.filterwarnings("always", category=StashUnmappedFieldWarning)

        self._configure_scene_creation_guard()
        await self._preload_stash_entities()
        return True

    async def start_creator_processing(self) -> None:
        """Connect, scan the creator folder, then process metadata in background."""
        if not await self._connect_stash():
            return

        await self.scan_creator_folder()
        account, performer = await self.process_creator()

        loop = asyncio.get_running_loop()
        self._background_task = loop.create_task(
            self._safe_background_processing(account, performer)
        )
        self.config.get_background_tasks().append(self._background_task)

    async def _safe_background_processing(
        self,
        account: Account | None,
        performer: Performer | None,
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
            # Static message + exc_info=e (sibling convention, e.g.
            # file_first.py:109). An f-string here interpolates exceptions whose
            # repr contains literal braces (e.g. "[{'message': 'boom'}]"), which
            # loguru then feeds to str.format(), raising KeyError and masking the
            # real failure.
            logger.exception("Background task failed", exc_info=e)
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
                    if getattr(task.get_coro(), "__qualname__", "").startswith(
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
        created_at: datetime | None,
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
            title = (
                f"{username} - {created_at.strftime('%Y/%m/%d')}"
                if created_at is not None
                else username
            )

        # Append position if multiple media
        if total_media and total_media > 1 and current_pos:
            title = f"{title} - {current_pos}/{total_media}"

        return title

    async def _build_media_index(
        self, items: Sequence[Post | Message]
    ) -> dict[str, tuple[Media, list[Post | Message]]]:
        """Map each downloaded file's leaf -> (Media, owning items).

        Keyed on PurePath(local_filename).name (the leaf) so a swept Stash file
        matches by basename. Includes variants (each has its own local_filename).
        A file shared across items carries every owner (earliest first, by id):
        its adjudicated entity joins all their galleries, and the earliest owner
        is canonical for the entity's own metadata (for a preview, a post comes
        before any message). "Shared" covers both the same
        Media object and distinct media resolving to the same download target
        (same leaf). A leaf claimed by a media with a DIFFERENT target is a
        genuine collision — keep the first, warn. Quoted media is owned by the
        original post and by the quoting post; the original always has the
        lower id, so it is the canonical one.
        """
        index: dict[str, tuple[Media, list[Post | Message]]] = {}
        for item in items:
            for media, owner in await self._collect_owned_media(item):
                for m in (media, *(media.variants or [])):
                    if m.local_filename:
                        self._register_in_media_index(index, m, owner)
                        if owner is not item:
                            self._register_in_media_index(index, m, item)
        for media, owners in index.values():
            # A preview that is in a post and in a message takes the post's
            # metadata; otherwise the earliest owner comes first.
            owners.sort(key=partial(self._owner_order, messages_last=media.is_preview))
        return index

    def _index_unposted_media(
        self,
        index: dict[str, tuple[Media, list[Post | Message]]],
        account: Account,
    ) -> None:
        """Add the creator's files on disk that no post or message owns.

        Each is registered with no owners, so it is linked to the creator and
        joins no gallery.

        Args:
            index: Media index keyed by filename -> (Media, owning items)
            account: The Account being processed
        """
        unposted = get_store().filter(
            Media, lambda m: m.accountId == account.id and bool(m.local_filename)
        )
        for media in unposted:
            if media.local_filename:
                index.setdefault(PurePath(media.local_filename).name, (media, []))

    @staticmethod
    def _owner_order(owner: Post | Message, *, messages_last: bool) -> tuple[bool, int]:
        """Sort key for a media's owners: by id, messages last when asked."""
        return (messages_last and isinstance(owner, Message), owner.id or 0)

    @staticmethod
    def _register_in_media_index(
        index: dict[str, tuple[Media, list[Post | Message]]],
        media: Media,
        item: Post | Message,
    ) -> None:
        """Record media under its leaf, fanning shared files to every owner.

        First writer for a leaf wins the Media. A later item is appended as an
        additional owner when it resolves to the same file — the same Media
        object, a distinct media with the same download target, or a hash
        duplicate recorded under the first media's file name. Any other media
        with that leaf is a collision (kept out, logged).
        """
        if media.local_filename is None:
            # Caller (_build_media_index) only registers media with a filename.
            raise ValueError(f"media {media.id} has no local_filename; cannot index.")
        leaf = PurePath(media.local_filename).name
        existing = index.get(leaf)
        if existing is None:
            index[leaf] = (media, [item])
            return
        existing_media, owners = existing
        # Same file when it's the same Media object, OR distinct media that
        # resolve to the same downloaded file (shared download target — the
        # leaf is built from `download_id or id`, so a same-leaf clash with a
        # matching target IS one physical file). Only a different target is a
        # genuine ambiguous collision.
        # A hash duplicate's record names the first media's file: same hash,
        # same name, one physical file.
        same_file = (
            existing_media is media
            or (existing_media.download_id or existing_media.id)
            == (media.download_id or media.id)
            or (
                bool(media.content_hash)
                and existing_media.content_hash == media.content_hash
            )
        )
        if not same_file:
            logger.warning(
                f"Media-index leaf collision on {leaf!r}: media "
                f"{existing_media.id} already claims it; ignoring media {media.id}."
            )
            return
        # Keep the first Media canonical for the entity's own metadata, but
        # record this item as an additional owner so its gallery still receives
        # the adjudicated entity (no dropped post→gallery joins).
        if all(owner.id != item.id for owner in owners):
            owners.append(item)

    async def _sweep_creator_files(self) -> AsyncIterator[BaseFile]:
        """Stream every Stash file under the creator's (Stash-visible) root.

        Polymorphic: yields VideoFile / ImageFile / GalleryFile / BasicFile with
        reverse fields populated on a capable server (resolve-on-demand otherwise).
        """
        if not self.state.base_path:
            return
        root = get_stash_path(self.state.base_path, self.config).rstrip("/")
        # Trailing sep anchors the scope: a bare 'root' substring would also pull
        # a sibling creator '/dl/annabelle/...' into '/dl/anna's sweep.
        async for file in self.store.find_iter(
            BaseFile,
            query_batch=self.config.stash_files_batch,
            path__contains=root + "/",
        ):
            yield file
