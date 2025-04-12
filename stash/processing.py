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

    id: int
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
                performer = Performer.from_account(account)
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
                "statement": str(stmt.compile(compile_kwargs={"literal_binds": True})),
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
                "statement": str(stmt.compile(compile_kwargs={"literal_binds": True})),
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

        # Use reasonable default concurrency limit
        # Limited to avoid overwhelming Stash server
        max_concurrent = min(10, (os.cpu_count() // 2) or 1)
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
        # Use same concurrency as semaphore
        max_concurrent = semaphore._value

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
                        gallery.tags = tags

            # Process attachments and collect images/scenes
            all_images = []
            all_scenes = []
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
                    result = await self.process_creator_attachment(
                        attachment=attachment,
                        item=item,
                        account=account,
                        session=session,
                    )
                    if result["images"] or result["scenes"]:
                        all_images.extend(result["images"])
                        all_scenes.extend(result["scenes"])
                        debug_print(
                            {
                                "method": "StashProcessing - _process_item_gallery",
                                "status": "attachment_processed",
                                "item_id": item.id,
                                "attachment_id": attachment.id,
                                "progress": f"{i}/{len(attachments)}",
                                "images_added": len(result["images"]),
                                "scenes_added": len(result["scenes"]),
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

            if not all_images and not all_scenes:
                # No content was processed, delete the gallery if we just created it
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

            debug_print(
                {
                    "method": "StashProcessing - _process_item_gallery",
                    "status": "content_summary",
                    "item_id": item.id,
                    "gallery_id": gallery.id,
                    "image_count": len(all_images),
                    "scene_count": len(all_scenes),
                }
            )

            # Link images and scenes to gallery
            try:
                # Link images using the special API endpoint
                if all_images:
                    # Try up to 3 times with increasing delays
                    for attempt in range(3):
                        try:
                            success = await self.context.client.add_gallery_images(
                                gallery_id=gallery.id,
                                image_ids=[img.id for img in all_images],
                            )
                            if success:
                                debug_print(
                                    {
                                        "method": "StashProcessing - _process_item_gallery",
                                        "status": "gallery_images_added",
                                        "item_id": item.id,
                                        "gallery_id": gallery.id,
                                        "success": success,
                                        "image_count": len(all_images),
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
                                        "image_count": len(all_images),
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
                                await asyncio.sleep(2**attempt)  # Exponential backoff

                # Link scenes using the standard gallery update
                if all_scenes:
                    gallery.scenes = all_scenes
                    debug_print(
                        {
                            "method": "StashProcessing - _process_item_gallery",
                            "status": "gallery_scenes_added",
                            "item_id": item.id,
                            "gallery_id": gallery.id,
                            "scene_count": len(all_scenes),
                            "scenes": pformat(all_scenes),
                        }
                    )
                await gallery.save(self.context.client)
            except Exception as e:
                logger.exception(
                    f"Failed to link content to gallery for {item_type} {item.id}",
                    exc_info=e,
                )
                debug_print(
                    {
                        "method": "StashProcessing - _process_item_gallery",
                        "status": "gallery_content_error",
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

    async def _process_media(
        self,
        media: Media,
        item: HasMetadata,
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
        item: HasMetadata,
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
        item: HasMetadata,
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
                    agg_result = await self.process_creator_attachment(
                        attachment=agg_attachment,
                        item=agg_post,
                        account=account,
                        session=session,
                    )
                    result["images"].extend(agg_result["images"])
                    result["scenes"].extend(agg_result["scenes"])

        return result

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
        if isinstance(item, Post):
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
            pformat(
                {
                    "method": "StashProcessing - _update_stash_metadata",
                    "status": "update_metadata--before_save",
                    "object_type": stash_obj.__type_name__,
                    "object_id": stash_obj.id,
                    "object": stash_obj.__dict__,
                }
            )
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

    async def _process_hashtags_to_tags(
        self,
        hashtags: list[Any],
    ) -> list[Tag]:
        """Process hashtags into Stash tags.

        Args:
            hashtags: List of hashtag objects with value attribute

        Returns:
            List of Tag objects
        """
        tags = []
        for hashtag in hashtags:
            # Try to find existing tag by exact name or alias
            tag_name = hashtag.value.lower()  # Case-insensitive comparison
            found_tag = None

            # First try exact name match
            name_results = await self.context.client.find_tags(
                tag_filter={"name": {"value": tag_name, "modifier": "EQUALS"}},
            )
            if name_results.count > 0:
                # Convert dict to Tag object using unpacking
                found_tag = Tag(**name_results.tags[0])
                debug_print(
                    {
                        "method": "StashProcessing - _process_hashtags_to_tags",
                        "status": "tag_found_by_name",
                        "tag_name": tag_name,
                        "found_tag": found_tag.name,
                    }
                )
            else:
                # Then try alias match
                alias_results = await self.context.client.find_tags(
                    tag_filter={"aliases": {"value": tag_name, "modifier": "INCLUDES"}},
                )
                if alias_results.count > 0:
                    # Convert dict to Tag object using unpacking
                    found_tag = Tag(**alias_results.tags[0])
                    debug_print(
                        {
                            "method": "StashProcessing - _process_hashtags_to_tags",
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
                    if created_tag := await self.context.client.create_tag(new_tag):
                        tags.append(created_tag)
                        debug_print(
                            {
                                "method": "StashProcessing - _process_hashtags_to_tags",
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
                        if found_tag := await self.context.client.create_tag(new_tag):
                            tags.append(found_tag)
                            debug_print(
                                {
                                    "method": "StashProcessing - _process_hashtags_to_tags",
                                    "status": "tag_found_after_create_failed",
                                    "tag_name": tag_name,
                                    "found_tag": found_tag.name,
                                }
                            )
                    else:
                        # Re-raise if it's not a "tag already exists" error
                        raise

        return tags

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
