"""Module for processing metadata and synchronizing with Stash."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from sqlalchemy.sql.expression import select
from stashapi.stashapp import StashInterface

from config import FanslyConfig
from download.core import DownloadState
from metadata import Account
from metadata.database import Database
from textio import print_error, print_info

from .performer import Performer


@dataclass
class StashProcessing:
    """Class for handling Stash metadata processing."""

    config: FanslyConfig
    state: DownloadState
    stash_interface: StashInterface
    database: Database
    db_path: Path
    _owns_db_connection: bool = False

    @classmethod
    def from_config(
        cls, config: FanslyConfig, state: DownloadState
    ) -> "StashProcessing":
        """Create a StashProcessing instance from config.

        Args:
            config: The FanslyConfig instance
            state: The DownloadState instance

        Returns:
            A new StashProcessing instance
        """
        from copy import deepcopy

        # Deep copy state to prevent modification during background processing
        state_copy = deepcopy(state)
        stash_interface = config.get_stash_api()

        # Handle database connection based on metadata mode
        if config.separate_metadata:
            # For separate metadata, create a new connection
            db_path = config.get_creator_database_path(state.creator_name)
            config_copy = deepcopy(config)
            config_copy.metadata_db_file = db_path
            database = Database(config_copy)
            owns_db = True
        else:
            # For global metadata, reuse the existing connection
            db_path = config.metadata_db_file
            database = config._database
            owns_db = False

        return cls(
            config=config,
            state=state_copy,
            stash_interface=stash_interface,
            database=database,
            db_path=db_path,
            _owns_db_connection=owns_db,
        )

    async def start_creator_processing(self) -> None:
        """Start processing creator metadata.

        This method:
        1. Checks if StashContext is configured
        2. Scans the creator folder
        3. Processes the creator metadata
        4. Continues processing in the background
        """
        if self.config.stash_context_conn is None:
            print_info("StashContext is not configured. Skipping metadata processing.")
            return

        await self.scan_creator_folder()
        account, performer = await self.process_creator()

        # Continue Stash GraphQL processing in the background
        loop = asyncio.get_running_loop()
        task = loop.create_task(self.continue_stash_processing(account, performer))
        self.config._background_tasks.append(task)

    async def scan_creator_folder(self) -> None:
        """Scan the creator's folder for media files.

        This method initiates a Stash metadata scan with specific flags for
        generating various media assets (covers, previews, thumbnails, etc.).

        Raises:
            RuntimeError: If the metadata scan fails
        """
        scan_metadata_input = {
            "rescan": False,
            "scanGenerateCovers": True,
            "scanGeneratePreviews": True,
            "scanGenerateThumbnails": True,
            "scanGenerateImagePreviews": True,
            "scanGenerateSprites": True,
            "scanGeneratePhashes": True,
            "scanGenerateClipPreviews": True,
        }

        try:
            job_id = self.stash_interface.metadata_scan(
                paths=[str(self.state.download_path)], flags=scan_metadata_input
            )
            print_info(f"Metadata scan job ID: {job_id}")

            finished_job = False
            while not finished_job:
                try:
                    finished_job = self.stash_interface.wait_for_job(job_id)
                except Exception:
                    finished_job = False

        except RuntimeError as e:
            raise RuntimeError(f"Failed to process metadata: {e}") from e

    async def process_creator(self) -> tuple[Account | None, Performer | None]:
        """Process creator metadata and create/update Stash performer.

        This method:
        1. Retrieves account information from the database
        2. Finds or creates a corresponding performer in Stash
        3. Updates performer information if needed

        Returns:
            A tuple containing the Account and Performer objects, or (None, None) if processing fails
        """
        async with self.database.get_async_session() as session:
            try:
                # Find account in database
                stmt = select(Account).where(Account.id == self.state.creator_id)
                account = await session.execute(stmt)
                account = account.scalar_one_or_none()
                if not account:
                    print_info(
                        f"No account found for username: {self.state.creator_name}"
                    )
                    return (None, None)

                # Find performer in Stash
                performer_data = None
                if account.stash_id:
                    performer_data = self.stash_interface.find_performer(
                        account.stash_id
                    )
                if not performer_data:
                    performer_data = self.stash_interface.find_performer(
                        account.username
                    )

                # Create or update performer
                if performer_data is None:
                    # Create new performer
                    performer = Performer(
                        id="new",  # Will be replaced with actual ID after creation
                        name=account.displayName or account.username,
                        disambiguation=account.username,  # Use disambiguation instead of aliases
                        details=account.about,
                        urls=[f"https://fansly.com/{account.username}/posts"],
                        country=account.location,
                    )
                    try:
                        created_data = performer.stash_create(self.stash_interface)
                        if not created_data or "id" not in created_data:
                            raise ValueError(
                                "Invalid response from Stash API - missing ID"
                            )

                        # Update performer with created data
                        performer = Performer.from_dict(created_data)
                        if not performer.id:
                            raise ValueError("Failed to set performer ID")

                        print_info(
                            f"Created performer: {performer.name} with ID: {performer.id}"
                        )
                    except Exception as e:
                        print_error(f"Error during performer creation: {e}")
                        raise
                else:
                    # Use existing performer
                    if not performer_data or "id" not in performer_data:
                        raise ValueError("Invalid performer data - missing ID")

                    performer = Performer.from_dict(performer_data)
                    if not performer.id:
                        raise ValueError("Found performer missing ID")
                    print_info(
                        f"Found performer: {performer.name} with ID: {performer.id}"
                    )

                # Final ID check
                if not performer.id:
                    raise AttributeError(
                        "Performer object missing required 'id' attribute"
                    )

                return (account, performer)
            except Exception as e:
                print_error(f"Failed to process creator: {e}")
                return (None, None)

    async def continue_stash_processing(
        self, account: Account | None, performer: Performer | None
    ) -> None:
        """Continue processing in the background.

        This method:
        1. Updates the account's stash_id if needed
        2. Performs any necessary cleanup

        Args:
            account: The Account object to update
            performer: The Performer object containing the stash ID
        """
        print_info("Continuing Stash GraphQL processing in the background...")
        try:
            if account and performer and account.stash_id != performer.id:
                async with self.database.get_async_session() as session:
                    account.stash_id = performer.id
                    await session.commit()
        finally:
            if self._owns_db_connection:
                self.database.close()
