"""Processing module for Stash integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import sys
import traceback
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from pprint import pformat
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import func, select

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
from textio import print_error, print_info
from textio.logging import SizeAndTimeRotatingFileHandler

from .client import StashClient
from .context import StashContext
from .types import (
    Gallery,
    GalleryChapter,
    Image,
    ImageFile,
    Performer,
    Scene,
    Studio,
    VideoFile,
    VisualFile,
)

if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

from .logging import debug_print
from .logging import processing_logger as logger


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
        from metadata import Database

        state_copy = deepcopy(state)
        context = config.get_stash_context()
        database = Database(config, creator_name=state.creator_name)
        owns_db = True
        instance = cls(
            config=config,
            state=state_copy,
            context=context,
            database=database,
            _background_task=None,
            _cleanup_event=asyncio.Event(),
            _owns_db=owns_db,
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
            print_info(f"No account found for username: {self.state.creator_name}")
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
                debug_print(
                    {
                        "method": "StashProcessing - _update_performer_avatar",
                        "status": "avatar_update_failed",
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
                )

    async def _find_existing_studio(self, account: Account) -> Studio | None:
        """Find existing studio in Stash.

        Args:
            account: Account to find studio for

        Returns:
            Studio data if found, None otherwise
        """
        # Use process_creator_studio with None performer
        return await self.process_creator_studio(account=account, performer=None)

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
            print_info("StashContext is not configured. Skipping metadata processing.")
            return

        # Initialize Stash client
        await self.context.get_client()

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
            # Handle task cancellation
            debug_print({"status": "background_task_cancelled"})
            raise
        except Exception as e:
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
        print_info("Continuing Stash GraphQL processing in the background...")
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

        # Process direct messages
        stmt = (
            select(Group)
            .join(Group.users)
            .join(Group.messages)
            .join(Message.attachments)
            .where(Group.users.any(Account.id == account.id))
        )
        groups = await session.execute(stmt)
        groups = groups.scalars().all()

        # Process messages in batches
        batch_size = 15  # Process one timeline page worth of messages at a time
        for group in groups:
            messages = await group.awaitable_attrs.messages
            # Filter messages with attachments
            messages_with_attachments = [m for m in messages if m.attachments]

            # Process in batches
            for i in range(0, len(messages_with_attachments), batch_size):
                batch = messages_with_attachments[i : i + batch_size]
                try:
                    await self._process_items_with_gallery(
                        account=account,
                        performer=performer,
                        studio=studio,
                        item_type="message",
                        items=batch,
                        url_pattern_func=get_message_url,
                        session=session,
                    )
                except Exception as e:
                    first_id = batch[0].id if batch else "unknown"
                    print_error(
                        f"Failed to process message batch starting with {first_id}: {e}"
                    )
                    debug_print(
                        {
                            "method": "StashProcessing - process_creator_messages",
                            "status": "message_processing_failed",
                            "batch_start_id": first_id,
                            "batch_size": len(batch) if batch else 0,
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                        }
                    )
                    continue

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
        stmt = (
            select(Post)
            .join(Post.attachments)
            .where(Post.accountId == account.id)
            .options(
                selectinload(Post.attachments),
                selectinload(Post.accountMentions),
            )
        )
        debug_print({"status": "building_post_query", "account_id": account.id})

        def get_post_url(post: Post) -> str:
            return f"https://fansly.com/post/{post.id}"

        result = await session.execute(stmt)
        posts = result.unique().scalars().all()
        debug_print(
            {
                "status": "got_posts",
                "count": len(posts),
                "account_id": account.id,
                "posts_with_attachments": [p.id for p in posts],
            }
        )
        # Process posts in batches
        print_info(f"Processing {len(posts)} posts...")
        debug_print({"status": "processing_posts", "count": len(posts)})
        batch_size = 50  # Adjust based on testing
        for i in range(0, len(posts), batch_size):
            batch = posts[i : i + batch_size]
            try:
                print_info(f"Processing posts {i+1}-{i+len(batch)}/{len(posts)}...")
                # Get fresh batch with all needed relationships
                batch_ids = [post.id for post in batch]
                stmt = (
                    select(Post)
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
                    .where(Post.id.in_(batch_ids))
                )
                result = await session.execute(stmt)
                fresh_batch = result.unique().scalars().all()

                # Ensure all objects are bound to the session
                for post in fresh_batch:
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

                await self._process_items_with_gallery(
                    account=account,
                    performer=performer,
                    studio=studio,
                    item_type="post",
                    items=fresh_batch,
                    url_pattern_func=get_post_url,
                    session=session,
                )
                print_info(f"Completed posts {i+1}-{i+len(batch)}/{len(posts)}")
            except Exception as e:
                first_id = batch_ids[0] if batch_ids else "unknown"
                print_error(
                    f"Error processing post batch {i+1}-{i+len(batch)}/{len(posts)} starting with {first_id}: {e}"
                )
                debug_print(
                    {
                        "method": "StashProcessing - process_creator_posts",
                        "status": "post_processing_failed",
                        "batch_start_id": first_id,
                        "batch_size": len(batch) if batch else 0,
                        "batch_ids": batch_ids,
                        "batch_range": f"{i+1}-{i+len(batch)}/{len(posts)}",
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
                )

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
                                "file_details": [
                                    {
                                        "id": f.id,
                                        "stash_id": (
                                            f.stash_ids[0].stash_id
                                            if hasattr(f, "stash_ids") and f.stash_ids
                                            else getattr(f, "stash_id", None)
                                        ),
                                        "type": f.__class__.__name__,
                                        "path": (
                                            str(f.path) if hasattr(f, "path") else None
                                        ),
                                    }
                                    for f in attachment_files
                                ],
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

            # Split files by type
            image_files = [f for f in files if isinstance(f, ImageFile)]
            scene_files = [f for f in files if isinstance(f, VideoFile)]

            debug_print(
                {
                    "method": "StashProcessing - _process_item_gallery",
                    "status": "files_summary",
                    "item_id": item.id,
                    "gallery_id": gallery.id,
                    "total_files": len(files),
                    "image_files": len(image_files),
                    "scene_files": len(scene_files),
                    "image_ids": [f.id for f in image_files],
                    "scene_ids": [f.id for f in scene_files],
                }
            )

            # Add images through galleryAdd mutation
            if image_files:
                try:
                    success = await self.context.client.add_gallery_images(
                        gallery_id=gallery.id,
                        image_ids=[f.id for f in image_files],
                    )
                    debug_print(
                        {
                            "method": "StashProcessing - _process_item_gallery",
                            "status": "gallery_images_added",
                            "item_id": item.id,
                            "gallery_id": gallery.id,
                            "success": success,
                            "image_count": len(image_files),
                        }
                    )
                    if not success:
                        debug_print(
                            {
                                "method": "StashProcessing - _process_item_gallery",
                                "status": "image_add_failed",
                                "item_id": item.id,
                                "gallery_id": gallery.id,
                                "image_count": len(image_files),
                            }
                        )
                except Exception as e:
                    debug_print(
                        {
                            "method": "StashProcessing - _process_item_gallery",
                            "status": "image_add_error",
                            "item_id": item.id,
                            "gallery_id": gallery.id,
                            "image_count": len(image_files),
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                        }
                    )

            # Add scenes through gallery update
            if scene_files:
                gallery.scenes = scene_files
                await gallery.save(self.context.client)

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
            if (
                gallery.title == title
                and gallery.date == item.createdAt.strftime("%Y-%m-%d")
                and (not studio or gallery.studio_id == studio.id)
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
            created_at=item.createdAt,
            updated_at=datetime.now(),
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
            media_files = await self._process_media_to_files(
                media=attachment.media.media,
                session=session,
            )
            debug_print(
                {
                    "method": "StashProcessing - process_creator_attachment",
                    "status": "processed_media",
                    "attachment_id": attachment.id,
                    "files_found": len(media_files),
                }
            )
            files.extend(media_files)

        # Handle media bundles
        if attachment.bundle:
            bundle: AccountMediaBundle = attachment.bundle
            # Process each media item in the bundle
            for account_media in bundle.accountMedia:
                if account_media.media:
                    files.extend(
                        await self._process_media_to_files(
                            media=account_media.media,
                            session=session,
                        )
                    )

        # Handle aggregated posts
        if (
            attachment.is_aggregated_post
            and await attachment.awaitable_attrs.aggregated_post
        ):
            agg_post: Post = await attachment.awaitable_attrs.aggregated_post
            # Get attachments from aggregated post
            agg_attachments: list[Attachment] = (
                await agg_post.awaitable_attrs.attachments
            )
            for agg_attachment in agg_attachments:
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

    async def _process_media_file(
        self,
        media_file: Any,  # AccountMedia.media or AccountMedia.preview
        media_obj: AccountMedia,
        is_preview: bool = False,
    ) -> tuple[dict | None, Scene | Image | None]:
        """Process a media file from AccountMedia.

        Args:
            media_file: The media file to process
            media_obj: The parent AccountMedia object
            is_preview: Whether this is a preview file

        Returns:
            Tuple of (raw stash object, processed file object)
        """
        try:
            if media_file.stash_id:
                return await self._find_stash_file_by_id(
                    stash_id=media_file.stash_id,
                    mime_type=media_file.mimetype,
                )
            elif media_file.local_filename or await media_file.awaitable_attrs.variants:
                return await self._find_stash_file_by_path(
                    media_file=media_file,
                    mime_type=media_file.mimetype,
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

    async def _find_stash_file_by_id(
        self,
        stash_id: str,
        mime_type: str,
    ) -> tuple[dict | None, Scene | Image | None]:
        """Find a file in Stash by ID.

        Args:
            stash_id: The Stash ID to search for
            mime_type: The MIME type of the file

        Returns:
            Tuple of (raw stash object, processed file object)

        Raises:
            ValueError: If mime_type is invalid
        """

        def convert_obj(
            obj: Any, cls: type[Scene] | type[Image]
        ) -> Scene | Image | None:
            """Convert object to Scene or Image."""
            if not obj:
                return None
            data = obj.__dict__ if hasattr(obj, "__dict__") else obj
            return cls.from_dict(data)

        match mime_type:
            case str() as mime if mime.startswith("image"):
                stash_obj = await self.context.client.find_image(stash_id)
                return (stash_obj, convert_obj(stash_obj, Image))
            case str() as mime if mime.startswith("video"):
                stash_obj = await self.context.client.find_scene(stash_id)
                return (stash_obj, convert_obj(stash_obj, Scene))
            case str() as mime if mime.startswith("application"):
                stash_obj = await self.context.client.find_scene(stash_id)
                return (stash_obj, convert_obj(stash_obj, Scene))
            case _:
                raise ValueError(f"Invalid media type: {mime_type}")

    async def _find_stash_file_by_path(
        self,
        media_file: Any,
        mime_type: str,
    ) -> tuple[dict | None, Scene | Image | None]:
        """Find a file in Stash by path.

        Args:
            media_file: The media file object (with local_filename and variants)
            mime_type: The MIME type of the file

        Returns:
            Tuple of (raw stash object, processed file object)

        Raises:
            ValueError: If mime_type is invalid
        """
        filter_params = {
            "per_page": -1,
            "sort": "created_at",
            "direction": "DESC",
        }

        # Build list of paths to search
        paths = [media_file.local_filename] if media_file.local_filename else []
        if await media_file.awaitable_attrs.variants:
            for variant in await media_file.awaitable_attrs.variants:
                if variant.local_filename:
                    paths.append(variant.local_filename)

        # Try each path until we find a match
        for path in paths:
            # Extract ID from filename
            if match := re.search(r"_id_(\d+)", path):
                media_id = match.group(1)
                path_filter = {
                    "path": {
                        "modifier": "INCLUDES",
                        "value": media_id,
                    }
                }
            else:
                # Fallback to full path if no ID found
                path_filter = {
                    "path": {
                        "modifier": "INCLUDES",
                        "value": path,
                    }
                }

            try:
                match mime_type:
                    case str() as mime if mime.startswith("image"):
                        results = await self.context.client.find_images(
                            image_filter=path_filter,
                            filter_=filter_params,
                        )
                        if results.count > 0:
                            stash_obj = results.images[0]
                            return (
                                stash_obj,
                                Image.from_dict(stash_obj) if stash_obj else None,
                            )
                        debug_print(
                            {
                                "method": "StashProcessing - _find_stash_file_by_path",
                                "status": "no_image_found",
                                "path": path,
                                "filter": path_filter,
                            }
                        )
                    case str() as mime if mime.startswith("video"):
                        try:
                            results = await self.context.client.find_scenes(
                                scene_filter=path_filter,
                                filter_=filter_params,
                            )
                            # Debug the results structure
                            debug_print(
                                {
                                    "method": "StashProcessing - _find_stash_file_by_path",
                                    "status": "debug_results",
                                    "path": path,
                                    "results_type": type(results).__name__,
                                    "results": results,
                                }
                            )

                            if results.count > 0:
                                stash_obj = results.scenes[0]
                                return (
                                    stash_obj,
                                    Scene.from_dict(stash_obj) if stash_obj else None,
                                )

                        except Exception as e:
                            debug_print(
                                {
                                    "method": "StashProcessing - _find_stash_file_by_path",
                                    "status": "path_search_failed",
                                    "path": path,
                                    "error": str(e),
                                    "traceback": traceback.format_exc(),
                                }
                            )
                        debug_print(
                            {
                                "method": "StashProcessing - _find_stash_file_by_path",
                                "status": "no_video_found",
                                "path": path,
                                "filter": path_filter,
                            }
                        )
                    case str() as mime if mime.startswith("application"):
                        results = await self.context.client.find_scenes(
                            scene_filter=path_filter,
                            filter_=filter_params,
                        )
                        if results.count > 0:
                            stash_obj = results.scenes[0]
                            return (
                                stash_obj,
                                Scene.from_dict(stash_obj) if stash_obj else None,
                            )
                        debug_print(
                            {
                                "method": "StashProcessing - _find_stash_file_by_path",
                                "status": "no_application_found",
                                "path": path,
                                "filter": path_filter,
                            }
                        )
                    case _:
                        raise ValueError(f"Invalid media type: {mime_type}")
            except Exception as e:
                debug_print(
                    {
                        "method": "StashProcessing - _find_stash_file_by_path",
                        "status": "path_search_failed",
                        "path": path,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
                )
                continue

        return (None, None)

    @with_session()
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
        stmt = (
            select(Attachment)
            .options(
                selectinload(Attachment.post).selectinload(Post.account),
                selectinload(Attachment.message).selectinload(Message.sender),
            )
            .where(
                (Attachment.contentType == ContentType.ACCOUNT_MEDIA)
                & (Attachment.contentId == media_obj.id)
            )
        )
        result = await session.execute(stmt)
        attachment = result.scalar_one_or_none()

        # Update metadata based on source
        if attachment:
            content = attachment.post or attachment.message
            if content:
                # Get account based on content type
                if hasattr(content, "account"):  # Post
                    account = content.account
                else:  # Message
                    account = content.sender

                # Get username
                if hasattr(account, "awaitable_attrs"):
                    username = await account.awaitable_attrs.username
                else:
                    username = account.username

                # Generate title
                title = self._generate_title_from_content(
                    content=content.content,
                    username=username,
                    created_at=content.createdAt,
                    current_pos=(
                        media_obj.position if hasattr(media_obj, "position") else None
                    ),
                    total_media=(
                        media_obj.total_media
                        if hasattr(media_obj, "total_media")
                        else None
                    ),
                )  # Not a coroutine anymore
                file.title = title

                # Update other metadata
                await self._update_content_metadata(
                    file=file,
                    content=content,
                    media_obj=media_obj,
                    session=session,
                )

        # Update date for all files
        file.date = media_obj.createdAt

        # Add preview tag if needed
        if is_preview:
            await self._add_preview_tag(file)

        # Save changes to Stash
        await file.save(self.context.client)

        # Write back stash_id to media object
        if not media_obj.stash_id:
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
        # Update title and details
        if hasattr(content, "account"):  # Post
            account = content.account
        else:  # Message
            account = content.sender

        # Get username for title
        if hasattr(account, "awaitable_attrs"):
            username = await account.awaitable_attrs.username
        else:
            username = account.username

        # Generate title using same method as gallery titles
        file.title = self._generate_title_from_content(
            content=content.content,
            username=username,
            created_at=content.createdAt,
        )
        file.details = content.content

        # Update date
        file.date = content.createdAt

        # Start with empty performers list
        performers = []

        # Add main account as performer
        if hasattr(content, "account"):  # Post
            account = content.account
        else:  # Message
            account = content.sender
        if main_performer := await self._find_existing_performer(account):
            performers.append(main_performer)

        # Add account mentions as performers
        if content.accountMentions:
            for mention in content.accountMentions:
                if mention_performer := await self._find_existing_performer(mention):
                    performers.append(mention_performer)

        # Set performers if we have any
        if performers:
            file.performers = performers

        # Set studio if available
        if studio := await self._find_existing_studio(account):
            file.studio = studio
            # Set code based on content type
            if isinstance(file, Scene) or isinstance(file, Image):
                file.code = str(media_obj.id)  # Media ID for Image/Scene
            else:
                file.code = str(content.id)  # Post/Message ID for Gallery

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
            file.tags.append(tag_data.tags[0])

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
                created_at=account.createdAt,
                updated_at=account.createdAt,  # Initially same as created_at
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
