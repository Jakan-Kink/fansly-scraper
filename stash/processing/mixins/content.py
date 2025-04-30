"""Content processing mixin for posts and messages."""

from __future__ import annotations

import asyncio
import contextlib
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import select

from metadata import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Attachment,
    ContentType,
    Group,
    Media,
    Message,
    Post,
    with_session,
)
from textio import print_error, print_info

from ...logging import debug_print
from ...logging import processing_logger as logger
from ...types import Performer, Studio

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ContentProcessingMixin:
    """Content processing for posts and messages."""

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
            session: Optional database session to use
        """

        def get_message_url(group: Group) -> str:
            """Get URL for a message in a group."""
            return f"https://fansly.com/messages/{group.id}"

        # Get a fresh account instance bound to the session
        stmt = select(Account).where(Account.id == account.id)
        result = await session.execute(stmt)
        account = result.scalar_one()

        # Get all messages with attachments in one query with relationships
        # For an awaitable account, we need to get the id properly
        account_id = account.id
        if hasattr(account, "__await__"):
            # This might be an awaitable, try to access the id directly
            if hasattr(account, "id"):
                account_id = account.id

        stmt = (
            select(Message)
            .join(Message.attachments)  # Join to filter messages with attachments
            .join(Message.group)
            .join(Group.users)
            .where(Group.users.any(Account.id == account_id))
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

        # Set up worker pool
        task_pbar, process_pbar, semaphore, queue = await self._setup_worker_pool(
            messages, "message"
        )

        async def process_message(message: Message) -> None:
            async with semaphore:
                try:
                    # Ensure object is bound to the session
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

                    # Process the message
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

        # Run the worker pool
        await self._run_worker_pool(
            items=messages,
            task_pbar=task_pbar,
            process_pbar=process_pbar,
            semaphore=semaphore,
            queue=queue,
            process_item=process_message,
        )

        # We don't need to run batch processing again if it was already done in process_creator_posts
        # But if this method is called independently and batch processing is enabled, we should do it
        if (
            hasattr(self, "use_batch_processing")
            and self.use_batch_processing
            and (
                not hasattr(self, "_batch_processing_done")
                or not self._batch_processing_done
            )
        ):
            print_info("Starting batch processing of all media by mimetype...")
            batch_results = await self.process_account_media_by_mimetype(
                account=account,
                performer=performer,
                studio=studio,
                session=session,
            )
            print_info(
                f"Batch processing completed: {len(batch_results['images'])} images, {len(batch_results['scenes'])} scenes"
            )
            # Set flag to avoid duplicate batch processing
            self._batch_processing_done = True
        elif not hasattr(self, "use_batch_processing") or not self.use_batch_processing:
            print_info("Batch processing is disabled, skipping")
            # Clear the flag just to be sure
            self._batch_processing_done = False

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

        # Get account ID properly for query
        account_id = account.id
        if hasattr(account, "__await__"):
            # This might be an awaitable, try to access the id directly
            if hasattr(account, "id"):
                account_id = account.id

        # Now get posts with proper eager loading
        stmt = (
            select(Post)
            .join(Post.attachments)  # Join to filter posts with attachments
            .where(Post.accountId == account_id)
            .options(
                # Load attachments and their media content
                selectinload(Post.attachments)
                .selectinload(Attachment.media)
                .selectinload(AccountMedia.media),
                # Load bundle attachments and their media
                selectinload(Post.attachments)
                .selectinload(Attachment.bundle)
                .selectinload(AccountMediaBundle.accountMedia)
                .selectinload(AccountMedia.media),
                # Load account mentions
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

        # Set up worker pool
        task_pbar, process_pbar, semaphore, queue = await self._setup_worker_pool(
            posts, "post"
        )

        async def process_post(post: Post) -> None:
            async with semaphore:
                try:
                    # Ensure object is bound to the session
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

                    # Process the post
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

        # Run the worker pool
        await self._run_worker_pool(
            items=posts,
            task_pbar=task_pbar,
            process_pbar=process_pbar,
            semaphore=semaphore,
            queue=queue,
            process_item=process_post,
        )

        # After processing posts individually, do a batch processing of all media by mimetype if enabled
        # This will catch any media that might have been missed or not properly linked in the individual processing
        if hasattr(self, "use_batch_processing") and self.use_batch_processing:
            print_info("Starting batch processing of all media by mimetype...")
            batch_results = await self.process_account_media_by_mimetype(
                account=account,
                performer=performer,
                studio=studio,
                session=session,
            )
            print_info(
                f"Batch processing completed: {len(batch_results['images'])} images, {len(batch_results['scenes'])} scenes"
            )
            # Set flag to indicate batch processing has been done
            self._batch_processing_done = True
        else:
            print_info("Batch processing is disabled, skipping")
            # Clear the flag just to be sure
            self._batch_processing_done = False

    async def _collect_media_from_attachments(
        self,
        attachments: list[Attachment],
    ) -> list[Media]:
        """Collect all media objects from a list of attachments.

        This helper method extracts all Media objects from attachments, including
        direct media, bundles, and their variants, to enable batch processing.

        Args:
            attachments: List of Attachment objects to process

        Returns:
            List of Media objects collected from attachments
        """
        media_list = []

        for attachment in attachments:
            # Direct media
            if attachment.media:
                if attachment.media.media:
                    media_list.append(attachment.media.media)
                if attachment.media.preview:
                    media_list.append(attachment.media.preview)

            # Media bundles
            if hasattr(attachment, "bundle") and attachment.bundle:
                if hasattr(attachment.bundle, "awaitable_attrs"):
                    await attachment.bundle.awaitable_attrs.accountMedia
                    await attachment.bundle.awaitable_attrs.preview

                if hasattr(attachment.bundle, "accountMedia"):
                    for account_media in attachment.bundle.accountMedia:
                        if account_media.media:
                            media_list.append(account_media.media)
                        if account_media.preview:
                            media_list.append(account_media.preview)

                if attachment.bundle.preview:
                    media_list.append(attachment.bundle.preview)

            # Aggregated posts (recursively collect media)
            if hasattr(attachment, "is_aggregated_post") and getattr(
                attachment, "is_aggregated_post", False
            ):
                if hasattr(attachment, "awaitable_attrs"):
                    await attachment.awaitable_attrs.aggregated_post

                if attachment.aggregated_post:
                    agg_post = attachment.aggregated_post

                    if hasattr(agg_post, "awaitable_attrs"):
                        await agg_post.awaitable_attrs.attachments

                    if hasattr(agg_post, "attachments") and agg_post.attachments:
                        # Recursively collect media from aggregated post attachments
                        agg_media = await self._collect_media_from_attachments(
                            agg_post.attachments
                        )
                        media_list.extend(agg_media)

        return media_list

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
