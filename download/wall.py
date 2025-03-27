"""Wall Downloads"""

import random
import traceback
from asyncio import sleep
from typing import Any

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
    print_info_highlight,
)

from .common import (
    check_page_duplicates,
    get_unique_media_ids,
    process_download_accessible_media,
)
from .core import DownloadState
from .media import download_media_infos
from .transaction import in_transaction_or_new
from .types import DownloadType


async def process_wall_data(
    config: FanslyConfig,
    state: DownloadState,
    wall_id: str,
    wall_data: dict[str, Any],
    before_cursor: str,
    session: AsyncSession,
) -> None:
    """Process wall data with proper transaction handling.

    Args:
        config: FanslyConfig instance
        state: Current download state
        wall_id: ID of the wall to download
        wall_data: Wall data from API response
        before_cursor: Pagination cursor
        session: AsyncSession for database operations
    """
    # Check for duplicates before processing posts
    await check_page_duplicates(
        config=config,
        page_data=wall_data,
        page_type="wall",
        page_id=wall_id,
        cursor=before_cursor if before_cursor != "0" else None,
        session=session,
    )
    await session.flush()

    # Only process posts if no duplicates found
    await process_wall_posts(
        config,
        state,
        wall_id,
        wall_data,
        session=session,
    )
    await session.flush()


async def process_wall_media(
    config: FanslyConfig,
    state: DownloadState,
    media_ids: list[str],
    session: AsyncSession,
) -> bool:
    """Process wall media with proper transaction handling.

    Args:
        config: FanslyConfig instance
        state: Current download state
        media_ids: List of media IDs to download
        session: AsyncSession for database operations

    Returns:
        False if deduplication error occurred, True otherwise
    """
    media_infos = await download_media_infos(
        config=config,
        state=state,
        media_ids=media_ids,
        session=session,
    )
    await session.flush()

    result = await process_download_accessible_media(
        config,
        state,
        media_infos,
        session=session,
    )
    await session.flush()
    return result


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

    # Reset duplicate count for this wall
    state.duplicate_count = 0
    state.current_batch_duplicates = 0

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

                # Process the wall data with transaction handling
                await in_transaction_or_new(
                    session,
                    process_wall_data,
                    config.debug,
                    "wall data processing",
                    config,
                    state,
                    wall_id,
                    wall_data,
                    before_cursor,
                )

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

                # Process media with transaction handling
                should_continue = await in_transaction_or_new(
                    session,
                    process_wall_media,
                    config.debug,
                    "media processing",
                    config,
                    state,
                    all_media_ids,
                )

                if not should_continue:
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
                await session.commit()

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
            print_info_highlight(str(e))
            print()
            setattr(e, "_handled", True)
            break  # Break out of the loop to stop processing this wall

        except Exception:
            print_error(
                f"Unexpected error during wall download: \n{traceback.format_exc()}",
                36,
            )
            input_enter_continue(config.interactive)
