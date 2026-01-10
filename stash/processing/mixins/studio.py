"""Studio processing mixin."""

from __future__ import annotations

import asyncio
import traceback
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy.orm import Session
from stash_graphql_client.types import Performer, Studio

from metadata import Account
from metadata.decorators import with_session
from textio import print_error, print_info

from ...logging import debug_print
from ...logging import processing_logger as logger


if TYPE_CHECKING:
    pass


class StudioProcessingMixin:
    """Studio processing functionality."""

    # Class-level locks for studio creation, keyed by username
    # Prevents TOCTOU race condition when concurrent workers try to create
    # the same studio simultaneously
    _studio_creation_locks: ClassVar[dict[str, asyncio.Lock]] = {}
    _studio_creation_locks_lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    async def _get_studio_lock(self, username: str) -> asyncio.Lock:
        """Get or create a lock for studio creation for a specific username.

        Uses double-checked locking to minimize lock contention.
        """
        if username not in self._studio_creation_locks:
            async with self._studio_creation_locks_lock:
                # Double-check after acquiring lock
                if username not in self._studio_creation_locks:
                    self._studio_creation_locks[username] = asyncio.Lock()
        return self._studio_creation_locks[username]

    async def _find_existing_studio(self, account: Account) -> Studio | None:
        """Find existing studio in Stash.

        Args:
            account: Account to find studio for

        Returns:
            Studio data if found, None otherwise
        """
        # Use process_creator_studio with None performer
        return await self.process_creator_studio(account=account, performer=None)

    @with_session()
    async def process_creator_studio(
        self,
        account: Account,
        performer: Performer,
        session: Session | None = None,  # noqa: ARG002
    ) -> Studio | None:
        """Process creator studio metadata using ORM get_or_create.

        Migrated to use store.get_or_create() for:
        - Automatic conflict handling (no manual cache invalidation!)
        - Race condition safety built-in
        - Identity map ensures same studio ID = same object instance

        Args:
            account: The Account object
            performer: The Performer object
            session: Optional database session to use

        Returns:
            Studio object from Stash (either found or newly created)

        Note:
            Manual cache invalidation removed - store handles coherency automatically.
        """
        # Find Fansly parent studio (use store for caching)
        fansly_studio = await self.store.find_one(Studio, name="Fansly (network)")
        if not fansly_studio:
            raise ValueError("Fansly Studio not found in Stash")

        debug_print(
            {
                "method": "StashProcessing - process_creator_studio",
                "fansly_studio": fansly_studio.name,
                "fansly_studio_id": fansly_studio.id,
            }
        )

        creator_studio_name = f"{account.username} (Fansly)"

        # Use lock to prevent TOCTOU race condition when concurrent workers
        # try to create the same studio simultaneously
        studio_lock = await self._get_studio_lock(account.username)
        async with studio_lock:
            # Search by name only (relationship objects can't be serialized in GraphQL filters)
            try:
                studio = await self.store.find_one(Studio, name=creator_studio_name)

                if studio:
                    # Found existing studio
                    logger.debug(
                        f"Found existing studio: {studio.name} (ID: {studio.id})"
                    )
                    print_info(f"Studio ready: {studio.name}")
                    return studio

                # Not found - create new studio with all fields
                studio = Studio(
                    name=creator_studio_name,
                    parent_studio=fansly_studio,
                    urls=[f"https://fansly.com/{account.username}"],
                    performers=[performer] if performer else [],
                )

                # Save to Stash
                await self.store.save(studio)

            except Exception as e:
                # Log unexpected errors
                print_error(f"Failed to find/create studio: {e}")
                logger.exception("Failed to find/create studio", exc_info=e)
                debug_print(
                    {
                        "method": "StashProcessing - process_creator_studio",
                        "status": "studio_find_or_create_failed",
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
                )
            else:
                # Success - log and return
                logger.debug(f"Created new studio: {studio.name} (ID: {studio.id})")
                print_info(f"Studio created: {studio.name}")
                return studio

                # Fallback: Try find_one again (might have been created by concurrent task)
                studio = await self.store.find_one(Studio, name=creator_studio_name)
                if studio:
                    logger.debug(f"Fallback found existing studio: {studio.name}")
                    return studio

                # Still failed - return None
                return None
