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
        """Process creator studio metadata.

        This method:
        1. Finds or creates a corresponding studio in Stash
        2. Updates studio information if needed

        Args:
            account: The Account object
            performer: The Performer object
            session: Optional database session to use
        """
        fansly_studio_result = await self.context.client.find_studios(
            q="Fansly (network)",
        )
        debug_print(
            {
                "method": "StashProcessing - process_creator_studio",
                "fansly_studio_result": fansly_studio_result,
            }
        )
        if fansly_studio_result.count == 0:
            raise ValueError("Fansly Studio not found in Stash")

        # Get Studio object from results (already deserialized by client)
        fansly_studio = fansly_studio_result.studios[0]
        debug_print(
            {
                "method": "StashProcessing - process_creator_studio",
                "fansly_studio": fansly_studio,
            }
        )
        creator_studio_name = f"{account.username} (Fansly)"

        # Use lock to prevent TOCTOU race condition when concurrent workers
        # try to create the same studio simultaneously
        studio_lock = await self._get_studio_lock(account.username)
        async with studio_lock:
            # Query inside lock to get fresh data (no caching in stash-graphql-client)
            studio_data = await self.context.client.find_studios(q=creator_studio_name)

            if studio_data.count == 0:
                # Create new studio with required fields (library auto-generates UUID for id)
                studio = Studio(
                    name=creator_studio_name,
                    parent_studio=fansly_studio,
                    urls=[f"https://fansly.com/{account.username}"],
                )

                # Add performer if provided
                if performer:
                    studio.performers = [performer]

                # Create in Stash
                try:
                    studio = await self.context.client.create_studio(studio)
                    print_info(f"Created studio: {studio.name}")
                except Exception as e:
                    print_error(f"Failed to create studio: {e}")
                    logger.exception("Failed to create studio", exc_info=e)
                    debug_print(
                        {
                            "method": "StashProcessing - process_creator_studio",
                            "status": "studio_creation_failed",
                            "error": str(e),
                            "traceback": traceback.format_exc(),
                        }
                    )
                    # If we failed to create the studio, re-check if it already exists
                    # This can happen with parallel processing
                    # Invalidate Studio cache to force a fresh query
                    Studio._store.invalidate_type(Studio)
                    studio_data = await self.context.client.find_studios(
                        q=creator_studio_name
                    )
                    if studio_data.count == 0:
                        # If still not found, return None
                        return None
                    # Fall through to return existing studio from retry query
                    return studio_data.studios[0]
                else:
                    return studio
            else:
                # Studio already exists, return first matching studio
                return studio_data.studios[0]
