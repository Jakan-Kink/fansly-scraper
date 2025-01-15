"""Module for processing metadata and synchronizing with Stash."""

import asyncio
import logging
import sys
import threading
import traceback
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from pprint import pformat
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm.session import Session
from sqlalchemy.sql.expression import func, select

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
from pathio import set_create_directory_for_download
from textio import print_error, print_info
from textio.logging import SizeAndTimeRotatingFileHandler

from .file import FileType, VisualFile
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
    accountMentions: list[Account]


# Thread-safe logging setup
_logging_lock = threading.Lock()
logs_dir = Path.cwd() / "logs"
logs_dir.mkdir(exist_ok=True)
log_file = logs_dir / "stash_processing.log"

logger = logging.getLogger("fansly.stash.processing")
logger.handlers.clear()
logger.setLevel(logging.DEBUG)
logger.propagate = False

# File handler with rotation and thread safety
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
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter("%(levelname)s: %(message)s")
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)


def debug_print(obj):
    """Thread-safe debug printing."""
    try:
        with _logging_lock:
            formatted = pformat(obj, indent=2)
            logger.debug(formatted)
            for handler in logger.handlers:
                handler.flush()
    except Exception as e:
        print(f"Failed to log debug message: {e}", file=sys.stderr)


@dataclass
class StashProcessing:
    """Class for handling Stash metadata processing."""

    config: FanslyConfig
    state: DownloadState
    stash_interface: StashInterface
    database: Database
    db_path: Path
    _owns_db_connection: bool = False
    _background_task: asyncio.Task | None = None
    _cleanup_event: asyncio.Event | None = None

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

        # Handle database connection based on metadata mode
        if config.separate_metadata:
            # For separate metadata, create a new connection
            db_path = config.get_creator_database_path(state.creator_name)
            config_copy = deepcopy(config)
            config_copy.metadata_db_file = db_path
            database = Database(config_copy)
            owns_db = True
        else:
            # For global metadata, reuse the existing connection
            db_path = config.metadata_db_file
            database = config._database
            owns_db = False

        instance = cls(
            config=config,
            state=state_copy,
            stash_interface=stash_interface,
            database=database,
            db_path=db_path,
            _owns_db_connection=owns_db,
            _background_task=None,
            _cleanup_event=asyncio.Event(),
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
        self, account: Account | None, performer: Performer | None
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
        """Safely cleanup resources."""
        if self._background_task and not self._background_task.done():
            self._background_task.cancel()
            if self._cleanup_event:
                await self._cleanup_event.wait()

        if self._owns_db_connection:
            self.database.close()

        with _logging_lock:
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
                paths=[str(self.state.download_path)], flags=scan_metadata_input
            )
            print_info(f"Metadata scan job ID: {job_id}")

            finished_job = False
            while not finished_job:
                try:
                    finished_job = self.stash_interface.wait_for_job(job_id)
                except Exception:
                    finished_job = False

        except RuntimeError as e:
            raise RuntimeError(f"Failed to process metadata: {e}") from e

    async def _find_account(self, session: Session) -> Account | None:
        """Find account in database.

        Args:
            session: Database session to use

        Returns:
            Account if found, None otherwise
        """
        if self.state.creator_id is not None:
            stmt = select(Account).where(Account.id == int(self.state.creator_id))
        else:
            stmt = select(Account).where(
                func.lower(Account.username) == func.lower(self.state.creator_name)
            )
        account = await session.execute(stmt)
        account = account.scalar_one_or_none()
        if not account:
            print_info(f"No account found for username: {self.state.creator_name}")
            return None
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

        created_data = performer.stash_create(self.stash_interface)
        if not created_data or "id" not in created_data:
            raise ValueError("Invalid response from Stash API - missing ID")

        performer = Performer.from_dict(created_data)
        if not performer.id:
            raise ValueError("Failed to set performer ID")

        print_info(f"Created performer: {performer.name} with ID: {performer.id}")
        return performer

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

    async def process_creator(self) -> tuple[Account | None, Performer | None]:
        """Process creator metadata and create/update Stash performer.

        This method:
        1. Retrieves account information from the database
        2. Finds or creates a corresponding performer in Stash
        3. Updates performer information if needed

        Returns:
            A tuple containing the Account and Performer objects, or (None, None) if processing fails
        """
        async with self.database.get_async_session() as session:
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
                    return (None, None)

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
                print_error(
                    f"Failed to process creator: {e} - {traceback.format_exc()}"
                )
                return (None, None)

    async def continue_stash_processing(
        self, account: Account | None, performer: Performer | None
    ) -> None:
        """Continue processing in the background.

        This method:
        1. Updates the account's stash_id if needed
        2. Performs any necessary cleanup

        Args:
            account: The Account object to update
            performer: The Performer object containing the stash ID
        """
        print_info("Continuing Stash GraphQL processing in the background...")
        try:
            if account and performer and account.stash_id != performer.id:
                async with self.database.get_async_session() as session:
                    account.stash_id = performer.id
                    await session.commit()
            if not account or not performer:
                raise ValueError("Missing account or performer data")
            print_info("Processing creator Studio...")
            studio = await self.process_creator_studio(account, performer)
            print_info("Processing creator posts...")
            await self.process_creator_posts(account, performer, studio)
            print_info("Processing creator messages...")
            await self.process_creator_messages(account, performer, studio)
        finally:
            if self._owns_db_connection:
                self.database.close()

    async def process_creator_studio(
        self, account: Account, performer: Performer
    ) -> Studio | None:
        """Process creator studio metadata.

        This method:
        1. Finds or creates a corresponding studio in Stash
        2. Updates studio information if needed

        Args:
            account: The Account object
            performer: The Performer object
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
        # Find or create studio
        studio_data = self.stash_interface.find_studio(
            f"{account.username} (Fansly)", create=True
        )
        debug_print(
            {
                "method": "StashProcessing - process_creator_studio",
                "studio_data": studio_data,
            }
        )
        if studio_data is None:
            raise ValueError("Failed to find or create studio")
        studio = Studio.from_dict(studio_data.get("findStudio", studio_data))
        debug_print(
            {
                "method": "StashProcessing - process_creator_studio",
                "studio": studio,
            }
        )
        if not studio.id:
            raise ValueError("Failed to set studio ID")
        if not studio.parent_studio:
            studio.parent_studio = fansly_studio
            studio.save(self.stash_interface)
        return studio

    async def process_creator_posts(
        self, account: Account, performer: Performer, studio: Studio | None
    ) -> None:
        """Process creator post metadata.

        This method:
        1. Retrieves post information from the database
        2. Creates galleries for posts with media
        3. Links media files to galleries
        4. Associates galleries with performer and studio

        Args:
            account: The Account object
            performer: The Performer object
            studio: The Studio object
        """
        debug_print(
            {"method": "StashProcessing - process_creator_posts", "state": "entry"}
        )
        async with self.database.get_async_session() as session:
            try:
                # Get all posts with attachments
                stmt = (
                    select(Post)
                    .join(Post.attachments)
                    .where(Post.accountId == account.id)
                )
                posts = await session.execute(stmt)
                posts = posts.scalars().all()

                for post in posts:
                    try:
                        # Get post attachments
                        attachments: list[Attachment] = (
                            await post.awaitable_attrs.attachments or []
                        )
                        if not attachments:
                            continue
                        gallery = None
                        if post.stash_id:
                            print_info(f"Post {post.id} already processed")
                            gallery_data = self.stash_interface.find_gallery(
                                post.stash_id
                            )
                            if gallery_data:
                                gallery = Gallery.from_dict(
                                    gallery_data.get("findGallery", gallery_data)
                                )
                                if not gallery:
                                    print_error(
                                        f"Failed to load gallery for post {post.id}"
                                    )
                        if not gallery:
                            # Create gallery for this post
                            gallery = Gallery(
                                id="new",  # Will be replaced after creation
                                title=f"Post from {account.username} - {post.id}",
                                details=post.content if post.content else None,
                                date=post.createdAt,
                                urls=[
                                    f"https://fansly.com/{account.username}/posts/{post.id}"
                                ],
                                studio=studio,
                                performers=[performer],  # Link to creator
                            )

                        # Process attachments and add files to gallery
                        files = []
                        attachment: Attachment
                        for attachment in attachments:
                            attachment_files = await self.process_creator_attachment(
                                session=session,
                                attachment=attachment,
                                post=post,
                                account=account,
                            )
                            files.extend(attachment_files)

                        if not files:
                            continue

                        # Add files to gallery
                        gallery.files = files
                        if gallery.id == "new":
                            # Create gallery in Stash
                            created_data = gallery.stash_create(self.stash_interface)
                            if not created_data:
                                print_error(
                                    f"Failed to create gallery for post {post.id}"
                                )
                                continue
                            gallery.id = created_data

                            debug_print(
                                {
                                    "method": "StashProcessing - process_creator_posts",
                                    "status": "gallery_created",
                                    "post_id": post.id,
                                    "gallery_data": created_data,
                                }
                            )
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

                        post.stash_id = gallery.id
                        session.add(post)
                        await session.commit()

                    except Exception as e:
                        print_error(f"Failed to process post {post.id}: {e}")
                        debug_print(
                            {
                                "method": "StashProcessing - process_creator_posts",
                                "status": "post_processing_failed",
                                "post_id": post.id,
                                "error": str(e),
                                "traceback": traceback.format_exc(),
                            }
                        )
                        continue

            except Exception as e:
                logger.error(f"Failed to process posts: {e}")
                debug_print(
                    {
                        "method": "StashProcessing - process_creator_posts",
                        "status": "posts_processing_failed",
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
                )

    async def process_creator_messages(
        self, account: Account, performer: Performer, studio: Studio | None
    ) -> None:
        """Process creator message metadata.

        This method:
        1. Retrieves message information from the database
        2. Creates galleries for messages with media
        3. Links media files to galleries
        4. Associates galleries with performer and studio

        Args:
            account: The Account object
            performer: The Performer object
            studio: The Studio object
        """
        debug_print(
            {"method": "StashProcessing - process_creator_messages", "state": "entry"}
        )
        async with self.database.get_async_session() as session:
            try:
                # Get all groups with messages that have attachments
                stmt = (
                    select(Group)
                    .join(Group.users)
                    .join(Group.messages)
                    .join(Message.attachments)
                    .where(Group.users.any(Account.id == account.id))
                )
                groups = await session.execute(stmt)
                groups = groups.scalars().all()

                for group in groups:
                    messages = await group.awaitable_attrs.messages
                    message: Message
                    for message in messages:
                        try:
                            attachments: list[Attachment] = (
                                await message.awaitable_attrs.attachments or []
                            )
                            if not attachments:
                                continue

                            if message.stash_id:
                                print_info(f"Message {message.id} already processed")
                                gallery_data = self.stash_interface.find_gallery(
                                    message.stash_id
                                )
                                if gallery_data:
                                    gallery = Gallery.from_dict(
                                        gallery_data.get("findGallery", gallery_data)
                                    )
                                    if not gallery:
                                        print_error(
                                            f"Failed to load gallery for message {message.id}"
                                        )
                            if not gallery:
                                # Create gallery for this message
                                gallery = Gallery(
                                    id="new",  # Will be replaced after creation
                                    title=f"Message from {account.username} - {message.id}",
                                    details=(
                                        message.content if message.content else None
                                    ),
                                    date=message.createdAt,
                                    urls=[f"https://fansly.com/messages/{group.id}"],
                                    studio=studio,
                                    performers=[performer],  # Link to creator
                                )

                            # Process attachments and add files to gallery
                            files = []
                            attachment: Attachment
                            for attachment in attachments:
                                attachment_files = (
                                    await self.process_creator_attachment(
                                        session=session,
                                        attachment=attachment,
                                        post=message,
                                        account=account,
                                    )
                                )
                                files.extend(attachment_files)

                            if not files:
                                continue

                            # Add files to gallery
                            gallery.files = files
                            if gallery.id == "new":
                                # Create gallery in Stash
                                created_data = gallery.stash_create(
                                    self.stash_interface
                                )
                                if not created_data:
                                    print_error(
                                        f"Failed to create gallery for message {message.id}"
                                    )
                                    continue
                                gallery.id = created_data

                                debug_print(
                                    {
                                        "method": "StashProcessing - process_creator_messages",
                                        "status": "gallery_created",
                                        "message_id": message.id,
                                        "gallery_data": created_data,
                                    }
                                )
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

                            message.stash_id = gallery.id
                            session.add(message)
                            await session.commit()
                        except Exception as e:
                            print_error(f"Failed to process message {message.id}: {e}")
                            debug_print(
                                {
                                    "method": "StashProcessing - process_creator_messages",
                                    "status": "message_processing_failed",
                                    "message_id": message.id,
                                    "error": str(e),
                                    "traceback": traceback.format_exc(),
                                }
                            )
                            continue

            except Exception as e:
                logger.error(f"Failed to process messages: {e}")
                debug_print(
                    {
                        "method": "StashProcessing - process_creator_messages",
                        "status": "messages_processing_failed",
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

    async def _find_media_position(
        self, attachments: list, media_obj: AccountMedia
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
            if await attachment.awaitable_attrs.media:
                media = await attachment.awaitable_attrs.media
                if media == media_obj:
                    break
                current_pos += 1
            if await attachment.awaitable_attrs.bundle:
                bundle = await attachment.awaitable_attrs.bundle
                bundle_media = await bundle.awaitable_attrs.accountMedia
                if media_obj in bundle_media:
                    current_pos += bundle_media.index(media_obj)
                    break
                current_pos += len(bundle_media)
            if await attachment.awaitable_attrs.aggregated_post:
                agg_posts = await attachment.awaitable_attrs.aggregated_post
                for agg_post in agg_posts:
                    agg_attachments = await agg_post.awaitable_attrs.attachments
                    for agg_attachment in agg_attachments:
                        if await agg_attachment.awaitable_attrs.media:
                            media = await agg_attachment.awaitable_attrs.media
                            if media == media_obj:
                                break
                            current_pos += 1
                        if await agg_attachment.awaitable_attrs.bundle:
                            agg_bundle = await agg_attachment.awaitable_attrs.bundle
                            agg_bundle_media = (
                                await agg_bundle.awaitable_attrs.accountMedia
                            )
                            if media_obj in agg_bundle_media:
                                current_pos += agg_bundle_media.index(media_obj)
                                break
                            current_pos += len(agg_bundle_media)
        return current_pos

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

    async def _add_tags_from_hashtags(
        self, file: Scene | Image, hashtags: list, session: Session
    ) -> None:
        """Add tags to file from hashtags.

        Args:
            file: Scene or Image to update
            hashtags: List of hashtags
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

    async def _get_message_username(self, message: Message) -> str:
        """Get username from message.

        Args:
            message: Message to get username from

        Returns:
            Username of message sender
        """
        group = await message.awaitable_attrs.group
        group_users = await group.awaitable_attrs.users
        return next(
            (user.username for user in group_users if user.id == message.userId),
            "Unknown User",
        )

    async def _get_content_username(
        self, content: Post | Message, session: Session
    ) -> str:
        """Get username for content.

        Args:
            content: Post or Message to get username from
            session: Database session to use

        Returns:
            Username for the content
        """
        if isinstance(content, Post):
            account = await session.get(Account, content.accountId)
            return account.username if account else "Unknown User"
        else:  # Message
            return await self._get_message_username(content)

    async def _update_content_metadata(
        self,
        file: Scene | Image,
        content: HasMetadata,
        media_obj: AccountMedia,
        session: Session,
    ) -> None:
        """Update file metadata from content.

        Args:
            file: Scene or Image to update
            content: Object implementing HasMetadata protocol
            media_obj: Media object being processed
            session: Database session
        """
        # Get media position info
        attachments = await content.awaitable_attrs.attachments
        total_media = await self._count_total_media(attachments)
        current_pos = await self._find_media_position(attachments, media_obj)

        # Get username and set title/details
        username = await self._get_content_username(content, session=session)
        file.title = self._generate_title_from_content(
            content.content, username, content.createdAt, current_pos, total_media
        )
        file.details = content.content if content.content else None

        # Add performers
        mentioned_accounts = await content.awaitable_attrs.accountMentions
        if mentioned_accounts:
            await self._add_performers_from_mentions(file, mentioned_accounts)

        # Add hashtags for posts
        if isinstance(content, Post):
            hashtags = await content.awaitable_attrs.hashtags
            if hashtags:
                await self._add_tags_from_hashtags(file, hashtags, session)

    async def _update_post_metadata(
        self,
        file: Scene | Image,
        post: Post,
        media_obj: AccountMedia,
        session: Session,
    ) -> None:
        """Update file metadata from post.

        Args:
            file: Scene or Image to update
            post: Post containing the media
            media_obj: Media object being processed
            session: Database session
        """
        await self._update_content_metadata(file, post, media_obj, session)

    async def _update_message_metadata(
        self,
        file: Scene | Image,
        message: Message,
        media_obj: AccountMedia,
        session: Session,
    ) -> None:
        """Update file metadata from message.

        Args:
            file: Scene or Image to update
            message: Message containing the media
            media_obj: Media object being processed
            session: Database session
        """
        await self._update_content_metadata(file, message, media_obj, session)

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
        if session is None:
            async with self.database.get_async_session() as session:
                await self._update_file_metadata_with_session(
                    file, media_obj, is_preview, session
                )
        else:
            await self._update_file_metadata_with_session(
                file, media_obj, is_preview, session
            )

    async def _update_file_metadata_with_session(
        self,
        file: Scene | Image,
        media_obj: AccountMedia,
        is_preview: bool,
        session: Session,
    ) -> None:
        """Update file metadata in Stash with a session.

        Args:
            file: Scene or Image object to update
            media_obj: AccountMedia object containing metadata
            is_preview: Whether this is a preview/trailer file
            session: Database session
        """
        # Find attachment that contains this media
        attachment = await session.execute(
            select(Attachment).where(
                (Attachment.contentType == ContentType.ACCOUNT_MEDIA)
                & (Attachment.contentId == media_obj.id)
            )
        )
        attachment = attachment.scalar_one_or_none()

        # Update metadata based on source
        if attachment:
            if attachment.post:
                await self._update_post_metadata(
                    file, attachment.post, media_obj, session
                )
            elif attachment.message:
                await self._update_message_metadata(
                    file, attachment.message, media_obj, session
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
                return stash_obj, Image.from_dict(stash_obj)
            case str() as mime if mime.startswith("video"):
                stash_obj = self.stash_interface.find_scene(stash_id)[0]
                return stash_obj, Scene.from_dict(stash_obj)
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
                            return results[0], Image.from_dict(results[0])
                    case str() as mime if mime.startswith("video"):
                        results = self.stash_interface.find_scenes(
                            scene_filter=path_filter,
                            filter_=filter_params,
                        )
                        if results:
                            return results[0], Scene.from_dict(results[0])
                    case _:
                        raise ValueError(f"Invalid media type: {mime_type}")
            except Exception as e:
                debug_print(
                    {
                        "method": "StashProcessing - _find_stash_file_by_path",
                        "status": "path_search_failed",
                        "path": path,
                        "error": str(e),
                    }
                )
                continue

        return None, None

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
        return None, None

    async def _process_media_to_files(
        self, media: AccountMedia, session: Session
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
                    file, media, is_preview=True, session=session
                )
                files.append(file)

        # Process main media
        if await media.awaitable_attrs.media:
            stash_obj, file = await self._process_media_file(media.media, media)
            if stash_obj and file:
                await self._update_file_metadata(file, media, session=session)
                files.append(file)

        return files

    async def process_creator_attachment(
        self,
        session: Session,
        attachment: Attachment,
        post: Post,
        account: Account,
    ) -> list[VisualFile]:
        """Process attachment into VisualFile objects.

        Args:
            session: Database session to use
            attachment: Attachment object to process
            post: Post object containing the attachment
            account: Account object containing the post

        Returns:
            List of VisualFile objects created from the attachment
        """
        files = []

        # Handle direct media
        if await attachment.awaitable_attrs.media:
            media: AccountMedia = attachment.media
            files.extend(await self._process_media_to_files(media, session))

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
                files.extend(await self._process_media_to_files(media, session))

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
                    session=session,
                    attachment=agg_attachment,
                    post=agg_post,
                    account=account,
                )
                files.extend(agg_files)

        return files

    def __del__(self):
        """Ensure cleanup runs."""
        if self._cleanup_event and not self._cleanup_event.is_set():
            # Create a new event loop if necessary
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            loop.run_until_complete(self.cleanup())
