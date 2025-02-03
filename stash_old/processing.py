"""Module for processing metadata and synchronizing with Stash."""

import asyncio
import contextlib
import logging
import sys
import traceback
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from pprint import pformat
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import selectinload
from sqlalchemy.orm.session import Session
from sqlalchemy.sql.expression import func, select, text

from config import FanslyConfig
from download.core import DownloadState
from metadata import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Attachment,
    ContentType,
    Group,
    Hashtag,
    Message,
    Post,
    account_media_bundle_media,
)
from metadata.database import Database
from metadata.decorators import with_session
from pathio import get_creator_database_path, set_create_directory_for_download
from textio import print_error, print_info
from textio.logging import SizeAndTimeRotatingFileHandler

from .file import VisualFile
from .gallery import Gallery
from .image import Image
from .performer import Performer
from .scene import Scene
from .stash_interface import StashInterface
from .studio import Studio
from .tag import Tag


@runtime_checkable
class HasMetadata(Protocol):
    """Protocol for models that have metadata for Stash."""

    content: str | None
    createdAt: datetime
    attachments: list[Attachment]
    # Messages don't have accountMentions, only Posts do
    accountMentions: list[Account] | None = None


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


@dataclass
class StashProcessing:
    """Class for handling Stash metadata processing."""

    # Required instance attributes (non-default)
    config: FanslyConfig
    state: DownloadState
    stash_interface: StashInterface
    database: Database  # Database instance (either creator-specific or global)

    # Optional instance attributes (with defaults)
    _background_task: asyncio.Task | None = None
    _cleanup_event: asyncio.Event | None = None
    _owns_db: bool = False  # True if we take ownership in separate_metadata mode

    @classmethod
    def from_config(
        cls, config: FanslyConfig, state: DownloadState
    ) -> "StashProcessing":
        """Create a StashProcessing instance from config.

        Args:
            config: The FanslyConfig instance
            state: The DownloadState instance

        Returns:
            A new StashProcessing instance
        """

        # Deep copy state to prevent modification during background processing
        state_copy = deepcopy(state)
        stash_interface = config.get_stash_api()

        # Create our own database instance that will connect to the same shared memory
        # as main's database (when using the same creator_name)
        database = Database(config, creator_name=state.creator_name)

        # We always own our database instance
        owns_db = True

        instance = cls(
            config=config,
            state=state_copy,
            stash_interface=stash_interface,
            database=database,
            _background_task=None,
            _cleanup_event=asyncio.Event(),
            _owns_db=owns_db,
        )
        return instance

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
        self.config._background_tasks.append(self._background_task)

    async def _safe_background_processing(
        self,
        account: Account | None,
        performer: Performer | None,
    ) -> None:
        """Safely handle background processing with cleanup."""
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

    async def cleanup(self) -> None:
        """Safely cleanup resources.

        This method:
        1. Cancels any background processing
        2. Waits for cleanup event
        3. Syncs and closes database if we own it
        4. Cleans up logging

        Note: Does not close global database when separate_metadata=False
        """
        try:
            # Cancel and wait for background task
            if self._background_task and not self._background_task.done():
                self._background_task.cancel()
                if self._cleanup_event:
                    await self._cleanup_event.wait()

            # Only cleanup database if we own it (separate_metadata=True)
            if self._owns_db and self.database is not None:
                # Ensure all processing is done before cleanup
                if hasattr(self.database, "optimized_storage"):
                    # Commit any pending transactions
                    with self.database.get_sync_session() as session:
                        session.commit()
                # Now close the database
                await self.database.cleanup()

        finally:
            # Always cleanup logging
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)

    async def scan_creator_folder(self) -> None:
        """Scan the creator's folder for media files.

        This method initiates a Stash metadata scan with specific flags for
        generating various media assets (covers, previews, thumbnails, etc.).

        Raises:
            RuntimeError: If the metadata scan fails
        """
        # Ensure we have a valid download path
        if not self.state.download_path:
            print_info("No download path set, attempting to create one...")
            try:
                self.state.download_path = set_create_directory_for_download(
                    self.config, self.state
                )
                print_info(f"Created download path: {self.state.download_path}")
            except Exception as e:
                print_error(f"Failed to create download path: {e}")
                return

        scan_metadata_input = {
            "rescan": False,
            "scanGenerateCovers": True,
            "scanGeneratePreviews": True,
            "scanGenerateThumbnails": True,
            "scanGenerateImagePreviews": True,
            "scanGenerateSprites": True,
            "scanGeneratePhashes": True,
            "scanGenerateClipPreviews": True,
        }

        try:
            job_id = self.stash_interface.metadata_scan(
                paths=[str(self.state.download_path)],
                flags=scan_metadata_input,
            )
            print_info(f"Metadata scan job ID: {job_id}")

            finished_job = False
            while not finished_job:
                try:
                    # waiting for job to finish to make sure stash knows all of the recently downloaded files
                    finished_job = self.stash_interface.wait_for_job(job_id)
                except Exception:
                    finished_job = False

        except RuntimeError as e:
            raise RuntimeError(f"Failed to process metadata: {e}") from e

    @with_session()
    async def _find_account(self, session: Session | None = None) -> Account | None:
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

    def _find_existing_performer(self, account: Account) -> dict | None:
        """Find existing performer in Stash.

        Args:
            account: Account to find performer for

        Returns:
            Performer data if found, None otherwise
        """
        # Try finding by stash_id first
        if account.stash_id:
            performer_data = self.stash_interface.find_performer(account.stash_id)
            if performer_data:
                debug_print(
                    {
                        "method": "StashProcessing - _find_existing_performer",
                        "stash_id": account.stash_id,
                        "performer_data": performer_data,
                    }
                )
                return performer_data.get("findPerformer", None)
        performer_data = self.stash_interface.find_performer(account.username)
        debug_print(
            {
                "method": "StashProcessing - _find_existing_performer",
                "username": account.username,
                "performer_data": performer_data,
            }
        )
        return performer_data.get("findPerformer", None) if performer_data else None

    def _create_performer(self, account: Account) -> Performer:
        """Create new performer in Stash.

        Args:
            account: Account to create performer for

        Returns:
            Created performer

        Raises:
            ValueError: If performer creation fails
        """
        performer = Performer(
            id="new",  # Will be replaced with actual ID after creation
            name=account.displayName or account.username,
            disambiguation=account.username,  # Use disambiguation instead of aliases
            details=account.about,
            urls=[f"https://fansly.com/{account.username}/posts"],
            country=account.location,
        )

        try:
            created_data = performer.stash_create(self.stash_interface)
            if not created_data or "id" not in created_data:
                raise ValueError("Invalid response from Stash API - missing ID")

            performer = Performer.from_dict(created_data)
            if not performer.id:
                raise ValueError("Failed to set performer ID")

            print_info(f"Created performer: {performer.name} with ID: {performer.id}")
            return performer
        except Exception as e:
            # Check if error is due to performer already existing
            error_str = str(e).lower()
            if "already exists" in error_str:
                # Try to find the existing performer again
                performer_data = self.stash_interface.find_performer(account.username)
                if performer_data and "findPerformer" in performer_data:
                    performer = Performer.from_dict(performer_data["findPerformer"])
                    if performer.id:
                        print_info(
                            f"Using existing performer: {performer.name} with ID: {performer.id}"
                        )
                        return performer
            # If not a duplicate error or couldn't find existing performer, re-raise
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
            avatar_stash_obj = self.stash_interface.find_images(
                f={
                    "path": {
                        "modifier": "INCLUDES",
                        "value": account.avatar.local_filename,
                    }
                },
                filter={
                    "per_page": -1,
                    "sort": "created_at",
                    "direction": "DESC",
                },
            )
            avatar = Image.from_dict(avatar_stash_obj)
            avatar_path = Path(avatar.visual_files[0].file.path)
            try:
                from stashapi.tools import file_to_base64

                # Convert image to base64
                image_base64 = file_to_base64(str(avatar_path))

                # Update performer with new image
                performer_input = performer.to_update_input_dict()
                performer_input["image"] = image_base64

                self.stash_interface.update_performer(performer_input)
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

    def _get_performer(self, performer_data: dict) -> Performer:
        """Get performer from performer data.

        Args:
            performer_data: Raw performer data from Stash

        Returns:
            Performer instance

        Raises:
            ValueError: If performer data is invalid
        """
        if not performer_data or "id" not in performer_data:
            raise ValueError("Invalid performer data - missing ID")

        performer = Performer.from_dict(performer_data)
        if not performer.id:
            raise ValueError("Found performer missing ID")

        print_info(f"Found performer: {performer.name} with ID: {performer.id}")
        return performer

    @with_session()
    async def process_creator(
        self,
        session: Session | None = None,
    ) -> tuple[Account | None, Performer | None]:
        """Process initial creator metadata and create/update Stash performer.

        This method handles the initial setup of account and performer data.
        Further processing (studio, posts, messages) is handled asynchronously
        by continue_stash_processing.

        This method:
        1. Retrieves account information from the database
        2. Finds or creates a corresponding performer in Stash
        3. Updates performer information and avatar if needed

        Args:
            session: Optional database session to use

        Returns:
            A tuple containing the Account and Performer objects, or (None, None) if processing fails.
            On success, these objects are passed to continue_stash_processing for background processing.
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

            # Find or create performer
            performer_data = self._find_existing_performer(account)
            debug_print(
                {
                    "method": "StashProcessing - process_creator",
                    "performer_data": performer_data,
                }
            )
            performer = (
                self._create_performer(account)
                if performer_data is None
                else self._get_performer(performer_data)
            )
            debug_print(
                {
                    "method": "StashProcessing - process_creator",
                    "performer": performer,
                }
            )
            # Handle avatar if needed
            await self._update_performer_avatar(account, performer)

            return (account, performer)
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

    async def continue_stash_processing(
        self, account: Account | None, performer: Performer | None
    ) -> None:
        """Continue processing in the background.

        This method:
        1. Updates the account's stash_id if needed
        2. Processes creator studio
        3. Processes posts and messages sequentially

        Args:
            account: The Account object to update
            performer: The Performer object containing the stash ID
        """
        print_info("Continuing Stash GraphQL processing in the background...")
        try:
            if not account or not performer:
                raise ValueError("Missing account or performer data")

            # Update account's stash ID if needed
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
            if self._owns_db:
                self.database.close()

    @with_session()
    async def _update_account_stash_id(
        self,
        account: Account,
        performer: Performer,
        session: Session | None = None,
    ) -> None:
        """Update account's stash ID.

        Args:
            account: Account to update
            performer: Performer containing the stash ID
            session: Optional database session to use
        """
        async with self.database.get_async_session() as local_session:
            # Use provided session or local session
            session = session or local_session
            account.stash_id = performer.id
            session.add(account)
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
        fansly_studio_dict = self.stash_interface.find_studio("Fansly (network)")
        debug_print(
            {
                "method": "StashProcessing - process_creator_studio",
                "fansly_studio_dict": fansly_studio_dict,
            }
        )
        if fansly_studio_dict is not None:
            fansly_studio = Studio.from_dict(
                fansly_studio_dict.get("findStudio", fansly_studio_dict)
            )
            debug_print(
                {
                    "method": "StashProcessing - process_creator_studio",
                    "fansly_studio": fansly_studio,
                }
            )

            # Find or create creator's studio
            creator_studio_name = f"{account.username} (Fansly)"
            studio_data = self.stash_interface.find_studio(creator_studio_name)

            if studio_data is None:
                # Create new studio
                studio = Studio(
                    id="new",
                    name=creator_studio_name,
                    parent_studio=fansly_studio,
                    url=f"https://fansly.com/{account.username}",
                )
                created_data = studio.stash_create(self.stash_interface)
                if not created_data:
                    print_error(f"Failed to create studio for {account.username}")
                    return None
                studio = Studio.from_dict(created_data)
            else:
                # Use existing studio
                studio = Studio.from_dict(studio_data.get("findStudio", studio_data))
                if not studio.parent_studio:
                    studio.parent_studio = fansly_studio
                    studio.save(self.stash_interface)

            debug_print(
                {
                    "method": "StashProcessing - process_creator_studio",
                    "studio": studio,
                }
            )
            return studio

    @with_session()
    async def _process_items_with_gallery(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None,
        item_type: str,
        items: list[HasMetadata],
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

    async def process_creator_posts(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None = None,
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

        def get_post_url(account: Account, post: Post, _: Any) -> str:
            return f"https://fansly.com/{account.username}/posts/{post.id}"

        # Get all posts in one query
        async with self.database.get_async_session() as session:
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
                await self._process_items_with_gallery(
                    account=account,
                    performer=performer,
                    studio=studio,
                    item_type="post",
                    items=batch,
                    url_pattern_func=get_post_url,
                    session=session,
                )
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
                            selectinload(Message.accountMentions),
                        )
                    )
                    result = await session.execute(stmt)
                    messages = result.unique().scalars().all()

                    # Process messages in batches
                    batch_size = 50
                    for j in range(0, len(messages), batch_size):
                        batch = messages[j : j + batch_size]
                        await self._process_items_with_gallery(
                            account=account,
                            performer=performer,
                            studio=studio,
                            item_type="group_message",
                            items=batch,
                            url_pattern_func=get_message_url,
                            session=session,
                        )
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

    async def _count_total_media(self, attachments: list) -> int:
        """Count total media items in attachments (excluding previews).

        Args:
            attachments: List of attachments to count media in

        Returns:
            Total number of media items
        """
        total_media = 0
        attachment: Attachment
        for attachment in attachments:
            if await attachment.awaitable_attrs.media:
                total_media += 1
            if await attachment.awaitable_attrs.bundle:
                bundle = await attachment.awaitable_attrs.bundle
                bundle_media = await bundle.awaitable_attrs.accountMedia
                total_media += len(bundle_media)
            if await attachment.awaitable_attrs.aggregated_post:
                agg_posts = await attachment.awaitable_attrs.aggregated_post
                for agg_post in agg_posts:
                    agg_attachments = await agg_post.awaitable_attrs.attachments
                    for agg_attachment in agg_attachments:
                        if await agg_attachment.awaitable_attrs.media:
                            total_media += 1
                        if await agg_attachment.awaitable_attrs.bundle:
                            agg_bundle = await agg_attachment.awaitable_attrs.bundle
                            agg_bundle_media = (
                                await agg_bundle.awaitable_attrs.accountMedia
                            )
                            total_media += len(agg_bundle_media)
        return total_media

    async def _check_direct_media(
        self, attachment: Attachment, media_obj: AccountMedia
    ) -> tuple[bool, int]:
        """Check if media object is directly attached.

        Args:
            attachment: Attachment to check
            media_obj: Media object to find

        Returns:
            Tuple of (found, position_increment)
        """
        if not await attachment.awaitable_attrs.media:
            return (False, 0)

        media = await attachment.awaitable_attrs.media
        return (media == media_obj, 1)

    async def _check_bundle_media(
        self, attachment: Attachment, media_obj: AccountMedia
    ) -> tuple[bool, int]:
        """Check if media object is in a bundle.

        Args:
            attachment: Attachment to check
            media_obj: Media object to find

        Returns:
            Tuple of (found, position_increment)
        """
        if not await attachment.awaitable_attrs.bundle:
            return (False, 0)

        bundle = await attachment.awaitable_attrs.bundle
        bundle_media = await bundle.awaitable_attrs.accountMedia

        if media_obj in bundle_media:
            return (True, bundle_media.index(media_obj))
        return (False, len(bundle_media))

    async def _check_aggregated_media(
        self, attachment: Attachment, media_obj: AccountMedia
    ) -> tuple[bool, int]:
        """Check if media object is in aggregated posts.

        Args:
            attachment: Attachment to check
            media_obj: Media object to find

        Returns:
            Tuple of (found, position_increment)
        """
        if not await attachment.awaitable_attrs.aggregated_post:
            return (False, 0)

        position = 0
        agg_posts = await attachment.awaitable_attrs.aggregated_post

        for agg_post in agg_posts:
            agg_attachments = await agg_post.awaitable_attrs.attachments
            for agg_attachment in agg_attachments:
                # Check direct media in aggregated post
                found, increment = await self._check_direct_media(
                    agg_attachment, media_obj
                )
                if found:
                    return (True, position + increment)
                position += increment

                # Check bundle media in aggregated post
                found, increment = await self._check_bundle_media(
                    agg_attachment, media_obj
                )
                if found:
                    return (True, position + increment)
                position += increment

        return (False, position)

    async def _find_media_position(
        self, attachments: list[Attachment], media_obj: AccountMedia
    ) -> int:
        """Find position of media object in attachments.

        Args:
            attachments: List of attachments to search in
            media_obj: Media object to find

        Returns:
            Position of media object (1-based)
        """
        current_pos = 1

        for attachment in attachments:
            # Check direct media
            found, increment = await self._check_direct_media(attachment, media_obj)
            if found:
                break
            current_pos += increment

            # Check bundle media
            found, increment = await self._check_bundle_media(attachment, media_obj)
            if found:
                current_pos += increment
                break
            current_pos += increment

            # Check aggregated media
            found, increment = await self._check_aggregated_media(attachment, media_obj)
            if found:
                current_pos += increment
                break
            current_pos += increment

        return current_pos

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
        """Get existing gallery or create a new one.

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
            print_info(f"{item_type.title()} {item.id} already processed")
            gallery_data = self.stash_interface.find_gallery(item.stash_id)
            if gallery_data:
                gallery = Gallery.from_dict(
                    gallery_data.get("findGallery", gallery_data)
                )
                if not gallery:
                    print_error(f"Failed to load gallery for {item_type} {item.id}")
                return gallery

        # Create new gallery
        return Gallery(
            id="new",
            title=f"{item_type.title()} from {account.username} - {item.id}",
            details=item.content if item.content else None,
            date=item.createdAt,
            urls=[url_pattern],
            studio=studio,
            performers=[performer],
        )

    @with_session()
    async def _update_gallery_files(
        self,
        gallery: Gallery,
        files: list[Image | Scene],
        item: HasMetadata,
        session: Session | None = None,
    ) -> None:
        """Update gallery with files.

        Args:
            gallery: The gallery to update
            files: List of files to add
            item: Item being processed
            session: Optional database session to use
        """
        gallery.files = files
        if gallery.id == "new":
            # Create gallery in Stash
            created_data = gallery.stash_create(self.stash_interface)
            if not created_data:
                print_error(f"Failed to create gallery for item {item.id}")
                return
            gallery.id = created_data
        else:
            # Update existing gallery
            gallery.save(self.stash_interface)

        self.stash_interface.add_gallery_images(
            gallery_id=gallery.id,
            image_ids=[f.id for f in files if isinstance(f, Image)],
        )
        for f in files:
            if isinstance(f, Scene):
                f.galleries.append(gallery)
                f.save(self.stash_interface)

        # Merge item into current session if it's attached to a different one
        if item in session:
            item.stash_id = gallery.id
        else:
            merged_item = await session.merge(item)
            merged_item.stash_id = gallery.id
        await session.commit()

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

    async def _add_performers_from_mentions(
        self, file: Scene | Image, mentions: list
    ) -> None:
        """Add performers to file from mentions.

        Args:
            file: Scene or Image to update
            mentions: List of account mentions
        """
        for mention in mentions:
            performer_data = self.stash_interface.find_performer(mention.username)
            if performer_data:
                performer = Performer.from_dict(
                    performer_data.get("findPerformer", performer_data)
                )
                if performer not in file.performers:
                    file.performers.append(performer)

    @with_session()
    async def _add_tags_from_hashtags(
        self,
        file: Scene | Image,
        hashtags: list,
        session: Session | None = None,
    ) -> None:
        """Add tags to file from hashtags.

        Args:
            file: Scene or Image to update
            hashtags: List of hashtags
            session: Optional database session to use
        """
        hashtag: Hashtag
        for hashtag in hashtags:
            tag_name = hashtag.value.title()
            tag_data = self.stash_interface.find_tag(tag_name, create=True)
            if tag_data:
                tag = Tag.from_dict(tag_data.get("findTag", tag_data))
                hashtag.stash_id = tag.id
                session.add(hashtag)
                await session.flush()
                if tag not in file.tags:
                    file.tags.append(tag)

    def _add_preview_tag(self, file: Scene | Image) -> None:
        """Add preview/trailer tag to file.

        Args:
            file: Scene or Image to update
        """
        preview_tag_data = self.stash_interface.find_tag("Preview/Trailer", create=True)
        if preview_tag_data:
            preview_tag = Tag.from_dict(
                preview_tag_data.get("findTag", preview_tag_data)
            )
            if preview_tag not in file.tags:
                file.tags.append(preview_tag)

    @with_session()
    async def _get_message_username(
        self, message: Message, session: Session | None = None
    ) -> str:
        """Get username from message.

        Args:
            message: Message to get username from
            session: Optional database session to use

        Returns:
            Username of message sender
        """
        group = await message.awaitable_attrs.group
        group_users = await group.awaitable_attrs.users
        return next(
            (
                user.username
                for user in group_users
                if (user.id == message.senderId) or (user.id == message.recipientId)
            ),
            "Unknown User",
        )

    async def _get_content_username(
        self,
        content: Post | Message,
        session: Session | None = None,
    ) -> str:
        """Get username for content.

        Args:
            content: Post or Message to get username from
            session: Optional database session to use

        Returns:
            Username for the content
        """
        if isinstance(content, Post):
            account = await session.get(Account, content.accountId)
            return account.username if account else "Unknown User"
        else:  # Message
            return await self._get_message_username(message=content)

    @with_session()
    async def _update_content_metadata(
        self,
        file: Scene | Image,
        content: HasMetadata,
        media_obj: AccountMedia,
        session: Session | None = None,
    ) -> None:
        """Update file metadata from content.

        Args:
            file: Scene or Image to update
            content: Object implementing HasMetadata protocol
            media_obj: Media object being processed
            session: Optional database session to use
        """
        # Get media position info
        attachments = await content.awaitable_attrs.attachments
        total_media = await self._count_total_media(attachments)
        current_pos = await self._find_media_position(attachments, media_obj)

        # Get username and set title/details
        username = await self._get_content_username(content=content, session=session)
        file.title = self._generate_title_from_content(
            content.content, username, content.createdAt, current_pos, total_media
        )
        file.details = content.content if content.content else None

        # Add performers
        # Only Posts have accountMentions
        if isinstance(content, Post):
            mentioned_accounts = await content.awaitable_attrs.accountMentions
            if mentioned_accounts:
                await self._add_performers_from_mentions(file, mentioned_accounts)

        # Add hashtags for posts
        if isinstance(content, Post):
            hashtags = await content.awaitable_attrs.hashtags
            if hashtags:
                await self._add_tags_from_hashtags(
                    file=file,
                    hashtags=hashtags,
                    session=session,
                )

    @with_session()
    async def _update_file_metadata(
        self,
        file: Scene | Image,
        media_obj: AccountMedia,
        is_preview: bool = False,
        session: Session | None = None,
    ) -> None:
        """Update file metadata in Stash.

        Args:
            file: Scene or Image object to update
            media_obj: AccountMedia object containing metadata
            is_preview: Whether this is a preview/trailer file
            session: Optional database session
        """
        # Find attachment that contains this media
        attachment = await session.execute(
            select(Attachment)
            .options(
                selectinload(Attachment.post),
                selectinload(Attachment.message),
            )
            .where(
                (Attachment.contentType == ContentType.ACCOUNT_MEDIA)
                & (Attachment.contentId == media_obj.id)
            )
        )
        attachment = attachment.scalar_one_or_none()

        # Update metadata based on source
        if attachment:
            content = attachment.post or attachment.message
            if content:
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
            self._add_preview_tag(file)

        # Save changes to Stash
        file.save(self.stash_interface)

    def _find_stash_file_by_id(
        self, stash_id: str, mime_type: str
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
        match mime_type:
            case str() as mime if mime.startswith("image"):
                stash_obj = self.stash_interface.find_image(stash_id)[0]
                return (stash_obj, Image.from_dict(stash_obj))
            case str() as mime if mime.startswith("video"):
                stash_obj = self.stash_interface.find_scene(stash_id)[0]
                return (stash_obj, Scene.from_dict(stash_obj))
            case str() as mime if mime.startswith("application"):
                stash_obj = self.stash_interface.find_scene(stash_id)[0]
                return (stash_obj, Scene.from_dict(stash_obj))
            case _:
                raise ValueError(f"Invalid media type: {mime_type}")

    async def _find_stash_file_by_path(
        self, media_file: Any, mime_type: str
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
            path_filter = {
                "path": {
                    "modifier": "INCLUDES",
                    "value": path,
                }
            }

            try:
                match mime_type:
                    case str() as mime if mime.startswith("image"):
                        results = self.stash_interface.find_images(
                            f=path_filter,
                            filter=filter_params,
                        )
                        if results:
                            return (results[0], Image.from_dict(results[0]))
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
                            results = self.stash_interface.find_scenes(
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

                            # Handle different response structures
                            if isinstance(results, dict):
                                # Handle nested GraphQL response format
                                find_scenes = results.get("findScenes", {})
                                if isinstance(find_scenes, dict):
                                    # Check count first
                                    count = find_scenes.get("count", 0)
                                    if count == 0:
                                        debug_print(
                                            {
                                                "method": "StashProcessing - _find_stash_file_by_path",
                                                "status": "no_scenes_found",
                                                "path": path,
                                                "count": 0,
                                            }
                                        )
                                        scenes = []
                                    else:
                                        scenes = find_scenes.get("scenes", [])
                                        if not scenes:
                                            debug_print(
                                                {
                                                    "method": "StashProcessing - _find_stash_file_by_path",
                                                    "status": "count_mismatch",
                                                    "path": path,
                                                    "count": count,
                                                    "actual_scenes": len(scenes),
                                                }
                                            )
                                else:
                                    scenes = []
                            elif isinstance(results, list):
                                # Handle direct list format
                                scenes = results
                            else:
                                scenes = []

                            if scenes and len(scenes) > 0:
                                scene_data = scenes[0]
                                return (scene_data, Scene.from_dict(scene_data))

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
                        results = self.stash_interface.find_scenes(
                            scene_filter=path_filter,
                            filter_=filter_params,
                        )
                        if results:
                            return (results[0], Scene.from_dict(results[0]))
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
                return self._find_stash_file_by_id(
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
    async def _process_media_to_files(
        self, media: AccountMedia, session: Session | None = None
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
                media.preview, media, is_preview=True
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
            stash_obj, file = await self._process_media_file(media.media, media)
            if stash_obj and file:
                await self._update_file_metadata(
                    file=file, media_obj=media, session=session
                )
                files.append(file)

        return files

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

    def __del__(self):
        """Ensure cleanup runs."""
        if self._cleanup_event and not self._cleanup_event.is_set():
            from .processing_fix import run_coroutine_threadsafe

            run_coroutine_threadsafe(self.cleanup())
