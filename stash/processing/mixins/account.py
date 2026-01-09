"""Account and performer processing mixin."""

from __future__ import annotations

import asyncio
import traceback
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session
from sqlalchemy.sql import func, select
from stash_graphql_client.types import Performer, is_set

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

        Uses intelligent fuzzy search to find performers by:
        1. Exact name match (displayName or username)
        2. Alias match (username)
        3. URL match (Fansly profile URL)
        4. Creates new performer from account if no match found

        Args:
            account: Account database model to search for or create from

        Returns:
            Performer object from Stash (either found or newly created)

        Note:
            This searches Stash BEFORE creating a Performer object to avoid
            polluting the StashEntityStore with temporary UUID placeholders.
        """
        # Determine search criteria from account
        search_name = account.displayName or account.username
        fansly_url = f"https://fansly.com/{account.username}"

        # Try exact name match
        result = await self.context.client.find_performers(
            performer_filter={"name": {"value": search_name, "modifier": "EQUALS"}}
        )
        if is_set(result.count) and result.count > 0:
            logger.debug(f"Found existing performer by name: {search_name}")
            return result.performers[0]

        # Try alias match with username
        result = await self.context.client.find_performers(
            performer_filter={
                "aliases": {"value": account.username, "modifier": "INCLUDES"}
            }
        )
        if is_set(result.count) and result.count > 0:
            logger.debug(f"Found existing performer by alias: {account.username}")
            return result.performers[0]

        # Try URL match
        result = await self.context.client.find_performers(
            performer_filter={"url": {"value": fansly_url, "modifier": "INCLUDES"}}
        )
        if is_set(result.count) and result.count > 0:
            logger.debug(f"Found existing performer by URL: {fansly_url}")
            return result.performers[0]

        # Not found - create from account
        logger.debug(f"Creating new performer for account: {account.username}")
        performer = self._performer_from_account(account)
        return await self.context.client.create_performer(performer)

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
            avatar_stash_obj = await self.context.client.find_images(
                image_filter={
                    "path": {
                        "modifier": "INCLUDES",
                        "value": avatar.local_filename,
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
            # Library returns Image objects directly - no conversion needed
            avatar = avatar_stash_obj.images[0]
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
        # Refresh account to ensure it's attached to the session and not expired
        await session.refresh(account)

        # Update stash ID (convert from string to int)
        account.stash_id = int(performer.id)
        session.add(account)
        await session.flush()
