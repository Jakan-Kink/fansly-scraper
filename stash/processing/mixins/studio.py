"""Studio processing mixin."""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from metadata import Account
from metadata.decorators import with_session
from textio import print_error, print_info

from ...client_helpers import async_lru_cache
from ...logging import debug_print
from ...logging import processing_logger as logger
from ...types import Performer, Studio


if TYPE_CHECKING:
    pass


class StudioProcessingMixin:
    """Studio processing functionality."""

    @async_lru_cache(maxsize=128)
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
        session: Session | None = None,
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
                "fansly_studio_dict": fansly_studio_result.__dict__,
            }
        )
        if fansly_studio_result.count == 0:
            raise ValueError("Fansly Studio not found in Stash")

        # Convert dict to Studio object
        fansly_studio = Studio(**fansly_studio_result.studios[0])
        debug_print(
            {
                "method": "StashProcessing - process_creator_studio",
                "fansly_studio": fansly_studio,
            }
        )
        creator_studio_name = f"{account.username} (Fansly)"
        studio_data = await self.context.client.find_studios(q=creator_studio_name)
        if studio_data.count == 0:
            # Create new studio with required fields
            studio = Studio(
                id="new",  # Special value indicating new object
                name=creator_studio_name,
                parent_studio=fansly_studio,
                url=f"https://fansly.com/{account.username}",
            )

            # Add performer if provided
            if performer:
                studio.performers = [performer]

            # Create in Stash
            try:
                studio = await self.context.client.create_studio(studio)
                print_info(f"Created studio: {studio.name}")
                return studio
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
                studio_data = await self.context.client.find_studios(
                    q=creator_studio_name
                )
                if studio_data.count == 0:
                    # If still not found, return None
                    return None
                # Fall through to return existing studio

        # Return first matching studio
        return Studio(**studio_data.studios[0])
