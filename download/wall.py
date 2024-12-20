"""Wall Downloads"""

import random
import traceback
from time import sleep

from requests import Response

from config import FanslyConfig
from errors import ApiError
from metadata import process_wall_posts
from textio import input_enter_continue, print_debug, print_error, print_info

from .common import get_unique_media_ids, process_download_accessible_media
from .core import DownloadState
from .media import download_media_infos
from .types import DownloadType


def download_wall(config: FanslyConfig, state: DownloadState, wall_id: str) -> None:
    """Download all posts from a specific wall.

    Args:
        config: FanslyConfig instance
        state: Current download state
        wall_id: ID of the wall to download
    """
    print_info(f"Downloading wall {wall_id}. Anticipate remarkable outcomes!")
    print()

    # Set download type for directory creation
    state.download_type = DownloadType.WALL

    # Initialize pagination cursor
    before_cursor = "0"
    attempts = 0

    # Careful - "retry" means (1 + retries) runs
    while True and attempts <= config.timeline_retries:
        starting_duplicates = state.duplicate_count

        if before_cursor == "0":
            print_info(
                f"Inspecting most recent wall posts... [CID: {state.creator_id}, WID: {wall_id}]"
            )
        else:
            print_info(
                f"Inspecting wall posts before: {before_cursor} [CID: {state.creator_id}, WID: {wall_id}]"
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
                process_wall_posts(config, state, wall_id, wall_data)

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
                        sleep(config.timeline_delay_seconds)
                    # Try again
                    attempts += 1
                    continue
                else:
                    # Reset attempts eg. new page
                    attempts = 0

                media_infos = download_media_infos(config, all_media_ids)

                if not process_download_accessible_media(config, state, media_infos):
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
                    sleep(random.uniform(2, 4))

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

        except Exception:
            print_error(
                f"Unexpected error during wall download: \n{traceback.format_exc()}",
                36,
            )
            input_enter_continue(config.interactive)
