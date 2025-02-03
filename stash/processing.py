"""Processing module for Stash integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
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

from config.decorators import with_database_session
from metadata import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Attachment,
    Database,
    Group,
    Message,
    Post,
    account_media_bundle_media,
)
from metadata.decorators import with_session
from pathio import set_create_directory_for_download
from textio import print_error, print_info
from textio.logging import SizeAndTimeRotatingFileHandler

from .client import StashClient
from .context import StashContext
from .types import Gallery, Image, Performer, Scene, Studio, VisualFile

if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState

# Logging setup
logs_dir = Path.cwd() / "logs"
logs_dir.mkdir(exist_ok=True)
log_file = logs_dir / "stash_processing.log"

logger = logging.getLogger("fansly.stash.processing")
logger.handlers.clear()
logger.setLevel(logging.DEBUG)
logger.propagate = False

# File handler with rotation
file_handler = SizeAndTimeRotatingFileHandler(
    filename=str(log_file),
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,
    when="h",  # Hourly rotation
    interval=1,
    utc=True,
    compression="gz",
    keep_uncompressed=2,  # Keep 2 most recent logs uncompressed
)
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG)  # Show debug messages in console
console_formatter = logging.Formatter("%(levelname)s: %(message)s")
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)


def debug_print(obj):
    """Debug printing with proper formatting."""
    try:
        formatted = pformat(obj, indent=2)
        logger.debug(formatted)
        for handler in logger.handlers:
            handler.flush()
    except Exception as e:
        print(f"Failed to log debug message: {e}", file=sys.stderr)


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

            # Convert account to performer and studio
            client = self.context.client
            performer_data = await self._find_existing_performer(account)

            # Handle performer_data which might be a coroutine or Performer
            performer = performer_data
            if asyncio.iscoroutine(performer_data):
                performer = await performer_data
            if performer is None:
                performer = await Performer.from_account(account)
                await performer.save(client)

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
        print_info("Continuing Stash GraphQL processing in the background...")
        try:
            if not account or not performer:
                raise ValueError("Missing account or performer data")
            if not isinstance(performer, Performer):
                raise ValueError("Invalid performer data")
            if not isinstance(account, Account):
                raise ValueError("Invalid account data")

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
            )

            # Process creator content
            print_info("Processing creator posts...")
            await self.process_creator_posts(
                account=account,
                performer=performer,
                studio=studio,
            )

            print_info("Processing creator messages...")
            await self.process_creator_messages(
                account=account,
                performer=performer,
                studio=studio,
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

    async def process_creator_messages(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None = None,
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

        def get_message_url(_: Account, group: Group, __: Message) -> str:
            """Get URL for a message in a group.

            Args:
                _: Account (unused)
                group: Group containing the message
                __: Message (unused)
            """
            return f"https://fansly.com/messages/{group.id}"

        async with self.database.get_async_session() as session:
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
                        pass
                        # await self._process_items_with_gallery(
                        #     account=account,
                        #     performer=performer,
                        #     studio=studio,
                        #     item_type="message",
                        #     items=batch,
                        #     url_pattern_func=get_message_url,
                        #     session=session,
                        # )
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

            # Process group messages
            group_stmt = (
                select(Group)
                .join(Group.users)
                .join(Group.messages)
                .join(Message.attachments)
                .where(Group.users.any(Account.id == account.id))
            )

            result = await session.execute(group_stmt)
            groups = result.scalars().all()

            print_info(f"Processing {len(groups)} group messages...")
            debug_print({"status": "processing_groups", "count": len(groups)})

            for i, group in enumerate(groups, 1):
                try:
                    print_info(f"Processing group message {i}/{len(groups)}...")
                    # Get messages with attachments in one query
                    stmt = (
                        select(Message)
                        .join(Message.attachments)
                        .where(Message.groupId == group.id)
                        .options(
                            selectinload(Message.attachments),
                        )
                    )
                    result = await session.execute(stmt)
                    messages = result.unique().scalars().all()

                    # Process messages in batches
                    batch_size = 50
                    for j in range(0, len(messages), batch_size):
                        batch = messages[j : j + batch_size]
                        # await self._process_items_with_gallery(
                        #     account=account,
                        #     performer=performer,
                        #     studio=studio,
                        #     item_type="group_message",
                        #     items=batch,
                        #     url_pattern_func=get_message_url,
                        #     session=session,
                        # )
                    print_info(f"Completed group message {i}/{len(groups)}")
                except Exception as e:
                    print_error(f"Error processing group message: {e}")
                    debug_print(
                        {
                            "method": "StashProcessing - process_creator_messages",
                            "status": "group_message_processing_failed",
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                        }
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
        2. Creates galleries for posts with media in parallel
        3. Links media files to galleries
        4. Associates galleries with performer and studio

        Args:
            account: The Account object
            performer: The Performer object
            studio: Optional Studio object
        """
        # Get all posts with attachments in one query with relationships
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
                # await self._process_items_with_gallery(
                #     account=account,
                #     performer=performer,
                #     studio=studio,
                #     item_type="post",
                #     items=batch,
                #     url_pattern_func=get_post_url,
                #     session=session,
                # )
                print_info(f"Completed posts {i+1}-{i+len(batch)}/{len(posts)}")
            except Exception as e:
                first_id = batch[0].id if batch else "unknown"
                print_error(
                    f"Error processing post batch starting with {first_id}: {e}"
                )
                debug_print(
                    {
                        "method": "StashProcessing - process_creator_posts",
                        "status": "post_processing_failed",
                        "batch_start_id": first_id,
                        "batch_size": len(batch) if batch else 0,
                        "status": "post_processing_failed",
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
        merged_items = []
        for item in items:
            merged_item = await session.merge(item)
            merged_items.append(merged_item)

        for item in merged_items:
            # Process the item directly
            items_to_process = [item]

            for sub_item in items_to_process:
                try:
                    await self._process_item_gallery(
                        item=sub_item,
                        account=account,
                        performer=performer,
                        studio=studio,
                        item_type=item_type,
                        url_pattern=url_pattern_func(account, item, sub_item),
                    )
                except Exception as e:
                    print_error(f"Failed to process {item_type} {sub_item.id}: {e}")
                    debug_print(
                        {
                            "method": f"StashProcessing - process_creator_{item_type}s",
                            "status": f"{item_type}_processing_failed",
                            f"{item_type}_id": sub_item.id,
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
            item_type: Type of item being processed
            session: Optional database session to use
            url_pattern: Pattern for generating URLs
        """
        async with contextlib.AsyncExitStack() as stack:
            if session is None:
                session = await stack.enter_async_context(
                    self.database.get_async_session()
                )

            attachments: list[Attachment] = await item.awaitable_attrs.attachments or []
            if not attachments:
                return

            gallery = await self._get_or_create_gallery(
                item=item,
                account=account,
                performer=performer,
                studio=studio,
                item_type=item_type,
                url_pattern=url_pattern,
            )
            if not gallery:
                return

            # Process attachments and add files to gallery
            files = []
            for attachment in attachments:
                attachment_files = await self.process_creator_attachment(
                    attachment=attachment,
                    item=item,
                    account=account,
                    session=session,
                )
                files.extend(attachment_files)

            if not files:
                return

            await self._update_gallery_files(
                gallery=gallery,
                files=files,
                item=item,
                session=session,
            )

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
            Gallery object or None if creation fails
        """
        if item.stash_id:
            gallery = await self.context.client.find_gallery(item.stash_id)
            if gallery:
                return gallery
        gallery = await Gallery.from_content(
            content=item,
            performer=performer,
            studio=studio,
        )
        gallery.urls = [url_pattern]
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
        if await attachment.awaitable_attrs.media:
            media: AccountMedia = attachment.media
            files.extend(
                await self._process_media_to_files(media=media, session=session)
            )

        # Handle media bundles
        if await attachment.awaitable_attrs.bundle:
            bundle: AccountMediaBundle = attachment.bundle
            # Get media through the bundle_media relationship
            bundle_media = await session.execute(
                select(AccountMedia)
                .join(
                    account_media_bundle_media,
                    AccountMedia.id == account_media_bundle_media.c.media_id,
                )
                .where(account_media_bundle_media.c.bundle_id == bundle.id)
                .order_by(account_media_bundle_media.c.pos)
            )
            bundle_media = bundle_media.scalars().all()
            for media in bundle_media:
                files.extend(
                    await self._process_media_to_files(media=media, session=session)
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
        media: AccountMedia,
        session: Session | None = None,
    ) -> list[VisualFile]:
        """Process media into VisualFile objects.

        Args:
            media: AccountMedia object to process
            session: Database session to use

        Returns:
            List of VisualFile objects created from the media
        """
        files = []

        # Process preview media
        if await media.awaitable_attrs.preview:
            stash_obj, file = await self._process_media_file(
                media.preview,
                media,
                is_preview=True,
            )
            if stash_obj and file:
                await self._update_file_metadata(
                    file=file,
                    media_obj=media,
                    is_preview=True,
                    session=session,
                )
                files.append(file)

        # Process main media
        if await media.awaitable_attrs.media:
            stash_obj, file = await self._process_media_file(
                media.media,
                media,
            )
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
                return self.context.client.fi(
                    media_file.stash_id,
                    media_file.mimetype,
                )
            elif media_file.local_filename or await media_file.awaitable_attrs.variants:
                return await self._find_stash_file_by_path(
                    media_file,
                    media_file.mimetype,
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
        return (None, None)

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
            session: Optional database session to use
        """
        account.stash_id = performer.id
        await session.merge(account)
        await session.commit()

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
        fansly_studio = (
            fansly_studio_result.studios[0] if fansly_studio_result.count > 0 else None
        )
        if not fansly_studio:
            raise ValueError("Fansly Studio not found in Stash")
        # No need to recreate Studio object, it's already a dict with the right data
        debug_print(
            {
                "method": "StashProcessing - process_creator_studio",
                "fansly_studio": fansly_studio,
            }
        )
        creator_studio_name = f"{account.username} (Fansly)"
        studio_data = await self.context.client.find_studios(q=creator_studio_name)
        if studio_data.count == 0:
            studio = Studio(
                name=creator_studio_name,
                parent_studio=fansly_studio,
                url=f"https://fansly.com/{account.username}/posts",
            )
            await studio.save(self.context.client)
        else:
            # Use the dict directly
            studio = studio_data.studios[0]
            if not studio.get("parent_studio"):
                studio["parent_studio"] = fansly_studio
                await self.context.client.update_studio(studio)
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
