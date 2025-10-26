"""Timeline Downloads"""

import random
import traceback

# from pprint import pprint
from asyncio import sleep

from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from config import FanslyConfig, with_database_session
from errors import ApiError, DuplicatePageError
from metadata import process_timeline_posts
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


async def process_timeline_data(
    config: FanslyConfig,
    state: DownloadState,
    timeline: dict,
    timeline_cursor: int,
    session: AsyncSession,
) -> None:
    """Process timeline data with proper transaction management.

    Args:
        config: FanslyConfig instance
        state: Current download state
        timeline: Timeline data from API
        timeline_cursor: Current timeline cursor
        session: Database session
    """
    # Check for duplicates before processing posts
    await check_page_duplicates(
        config=config,
        page_data=timeline,
        page_type="timeline",
        page_id=state.creator_id,
        cursor=timeline_cursor if timeline_cursor != 0 else None,
        session=session,
    )
    await session.flush()

    # Only process posts if no duplicates found
    await process_timeline_posts(
        config,
        state,
        timeline,
        session=session,
    )
    await session.flush()


async def process_timeline_media(
    config: FanslyConfig,
    state: DownloadState,
    all_media_ids: list[str],
    session: AsyncSession,
) -> bool:
    """Process timeline media with proper transaction management.

    Args:
        config: FanslyConfig instance
        state: Current download state
        all_media_ids: List of media IDs to download
        session: Database session

    Returns:
        True if processing should continue, False if it should stop
    """
    # Reset batch duplicate counter for new batch
    state.start_batch()

    media_infos = await download_media_infos(
        config=config,
        state=state,
        media_ids=all_media_ids,
        session=session,
    )
    await session.flush()

    results = await process_download_accessible_media(
        config,
        state,
        media_infos,
        session=session,
    )
    await session.flush()
    return results


@with_database_session(async_session=True)
async def download_timeline(
    config: FanslyConfig,
    state: DownloadState,
    session: AsyncSession | None = None,
) -> None:
    """Download timeline posts and media.

    Args:
        config: FanslyConfig instance
        state: Current download state
        session: Optional AsyncSession for database operations

    Raises:
        DuplicatePageError: If all posts on a page are already in metadata
            and use_pagination_duplication is True
    """
    print_info("Executing Timeline functionality...")
    print()

    # This is important for directory creation later on.
    state.download_type = DownloadType.TIMELINE

    # this has to be up here so it doesn't get looped
    timeline_cursor = 0
    attempts = 0
    if (
        config.use_duplicate_threshold or config.use_pagination_duplication
    ) and state.fetchedTimelineDuplication:
        print_info(
            "Skipping Timeline download as the fetchedAt time matches the last pass."
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

        timeline_response: Response | None = None

        try:
            if state.creator_id is None or timeline_cursor is None:
                raise RuntimeError("Creator name or timeline cursor should not be None")

            timeline_response = config.get_api().get_timeline(
                state.creator_id, str(timeline_cursor)
            )

            timeline_response.raise_for_status()

            if timeline_response.status_code == 200:
                timeline = config.get_api().get_json_response_contents(
                    timeline_response
                )

                # Process timeline data with proper transaction management
                await in_transaction_or_new(
                    session,
                    process_timeline_data,
                    config.debug,
                    "timeline data processing",
                    config,
                    state,
                    timeline,
                    timeline_cursor,
                )

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
                        await sleep(config.timeline_delay_seconds)
                    # Try again
                    attempts += 1
                    continue
                else:
                    # Reset attempts eg. new timeline
                    attempts = 0

                # Process timeline media with proper transaction management
                should_continue = await in_transaction_or_new(
                    session,
                    process_timeline_media,
                    config.debug,
                    "media processing",
                    config,
                    state,
                    all_media_ids,
                )

                if not should_continue:
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
                await session.commit()

                # get next timeline_cursor
                try:
                    # Slow down to avoid the Fansly rate-limit which was introduced in late August 2023
                    await sleep(random.uniform(2, 4))

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

        except DuplicatePageError as e:
            print_info_highlight(str(e))
            print()
            setattr(e, "_handled", True)
            break  # Break out of the loop to stop processing this timeline

        except Exception:
            print_error(
                f"Unexpected error during Timeline download: \n{traceback.format_exc()}",
                36,
            )
            input_enter_continue(config.interactive)
