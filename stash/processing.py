"""Processing module for Stash integration."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import os
import re
import sys
import traceback
from asyncio import Queue
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from pprint import pformat
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import func, select
from tqdm import tqdm

from metadata import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Attachment,
    ContentType,
    Database,
    Group,
    Media,
    Message,
    Post,
    account_media_bundle_media,
    media_variants,
)
from metadata.decorators import with_session
from pathio import set_create_directory_for_download
from textio import print_error, print_info, print_warning
from textio.logging import SizeAndTimeRotatingFileHandler

from .client import StashClient
from .client_helpers import async_lru_cache
from .context import StashContext
from .logging import debug_print
from .logging import processing_logger as logger
from .types import (
    Gallery,
    GalleryChapter,
    GalleryFile,
    Image,
    ImageFile,
    Performer,
    Scene,
    Studio,
    Tag,
    VideoFile,
    VisualFile,
)

if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState


@runtime_checkable
class HasMetadata(Protocol):
    """Protocol for models that have metadata for Stash."""

    content: str | None
    createdAt: datetime
    attachments: list[Attachment]
    # Messages don't have accountMentions, only Posts do
    accountMentions: list[Account] | None = None


class StashProcessing:
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

    Attributes:
        config: Configuration instance
        state: State instance
        context: StashContext instance
        database: Database instance
        _background_task: Optional background task
        _cleanup_event: Optional cleanup event
        _owns_db: Whether this instance owns the database connection
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
        """Initialize StashProcessing."""
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
    ) -> StashProcessing:
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

    @with_session()
    async def _find_account(
        self,
        session: Session | None = None,
    ) -> Account | None:
        """Find account in database.

        Args:
            session: Optional database session to use

        Returns:
            Account if found, None otherwise
        """
        if self.state.creator_id is not None:
            stmt = select(Account).where(Account.id == int(self.state.creator_id))
        else:
            stmt = select(Account).where(
                func.lower(Account.username) == func.lower(self.state.creator_name)
            )
        result = await session.execute(stmt)
        account = result.scalar_one_or_none()
        if not account:
            print_warning(f"No account found for username: {self.state.creator_name}")
        return account

    @with_session()
    async def process_creator(
        self,
        session: Session | None = None,
    ) -> tuple[Account, Performer]:
        """Process creator metadata into Stash.

        Args:
            config: Optional config override (uses self.config if not provided)

        Returns:
            Tuple of (Account, Performer)

        Raises:
            ValueError: If creator_id is not available in state
        """
        try:
            # Find account
            account = await self._find_account(session)
            debug_print(
                {
                    "method": "StashProcessing - process_creator",
                    "account": account,
                }
            )
            if not account:
                raise ValueError(
                    f"No account found for creator: {self.state.creator_name} "
                    f"(ID: {self.state.creator_id})"
                )

            # Try to find existing performer
            performer = await self._find_existing_performer(account)
            if performer is None:
                # Create new performer
                performer = await Performer.from_account(account)
                await performer.save(self.context.client)

            debug_print(
                {
                    "method": "StashProcessing - process_creator",
                    "performer": performer,
                }
            )
            # Handle avatar if needed
            await self._update_performer_avatar(account, performer)

            return account, performer
        except Exception as e:
            print_error(f"Failed to process creator: {e}")
            logger.exception("Failed to process creator", exc_info=e)
            debug_print(
                {
                    "method": "StashProcessing - process_creator",
                    "status": "creator_processing_failed",
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }
            )
            raise

    async def _update_performer_avatar(
        self, account: Account, performer: Performer
    ) -> None:
        """Update performer's avatar if needed.

        Only updates the avatar if the current image is the default one.

        Args:
            account: Account object containing avatar information
            performer: Performer object to update
        """
        if (
            not await account.awaitable_attrs.avatar
            or not (await account.awaitable_attrs.avatar).local_filename
        ):
            debug_print(
                {
                    "method": "StashProcessing - _update_performer_avatar",
                    "status": "no_avatar_found",
                    "account": account.username,
                }
            )
            return

        # Only update if current image is default
        if not performer.image_path or "default=true" in performer.image_path:
            # Get avatar file path
            avatar_stash_obj = await self.context.client.find_images(
                image_filter={
                    "path": {
                        "modifier": "INCLUDES",
                        "value": account.avatar.local_filename,
                    }
                },
                filter_={
                    "per_page": -1,
                    "sort": "created_at",
                    "direction": "DESC",
                },
            )
            if avatar_stash_obj.count == 0:
                debug_print(
                    {
                        "method": "StashProcessing - _update_performer_avatar",
                        "status": "no_avatar_found",
                        "account": account.username,
                    }
                )
                return
            avatar = avatar_stash_obj.images[0]
            avatar_path = Path(avatar.visual_files[0].path)
            try:
                await performer.update_avatar(self.context.client, avatar_path)
                debug_print(
                    {
                        "method": "StashProcessing - _update_performer_avatar",
                        "status": "avatar_updated",
                        "performer": performer.name,
                    }
                )
            except Exception as e:
                print_error(f"Failed to update performer avatar: {e}")
                logger.exception("Failed to update performer avatar", exc_info=e)
                debug_print(
                    {
                        "method": "StashProcessing - _update_performer_avatar",
                        "status": "avatar_update_failed",
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
                )

    @async_lru_cache(maxsize=128)
    async def _find_existing_studio(self, account: Account) -> Studio | None:
        """Find existing studio in Stash.

        Args:
            account: Account to find studio for

        Returns:
            Studio data if found, None otherwise
        """
        # Use process_creator_studio with None performer
        return await self.process_creator_studio(account=account, performer=None)

    @async_lru_cache(maxsize=128)
    async def _find_existing_performer(self, account: Account) -> Performer | None:
        """Find existing performer in Stash.

        Args:
            account: Account to find performer for

        Returns:
            Performer data if found, None otherwise
        """
        # Try finding by stash_id first
        if account.stash_id:
            performer_data = await self.context.client.find_performer(account.stash_id)
            if performer_data:
                debug_print(
                    {
                        "method": "StashProcessing - _find_existing_performer",
                        "stash_id": account.stash_id,
                        "performer_data": performer_data,
                    }
                )
                # Await the coroutine if we got one
                if asyncio.iscoroutine(performer_data):
                    performer_data = await performer_data
                return performer_data or None
        performer_data = await self.context.client.find_performer(account.username)
        debug_print(
            {
                "method": "StashProcessing - _find_existing_performer",
                "username": account.username,
                "performer_data": performer_data,
            }
        )
        # Await the coroutine if we got one
        if asyncio.iscoroutine(performer_data):
            performer_data = await performer_data
        return performer_data or None

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
        # print_info("Continuing Stash GraphQL processing in the background...")
        try:
            if not account or not performer:
                raise ValueError("Missing account or performer data")
            # Convert dict to Performer if needed
            if isinstance(performer, dict):
                performer = Performer.from_dict(performer)
            elif not isinstance(performer, Performer):
                raise TypeError("performer must be a Stash Performer object or dict")

            # Ensure we have a fresh account instance bound to the session
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

        # TODO: Implement background processing
        # This will:
        # 1. Process posts to scenes
        # 2. Process messages to galleries
        # 3. Process media to images
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
            print_info(f"Finished Stash processing for {performer.name}")

    @with_session()
    async def process_creator_messages(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        """Process creator message metadata.

        This method:
        1. Retrieves message information from the database
        2. Creates galleries for messages with media in parallel
        3. Links media files to galleries
        4. Associates galleries with performer and studio

        Args:
            account: The Account object
            performer: The Performer object
            studio: Optional Studio object
        """

        def get_message_url(group: Group) -> str:
            """Get URL for a message in a group."""
            return f"https://fansly.com/messages/{group.id}"

        # Get a fresh account instance bound to the session
        stmt = select(Account).where(Account.id == account.id)
        result = await session.execute(stmt)
        account = result.scalar_one()

        # Get all messages with attachments in one query with relationships
        stmt = (
            select(Message)
            .join(Message.attachments)  # Join to filter messages with attachments
            .join(Message.group)
            .join(Group.users)
            .where(Group.users.any(Account.id == account.id))
            .options(
                selectinload(Message.attachments)
                .selectinload(Attachment.media)
                .selectinload(AccountMedia.media),
                selectinload(Message.attachments)
                .selectinload(Attachment.bundle)
                .selectinload(AccountMediaBundle.accountMedia)
                .selectinload(AccountMedia.media),
                selectinload(Message.group),
            )
        )
        debug_print(
            {
                "status": "building_message_query",
                "account_id": account.id,
                "statement": stmt,
            }
        )

        result = await session.execute(stmt)
        messages = result.unique().scalars().all()
        print_info(f"Processing {len(messages)} messages...")

        # Set up batch processing
        task_pbar, process_pbar, semaphore, queue = await self._setup_batch_processing(
            messages, "message"
        )

        batch_size = 25

        async def process_batch(batch: list[Message]) -> None:
            async with semaphore:
                try:
                    # Ensure all objects are bound to the session
                    for message in batch:
                        session.add(message)
                        for attachment in message.attachments:
                            session.add(attachment)
                            if attachment.media:
                                session.add(attachment.media)
                                if attachment.media.media:
                                    session.add(attachment.media.media)
                            if attachment.bundle:
                                session.add(attachment.bundle)
                                for account_media in attachment.bundle.accountMedia:
                                    session.add(account_media)
                                    if account_media.media:
                                        session.add(account_media.media)

                    # Refresh account before processing
                    await session.refresh(account)

                    # Process each message in the batch
                    for message in batch:
                        try:
                            await self._process_items_with_gallery(
                                account=account,
                                performer=performer,
                                studio=studio,
                                item_type="message",
                                items=[message],
                                url_pattern_func=get_message_url,
                                session=session,
                            )
                        except Exception as e:
                            print_error(f"Error processing message {message.id}: {e}")
                            logger.exception(
                                f"Error processing message {message.id}",
                                exc_info=e,
                                traceback=True,
                                stack_info=True,
                            )
                            debug_print(
                                {
                                    "method": "StashProcessing - process_creator_messages",
                                    "status": "message_processing_failed",
                                    "message_id": message.id,
                                    "error": str(e),
                                    "traceback": traceback.format_exc(),
                                }
                            )
                        finally:
                            process_pbar.update(1)
                except Exception as e:
                    print_error(f"Error processing batch: {e}")
                    logger.exception(
                        "Error processing batch",
                        exc_info=e,
                        traceback=True,
                        stack_info=True,
                    )
                    debug_print(
                        {
                            "method": "StashProcessing - process_creator_messages",
                            "status": "batch_processing_failed",
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                        }
                    )

        # Run the batch processor
        await self._run_batch_processor(
            items=messages,
            batch_size=batch_size,
            task_pbar=task_pbar,
            process_pbar=process_pbar,
            semaphore=semaphore,
            queue=queue,
            process_batch=process_batch,
        )

    @with_session()
    async def process_creator_posts(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        """Process creator post metadata.

        This method:
        1. Retrieves post information from the database in batches
        2. Processes posts into Stash galleries
        3. Handles media attachments and bundles

        Note: This method requires a session and will ensure all objects are properly bound to it.
        The performer and studio objects are Stash GraphQL types, not SQLAlchemy models.
        """
        # Ensure account is bound to the session
        session.add(account)

        # Get all posts with attachments in one query with relationships
        # First ensure we have a fresh account instance
        stmt = select(Account).where(Account.id == account.id)
        result = await session.execute(stmt)
        account = result.scalar_one()

        # Now get posts with proper eager loading
        # Get all posts with attachments - we only need IDs for batching
        stmt = (
            select(Post)
            .join(Post.attachments)  # Join to filter posts with attachments
            .where(Post.accountId == account.id)
            .options(
                selectinload(Post.attachments)
                .selectinload(Attachment.media)
                .selectinload(AccountMedia.media),
                selectinload(Post.attachments)
                .selectinload(Attachment.bundle)
                .selectinload(AccountMediaBundle.accountMedia)
                .selectinload(AccountMedia.media),
                selectinload(Post.accountMentions),
            )
        )
        debug_print(
            {
                "status": "building_post_query",
                "account_id": account.id,
                "statement": stmt,
            }
        )

        def get_post_url(post: Post) -> str:
            return f"https://fansly.com/post/{post.id}"

        result = await session.execute(stmt)
        posts = result.unique().scalars().all()
        print_info(f"Processing {len(posts)} posts...")

        # Set up batch processing
        task_pbar, process_pbar, semaphore, queue = await self._setup_batch_processing(
            posts, "post"
        )

        batch_size = 25

        async def process_batch(batch: list[Post]) -> None:
            async with semaphore:
                try:
                    # Ensure all objects are bound to the session
                    for post in batch:
                        session.add(post)
                        for attachment in post.attachments:
                            session.add(attachment)
                            if attachment.media:
                                session.add(attachment.media)
                                if attachment.media.media:
                                    session.add(attachment.media.media)
                            if attachment.bundle:
                                session.add(attachment.bundle)
                                for account_media in attachment.bundle.accountMedia:
                                    session.add(account_media)
                                    if account_media.media:
                                        session.add(account_media.media)

                    # Refresh account before processing
                    await session.refresh(account)

                    # Process each post in the batch
                    for post in batch:
                        try:
                            await self._process_items_with_gallery(
                                account=account,
                                performer=performer,
                                studio=studio,
                                item_type="post",
                                items=[post],
                                url_pattern_func=get_post_url,
                                session=session,
                            )
                        except Exception as e:
                            print_error(f"Error processing post {post.id}: {e}")
                            logger.exception(
                                f"Error processing post {post.id}",
                                exc_info=e,
                                traceback=True,
                                stack_info=True,
                            )
                            debug_print(
                                {
                                    "method": "StashProcessing - process_creator_posts",
                                    "status": "post_processing_failed",
                                    "post_id": post.id,
                                    "error": str(e),
                                    "traceback": traceback.format_exc(),
                                }
                            )
                        finally:
                            process_pbar.update(1)
                except Exception as e:
                    print_error(f"Error processing batch: {e}")
                    logger.exception(
                        "Error processing batch",
                        exc_info=e,
                        traceback=True,
                        stack_info=True,
                    )
                    debug_print(
                        {
                            "method": "StashProcessing - process_creator_posts",
                            "status": "batch_processing_failed",
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                        }
                    )

        # Run the batch processor
        await self._run_batch_processor(
            items=posts,
            batch_size=batch_size,
            task_pbar=task_pbar,
            process_pbar=process_pbar,
            semaphore=semaphore,
            queue=queue,
            process_batch=process_batch,
        )

    async def _setup_batch_processing(
        self,
        items: list[Any],
        item_type: str,
    ) -> tuple[tqdm, tqdm, asyncio.Semaphore, Queue]:
        """Set up common batch processing infrastructure.

        Args:
            items: List of items to process
            item_type: Type of items ("post" or "message")

        Returns:
            Tuple of (task_pbar, process_pbar, semaphore, queue)
        """
        # Create progress bars
        task_pbar = tqdm(
            total=len(items),
            desc=f"Adding {len(items)} {item_type} tasks",
            position=0,
            unit="task",
        )
        process_pbar = tqdm(
            total=len(items),
            desc=f"Processing {len(items)} {item_type}s",
            position=1,
            unit=item_type,
        )

        # Create semaphore and queue for concurrent processing
        max_concurrent = min(25, int(os.getenv("FDLNG_MAX_CONCURRENT", "25")))
        semaphore = asyncio.Semaphore(max_concurrent)
        queue = Queue(
            maxsize=max_concurrent * 4
        )  # Quadruple buffer for more throughput

        return task_pbar, process_pbar, semaphore, queue

    async def _run_batch_processor(
        self,
        items: list[Any],
        batch_size: int,
        task_pbar: tqdm,
        process_pbar: tqdm,
        semaphore: asyncio.Semaphore,
        queue: Queue,
        process_batch: callable,
    ) -> None:
        """Run batch processing with producer/consumer pattern.

        Args:
            items: List of items to process
            batch_size: Size of each batch
            task_pbar: Progress bar for task creation
            process_pbar: Progress bar for processing
            semaphore: Semaphore for concurrency control
            queue: Queue for producer/consumer pattern
            process_batch: Callback function to process each batch
        """
        max_concurrent = min(25, int(os.getenv("FDLNG_MAX_CONCURRENT", "25")))

        async def producer():
            # Process in batches
            for i in range(0, len(items), batch_size):
                batch = items[i : i + batch_size]
                await queue.put(batch)
                task_pbar.update(len(batch))
            # Signal consumers we're done
            for _ in range(max_concurrent):
                await queue.put(None)
            task_pbar.close()

        async def consumer():
            while True:
                batch = await queue.get()
                if batch is None:  # Sentinel value
                    queue.task_done()
                    break
                try:
                    await process_batch(batch)
                finally:
                    queue.task_done()

        try:
            # Start consumers
            consumers = [asyncio.create_task(consumer()) for _ in range(max_concurrent)]
            # Start producer
            producer_task = asyncio.create_task(producer())
            # Wait for all work to complete
            await queue.join()
            await producer_task
            await asyncio.gather(*consumers, return_exceptions=True)
        finally:
            process_pbar.close()

    @with_session()
    async def _process_items_with_gallery(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None,
        item_type: str,
        items: list[Message | Post],
        url_pattern_func: callable,
        session: Session | None = None,
    ) -> None:
        """Process items (posts or messages) with gallery.

        Args:
            account: The Account object
            performer: The Performer object
            studio: Optional Studio object
            item_type: Type of item being processed ("post" or "message")
            items: List of items to process (already loaded with relationships)
            url_pattern_func: Function to generate URLs for items
        """
        debug_print(
            {
                "method": f"StashProcessing - process_creator_{item_type}s",
                "state": "entry",
                "count": len(items),
            }
        )

        # Merge items into current session
        # First ensure we have a fresh account instance
        stmt = select(Account).where(Account.id == account.id)
        result = await session.execute(stmt)
        account = result.scalar_one()
        session.add(account)

        # Process each item (already merged in process_creator_posts)
        for item in items:
            try:
                debug_print(
                    {
                        "method": f"StashProcessing - process_creator_{item_type}s",
                        "status": f"processing_{item_type}",
                        f"{item_type}_id": item.id,
                        "attachment_count": (
                            len(item.attachments) if hasattr(item, "attachments") else 0
                        ),
                    }
                )
                await self._process_item_gallery(
                    item=item,
                    account=account,
                    performer=performer,
                    studio=studio,
                    item_type=item_type,
                    url_pattern=url_pattern_func(item),
                    session=session,
                )
                debug_print(
                    {
                        "method": f"StashProcessing - process_creator_{item_type}s",
                        "status": f"{item_type}_processed",
                        f"{item_type}_id": item.id,
                        "attachment_count": (
                            len(item.attachments) if hasattr(item, "attachments") else 0
                        ),
                    }
                )
            except Exception as e:
                print_error(f"Failed to process {item_type} {item.id}: {e}")
                logger.exception(f"Failed to process {item_type} {item.id}", exc_info=e)
                debug_print(
                    {
                        "method": f"StashProcessing - process_creator_{item_type}s",
                        "status": f"{item_type}_processing_failed",
                        f"{item_type}_id": item.id,
                        "attachment_count": (
                            len(item.attachments) if hasattr(item, "attachments") else 0
                        ),
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
                )
                continue

    async def _process_item_gallery(
        self,
        item: HasMetadata,
        account: Account,
        performer: Performer,
        studio: Studio | None,
        item_type: str,
        url_pattern: str,
        session: Session | None = None,
    ) -> None:
        """Process a single item's gallery.

        Args:
            item: Item to process
            account: Account that owns the item
            performer: Performer to associate with gallery
            studio: Optional studio to associate with gallery
            item_type: Type of item ("post" or "message")
            url_pattern: URL pattern for the item
            session: Optional database session
        """
        debug_print(
            {
                "method": "StashProcessing - _process_item_gallery",
                "status": "entry",
                "item_id": item.id,
                "item_type": item_type,
                "attachment_count": (
                    len(item.attachments) if hasattr(item, "attachments") else 0
                ),
            }
        )

        async with contextlib.AsyncExitStack() as stack:
            if session is None:
                session = await stack.enter_async_context(
                    self.database.get_async_session()
                )

            attachments: list[Attachment] = await item.awaitable_attrs.attachments or []
            debug_print(
                {
                    "method": "StashProcessing - _process_item_gallery",
                    "status": "got_attachments",
                    "item_id": item.id,
                    "attachment_count": len(attachments),
                    "attachment_ids": (
                        [a.id for a in attachments] if attachments else []
                    ),
                }
            )
            if not attachments:
                debug_print(
                    {
                        "method": "StashProcessing - _process_item_gallery",
                        "status": "no_attachments",
                        "item_id": item.id,
                    }
                )
                return

            debug_print(
                {
                    "method": "StashProcessing - _process_item_gallery",
                    "status": "processing_attachments",
                    "item_id": item.id,
                    "attachment_count": len(attachments),
                    "attachment_ids": [a.id for a in attachments],
                }
            )

            debug_print(
                {
                    "method": "StashProcessing - _process_item_gallery",
                    "status": "creating_gallery",
                    "item_id": item.id,
                }
            )
            gallery = await self._get_or_create_gallery(
                item=item,
                account=account,
                performer=performer,
                studio=studio,
                item_type=item_type,
                url_pattern=url_pattern,
            )
            if not gallery:
                debug_print(
                    {
                        "method": "StashProcessing - _process_item_gallery",
                        "status": "gallery_creation_failed",
                        "item_id": item.id,
                    }
                )
                return
            debug_print(
                {
                    "method": "StashProcessing - _process_item_gallery",
                    "status": "gallery_created",
                    "item_id": item.id,
                    "gallery_id": gallery.id if gallery else None,
                }
            )

            # Process attachments and add files to gallery
            files = []
            for i, attachment in enumerate(attachments, 1):
                debug_print(
                    {
                        "method": "StashProcessing - _process_item_gallery",
                        "status": "processing_attachment",
                        "item_id": item.id,
                        "attachment_id": attachment.id,
                        "progress": f"{i}/{len(attachments)}",
                    }
                )
                try:
                    debug_print(
                        {
                            "method": "StashProcessing - _process_item_gallery",
                            "status": "attachment_details",
                            "item_id": item.id,
                            "attachment_id": attachment.id,
                            "content_id": getattr(attachment, "contentId", None),
                            "content_type": getattr(attachment, "contentType", None),
                        }
                    )
                    attachment_files = await self.process_creator_attachment(
                        attachment=attachment,
                        item=item,
                        account=account,
                        session=session,
                    )
                    if attachment_files:
                        files.extend(attachment_files)
                        debug_print(
                            {
                                "method": "StashProcessing - _process_item_gallery",
                                "status": "attachment_processed",
                                "item_id": item.id,
                                "attachment_id": attachment.id,
                                "progress": f"{i}/{len(attachments)}",
                                "files_added": len(attachment_files),
                                "file_details": [f for f in attachment_files],
                            }
                        )
                    else:
                        debug_print(
                            {
                                "method": "StashProcessing - _process_item_gallery",
                                "status": "attachment_skipped",
                                "item_id": item.id,
                                "attachment_id": attachment.id,
                                "progress": f"{i}/{len(attachments)}",
                            }
                        )
                except Exception as e:
                    debug_print(
                        {
                            "method": "StashProcessing - _process_item_gallery",
                            "status": "attachment_failed",
                            "item_id": item.id,
                            "attachment_id": attachment.id,
                            "progress": f"{i}/{len(attachments)}",
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                        }
                    )

            if not files:
                # No files were processed, delete the gallery if we just created it
                if gallery.id == "new":
                    debug_print(
                        {
                            "method": "StashProcessing - _process_item_gallery",
                            "status": "deleting_empty_gallery",
                            "item_id": item.id,
                            "gallery_id": gallery.id,
                        }
                    )
                    await gallery.destroy(self.context.client)
                return

            # Create GalleryFile objects for each file
            gallery_files = []
            for file in files:
                if not isinstance(file, (ImageFile, VideoFile)):
                    # Create GalleryFile from BaseFile
                    gallery_file = GalleryFile(
                        id=file.get("id", None),
                        path=file.get("path", None),
                        basename=file.get("basename", None),
                        parent_folder_id=file.get("parent_folder_id", None),
                        zip_file_id=file.get("zip_file_id", None),
                        mod_time=file.get("mod_time", None),
                        size=file.get("size", None),
                        fingerprints=file.get("fingerprints", None),
                        # created_at and updated_at handled by Stash
                    )
                    gallery_files.append(gallery_file)
                else:
                    gallery_files.append(file)

            debug_print(
                {
                    "method": "StashProcessing - _process_item_gallery",
                    "status": "files_summary",
                    "item_id": item.id,
                    "gallery_id": gallery.id,
                    "total_files": len(files),
                    "gallery_files": len(gallery_files),
                    "file_details": [f for f in gallery_files],
                }
            )

            # Add files to gallery
            if gallery_files:
                try:
                    # Add files to gallery
                    gallery.files = gallery_files
                    await gallery.save(self.context.client)

                    # For images, also establish the Image -> Gallery relationship
                    image_files = [f for f in files if isinstance(f, ImageFile)]
                    if image_files:
                        # Try up to 3 times with increasing delays
                        for attempt in range(3):
                            try:
                                success = await self.context.client.add_gallery_images(
                                    gallery_id=gallery.id,
                                    image_ids=[f.id for f in image_files],
                                )
                                if success:
                                    debug_print(
                                        {
                                            "method": "StashProcessing - _process_item_gallery",
                                            "status": "gallery_images_added",
                                            "item_id": item.id,
                                            "gallery_id": gallery.id,
                                            "success": success,
                                            "image_count": len(image_files),
                                            "attempt": attempt + 1,
                                        }
                                    )
                                    break
                                else:
                                    debug_print(
                                        {
                                            "method": "StashProcessing - _process_item_gallery",
                                            "status": "gallery_images_add_failed",
                                            "item_id": item.id,
                                            "gallery_id": gallery.id,
                                            "attempt": attempt + 1,
                                            "image_count": len(image_files),
                                        }
                                    )
                                    if attempt < 2:  # Don't sleep on last attempt
                                        await asyncio.sleep(
                                            2**attempt
                                        )  # Exponential backoff
                            except Exception as e:
                                logger.exception(
                                    f"Failed to add gallery images for {item_type} {item.id}",
                                    exc_info=e,
                                )
                                debug_print(
                                    {
                                        "method": "StashProcessing - _process_item_gallery",
                                        "status": "gallery_images_add_error",
                                        "item_id": item.id,
                                        "gallery_id": gallery.id,
                                        "attempt": attempt + 1,
                                        "error": str(e),
                                        "traceback": traceback.format_exc(),
                                    }
                                )
                                if attempt < 2:  # Don't sleep on last attempt
                                    await asyncio.sleep(
                                        2**attempt
                                    )  # Exponential backoff
                except Exception as e:
                    logger.exception(
                        f"Failed to save gallery files for {item_type} {item.id}",
                        exc_info=e,
                    )
                    debug_print(
                        {
                            "method": "StashProcessing - _process_item_gallery",
                            "status": "gallery_files_error",
                            "item_id": item.id,
                            "gallery_id": gallery.id,
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                        }
                    )

    async def _get_gallery_by_stash_id(
        self,
        item: HasMetadata,
    ) -> Gallery | None:
        """Try to find gallery by stash_id."""
        if not item.stash_id:
            return None

        gallery = await self.context.client.find_gallery(item.stash_id)
        if gallery:
            debug_print(
                {
                    "method": "StashProcessing - _get_gallery_by_stash_id",
                    "status": "found",
                    "item_id": item.id,
                    "gallery_id": gallery.id,
                }
            )
        return gallery

    async def _get_gallery_by_title(
        self,
        item: HasMetadata,
        title: str,
        studio: Studio | None,
    ) -> Gallery | None:
        """Try to find gallery by title and metadata."""
        galleries = await self.context.client.find_galleries(
            gallery_filter={
                "title": {
                    "value": title,
                    "modifier": "EQUALS",
                }
            }
        )
        if not galleries or galleries.count == 0:
            return None

        for gallery_dict in galleries.galleries:
            gallery = Gallery(**gallery_dict)
            debug_print(
                {
                    "method": "StashProcessing - _get_gallery_by_title",
                    "gallery_studio_type": (
                        type(gallery.studio).__name__ if gallery.studio else None
                    ),
                    "gallery_studio": gallery.studio,
                }
            )
            if (
                gallery.title == title
                and gallery.date == item.createdAt.strftime("%Y-%m-%d")
                and (
                    not studio
                    or (gallery.studio and gallery.studio.get("id") == studio.id)
                )
            ):
                debug_print(
                    {
                        "method": "StashProcessing - _get_gallery_by_title",
                        "status": "found",
                        "item_id": item.id,
                        "gallery_id": gallery.id,
                    }
                )
                item.stash_id = gallery.id
                return gallery
        return None

    async def _get_gallery_by_code(
        self,
        item: HasMetadata,
    ) -> Gallery | None:
        """Try to find gallery by code (post/message ID)."""
        galleries = await self.context.client.find_galleries(
            gallery_filter={
                "code": {
                    "value": str(item.id),
                    "modifier": "EQUALS",
                }
            }
        )
        if not galleries or galleries.count == 0:
            return None

        for gallery_dict in galleries.galleries:
            gallery = Gallery(**gallery_dict)
            if gallery.code == str(item.id):
                debug_print(
                    {
                        "method": "StashProcessing - _get_gallery_by_code",
                        "status": "found",
                        "item_id": item.id,
                        "gallery_id": gallery.id,
                    }
                )
                item.stash_id = gallery.id
                return gallery
        return None

    async def _get_gallery_by_url(
        self,
        item: HasMetadata,
        url: str,
    ) -> Gallery | None:
        """Try to find gallery by URL."""
        galleries = await self.context.client.find_galleries(
            gallery_filter={
                "url": {
                    "value": url,
                    "modifier": "EQUALS",
                }
            }
        )
        if not galleries or galleries.count == 0:
            return None

        for gallery_dict in galleries.galleries:
            gallery = Gallery(**gallery_dict)
            if gallery.url == url:
                debug_print(
                    {
                        "method": "StashProcessing - _get_gallery_by_url",
                        "status": "found",
                        "item_id": item.id,
                        "gallery_id": gallery.id,
                    }
                )
                item.stash_id = gallery.id
                gallery.code = str(item.id)
                await gallery.save(self.context.client)
                return gallery
        return None

    async def _create_new_gallery(
        self,
        item: HasMetadata,
        title: str,
    ) -> Gallery:
        """Create a new gallery with basic fields."""
        debug_print(
            {
                "method": "StashProcessing - _create_new_gallery",
                "status": "creating",
                "item_id": item.id,
            }
        )
        return Gallery(
            id="new",  # Will be replaced on save
            title=title,
            details=item.content,
            code=str(item.id),  # Use post/message ID as code for uniqueness
            date=item.createdAt.strftime("%Y-%m-%d"),
            # created_at and updated_at handled by Stash
            organized=True,  # Mark as organized since we have metadata
        )

    async def _get_gallery_metadata(
        self,
        item: HasMetadata,
        account: Account,
        url_pattern: str,
    ) -> tuple[str, str, str]:
        """Get metadata needed for gallery operations.

        Args:
            item: The item to process
            account: The Account object
            url_pattern: URL pattern for the item

        Returns:
            Tuple of (username, title, url)
        """
        # Get username
        username = (
            await account.awaitable_attrs.username
            if hasattr(account, "awaitable_attrs")
            else account.username
        )

        # Generate title
        title = self._generate_title_from_content(
            content=item.content,
            username=username,
            created_at=item.createdAt,
        )

        # Generate URL
        url = url_pattern.format(username=username, id=item.id)

        return username, title, url

    async def _setup_gallery_performers(
        self,
        gallery: Gallery,
        item: HasMetadata,
        performer: Performer,
    ) -> None:
        """Set up performers for a gallery.

        Args:
            gallery: Gallery to set up
            item: Source item with mentions
            performer: Main performer
        """
        performers = []

        # Add main performer
        if performer:
            if hasattr(performer, "awaitable_attrs"):
                await performer.awaitable_attrs.id
            performers.append(performer)

        # Add mentioned accounts as performers
        if hasattr(item, "accountMentions") and item.accountMentions:
            for mention in item.accountMentions:
                if mention_performer := await self._find_existing_performer(mention):
                    performers.append(mention_performer)

        # Set performers if we have any
        if performers:
            gallery.performers = performers

    async def _check_aggregated_posts(self, posts: list[Post]) -> bool:
        """Check if any aggregated posts have media content.

        Args:
            posts: List of posts to check

        Returns:
            True if any post has media content, False otherwise
        """
        for post in posts:
            if await self._has_media_content(post):
                return True
        return False

    async def _has_media_content(self, item: HasMetadata) -> bool:
        """Check if an item has media content that needs a gallery.

        Args:
            item: The item to check

        Returns:
            True if the item has media content, False otherwise
        """
        # Check for attachments
        if hasattr(item, "attachments") and item.attachments:
            for attachment in item.attachments:
                # Direct media content
                if attachment.contentType in (
                    ContentType.ACCOUNT_MEDIA,
                    ContentType.ACCOUNT_MEDIA_BUNDLE,
                ):
                    debug_print(
                        {
                            "method": "StashProcessing - _has_media_content",
                            "status": "has_media",
                            "item_id": item.id,
                            "content_type": attachment.contentType.name,
                        }
                    )
                    return True

                # Aggregated posts (which might contain media)
                if attachment.contentType == ContentType.AGGREGATED_POSTS:
                    if post := await attachment.resolve_content():
                        if await self._check_aggregated_posts([post]):
                            debug_print(
                                {
                                    "method": "StashProcessing - _has_media_content",
                                    "status": "has_aggregated_media",
                                    "item_id": item.id,
                                    "post_id": post.id,
                                }
                            )
                            return True

        debug_print(
            {
                "method": "StashProcessing - _has_media_content",
                "status": "no_media",
                "item_id": item.id,
            }
        )
        return False

    async def _get_or_create_gallery(
        self,
        item: HasMetadata,
        account: Account,
        performer: Performer,
        studio: Studio | None,
        item_type: str,
        url_pattern: str,
    ) -> Gallery | None:
        """Get or create a gallery for an item.

        Args:
            item: The item to process
            account: The Account object
            performer: The Performer object
            studio: The Studio object
            item_type: Type of item ("post" or "message")
            url_pattern: URL pattern for the item

        Returns:
            Gallery object or None if creation fails or item has no media
        """
        # Only create/get gallery if there's media content
        if not await self._has_media_content(item):
            debug_print(
                {
                    "method": "StashProcessing - _get_or_create_gallery",
                    "status": "skipped_no_media",
                    "item_id": item.id,
                }
            )
            return None
        # Get metadata needed for all operations
        username, title, url = await self._get_gallery_metadata(
            item, account, url_pattern
        )

        # Try each search method in order
        for method in [
            lambda: self._get_gallery_by_stash_id(item),
            lambda: self._get_gallery_by_code(item),
            lambda: self._get_gallery_by_title(item, title, studio),
            lambda: self._get_gallery_by_url(item, url),
        ]:
            if gallery := await method():
                return gallery

        # Create new gallery if none found
        gallery = await self._create_new_gallery(item, title)

        # Set up performers
        await self._setup_gallery_performers(gallery, item, performer)

        # Set studio if provided
        if studio:
            if hasattr(studio, "awaitable_attrs"):
                await studio.awaitable_attrs.id
            gallery.studio = studio

        # Set URL and save
        gallery.urls = [url_pattern]

        # Add chapters for aggregated posts
        if hasattr(item, "attachments"):
            image_index = 0
            for attachment in item.attachments:
                if attachment.contentType == ContentType.AGGREGATED_POSTS:
                    if post := await attachment.resolve_content():
                        # Only create chapter if post has media
                        if await self._has_media_content(post):
                            # Generate chapter title using same method as gallery title
                            title = self._generate_title_from_content(
                                content=post.content,
                                username=username,  # Use same username as parent
                                created_at=post.createdAt,
                            )

                            # Create chapter
                            chapter = GalleryChapter(
                                id="new",
                                gallery=gallery,
                                title=title,
                                image_index=image_index,
                            )
                            gallery.chapters.append(chapter)
                            image_index += 1  # Increment for next chapter

        # Save gallery with chapters
        await gallery.save(self.context.client)
        return gallery

    @with_session()
    async def process_creator_attachment(
        self,
        attachment: Attachment,
        item: HasMetadata,
        account: Account,
        session: Session | None = None,
    ) -> list[VisualFile]:
        """Process attachment into VisualFile objects.

        Args:
            attachment: Attachment object to process
            session: Optional database session to use
            item: Post or Message containing the attachment
            account: Account that created the content

        Returns:
            List of VisualFile objects created from the attachment
        """
        files = []

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
        if attachment.media and attachment.media.media:
            # Process media and update metadata
            media = attachment.media.media
            if hasattr(media, "awaitable_attrs"):
                await media.awaitable_attrs.variants
                await media.awaitable_attrs.mimetype  # Load mimetype like we do for bundle media

            debug_print(
                {
                    "method": "StashProcessing - process_creator_attachment",
                    "status": "processing_media",
                    "attachment_id": attachment.id,
                    "media_id": media.id,
                    "variant_count": (
                        len(media.variants) if hasattr(media, "variants") else 0
                    ),
                    "variants": (
                        [v.id for v in media.variants]
                        if hasattr(media, "variants")
                        else []
                    ),
                }
            )

            # Find in Stash by path and update metadata
            result = None
            if media.stash_id:
                result = await self._find_stash_files_by_id(
                    [(media.stash_id, media.mimetype)]
                )
            else:
                # Collect all media IDs (original + variants)
                media_files = [(str(media.id), media.mimetype)]
                if hasattr(media, "variants") and media.variants:
                    media_files.extend((str(v.id), v.mimetype) for v in media.variants)
                debug_print(
                    {
                        "method": "StashProcessing - process_creator_attachment",
                        "status": "searching_media_files",
                        "media_files": media_files,
                    }
                )
                result = await self._find_stash_files_by_path(media_files)

            # Update metadata and collect files
            for stash_obj, file in result:
                await self._update_stash_metadata(
                    stash_obj=stash_obj,
                    item=item,
                    account=account,
                    media_id=str(media.id),
                )
                files.append(file)
        # Handle preview media
        if attachment.media and attachment.media.preview:
            preview_media = attachment.media.preview
            if hasattr(preview_media, "awaitable_attrs"):
                await preview_media.awaitable_attrs.variants
                await preview_media.awaitable_attrs.mimetype

            debug_print(
                {
                    "method": "StashProcessing - process_creator_attachment",
                    "status": "processing_preview_media",
                    "attachment_id": attachment.id,
                    "preview_media_id": preview_media.id,
                    "variant_count": (
                        len(preview_media.variants)
                        if hasattr(preview_media, "variants")
                        else 0
                    ),
                    "variants": (
                        [v.id for v in preview_media.variants]
                        if hasattr(preview_media, "variants")
                        else []
                    ),
                }
            )

            # Find in Stash by path and update metadata for preview
            preview_result = None
            if preview_media.stash_id:
                preview_result = await self._find_stash_files_by_id(
                    [(preview_media.stash_id, preview_media.mimetype)]
                )
            else:
                # Collect all preview media IDs (original + variants)
                preview_media_files = [(str(preview_media.id), preview_media.mimetype)]
                if hasattr(preview_media, "variants") and preview_media.variants:
                    preview_media_files.extend(
                        (str(v.id), v.mimetype) for v in preview_media.variants
                    )
                debug_print(
                    {
                        "method": "StashProcessing - process_creator_attachment",
                        "status": "searching_preview_media_files",
                        "preview_media_files": preview_media_files,
                    }
                )
                preview_result = await self._find_stash_files_by_path(
                    preview_media_files
                )

            # Update metadata and collect files for preview
            for stash_obj, file in preview_result:
                await self._update_stash_metadata(
                    stash_obj=stash_obj,
                    item=item,
                    account=account,
                    media_id=str(preview_media.id),
                )
                files.append(file)

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
        if hasattr(attachment, "awaitable_attrs"):
            await attachment.awaitable_attrs.bundle
        if attachment.bundle:
            bundle: AccountMediaBundle = attachment.bundle
            if hasattr(bundle, "awaitable_attrs"):
                await bundle.awaitable_attrs.accountMedia

            debug_print(
                {
                    "method": "StashProcessing - process_creator_attachment",
                    "status": "processing_bundle",
                    "attachment_id": attachment.id,
                    "bundle_id": bundle.id,
                    "media_count": (
                        len(bundle.accountMedia)
                        if hasattr(bundle, "accountMedia")
                        else 0
                    ),
                }
            )

            # Process each media item in the bundle
            for account_media in bundle.accountMedia:
                if account_media.media:
                    # Load variants for bundle media
                    if hasattr(account_media.media, "awaitable_attrs"):
                        await account_media.media.awaitable_attrs.variants
                        await account_media.media.awaitable_attrs.mimetype

                    # Find in Stash by path and update metadata
                    result = None
                    if account_media.media.stash_id:
                        result = await self._find_stash_files_by_id(
                            [
                                (
                                    account_media.media.stash_id,
                                    account_media.media.mimetype,
                                )
                            ]
                        )
                    else:
                        # Collect all media IDs (original + variants)
                        media_files = [
                            (str(account_media.media.id), account_media.media.mimetype)
                        ]
                        if (
                            hasattr(account_media.media, "variants")
                            and account_media.media.variants
                        ):
                            variant_files = [
                                (str(v.id), v.mimetype)
                                for v in account_media.media.variants
                                if hasattr(v, "mimetype") and v.mimetype
                            ]
                            if variant_files:
                                media_files.extend(variant_files)
                                debug_print(
                                    {
                                        "method": "StashProcessing - process_creator_attachment",
                                        "status": "including_bundle_variants",
                                        "bundle_id": bundle.id,
                                        "original_id": account_media.media.id,
                                        "variant_count": len(variant_files),
                                        "variant_ids": [v[0] for v in variant_files],
                                    }
                                )

                        debug_print(
                            {
                                "method": "StashProcessing - process_creator_attachment",
                                "status": "searching_bundle_media_files",
                                "bundle_id": bundle.id,
                                "media_files": media_files,
                            }
                        )
                        result = await self._find_stash_files_by_path(media_files)

                    # Update metadata and collect files
                    for stash_obj, file in result:
                        await self._update_stash_metadata(
                            stash_obj=stash_obj,
                            item=item,
                            account=account,
                            media_id=str(account_media.media.id),
                        )
                        files.append(file)

                # Handle preview media for each AccountMedia in the bundle
                if account_media.preview:
                    preview_media = account_media.preview
                    if hasattr(preview_media, "awaitable_attrs"):
                        await preview_media.awaitable_attrs.variants
                        await preview_media.awaitable_attrs.mimetype

                    debug_print(
                        {
                            "method": "StashProcessing - process_creator_attachment",
                            "status": "processing_account_media_preview",
                            "bundle_id": bundle.id,
                            "account_media_id": account_media.id,
                            "preview_media_id": preview_media.id,
                            "variant_count": (
                                len(preview_media.variants)
                                if hasattr(preview_media, "variants")
                                else 0
                            ),
                            "variants": (
                                [v.id for v in preview_media.variants]
                                if hasattr(preview_media, "variants")
                                else []
                            ),
                        }
                    )

                    # Find in Stash by path and update metadata for preview
                    preview_result = None
                    if preview_media.stash_id:
                        preview_result = await self._find_stash_files_by_id(
                            [(preview_media.stash_id, preview_media.mimetype)]
                        )
                    else:
                        # Collect all preview media IDs (original + variants)
                        preview_media_files = [
                            (str(preview_media.id), preview_media.mimetype)
                        ]
                        if (
                            hasattr(preview_media, "variants")
                            and preview_media.variants
                        ):
                            preview_media_files.extend(
                                (str(v.id), v.mimetype) for v in preview_media.variants
                            )
                        debug_print(
                            {
                                "method": "StashProcessing - process_creator_attachment",
                                "status": "searching_account_media_preview_files",
                                "preview_media_files": preview_media_files,
                            }
                        )
                        preview_result = await self._find_stash_files_by_path(
                            preview_media_files
                        )

                    # Update metadata and collect files for preview
                    for stash_obj, file in preview_result:
                        await self._update_stash_metadata(
                            stash_obj=stash_obj,
                            item=item,
                            account=account,
                            media_id=str(preview_media.id),
                        )
                        files.append(file)

            if bundle.preview:
                preview_media = bundle.preview
                if hasattr(preview_media, "awaitable_attrs"):
                    await preview_media.awaitable_attrs.variants
                    await preview_media.awaitable_attrs.mimetype

                debug_print(
                    {
                        "method": "StashProcessing - process_creator_attachment",
                        "status": "processing_bundle_preview_media",
                        "bundle_id": bundle.id,
                        "preview_media_id": preview_media.id,
                        "variant_count": (
                            len(preview_media.variants)
                            if hasattr(preview_media, "variants")
                            else 0
                        ),
                        "variants": (
                            [v.id for v in preview_media.variants]
                            if hasattr(preview_media, "variants")
                            else []
                        ),
                    }
                )

                # Find in Stash by path and update metadata for bundle preview
                preview_result = None
                if preview_media.stash_id:
                    preview_result = await self._find_stash_files_by_id(
                        [(preview_media.stash_id, preview_media.mimetype)]
                    )
                else:
                    # Collect all preview media IDs (original + variants)
                    preview_media_files = [
                        (str(preview_media.id), preview_media.mimetype)
                    ]
                    if hasattr(preview_media, "variants") and preview_media.variants:
                        preview_media_files.extend(
                            (str(v.id), v.mimetype) for v in preview_media.variants
                        )
                    debug_print(
                        {
                            "method": "StashProcessing - process_creator_attachment",
                            "status": "searching_bundle_preview_media_files",
                            "preview_media_files": preview_media_files,
                        }
                    )
                    preview_result = await self._find_stash_files_by_path(
                        preview_media_files
                    )

                # Update metadata and collect files for bundle preview
                for stash_obj, file in preview_result:
                    await self._update_stash_metadata(
                        stash_obj=stash_obj,
                        item=item,
                        account=account,
                        media_id=str(preview_media.id),
                    )
                    files.append(file)

        # Handle aggregated posts
        debug_print(
            {
                "method": "StashProcessing - process_creator_attachment",
                "status": "checking_aggregated",
                "attachment_id": attachment.id,
                "has_aggregated_post": hasattr(attachment, "aggregated_post"),
            }
        )

        # Load aggregated post attributes
        if hasattr(attachment, "awaitable_attrs"):
            await attachment.awaitable_attrs.is_aggregated_post
            if getattr(attachment, "is_aggregated_post", False):
                await attachment.awaitable_attrs.aggregated_post

        # Process if it's an aggregated post
        if (
            getattr(attachment, "is_aggregated_post", False)
            and attachment.aggregated_post
        ):
            agg_post: Post = attachment.aggregated_post

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
                    agg_files = await self.process_creator_attachment(
                        attachment=agg_attachment,
                        item=agg_post,
                        account=account,
                        session=session,
                    )
                    files.extend(agg_files)

        return files

    @with_session()
    async def _process_media_to_files(
        self,
        media: Media,
        session: Session | None = None,
    ) -> list[VisualFile]:
        """Process media into VisualFile objects.

        Args:
            media: Media object to process
            session: Database session to use

        Returns:
            List of VisualFile objects created from the media
        """
        files = []

        # Process variants as potential previews
        if await media.awaitable_attrs.variants:
            variants = await media.awaitable_attrs.variants
            if variants:
                # Use the smallest variant as preview
                preview_variant = min(variants, key=lambda v: v.width or float("inf"))
                result = await self._process_media_file(
                    preview_variant,
                    media,
                    is_preview=True,
                )
                if result:
                    stash_obj, file = result
                    if stash_obj and file:
                        await self._update_file_metadata(
                            file=file,
                            media_obj=media,
                            is_preview=True,
                            session=session,
                        )
                        files.append(file)

        # Process main media
        result = await self._process_media_file(
            media,
            media,  # Use same media as parent for metadata
        )
        if result:
            stash_obj, file = result
            if stash_obj and file:
                await self._update_file_metadata(
                    file=file,
                    media_obj=media,
                    session=session,
                )
                files.append(file)

        return files

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
            if stash_obj.visual_files:
                for file in stash_obj.visual_files:
                    if isinstance(file, ImageFile):
                        return file
        elif isinstance(stash_obj, Scene):
            # Get the primary VideoFile
            if stash_obj.files:
                return stash_obj.files[0]
        return None

    async def _process_media_file(
        self,
        media_file: Any,  # AccountMedia.media or AccountMedia.preview
        media_obj: AccountMedia,
        is_preview: bool = False,
        session: AsyncSession | None = None,
    ) -> tuple[dict | None, ImageFile | VideoFile | None]:
        """Process a media file from AccountMedia.

        Args:
            media_file: The media file to process
            media_obj: The parent AccountMedia object
            is_preview: Whether this is a preview file
            session: Optional database session to use

        Returns:
            Tuple of (raw stash object, processed file object)
        """
        try:
            if media_file.stash_id:
                return await self._find_stash_file_by_id(
                    stash_id=media_file.stash_id,
                    mime_type=media_file.mimetype,
                    media_obj=media_obj,
                    session=session,
                )
            elif media_file.local_filename or await media_file.awaitable_attrs.variants:
                return await self._find_stash_file_by_path(
                    media_file=media_file,
                    mime_type=media_file.mimetype,
                    media_obj=media_obj,
                    session=session,
                )
        except Exception as e:
            debug_print(
                {
                    "method": "StashProcessing - _process_media_file",
                    "status": "media_processing_failed",
                    "media_id": media_file.id if hasattr(media_file, "id") else None,
                    "error": str(e),
                }
            )
            return None, None

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
        if len(conditions) == 2:
            return {
                "OR": {
                    "path": conditions[0]["path"],
                    "OR": conditions[1],
                }
            }
        else:
            return {
                "OR": {
                    "path": conditions[0]["path"],
                    "OR": self._create_nested_path_or_conditions(media_ids[1:])["OR"],
                }
            }

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
                if results.count > 0:
                    for image_data in results.images:
                        image = (
                            Image(**image_data)
                            if isinstance(image_data, dict)
                            else image_data
                        )
                        if file := self._get_file_from_stash_obj(image):
                            found.append((image, file))
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
        item: HasMetadata,  # Post or Message
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
        # Update basic metadata
        stash_obj.title = self._generate_title_from_content(
            content=item.content,
            username=account.username,
            created_at=item.createdAt,
        )
        stash_obj.details = item.content
        stash_obj.date = item.createdAt.strftime("%Y-%m-%d")
        stash_obj.code = str(media_id)

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
                        mention_performer = await Performer.from_account(mention)
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
                tags = []
                for hashtag in item.hashtags:
                    # Try to find existing tag by exact name or alias
                    tag_name = hashtag.value.lower()  # Case-insensitive comparison
                    found_tag = None

                    # First try exact name match
                    name_results = await self.context.client.find_tags(
                        tag_filter={
                            "name": {"value": tag_name, "modifier": "INCLUDES"}
                        },
                    )
                    if name_results.count > 0:
                        # Convert dict to Tag object using unpacking
                        found_tag = Tag(**name_results.tags[0])
                        debug_print(
                            {
                                "method": "StashProcessing - _update_stash_metadata",
                                "status": "tag_found_by_name",
                                "tag_name": tag_name,
                                "found_tag": found_tag.name,
                            }
                        )
                    else:
                        # Then try alias match
                        alias_results = await self.context.client.find_tags(
                            tag_filter={
                                "aliases": {"value": tag_name, "modifier": "INCLUDES"}
                            },
                        )
                        if alias_results.count > 0:
                            # Convert dict to Tag object using unpacking
                            found_tag = Tag(**alias_results.tags[0])
                            debug_print(
                                {
                                    "method": "StashProcessing - _update_stash_metadata",
                                    "status": "tag_found_by_alias",
                                    "tag_name": tag_name,
                                    "found_tag": found_tag.name,
                                }
                            )

                    if found_tag:
                        tags.append(found_tag)
                    else:
                        # Create new tag if not found
                        new_tag = Tag(name=tag_name, id="new")
                        try:
                            if created_tag := await self.context.client.create_tag(
                                new_tag
                            ):
                                tags.append(created_tag)
                                debug_print(
                                    {
                                        "method": "StashProcessing - _update_stash_metadata",
                                        "status": "tag_created",
                                        "tag_name": hashtag.value,
                                    }
                                )
                        except Exception as e:
                            error_message = str(e)
                            # If tag already exists, it will be handled by create_tag
                            if (
                                "tag with name" in error_message
                                and "already exists" in error_message
                            ):
                                if found_tag := await self.context.client.create_tag(
                                    new_tag
                                ):
                                    tags.append(found_tag)
                                    debug_print(
                                        {
                                            "method": "StashProcessing - _update_stash_metadata",
                                            "status": "tag_found_after_create_failed",
                                            "tag_name": tag_name,
                                            "found_tag": found_tag.name,
                                        }
                                    )
                            else:
                                # Re-raise if it's not a "tag already exists" error
                                raise

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

    async def _update_file_metadata(
        self,
        file: Scene | Image,
        media_obj: AccountMedia,
        is_preview: bool = False,
        session: AsyncSession | None = None,
    ) -> None:
        """Update file metadata in Stash.

        Args:
            file: Scene or Image object to update
            media_obj: AccountMedia object containing metadata
            is_preview: Whether this is a preview/trailer file
            session: Optional database session
        """
        # Find attachment that contains this media
        debug_print(
            {
                "method": "StashProcessing - _update_file_metadata",
                "status": "finding_attachment",
                "file_id": file.id,
                "media_id": media_obj.id,
                "media_type": type(media_obj).__name__,
            }
        )

        # Collect all possible media IDs (original, variants, preview)
        media_ids = [media_obj.id]  # Start with the original media ID

        # Add variant IDs
        if hasattr(media_obj, "variants") and media_obj.variants:
            media_ids.extend(v.id for v in media_obj.variants)

        # Add preview ID
        if hasattr(media_obj, "preview") and media_obj.preview:
            media_ids.append(media_obj.preview.id)

        debug_print(
            {
                "method": "StashProcessing - _update_file_metadata",
                "status": "searching_media_ids",
                "file_id": file.id,
                "original_media_id": media_obj.id,
                "variant_count": (
                    len(media_obj.variants) if hasattr(media_obj, "variants") else 0
                ),
                "has_preview": (
                    bool(media_obj.preview) if hasattr(media_obj, "preview") else False
                ),
                "all_media_ids": media_ids,
            }
        )

        # Find attachment for any of these media IDs
        stmt = (
            select(Attachment)
            .join(Attachment.media)  # Attachment -> AccountMedia
            .join(AccountMedia.media)  # AccountMedia -> Media
            .options(
                selectinload(Attachment.post).selectinload(Post.account),
                selectinload(Attachment.message).selectinload(Message.sender),
                selectinload(Attachment.media).selectinload(AccountMedia.media),
                selectinload(Attachment.bundle)
                .selectinload(AccountMediaBundle.accountMedia)
                .selectinload(AccountMedia.media),
            )
            .where(Media.id.in_(media_ids))
        )

        # If not found, try finding through media variants
        result = await session.execute(stmt)
        attachment = result.scalar_one_or_none()
        if not attachment:
            debug_print(
                {
                    "method": "StashProcessing - _update_file_metadata",
                    "status": "checking_variants",
                    "file_id": file.id,
                    "media_id": media_obj.id,
                }
            )
            # Find through media variants
            debug_print(
                {
                    "method": "StashProcessing - _update_file_metadata",
                    "status": "building_variant_query",
                    "file_id": file.id,
                    "media_id": media_obj.id,
                    "media_type": type(media_obj).__name__,
                }
            )
            try:
                # First find the original media through variants
                variant_media = (
                    select(Media).join(Media.variants).where(Media.id == media_obj.id)
                ).scalar_subquery()

                # Then find the attachment that contains this media
                stmt = (
                    select(Attachment)
                    .join(Attachment.media)  # Attachment -> AccountMedia
                    .join(AccountMedia.media)  # AccountMedia -> Media
                    .where(Media.id == variant_media)
                    .options(
                        selectinload(Attachment.post).selectinload(Post.account),
                        selectinload(Attachment.message).selectinload(Message.sender),
                        selectinload(Attachment.media).selectinload(AccountMedia.media),
                        selectinload(Attachment.bundle)
                        .selectinload(AccountMediaBundle.accountMedia)
                        .selectinload(AccountMedia.media),
                    )
                )
                debug_print(
                    {
                        "method": "StashProcessing - _update_file_metadata",
                        "status": "variant_query_built",
                        "file_id": file.id,
                        "media_id": media_obj.id,
                        "query": str(stmt),
                    }
                )
                result = await session.execute(stmt)
                attachment = result.scalar_one_or_none()
            except Exception as e:
                debug_print(
                    {
                        "method": "StashProcessing - _update_file_metadata",
                        "status": "variant_query_failed",
                        "file_id": file.id,
                        "media_id": media_obj.id,
                        "error": str(e),
                    }
                )
                attachment = None
            except Exception as e:
                debug_print(
                    {
                        "method": "StashProcessing - _update_file_metadata",
                        "status": "variant_query_failed",
                        "file_id": file.id,
                        "media_id": media_obj.id,
                        "error": str(e),
                    }
                )
                attachment = None

        if attachment:
            debug_print(
                {
                    "method": "StashProcessing - _update_file_metadata",
                    "status": "found_attachment",
                    "file_id": file.id,
                    "media_id": media_obj.id,
                    "attachment_id": attachment.id,
                    "content_type": (
                        attachment.contentType.name if attachment.contentType else None
                    ),
                    "post_id": attachment.post.id if attachment.post else None,
                    "message_id": attachment.message.id if attachment.message else None,
                }
            )

        # Update metadata based on source
        if attachment:
            debug_print(
                {
                    "method": "StashProcessing - _update_file_metadata",
                    "status": "checking_content",
                    "file_id": file.id,
                    "media_id": media_obj.id,
                    "attachment_id": attachment.id,
                    "content_type": (
                        attachment.contentType.name if attachment.contentType else None
                    ),
                    "content_id": attachment.contentId,
                    "post_id": attachment.postId,
                    "message_id": attachment.messageId,
                }
            )

            # Load relationships
            if hasattr(attachment, "awaitable_attrs"):
                if attachment.postId:
                    await attachment.awaitable_attrs.post
                if attachment.messageId:
                    await attachment.awaitable_attrs.message

            # Get content from attachment
            content = attachment.post or attachment.message

            if content:
                debug_print(
                    {
                        "method": "StashProcessing - _update_file_metadata",
                        "status": "found_content",
                        "file_id": file.id,
                        "media_id": media_obj.id,
                        "content_type": type(content).__name__,
                        "content_id": content.id,
                    }
                )
                # Update metadata from content
                await self._update_content_metadata(
                    file=file,
                    content=content,
                    media_obj=media_obj,
                    session=session,
                )

        # Add preview tag if needed
        if is_preview:
            await self._add_preview_tag(file)

        # Add gallery relationships for scenes and images
        if hasattr(media_obj, "gallery") and media_obj.gallery:
            # Convert existing galleries to set to avoid duplicates
            if isinstance(file, (Scene, Image)):
                existing_galleries = (
                    set(file.galleries) if hasattr(file, "galleries") else set()
                )
                existing_galleries.add(media_obj.gallery)
                file.galleries = list(existing_galleries)

        # Save changes to Stash only if object is dirty
        if file.is_dirty():
            debug_print(
                {
                    "method": "StashProcessing - _update_file_metadata",
                    "status": "saving_changes",
                    "file_id": file.id,
                    "file_type": file.__type_name__,
                }
            )
            await file.save(self.context.client)
        else:
            debug_print(
                {
                    "method": "StashProcessing - _update_file_metadata",
                    "status": "no_changes",
                    "file_id": file.id,
                    "file_type": file.__type_name__,
                }
            )

        # Write back stash_id to media object if needed
        if not media_obj.stash_id:
            debug_print(
                {
                    "method": "StashProcessing - _update_file_metadata",
                    "status": "updating_stash_id",
                    "file_id": file.id,
                    "media_id": media_obj.id,
                }
            )
            media_obj.stash_id = file.id
            session.add(media_obj)
            await session.flush()

    @with_session()
    async def _update_content_metadata(
        self,
        file: Scene | Image,
        content: HasMetadata,
        media_obj: AccountMedia,
        session: AsyncSession | None = None,
    ) -> None:
        """Update file metadata from content.

        Args:
            file: Scene or Image object to update
            content: Post or Message containing metadata
            media_obj: AccountMedia object containing metadata
            session: Optional database session
        """
        # Load relationships
        if hasattr(content, "account"):  # Post
            account = content.account
        else:  # Message
            account = content.sender

        # Load account data
        if hasattr(account, "awaitable_attrs"):
            await account.awaitable_attrs.username
            await account.awaitable_attrs.displayName

        # Load mentions
        if hasattr(content, "awaitable_attrs"):
            await content.awaitable_attrs.accountMentions

        # Get username for title
        username = account.username

        # Generate new title
        new_title = self._generate_title_from_content(
            content=content.content,
            username=username,
            created_at=content.createdAt,
        )
        if file.title != new_title:
            debug_print(
                {
                    "method": "StashProcessing - _update_content_metadata",
                    "status": "updating_title",
                    "file_id": file.id,
                    "old_title": file.title,
                    "new_title": new_title,
                }
            )
            file.title = new_title

        # Update details if changed
        if file.details != content.content:
            debug_print(
                {
                    "method": "StashProcessing - _update_content_metadata",
                    "status": "updating_details",
                    "file_id": file.id,
                }
            )
            file.details = content.content

        # Update date if changed
        new_date = content.createdAt.strftime("%Y-%m-%d")  # Stash expects YYYY-MM-DD
        if file.date != new_date:
            debug_print(
                {
                    "method": "StashProcessing - _update_content_metadata",
                    "status": "updating_date",
                    "file_id": file.id,
                    "old_date": file.date,
                    "new_date": new_date,
                }
            )
            file.date = new_date

        # Build new performers list
        new_performers = []

        # Add main account as performer
        if main_performer := await self._find_existing_performer(account):
            debug_print(
                {
                    "method": "StashProcessing - _update_content_metadata",
                    "status": "adding_main_performer",
                    "file_id": file.id,
                    "performer_id": main_performer.id,
                    "performer_name": main_performer.name,
                }
            )
            new_performers.append(main_performer)

        # Add account mentions as performers
        if hasattr(content, "accountMentions") and content.accountMentions:
            for mention in content.accountMentions:
                if mention_performer := await self._find_existing_performer(mention):
                    debug_print(
                        {
                            "method": "StashProcessing - _update_content_metadata",
                            "status": "adding_mention_performer",
                            "file_id": file.id,
                            "performer_id": mention_performer.id,
                            "performer_name": mention_performer.name,
                        }
                    )
                    new_performers.append(mention_performer)

        # Update performers if changed
        current_performers = (
            {p.id for p in file.performers} if hasattr(file, "performers") else set()
        )
        new_performer_ids = {p.id for p in new_performers}
        if current_performers != new_performer_ids:
            debug_print(
                {
                    "method": "StashProcessing - _update_content_metadata",
                    "status": "updating_performers",
                    "file_id": file.id,
                    "old_performers": sorted(current_performers),
                    "new_performers": sorted(new_performer_ids),
                }
            )
            file.performers = new_performers

        # Update studio if needed
        studio = await self._find_existing_studio(account)
        current_studio_id = file.studio.id if file.studio else None
        new_studio_id = studio.id if studio else None
        if current_studio_id != new_studio_id:
            debug_print(
                {
                    "method": "StashProcessing - _update_content_metadata",
                    "status": "updating_studio",
                    "file_id": file.id,
                    "old_studio": current_studio_id,
                    "new_studio": new_studio_id,
                }
            )
            file.studio = studio

        # Update code if needed
        new_code = (
            str(media_obj.id) if isinstance(file, (Scene, Image)) else str(content.id)
        )
        if file.code != new_code:
            debug_print(
                {
                    "method": "StashProcessing - _update_content_metadata",
                    "status": "updating_code",
                    "file_id": file.id,
                    "old_code": file.code,
                    "new_code": new_code,
                }
            )
            file.code = new_code

        # Set organized flag since we have metadata
        # file.organized = True

    async def _add_preview_tag(
        self,
        file: Scene | Image,
    ) -> None:
        """Add preview tag to file.

        Args:
            file: Scene or Image object to update
        """
        # Try to find preview tag
        tag_data = await self.context.client.find_tags(
            q="Trailer",
        )
        if tag_data.count > 0:
            preview_tag = tag_data.tags[0]
            # Check if tag already exists
            current_tag_ids = (
                {t.id for t in file.tags} if hasattr(file, "tags") else set()
            )
            if preview_tag.id not in current_tag_ids:
                debug_print(
                    {
                        "method": "StashProcessing - _add_preview_tag",
                        "status": "adding_preview_tag",
                        "file_id": file.id,
                        "tag_id": preview_tag.id,
                    }
                )
                file.tags.append(preview_tag)
            else:
                debug_print(
                    {
                        "method": "StashProcessing - _add_preview_tag",
                        "status": "preview_tag_exists",
                        "file_id": file.id,
                        "tag_id": preview_tag.id,
                    }
                )

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

    @with_session()
    async def _update_account_stash_id(
        self,
        account: Account,
        performer: Performer,
        session: AsyncSession | None = None,
    ) -> None:
        """Update account's stash ID.

        Args:
            account: Account to update
            performer: Performer containing the stash ID
            session: Optional database session
        """
        # Get a fresh account instance bound to the session
        stmt = select(Account).where(Account.id == account.id)
        result = await session.execute(stmt)
        account = result.scalar_one()

        # Update stash ID
        account.stash_id = performer.id
        session.add(account)
        await session.flush()

    @with_session()
    async def process_creator_studio(
        self,
        account: Account,
        performer: Performer,
        session: Session | None = None,
    ) -> Studio | None:
        """Process creator studio metadata.

        This method:
        1. Finds or creates a corresponding studio in Stash
        2. Updates studio information if needed

        Args:
            account: The Account object
            performer: The Performer object
            session: Optional database session to use
        """
        fansly_studio_result = await self.context.client.find_studios(
            q="Fansly (network)",
        )
        debug_print(
            {
                "method": "StashProcessing - process_creator_studio",
                "fansly_studio_dict": fansly_studio_result.__dict__,
            }
        )
        if fansly_studio_result.count == 0:
            raise ValueError("Fansly Studio not found in Stash")

        # Convert dict to Studio object
        fansly_studio = Studio(**fansly_studio_result.studios[0])
        debug_print(
            {
                "method": "StashProcessing - process_creator_studio",
                "fansly_studio": fansly_studio,
            }
        )
        creator_studio_name = f"{account.username} (Fansly)"
        studio_data = await self.context.client.find_studios(q=creator_studio_name)
        if studio_data.count == 0:
            # Create new studio with required fields
            studio = Studio(
                id="new",  # Special value indicating new object to Stash
                name=creator_studio_name,
                parent_studio=fansly_studio,
                url=f"https://fansly.com/{account.username}/posts",
                # created_at and updated_at handled by Stash
            )
            await studio.save(self.context.client)
        else:
            # Convert dict to Studio object
            studio = Studio(**studio_data.studios[0])
            if not studio.parent_studio:
                studio.parent_studio = fansly_studio
                await studio.save(self.context.client)
        debug_print(
            {
                "method": "StashProcessing - process_creator_studio",
                "studio": studio,
            }
        )
        return studio

    async def cleanup(self) -> None:
        """Safely cleanup resources.

        This method:
        1. Cancels any background processing
        2. Waits for cleanup event
        3. Closes client connection
        """
        try:
            # Cancel and wait for background task
            if self._background_task and not self._background_task.done():
                self._background_task.cancel()
                if self._cleanup_event:
                    await self._cleanup_event.wait()

        finally:
            # Always close client
            await self.context.close()
