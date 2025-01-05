import asyncio

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
        self.config = config
        self.state = state
        self.stash_interface = config.get_stash_api()

    async def start_creator_processing(self):
        if self.config.stash_context_conn is None:
            print_info("StashContext is not configured. Skipping metadata processing.")
            return
        await self.scan_creator_folder()
        account, performer = await self.process_creator()

        # Continue Stash GraphQL processing in the background
        task = asyncio.create_task(self.continue_stash_processing(account, performer))
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
                paths=[self.state.download_path], flags=scan_metadata_input
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
        async with self.config._database.get_async_session() as session:
            try:
                account = (
                    session.query(Account)
                    .filter_by(username=self.state.creator_name)
                    .first()
                )
                if not account:
                    print_info(
                        f"No account found for username: {self.state.creator_name}"
                    )
                    return (None, None)

                performer_data = self.stash_interface.find_performer(
                    performer=account.username, fragment=performer_fragment
                )
                performer: StashPerformer = None
                if performer_data is None:
                    performer_data = {
                        "name": account.displayName or account.username,
                        "aliases": [account.username],
                        "details": account.about,
                        "urls": [f"https://fansly.com/{account.username}/posts"],
                        "country": account.location,
                    }
                    performer = StashPerformer.from_dict(performer_data)
                    performer_data = performer.stash_create(self.stash_interface)
                    print_info(f"Created performer: {performer.name}")
                else:
                    performer = StashPerformer.from_dict(performer_data)
                    print_info(f"Found performer: {performer.name}")

                return (account, performer)
            except Exception as e:
                print_error(f"Failed to process creator: {e}")
                return (None, None)

    async def continue_stash_processing(
        self, account: Account, performer: StashPerformer
    ):
        print_info("Continuing Stash GraphQL processing in the background...")
        async with self.config._database.get_async_session() as session:
            try:
                if account is not None:
                    account.stash_id = performer.id
                    session.add(account)
                    session.commit()
            except Exception as e:
                print_error(f"Failed to continue Stash processing: {e}")
                return

        pass
