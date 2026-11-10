"""Account and performer processing mixin."""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING

from stash_graphql_client.types import Image, Performer, UnsetType, is_set

from helpers.common import clean_mention_handle, fansly_username_from_url
from metadata import Account, PostMention
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
            if self.state.creator_name is None:
                return None
            name_lower = self.state.creator_name.lower()
            cached = store.filter(
                Account,
                lambda a: a.username is not None and a.username.lower() == name_lower,
            )
            account = cached[0] if cached else None
            if not account:
                account = await store.find_one(
                    Account, username__iexact=self.state.creator_name
                )

        if not account:
            print_warning(f"No account found for username: {self.state.creator_name}")
        return account

    @staticmethod
    def _performer_identity(account: Account | PostMention) -> tuple[str, str]:
        """The (username, name) an account or mention is matched on.

        A mention's handle is cleaned to the username it names. The name is the
        display name when there is one, else the username.
        """
        if isinstance(account, Account):
            username = account.username
        else:
            username = clean_mention_handle(account.handle)
        return username, getattr(account, "displayName", None) or username

    def _performer_from_account(self, account: Account | PostMention) -> Performer:
        """Build a new Performer for a Fansly Account or post mention.

        Args:
            account: The Account or PostMention to convert

        Returns:
            Unsaved Performer named after the account, carrying the username as
            alias and Fansly profile URL.
        """
        username, name = self._performer_identity(account)
        return Performer(
            name=name,
            alias_list=[username],
            urls=[f"https://fansly.com/{username}"],
            details=getattr(account, "about", "") or "",
        )

    async def _match_performers(
        self, account: Account | PostMention
    ) -> list[Performer] | None:
        """Find the Stash performers that belong to an account or mention.

        Matches on the account's own identifiers before its name: every
        performer carrying its Fansly profile URL, else every performer with
        the username as an alias, else the single performer with its name.
        Results are remembered per (username, name) for the processor's life.

        Args:
            account: The Account or PostMention to match

        Returns:
            Matching performers, lowest id first; an empty list when none
            match; None when only the name matches and several performers
            share it.
        """
        username, name = self._performer_identity(account)
        if not username:
            return []
        key = (username.lower(), name)
        if key not in self._performer_matches:
            self._performer_matches[key] = await self._lookup_performers(username, name)
        return self._performer_matches[key]

    async def _lookup_performers(
        self, username: str, name: str
    ) -> list[Performer] | None:
        """Query Stash for performers by Fansly URL, then alias, then name.

        Stash's INCLUDES filters are substring matches, so URL and alias hits
        are re-checked against the exact username.
        """
        wanted = username.lower()
        by_url = await self.store.find(
            Performer, url__contains=f"fansly.com/{username}"
        )
        matches = [
            p
            for p in by_url
            if is_set(p.urls)
            and p.urls
            and any(fansly_username_from_url(url) == wanted for url in p.urls)
        ]
        if not matches:
            by_alias = await self.store.find(Performer, aliases__contains=username)
            matches = [
                p
                for p in by_alias
                if is_set(p.alias_list)
                and p.alias_list
                and any(alias.lower() == wanted for alias in p.alias_list)
            ]
        if not matches:
            matches = await self.store.find(Performer, name__exact=name)
            if len(matches) > 1:
                logger.warning(
                    f"{len(matches)} Stash performers are named {name!r} and none "
                    f"carries https://fansly.com/{username} as a URL or "
                    f"{username!r} as an alias; not linking any of them."
                )
                return None
        return sorted(matches, key=lambda p: int(p.id))

    async def _get_or_create_performer(
        self, account: Account | PostMention
    ) -> Performer | None:
        """Get the account's Stash performer, creating one when none matches.

        Args:
            account: Account or PostMention to search for or create from

        Returns:
            The matching performer with the lowest id, or a newly created one.
            None when the match is ambiguous between namesakes, or a mention's
            handle holds no username.
        """
        matches = await self._match_performers(account)
        if matches is None:
            return None
        if matches:
            return matches[0]
        username, name = self._performer_identity(account)
        if not username:
            return None
        logger.debug(f"Creating new performer for account: {username}")
        performer = self._performer_from_account(account)
        await self.store.save(performer)
        self._performer_matches[username.lower(), name] = [performer]
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
            performer = await self._get_or_create_performer(account)
            if performer is None:
                raise ValueError(
                    f"Cannot pick a Stash performer for {account.username}: "
                    f"several share its name. Add "
                    f"https://fansly.com/{account.username} to the right one."
                )
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
        avatar = account.avatar

        if avatar is None or isinstance(avatar, UnsetType) or not avatar.local_filename:
            debug_print(
                {
                    "method": "StashProcessing - _update_performer_avatar",
                    "status": "no_avatar_found",
                    "account": account.username,
                }
            )
            return

        # Only update if current image is default
        image_path = performer.image_path
        if not is_set(image_path) or image_path is None or "default=true" in image_path:
            # Cache-first: try sync filter on preloaded images, then GraphQL
            filename = avatar.local_filename
            images = self.store.filter(
                Image,
                lambda img: (
                    hasattr(img, "visual_files")
                    and is_set(img.visual_files)
                    and any(
                        is_set(f.path) and filename in f.path for f in img.visual_files
                    )
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
            visual_files = avatar_img.visual_files
            if isinstance(visual_files, UnsetType) or not visual_files:
                return
            avatar_path = visual_files[0].path
            if isinstance(avatar_path, UnsetType):
                return
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

    async def _find_existing_performer(
        self, account: Account | PostMention
    ) -> Performer | None:
        """Find the account's existing Stash performer without creating one.

        Args:
            account: Account or PostMention to find a performer for

        Returns:
            The performer recorded as the account's ``stash_id``, else the
            matching performer with the lowest id; None when nothing matches or
            the match is ambiguous between namesakes.
        """
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

        matches = await self._match_performers(account)
        return matches[0] if matches else None

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
