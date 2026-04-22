"""Wall Downloads"""

import traceback
from asyncio import sleep
from typing import Any

from httpx import Response

from config import FanslyConfig
from errors import ApiError, DuplicatePageError
from helpers.rich_progress import get_progress_manager
from helpers.timer import timing_jitter
from metadata import Wall, process_wall_posts
from metadata.models import get_store
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
from .media import fetch_and_process_media
from .types import DownloadType


async def process_wall_data(
    config: FanslyConfig,
    state: DownloadState,
    wall_id: int | str,
    wall_data: dict[str, Any],
    before_cursor: str,
) -> None:
    """Process wall data — check for duplicates and persist posts.

    Args:
        config: FanslyConfig instance
        state: Current download state
        wall_id: ID of the wall to download
        wall_data: Wall data from API response
        before_cursor: Pagination cursor
    """
    await check_page_duplicates(
        config=config,
        page_data=wall_data,
        page_type="wall",
        page_id=wall_id,
        cursor=before_cursor if before_cursor != "0" else None,
    )

    await process_wall_posts(
        config,
        state,
        wall_id,
        wall_data,
    )


async def process_wall_media(
    config: FanslyConfig,
    state: DownloadState,
    media_ids: list[str],
) -> bool:
    """Process wall media — fetch info and download accessible items.

    Args:
        config: FanslyConfig instance
        state: Current download state
        media_ids: List of media IDs to download

    Returns:
        False if deduplication error occurred, True otherwise
    """
    accessible = await fetch_and_process_media(config, state, media_ids)
    result = await process_download_accessible_media(config, state, accessible)
    return result


async def download_wall(
    config: FanslyConfig,
    state: DownloadState,
    wall_id: int | str,
) -> None:
    """Download all posts from a specific wall.

    Args:
        config: FanslyConfig instance
        state: Current download state
        wall_id: ID of the wall to download
    """
    store = get_store()

    # Get wall name from database
    wall = await store.get(Wall, wall_id)
    wall_name = wall.name if wall and wall.name else None
    wall_info = f"'{wall_name}' ({wall_id})" if wall_name else wall_id

    # Update the walls progress bar description with the current wall name
    progress = get_progress_manager()
    progress.update_task(
        "download_walls",
        advance=0,
        description=f"  ├─ Wall: {wall_name or wall_id}",
    )

    # Set download type for directory creation
    state.download_type = DownloadType.WALL

    # Reset duplicate count for this wall
    state.duplicate_count = 0
    state.current_batch_duplicates = 0

    # Initialize pagination cursor
    before_cursor = "0"
    attempts = 0

    # Reliable short-circuit: creator's TimelineStats counts + wall
    # structure are both identical to DB → no activity since last run,
    # no need to scan this wall. Set by download.account.get_creator_account_info.
    if state.creator_content_unchanged:
        print_info(
            f"Creator counts and wall structure unchanged — skipping wall {wall_info}"
        )
        return

    if (
        config.use_duplicate_threshold or config.use_pagination_duplication
    ) and state.fetched_timeline_duplication:
        print_info(
            "Deduplication is enabled and the timeline has been fetched before. "
            "Only new media items will be downloaded."
        )
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

        wall_response: Response | None = None

        try:
            if state.creator_id is None:
                raise RuntimeError("Creator ID should not be None")

            wall_response = config.get_api().get_wall_posts(
                state.creator_id, wall_id, str(before_cursor)
            )

            wall_response.raise_for_status()

            if wall_response.status_code == 200:
                wall_data = config.get_api().get_json_response_contents(wall_response)

                await process_wall_data(
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
                # Reset attempts eg. new page
                attempts = 0

                should_continue = await process_wall_media(
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

                # Get next before_cursor
                try:
                    # Slow down to avoid the Fansly rate-limit
                    await sleep(timing_jitter(2, 4))

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
            e._handled = True
            break  # Break out of the loop to stop processing this wall

        except Exception:
            print_error(
                f"Unexpected error during wall download: \n{traceback.format_exc()}",
                36,
            )
            input_enter_continue(config.interactive)
