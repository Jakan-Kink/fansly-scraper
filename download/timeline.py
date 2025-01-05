"""Timeline Downloads"""

import random
import traceback

# from pprint import pprint
from time import sleep

from requests import Response

from config import FanslyConfig
from errors import ApiError
from metadata import process_timeline_posts
from textio import input_enter_continue, print_debug, print_error, print_info

from .common import get_unique_media_ids, process_download_accessible_media
from .core import DownloadState
from .media import download_media_infos
from .types import DownloadType


def download_timeline(config: FanslyConfig, state: DownloadState) -> None:

    print_info("Executing Timeline functionality. Anticipate remarkable outcomes!")
    print()

    # This is important for directory creation later on.
    state.download_type = DownloadType.TIMELINE

    # this has to be up here so it doesn't get looped
    timeline_cursor = 0
    attempts = 0
    if config.use_duplicate_threshold and state.fetchedTimelineDuplication:
        print_info(
            "Skipping Timeline download as the current fetchedAt time matches the last pass."
        )
        return

    # Careful - "retry" means (1 + retries) runs
    while True and attempts <= config.timeline_retries:
        if timeline_cursor == 0:
            print_info(
                f"Inspecting most recent Timeline cursor ... [CID: {state.creator_id}]"
            )

        else:
            print_info(
                f"Inspecting Timeline cursor: {timeline_cursor} [CID: {state.creator_id}]"
            )

        timeline_response = Response()

        try:
            if state.creator_id is None or timeline_cursor is None:
                raise RuntimeError("Creator name or timeline cursor should not be None")

            timeline_response = config.get_api().get_timeline(
                state.creator_id, str(timeline_cursor)
            )

            timeline_response.raise_for_status()

            if timeline_response.status_code == 200:

                timeline = timeline_response.json()["response"]
                process_timeline_posts(config, state, timeline)

                if config.debug:
                    print_debug(f"Timeline object: {timeline}")

                all_media_ids = get_unique_media_ids(timeline)

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
                    # Reset attempts eg. new timeline
                    attempts = 0

                # Reset batch duplicate counter for new batch
                state.start_batch()
                media_infos = download_media_infos(
                    config=config, state=state, media_ids=all_media_ids
                )

                if not process_download_accessible_media(config, state, media_infos):
                    # Break on deduplication error - already downloaded
                    break

                # Print info on skipped downloads if show_skipped_downloads is disabled
                if (
                    state.current_batch_duplicates > 0
                    and config.show_downloads
                    and not config.show_skipped_downloads
                ):
                    print_info(
                        f"Skipped {state.current_batch_duplicates} already downloaded media item{'' if state.current_batch_duplicates == 1 else 's'}."
                    )

                print()

                # get next timeline_cursor
                try:
                    # Slow down to avoid the Fansly rate-limit which was introduced in late August 2023
                    sleep(random.uniform(2, 4))

                    timeline_cursor = timeline["posts"][-1]["id"]

                except IndexError:
                    # break the whole while loop, if end is reached
                    break

                except Exception:
                    message = (
                        "Please copy & paste this on GitHub > Issues & provide a short explanation (34):"
                        f"\n{traceback.format_exc()}\n"
                    )

                    raise ApiError(message)

        except KeyError:
            print_error(
                "Couldn't find any scrapable media at all!\
                \n This most likely happend because you're not following the creator, your authorisation token is wrong\
                \n or the creator is not providing unlocked content.",
                35,
            )
            input_enter_continue(config.interactive)

        except Exception:
            print_error(
                f"Unexpected error during Timeline download: \n{traceback.format_exc()}",
                36,
            )
            input_enter_continue(config.interactive)
