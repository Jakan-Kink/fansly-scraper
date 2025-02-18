"""Wall Downloads"""

import random
import traceback
from asyncio import sleep

from requests import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import FanslyConfig, with_database_session
from errors import ApiError, DuplicatePageError
from metadata import Wall, process_wall_posts
from textio import (
    input_enter_continue,
    print_debug,
    print_error,
    print_info,
    print_warning,
)

from .common import (
    check_page_duplicates,
    get_unique_media_ids,
    process_download_accessible_media,
)
from .core import DownloadState
from .media import download_media_infos
from .types import DownloadType


@with_database_session(async_session=True)
async def download_wall(
    config: FanslyConfig,
    state: DownloadState,
    wall_id: str,
    session: AsyncSession | None = None,
) -> None:
    """Download all posts from a specific wall.

    Args:
        config: FanslyConfig instance
        state: Current download state
        wall_id: ID of the wall to download
        session: Optional AsyncSession for database operations
    """
    # Get wall name from database
    wall = await session.get(Wall, wall_id)
    wall_name = wall.name if wall and wall.name else None
    wall_info = f"'{wall_name}' ({wall_id})" if wall_name else wall_id

    print_info(f"Downloading wall {wall_info}...")

    # Set download type for directory creation
    state.download_type = DownloadType.WALL

    # Initialize pagination cursor
    before_cursor = "0"
    attempts = 0

    if config.use_duplicate_threshold and state.fetchedTimelineDuplication:
        print_info(
            "Deduplication is enabled and the timeline has been fetched before. "
            "Only new media items will be downloaded."
        )
        print()
        return

    # Careful - "retry" means (1 + retries) runs
    while True and attempts <= config.timeline_retries:
        starting_duplicates = state.duplicate_count

        if before_cursor == "0":
            print_info(
                f"Inspecting most recent wall posts from {wall_info}... [CID: {state.creator_id}]"
            )
        else:
            print_info(
                f"Inspecting wall posts from {wall_info} before: {before_cursor} [CID: {state.creator_id}]"
            )

        wall_response = Response()

        try:
            if state.creator_id is None:
                raise RuntimeError("Creator ID should not be None")

            wall_response = config.get_api().get_wall_posts(
                state.creator_id, wall_id, str(before_cursor)
            )

            wall_response.raise_for_status()

            if wall_response.status_code == 200:
                wall_data = wall_response.json()["response"]

                # Check for duplicates before processing posts
                await check_page_duplicates(
                    config=config,
                    page_data=wall_data,
                    page_type="wall",
                    page_id=wall_id,
                    cursor=before_cursor if before_cursor != "0" else None,
                    session=session,
                )

                # Only process posts if no duplicates found
                await process_wall_posts(config, state, wall_id, wall_data)

                if config.debug:
                    print_debug(f"Wall data object: {wall_data}")

                all_media_ids = get_unique_media_ids(wall_data)

                if len(all_media_ids) == 0:
                    # We might be a rate-limit victim, slow extremely down -
                    # but only if there are retries left
                    if attempts < config.timeline_retries:
                        print_info(
                            f"Slowing down for {config.timeline_delay_seconds} s ..."
                        )
                        await sleep(config.timeline_delay_seconds)
                    # Try again
                    attempts += 1
                    continue
                else:
                    # Reset attempts eg. new page
                    attempts = 0

                media_infos = await download_media_infos(
                    config=config, state=state, media_ids=all_media_ids
                )

                if not await process_download_accessible_media(
                    config, state, media_infos
                ):
                    # Break on deduplication error - already downloaded
                    break

                # Print info on skipped downloads if `show_skipped_downloads` is enabled
                skipped_downloads = state.duplicate_count - starting_duplicates
                if (
                    skipped_downloads > 1
                    and config.show_downloads
                    and not config.show_skipped_downloads
                ):
                    print_info(
                        f"Skipped {skipped_downloads} already downloaded media item{'' if skipped_downloads == 1 else 's'}."
                    )

                print()

                # Get next before_cursor
                try:
                    # Slow down to avoid the Fansly rate-limit
                    await sleep(random.uniform(2, 4))

                    # Get last post ID for next page
                    before_cursor = wall_data["posts"][-1]["id"]

                    # If we got fewer than 15 posts, we've reached the end
                    if len(wall_data["posts"]) < 15:
                        break

                except IndexError:
                    # Break the whole while loop if end is reached
                    break

                except Exception:
                    message = (
                        "Please copy & paste this on GitHub > Issues & provide a short explanation (34):"
                        f"\n{traceback.format_exc()}\n"
                    )
                    raise ApiError(message)

        except KeyError:
            print_error(
                "Couldn't find any scrapable media at all!\n"
                "This most likely happened because you're not following the creator, "
                "your authorization token is wrong\n"
                "or the creator is not providing unlocked content.",
                35,
            )
            input_enter_continue(config.interactive)

        except DuplicatePageError as e:
            print_warning(str(e))
            print()
            break  # Break out of the loop to stop processing this wall

        except Exception:
            print_error(
                f"Unexpected error during wall download: \n{traceback.format_exc()}",
                36,
            )
            input_enter_continue(config.interactive)
