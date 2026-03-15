"""Account and performer processing mixin."""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING

from stash_graphql_client.types import Image, Performer

from metadata import Account, Media
from metadata.models import get_store
from textio import print_error, print_warning

from ...logging import debug_print
from ...logging import processing_logger as logger
from ..protocols import StashProcessingProtocol


if TYPE_CHECKING:
    from metadata.entity_store import PostgresEntityStore


class AccountProcessingMixin(StashProcessingProtocol):
    """Account and performer processing functionality."""

    async def _find_account(self) -> Account | None:
        """Find account in database using identity map (cache-first).

        Returns:
            Account if found, None otherwise
        """
        store: PostgresEntityStore = get_store()

        if self.state.creator_id is not None:
            # Cache-first: identity map lookup is O(1)
            account = store.get_from_cache(Account, self.state.creator_id)
            if not account:
                account = await store.get(Account, self.state.creator_id)
        else:
            # Search by username (case-insensitive) — filter cache first
            name_lower = self.state.creator_name.lower()
            cached = [
                obj
                for obj in store._cache.get(Account, {}).values()
                if getattr(obj, "username", None) and obj.username.lower() == name_lower
            ]
            account = cached[0] if cached else None
            if not account:
                account = await store.find_one(
                    Account, username__iexact=self.state.creator_name
                )

        if not account:
            print_warning(f"No account found for username: {self.state.creator_name}")
        return account

    def _performer_from_account(self, account: Account) -> Performer:
        """Create a Performer object from a Fansly Account (or PostMention).

        This is a local helper that maps Account model fields to the Performer
        type from stash-graphql-client. Supports both Account (displayName,
        username, about) and PostMention (handle) via duck-typing.

        Args:
            account: The Account or PostMention to convert

        Returns:
            Performer object suitable for creating/updating in Stash.
            For new performers, the id field is omitted and auto-generates
            a UUID4 placeholder.
        """
        # Use displayName as the primary name, fallback to username or handle
        name = (
            getattr(account, "displayName", None)
            or getattr(account, "username", None)
            or getattr(account, "handle", None)
        )
        username = getattr(account, "username", None) or getattr(
            account, "handle", None
        )

        # Build Fansly profile URL
        url = f"https://fansly.com/{username}"

        # Create Performer without id (auto-generates UUID4 for new objects)
        return Performer(
            name=name,
            alias_list=[username],  # Username as alias for searchability
            urls=[url],
            details=getattr(account, "about", "") or "",  # Biography/about text
        )

    async def _get_or_create_performer(self, account: Account) -> Performer:
        """Get existing performer from Stash or create from account if not found.

        IMPORTANT: Performs deduplication checks (name→alias→URL) BEFORE creating.
        Pattern 1 migration: Use store.find_one() for identity map caching,
        preserving critical sequential deduplication logic.

        Args:
            account: Account database model to search for or create from

        Returns:
            Performer object from Stash (either found or newly created)
        """
        # Determine search criteria from account (or PostMention via duck-typing)
        search_name = (
            getattr(account, "displayName", None)
            or getattr(account, "username", None)
            or getattr(account, "handle", None)
        )
        username = getattr(account, "username", None) or getattr(
            account, "handle", None
        )
        fansly_url = f"https://fansly.com/{username}"

        # Cache-first: try sync filter() on preloaded performers before GraphQL.
        # Performers are preloaded in _preload_stash_entities() at startup.
        # Sequential name → alias → URL checks prevent duplicates.
        performer = None

        # 1. Exact name match
        results = self.store.filter(Performer, lambda p: p.name == search_name)
        if results:
            logger.debug(f"Cache hit: performer by name: {search_name}")
            performer = results[0]
        if not performer:
            performer = await self.store.find_one(Performer, name__exact=search_name)

        # 2. Alias match (critical for deduplication)
        if not performer:
            results = self.store.filter(
                Performer,
                lambda p: (
                    hasattr(p, "alias_list")
                    and p.alias_list
                    and username in p.alias_list
                ),
            )
            if results:
                logger.debug(f"Cache hit: performer by alias: {username}")
                performer = results[0]
        if not performer:
            performer = await self.store.find_one(Performer, aliases__contains=username)

        # 3. URL match (catches edge cases)
        if not performer:
            results = self.store.filter(
                Performer,
                lambda p: hasattr(p, "urls") and p.urls and fansly_url in p.urls,
            )
            if results:
                logger.debug(f"Cache hit: performer by URL: {fansly_url}")
                performer = results[0]
        if not performer:
            performer = await self.store.find_one(Performer, url__contains=fansly_url)

        if performer:
            return performer

        # Not found after all deduplication checks - create new performer
        logger.debug(f"Creating new performer for account: {username}")
        performer = self._performer_from_account(account)
        await self.store.save(performer)
        return performer

    async def process_creator(self) -> tuple[Account, Performer]:
        """Process creator metadata into Stash.

        Returns:
            Tuple of (Account, Performer)

        Raises:
            ValueError: If creator_id is not available in state
        """
        try:
            # Find account
            account = await self._find_account()
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

            logger.debug(f"Processing creator: {account.username}")
            # Get or create performer using intelligent fuzzy search
            performer = await self._get_or_create_performer(account)
            logger.debug(f"Obtained performer in Stash: {performer}")
            logger.debug(f"Context client (in process_creator): {self.context}")

            debug_print(
                {
                    "method": "StashProcessing - process_creator",
                    "performer": performer,
                }
            )
            # Handle avatar if needed
            await self._update_performer_avatar(account, performer)
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
        else:
            return account, performer

    async def _update_performer_avatar(
        self, account: Account, performer: Performer
    ) -> None:
        """Update performer's avatar if needed.

        Only updates the avatar if the current image is the default one.

        Args:
            account: Account object containing avatar information
            performer: Performer object to update
        """
        # Account.avatar is a Pydantic relationship — directly accessible
        avatar: Media | None = account.avatar
        has_avatar = avatar and avatar.local_filename

        if not has_avatar:
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
            # Cache-first: try sync filter on preloaded images, then GraphQL
            filename = avatar.local_filename
            images = self.store.filter(
                Image,
                lambda img: (
                    hasattr(img, "visual_files")
                    and img.visual_files
                    and any(filename in f.path for f in img.visual_files)
                ),
            )
            if not images:
                images = await self.store.find(Image, path__contains=filename)
            if not images:
                debug_print(
                    {
                        "method": "StashProcessing - _update_performer_avatar",
                        "status": "no_avatar_found",
                        "account": account.username,
                    }
                )
                return
            # Use first image (sorted by created_at in Stash)
            avatar_img = images[0]
            avatar_path = avatar_img.visual_files[0].path
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

    async def _find_existing_performer(self, account: Account) -> Performer | None:
        """Find existing performer in Stash using identity map.

        Args:
            account: Account to find performer for

        Returns:
            Performer data if found, None otherwise
        """
        # Cache-first: try sync get_cached() (zero-cost after preload),
        # fall back to async get() only if cache misses.
        # Uses getattr for duck-typing: works with both Account (stash_id/username)
        # and PostMention (handle) objects after the Pydantic migration.
        stash_id = getattr(account, "stash_id", None)
        if stash_id:
            try:
                performer = self.store.get_cached(Performer, str(stash_id))
                if not performer:
                    performer = await self.store.get(Performer, str(stash_id))
                if performer:
                    debug_print(
                        {
                            "method": "StashProcessing - _find_existing_performer",
                            "stash_id": stash_id,
                            "performer": performer,
                            "cached": "identity_map",
                        }
                    )
                    return performer
            except Exception as e:
                logger.debug(f"Failed to get performer by stash_id: {e}")

        # Fallback to name search (cache-first, then GraphQL)
        # Supports both Account (username) and PostMention (handle)
        name = getattr(account, "username", None) or getattr(account, "handle", None)
        if not name:
            return None
        results = self.store.filter(Performer, lambda p: p.name == name)
        performer = results[0] if results else None
        if not performer:
            performer = await self.store.find_one(Performer, name=name)
        if performer:
            debug_print(
                {
                    "method": "StashProcessing - _find_existing_performer",
                    "username": name,
                    "performer": performer,
                }
            )
        return performer

    async def _update_account_stash_id(
        self,
        account: Account,
        performer: Performer,
    ) -> None:
        """Update account's stash ID and persist to database.

        Supports both Account (has stash_id field) and PostMention (skipped).

        Args:
            account: Account (or PostMention) to update
            performer: Performer containing the stash ID
        """
        if not hasattr(account, "stash_id"):
            return  # PostMention doesn't have stash_id
        store: PostgresEntityStore = get_store()
        account.stash_id = int(performer.id)
        await store.save(account)
