import asyncio

from sqlalchemy.sql.expression import select
from stashapi.stashapp import StashInterface

from config import FanslyConfig
from download.core import DownloadState
from metadata import Account
from stash import StashPerformer, performer_fragment
from textio import print_error, print_info


class StashProcessing:
    config: FanslyConfig
    state: DownloadState
    stash_interface: StashInterface

    def __init__(self, config: FanslyConfig, state: DownloadState):
        from copy import deepcopy

        from metadata.database import Database

        self.config = config  # Config is shared and thread-safe
        self.state = deepcopy(
            state
        )  # Deep copy state to prevent modification during background processing
        self.stash_interface = config.get_stash_api()

        # Handle database connection based on metadata mode
        if config.separate_metadata:
            # For separate metadata, create a new connection
            self.db_path = config.get_creator_database_path(state.creator_name)
            config_copy = deepcopy(config)
            config_copy.metadata_db_file = self.db_path
            self.database = Database(config_copy)
            self._owns_db_connection = True
        else:
            # For global metadata, reuse the existing connection
            self.db_path = config.metadata_db_file
            self.database = config._database
            self._owns_db_connection = False

    async def start_creator_processing(self):
        if self.config.stash_context_conn is None:
            print_info("StashContext is not configured. Skipping metadata processing.")
            return
        await self.scan_creator_folder()
        account, performer = await self.process_creator()

        # Continue Stash GraphQL processing in the background
        # Get the current loop
        loop = asyncio.get_running_loop()
        # Create the task in the current loop
        task = loop.create_task(self.continue_stash_processing(account, performer))
        self.config._background_tasks.append(task)

        # Return to allow main() to resume processing
        return

    async def scan_creator_folder(self):
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
            print_info(f"Failed to process metadata: {e}")

    async def process_creator(self):
        async with self.database.get_async_session() as session:
            try:
                stmt = select(Account).where(Account.id == self.state.creator_id)
                account = await session.execute(stmt)
                account = account.scalar_one_or_none()
                if not account:
                    print_info(
                        f"No account found for username: {self.state.creator_name}"
                    )
                    return (None, None)

                # If we have a stash_id, use that to find the performer
                if account.stash_id:
                    print_info(
                        f"Searching for performer by stash_id: {account.stash_id}"
                    )
                    performer_data = self.stash_interface.find_performer(
                        performer=account.stash_id, fragment=performer_fragment
                    )
                else:
                    print_info(
                        f"Searching for performer by username: {account.username}"
                    )
                    performer_data = self.stash_interface.find_performer(
                        performer=account.username, fragment=performer_fragment
                    )
                performer: StashPerformer = None

                if performer_data is None:
                    print_info("No existing performer found, creating new one")
                    performer_data = {
                        "name": account.displayName or account.username,
                        "aliases": [account.username],
                        "details": account.about,
                        "urls": [f"https://fansly.com/{account.username}/posts"],
                        "country": account.location,
                    }
                    print_info(f"Initial performer data: {performer_data}")

                    performer = StashPerformer.from_dict(performer_data)
                    print_info("Converting to StashPerformer object")

                    print_info("Creating performer in Stash")
                    try:
                        created_performer_data = performer.stash_create(
                            self.stash_interface
                        )
                        print_info(f"Stash create response: {created_performer_data}")

                        if (
                            not created_performer_data
                            or "id" not in created_performer_data
                        ):
                            print_error(
                                "Stash API response missing required 'id' field"
                            )
                            print_error(f"Full response: {created_performer_data}")
                            raise ValueError(
                                "Invalid response from Stash API - missing ID"
                            )

                        # Update performer with the created data which includes the ID
                        performer = StashPerformer.from_dict(created_performer_data)
                        if not performer.id:
                            print_error("Failed to set performer ID after creation")
                            print_error(f"Response data: {created_performer_data}")
                            print_error(f"Performer object: {performer.to_dict()}")
                            raise ValueError("Failed to set performer ID")

                        print_info(
                            f"Created performer: {performer.name} with ID: {performer.id}"
                        )
                    except Exception as e:
                        print_error(f"Error during performer creation: {e}")
                        raise
                else:
                    print_info(f"Found existing performer data: {performer_data}")
                    if not performer_data or "id" not in performer_data:
                        print_error("Found performer data missing required 'id' field")
                        print_error(f"Full data: {performer_data}")
                        raise ValueError("Invalid performer data - missing ID")

                    performer = StashPerformer.from_dict(performer_data)
                    if not performer.id:
                        print_error("Found performer missing ID after object creation")
                        print_error(f"Original data: {performer_data}")
                        print_error(f"Performer object: {performer.to_dict()}")
                        raise ValueError("Found performer missing ID")
                    print_info(
                        f"Found performer: {performer.name} with ID: {performer.id}"
                    )

                # Final ID check
                if not hasattr(performer, "id") or not performer.id:
                    print_error(
                        "Performer object is missing 'id' attribute or ID is empty!"
                    )
                    print_error(f"Performer object attributes: {dir(performer)}")
                    print_error(f"Performer object dict: {performer.to_dict()}")
                    raise AttributeError(
                        "Performer object missing required 'id' attribute"
                    )

                return (account, performer)
            except Exception as e:
                print_error(f"Failed to process creator: {e}")
                return (None, None)

    async def continue_stash_processing(
        self, account: Account, performer: StashPerformer
    ):
        print_info("Continuing Stash GraphQL processing in the background...")
        try:
            async with self.database.get_async_session() as session:
                try:
                    if account is not None:
                        account.stash_id = performer.id
                        session.add(account)
                        await session.commit()
                except Exception as e:
                    print_error(f"Failed to continue Stash processing: {e}")
                    return
        finally:
            # Clean up database connection if we own it
            if self._owns_db_connection:
                try:
                    self.database.close()
                except Exception as e:
                    print_error(f"Error closing database connection: {e}")
