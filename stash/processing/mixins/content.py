"""Content processing mixin for posts and messages."""

from __future__ import annotations

import traceback

from stash_graphql_client.types import Performer, Studio

from metadata import (
    Account,
    Attachment,
    Group,
    Media,
    Message,
    Post,
)
from metadata.models import get_store
from textio import print_error, print_info

from ...logging import debug_print
from ...logging import processing_logger as logger
from ..protocols import StashProcessingProtocol


class ContentProcessingMixin(StashProcessingProtocol):
    """Content processing for posts and messages."""

    async def process_creator_messages(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None = None,
    ) -> None:
        """Process creator message metadata.

        This method:
        1. Retrieves message information from the identity map / database
        2. Creates galleries for messages with media in parallel
        3. Links media files to galleries
        4. Associates galleries with performer and studio

        Args:
            account: The Account object
            performer: The Performer object
            studio: Optional Studio object
        """

        def get_message_url(message: Message) -> str:
            """Get URL for a message in a group."""
            return f"https://fansly.com/messages/{message.groupId}/{message.id}"

        store = get_store()
        account_id = account.id

        # Find groups this account belongs to — cache-first via filter()
        account_groups = store.filter(
            Group,
            lambda g: g.users and any(u.id == account_id for u in g.users),
        )
        if not account_groups:
            # Fallback: query DB (groups may not be fully loaded)
            all_groups = await store.find(Group)
            account_groups = [
                g
                for g in all_groups
                if g.users and any(u.id == account_id for u in g.users)
            ]

        account_group_ids = {g.id for g in account_groups}

        # Find messages in those groups that have attachments — cache-first
        messages = store.filter(
            Message,
            lambda m: m.groupId in account_group_ids and bool(m.attachments),
        )
        if not messages and account_group_ids:
            # Fallback: query DB
            db_messages = await store.find(Message, groupId__in=list(account_group_ids))
            messages = [m for m in db_messages if m.attachments]

        debug_print(
            {
                "status": "found_messages",
                "account_id": account_id,
                "group_count": len(account_group_ids),
                "message_count": len(messages),
            }
        )
        print_info(f"Processing {len(messages)} messages...")

        # Set up worker pool
        task_name, process_name, semaphore, queue = await self._setup_worker_pool(
            messages, "message"
        )

        async def process_message(message: Message) -> None:
            async with semaphore:
                try:
                    await self._process_items_with_gallery(
                        account=account,
                        performer=performer,
                        studio=studio,
                        item_type="message",
                        items=[message],
                        url_pattern_func=get_message_url,
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
            task_name=task_name,
            process_name=process_name,
            semaphore=semaphore,
            queue=queue,
            process_item=process_message,
        )

    async def process_creator_posts(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None = None,
    ) -> None:
        """Process creator post metadata.

        This method:
        1. Retrieves post information from the identity map / database
        2. Processes posts into Stash galleries
        3. Handles media attachments and bundles
        """
        store = get_store()
        account_id = account.id

        # Cache-first: filter posts from identity map
        posts = store.filter(
            Post,
            lambda p: p.accountId == account_id and bool(p.attachments),
        )
        if not posts:
            # Fallback: query DB for posts, then filter for attachments
            db_posts = await store.find(Post, accountId=account_id)
            posts = [p for p in db_posts if p.attachments]

        debug_print(
            {
                "status": "found_posts",
                "account_id": account_id,
                "post_count": len(posts),
            }
        )

        def get_post_url(post: Post) -> str:
            return f"https://fansly.com/post/{post.id}"

        print_info(f"Processing {len(posts)} posts...")

        # Set up worker pool
        task_name, process_name, semaphore, queue = await self._setup_worker_pool(
            posts, "post"
        )

        async def process_post(post: Post) -> None:
            async with semaphore:
                try:
                    await self._process_items_with_gallery(
                        account=account,
                        performer=performer,
                        studio=studio,
                        item_type="post",
                        items=[post],
                        url_pattern_func=get_post_url,
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
            task_name=task_name,
            process_name=process_name,
            semaphore=semaphore,
            queue=queue,
            process_item=process_post,
        )

    async def _collect_media_from_attachments(
        self,
        attachments: list[Attachment],
    ) -> list[Media]:
        """Collect all media objects from a list of attachments.

        Extracts all Media objects from attachments, including
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
            if attachment.bundle:
                if attachment.bundle.accountMedia:
                    for account_media in attachment.bundle.accountMedia:
                        if account_media.media:
                            media_list.append(account_media.media)
                        if account_media.preview:
                            media_list.append(account_media.preview)

                if attachment.bundle.preview:
                    media_list.append(attachment.bundle.preview)

            # Aggregated posts (recursively collect media)
            if (
                getattr(attachment, "is_aggregated_post", False)
                and attachment.aggregated_post
            ):
                agg_post = attachment.aggregated_post
                if agg_post.attachments:
                    agg_media = await self._collect_media_from_attachments(
                        agg_post.attachments
                    )
                    media_list.extend(agg_media)

        return media_list

    async def _process_items_with_gallery(
        self,
        account: Account,
        performer: Performer,
        studio: Studio | None,
        item_type: str,
        items: list[Message | Post],
        url_pattern_func: callable,
    ) -> None:
        """Process items (posts or messages) with gallery.

        Args:
            account: The Account object
            performer: The Performer object
            studio: Optional Studio object
            item_type: Type of item being processed ("post" or "message")
            items: List of items to process
            url_pattern_func: Function to generate URLs for items
        """
        debug_print(
            {
                "method": f"StashProcessing - process_creator_{item_type}s",
                "state": "entry",
                "count": len(items),
            }
        )

        for item in items:
            attachment_count = len(item.attachments) if item.attachments else 0

            try:
                debug_print(
                    {
                        "method": f"StashProcessing - process_creator_{item_type}s",
                        "status": f"processing_{item_type}",
                        f"{item_type}_id": item.id,
                        "attachment_count": attachment_count,
                    }
                )
                await self._process_item_gallery(
                    item=item,
                    account=account,
                    performer=performer,
                    studio=studio,
                    item_type=item_type,
                    url_pattern=url_pattern_func(item),
                )
                debug_print(
                    {
                        "method": f"StashProcessing - process_creator_{item_type}s",
                        "status": f"{item_type}_processed",
                        f"{item_type}_id": item.id,
                        "attachment_count": attachment_count,
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
                        "attachment_count": attachment_count,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
                )
                continue
