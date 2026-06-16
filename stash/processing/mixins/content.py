"""Content processing mixin for posts and messages."""

from __future__ import annotations

from collections import defaultdict

from stash_graphql_client.types import is_set

from metadata import (
    Account,
    Attachment,
    Group,
    Media,
    Message,
    Post,
    PostMention,
)
from metadata.models import get_store

from ...logging import debug_print
from ..protocols import StashProcessingProtocol


# contentType codes that carry no downloadable media.
_NON_MEDIA_CONTENT_TYPES = {7, 7100}


class ContentProcessingMixin(StashProcessingProtocol):
    """Content processing for posts and messages."""

    def _reconstruct_attachment_lists(self) -> None:
        """Rebuild the ``has_many`` attachment lists a cold preload leaves empty.

        ``preload`` resolves ``belongs_to`` (autolink) and ``habtm`` (assoc
        tables) but never reconstructs ``has_many`` reverse-FK lists, so
        ``Post.attachments`` / ``Message.attachments`` come back empty from a
        cold identity map. STASH_ONLY runs no ``download_*`` populate, so the
        startup ``preload`` is the only thing warming the cache — leaving every
        post/message attachment-less and the gather (which filters on
        ``bool(.attachments)``) a silent no-op.

        Groups every cached ``Attachment`` by ``postId`` / ``messageId`` and
        assigns the ordered lists onto the cached owners. Idempotent: owners
        that already carry attachments are skipped, so after a normal download
        warmed the graph this is a no-op. Assigns without dirtying the owner
        (attachments is a relationship, excluded from DB writes) by updating the
        dirty-tracking snapshot alongside the field.
        """
        store = get_store()
        attachments = store.filter(Attachment)
        if not attachments:
            return

        by_post: dict[int, list[Attachment]] = defaultdict(list)
        by_message: dict[int, list[Attachment]] = defaultdict(list)
        for att in attachments:
            if att.contentType is not None and (
                att.contentType.value in _NON_MEDIA_CONTENT_TYPES
            ):
                continue
            if att.postId is not None:
                by_post[att.postId].append(att)
            elif att.messageId is not None:
                by_message[att.messageId].append(att)

        self._assign_attachment_lists(store.filter(Post), by_post)
        self._assign_attachment_lists(store.filter(Message), by_message)

    @staticmethod
    def _assign_attachment_lists(
        owners: list[Post] | list[Message],
        grouped: dict[int, list[Attachment]],
    ) -> None:
        """Assign grouped attachments onto owners, ordered, without dirtying."""
        for owner in owners:
            if owner.attachments:
                continue
            if owner.id is None:
                continue
            atts = grouped.get(owner.id)
            if not atts:
                continue
            ordered = sorted(atts, key=lambda a: (a.pos, a.id))
            object.__setattr__(owner, "attachments", ordered)
            if owner._snapshot is not None:
                owner._snapshot["attachments"] = ordered.copy()

    def _reconstruct_mention_lists(self) -> None:
        """Rebuild the ``Post.mentions`` has_many list a cold preload leaves empty.

        Parallel to ``_reconstruct_attachment_lists``: ``preload`` loads
        ``PostMention`` rows but never populates the ``Post.mentions`` reverse-FK
        list, so STASH_ONLY (and a daemon pass on a post that aged out of the
        identity map) would link no mentioned performers — ``_setup_gallery_
        performers`` / ``_stamp_performers`` run against an empty list. Groups
        cached ``PostMention`` by ``postId`` and assigns the id-ordered list onto
        each mention-less post without dirtying it (``mentions`` is excluded from
        DB writes). Idempotent: posts already carrying mentions are skipped.
        """
        store = get_store()
        mentions = store.filter(PostMention)
        if not mentions:
            return

        by_post: dict[int, list[PostMention]] = defaultdict(list)
        for mention in mentions:
            if mention.postId is not None:
                by_post[mention.postId].append(mention)

        for post in store.filter(Post):
            if post.mentions:
                continue
            post_mentions = by_post.get(post.id)
            if not post_mentions:
                continue
            ordered = sorted(post_mentions, key=lambda m: m.id or 0)
            object.__setattr__(post, "mentions", ordered)
            post._snapshot["mentions"] = ordered.copy()

    async def _gather_creator_posts(self, account: Account) -> list[Post]:
        """Gather the creator's posts that carry attachments.

        Cache-first via the identity map, with a DB fallback when the cache
        has not been populated. Returns only posts that have attachments.

        Args:
            account: The Account object whose posts to gather

        Returns:
            List of Post objects with attachments
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
        return posts

    async def _gather_creator_messages(self, account: Account) -> list[Message]:
        """Gather the creator's messages that carry attachments.

        Resolves the account's groups first (cache-first with DB fallback),
        then the messages in those groups that have attachments (again
        cache-first with DB fallback).

        Args:
            account: The Account object whose messages to gather

        Returns:
            List of Message objects with attachments
        """
        store = get_store()
        account_id = account.id
        if account_id is None:
            return []
        account_group_ids = await self._resolve_account_group_ids(account_id)
        if not account_group_ids:
            return []

        # Messages in those groups that have attachments — cache-first, DB fallback.
        messages = store.filter(
            Message,
            lambda m: m.groupId in account_group_ids and bool(m.attachments),
        )
        if not messages:
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
        return messages

    @staticmethod
    async def _resolve_account_group_ids(account_id: int) -> set[int]:
        """Ids of groups the account belongs to (cache-first, DB fallback)."""
        store = get_store()
        groups = store.filter(
            Group,
            lambda g: g.users and any(u.id == account_id for u in g.users),
        )
        if not groups:
            all_groups = await store.find(Group)
            groups = [
                g
                for g in all_groups
                if g.users and any(u.id == account_id for u in g.users)
            ]
        return {g.id for g in groups}

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
                direct_media = attachment.media.media
                if is_set(direct_media) and direct_media is not None:
                    media_list.append(direct_media)
                preview = attachment.media.preview
                if is_set(preview) and preview is not None:
                    # Stamp preview media so downstream Stash tagging
                    # ("Trailer") can detect it.
                    preview.is_preview = True
                    media_list.append(preview)

            # Media bundles
            if attachment.bundle:
                if attachment.bundle.accountMedia:
                    for account_media in attachment.bundle.accountMedia:
                        am_media = account_media.media
                        if is_set(am_media) and am_media is not None:
                            media_list.append(am_media)
                        preview = account_media.preview
                        if is_set(preview) and preview is not None:
                            preview.is_preview = True
                            media_list.append(preview)

                bundle_preview = attachment.bundle.preview
                if is_set(bundle_preview) and bundle_preview is not None:
                    bundle_preview.is_preview = True
                    media_list.append(bundle_preview)

            # Aggregated posts (recursively collect media)
            if attachment.is_aggregated_post and attachment.aggregated_post:
                agg_post = attachment.aggregated_post
                if agg_post.attachments:
                    agg_media = await self._collect_media_from_attachments(
                        agg_post.attachments
                    )
                    media_list.extend(agg_media)

        return media_list
