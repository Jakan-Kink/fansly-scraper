"""Download Statistics Module

This module handles both content statistics (pictures, videos, etc.) and file download
statistics (total files, sizes, etc.), as well as timing statistics.
"""

from datetime import datetime, timezone
from time import sleep

from config import FanslyConfig
from download.core import DownloadState, GlobalState
from helpers.timer import Timer
from textio import print_info


def print_timing_statistics(timer: Timer) -> None:
    """Prints timing statistics.

    :param timer: The timer object to print statistics for.
    :type timer: Timer
    """
    print_info(f"Total time elapsed: {timer.get_elapsed_time_str()}")
    print_info(Timer.get_all_timers_str())


def update_global_statistics(
    global_state: GlobalState, download_state: DownloadState
) -> None:
    """Updates the global statistics from each creator downloaded.

    This function updates both content statistics (pictures, videos, etc.) and
    file download statistics (total files, sizes, etc.).

    Args:
        global_state: The global state object to update
        download_state: The download state containing the new statistics
    """
    # Update content statistics
    global_state.duplicate_count += download_state.duplicate_count
    global_state.pic_count += download_state.pic_count
    global_state.vid_count += download_state.vid_count
    global_state.total_message_items += download_state.total_message_items
    global_state.total_timeline_pictures += download_state.total_timeline_pictures
    global_state.total_timeline_videos += download_state.total_timeline_videos

    # Update file download statistics
    if not hasattr(download_state, "download_stats"):
        download_state.download_stats = {
            "total_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "total_size": 0,
            "total_size_str": "0 B",
        }

    if not hasattr(global_state, "download_stats"):
        global_state.download_stats = {
            "start_time": datetime.now(timezone.utc),
            "total_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "total_size": 0,
            "total_size_str": "0 B",
        }

    stats = download_state.download_stats
    global_state.download_stats["total_count"] += stats["total_count"]
    global_state.download_stats["skipped_count"] += stats["skipped_count"]
    global_state.download_stats["failed_count"] += stats["failed_count"]
    global_state.download_stats["total_size"] += stats["total_size"]
    global_state.download_stats["total_size_str"] = stats["total_size_str"]


def print_statistics_helper(state: GlobalState, header: str, footer: str = "") -> None:
    """Print content statistics in a formatted way.

    Args:
        state: The state object containing the statistics
        header: The header text to display
        footer: Optional footer text to display
    """
    missing_message = ""

    if state.missing_items_count() > 0:
        missing_message = " (this may indicate a problem)"

    print_info(
        f"{header}"
        f"\n  Total timeline media: {state.total_timeline_pictures} pictures & {state.total_timeline_videos} videos (= {state.total_timeline_items()} items)"
        f"\n  Total message media: {state.total_message_items}"
        f"\n  Total media (timeline & messages): {state.total_timeline_items() + state.total_message_items}"
        f"\n  Downloaded media: {state.pic_count} pictures & {state.vid_count} videos (= {state.total_downloaded_items()} items)"
        f"\n  Duplicates skipped: {state.duplicate_count}"
        f"\n  Missing items: {state.missing_items_count()}{missing_message}"
        f"{footer}"
        f"\n{74 * ' '}═╝"
    )


def print_statistics(config: FanslyConfig, state: DownloadState) -> None:
    """Print statistics for a single creator.

    Args:
        config: The configuration object
        state: The download state containing the statistics
    """
    header = f"\n╔═\n  Finished {config.download_mode_str()} type download for @{state.creator_name}!"

    footer = ""

    if not state.following:
        footer += "\n  Follow the creator to be able to scrape media!"

    elif not state.subscribed:
        footer += (
            "\n  Subscribe to the creator if you would like to get the entire content."
        )

    elif not config.download_media_previews and state.missing_items_count() > 0:
        footer += (
            "\n  Try setting download_media_previews to True in the config.ini file."
        )
        footer += "\n  Doing so will help if the creator has marked all his content as previews."

    print_statistics_helper(state, header, footer)

    sleep(10)


def print_global_statistics(config: FanslyConfig, state: GlobalState) -> None:
    """Print global statistics for all creators.

    Args:
        config: The configuration object
        state: The global state containing the statistics
    """
    if config.user_names is None:
        raise RuntimeError("Internal error printing statistics - user names undefined.")

    header = (
        f"\n╔═\n  GRAND TOTAL DOWNLOAD SUMMARY"
        f"\n  Finished downloading media for {len(config.user_names)} creators!"
    )

    footer = ""

    if state.missing_items_count() > 0:
        footer += "\n  Make sure you are following and subscribed to all creators."

    print_statistics_helper(state, header, footer)
