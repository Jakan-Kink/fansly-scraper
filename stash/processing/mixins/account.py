"""Account and performer processing mixin."""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING

from sqlalchemy import inspect
from sqlalchemy.orm import Session
from sqlalchemy.sql import func, select
from stash_graphql_client.types import Image, Performer

from metadata import Account, Media, account_avatar
from metadata.decorators import with_session
from textio import print_error, print_warning

from ...logging import debug_print
from ...logging import processing_logger as logger


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class AccountProcessingMixin:
    """Account and performer processing functionality."""

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

    def _performer_from_account(self, account: Account) -> Performer:
        """Create a Performer object from a Fansly Account.

        This is a local helper that maps Account model fields to the Performer
        type from stash-graphql-client.

        Note: This was originally `Performer.from_account()` but when all of the
        StashClient/StashContext/Stash types were moved into a centralized library,
        application-specific code like this was removed so the library could be generic.

        Args:
            account: The Account database model to convert

        Returns:
            Performer object suitable for creating/updating in Stash.
            For new performers, the id field is omitted and auto-generates
            a UUID4 placeholder.
        """
        # Use displayName as the primary name, fallback to username
        name = account.displayName or account.username

        # Build Fansly profile URL
        url = f"https://fansly.com/{account.username}"

        # Create Performer without id (auto-generates UUID4 for new objects)
        return Performer(
            name=name,
            alias_list=[account.username],  # Username as alias for searchability
            urls=[url],
            details=account.about or "",  # Biography/about text
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

        Note:
            Migrated to use store for identity map benefits (cached lookups).
            Preserves sequential name/alias/URL checks to prevent duplicates.
        """
        # Determine search criteria from account
        search_name = account.displayName or account.username
        fansly_url = f"https://fansly.com/{account.username}"

        # Try exact name match first (identity map returns instantly if cached)
        performer = await self.store.find_one(Performer, name__exact=search_name)
        if performer:
            logger.debug(f"Found existing performer by name: {search_name}")
            return performer

        # Try alias match (critical for deduplication!)
        # Using Django-style filter syntax (stash-graphql-client v0.10.6+)
        # Both Django-style and raw GraphQL syntax tested in:
        #   tests/stash/processing/unit/test_account_mixin.py
        performer = await self.store.find_one(
            Performer, aliases__contains=account.username
        )
        if performer:
            logger.debug(f"Found existing performer by alias: {account.username}")
            return performer

        # Try URL match (catches edge cases)
        performer = await self.store.find_one(Performer, url__contains=fansly_url)
        if performer:
            logger.debug(f"Found existing performer by URL: {fansly_url}")
            return performer

        # Not found after all deduplication checks - create new performer
        logger.debug(f"Creating new performer for account: {account.username}")
        performer = self._performer_from_account(account)
        return await self.store.save(performer)

    @with_session()
    async def process_creator(
        self,
        session: Session | None = None,
    ) -> tuple[Account, Performer]:
        """Process creator metadata into Stash.

        Args:
            session: Optional database session to use

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
            await self._update_performer_avatar(account, performer, session=session)
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
        self, account: Account, performer: Performer, session: Session | None = None
    ) -> None:
        """Update performer's avatar if needed.

        Only updates the avatar if the current image is the default one.

        Args:
            account: Account object containing avatar information
            performer: Performer object to update
            session: Database session for querying avatar
        """
        # Query avatar explicitly instead of using relationship
        # (relationship lazy loading has issues with async sessions)
        avatar = None
        if session:
            try:
                stmt = (
                    select(Media)
                    .join(account_avatar)
                    .where(account_avatar.c.accountId == account.id)
                )
                result = await session.execute(stmt)
                avatar = result.scalar_one_or_none()
            except Exception as e:
                logger.error(
                    f"Failed to query avatar for account {account.id}: {e}",
                    exc_info=e,
                )

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
            # Get avatar file path
            # Pattern 5: Migrated to use store.find() with Django-style filtering
            images = await self.store.find(Image, path__contains=avatar.local_filename)
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
            avatar = images[0]
            avatar_path = avatar.visual_files[0].path
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

        Note:
            Migrated to use store.get() for identity map caching.
            Same ID = same object instance, instant return if cached.
        """
        # Try finding by stash_id first (uses identity map - O(1) if cached)
        if account.stash_id:
            try:
                performer = await self.store.get(Performer, str(account.stash_id))
                if performer:
                    debug_print(
                        {
                            "method": "StashProcessing - _find_existing_performer",
                            "stash_id": account.stash_id,
                            "performer": performer,
                            "cached": "identity_map",
                        }
                    )
                    return performer
            except Exception as e:
                logger.debug(f"Failed to get performer by stash_id: {e}")

        # Fallback to username search (also checks cache)
        performer = await self.store.find_one(Performer, name=account.username)
        if performer:
            debug_print(
                {
                    "method": "StashProcessing - _find_existing_performer",
                    "username": account.username,
                    "performer": performer,
                }
            )
        return performer

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
        # Get account ID safely without triggering lazy loading
        identity = inspect(account).identity
        account_id = account.id if identity is None else identity[0]

        # Get a fresh account instance bound to the session
        stmt = select(Account).where(Account.id == account_id)
        result = await session.execute(stmt)
        account = result.scalar_one()

        # Update stash ID (convert from string to int)
        account.stash_id = int(performer.id)
        session.add(account)
        await session.flush()
