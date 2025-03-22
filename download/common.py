"""Common Download Functions"""

import asyncio
import traceback
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import FanslyConfig
from errors import ApiError, DuplicateCountError, DuplicatePageError
from media import MediaItem, parse_media_info
from metadata import Post, Wall, process_media_info
from pathio import set_create_directory_for_download
from textio import input_enter_continue, print_error, print_info, print_warning

from .downloadstate import DownloadState
from .media import download_media
from .types import DownloadType


def get_unique_media_ids(info_object: dict[str, Any]) -> list[str]:
    """Extracts a unique list of media IDs from `accountMedia` and
    `accountMediaBundles` of prominent Fansly API objects.

    :param info_object: A dictionary object obtained via JSON representing
        a timeline, post or messages object.

    :return: An unique list of media ID strings.
    :rtype: list[str]
    """
    # Notes for the "bundles desaster":
    #
    # https://apiv3.fansly.com/api/v1/post?ids=XXX&ngsw-bypass=true
    # response.post.attachments.contentId
    # https://apiv3.fansly.com/api/v1/timelinenew/XXX?before=YYY&after=0&wallId=&contentSearch=&ngsw-bypass=true
    # response posts[0].attachments.contentId
    # points to response.accountMediaBundles
    #
    # response.accountMediaBundles[0].accountMediaIds
    # has media IDs (array) -> no locations, use "account/media" API!!!
    # response.accountMediaBundles[0]
    # has properties like "previewId" and "createdAt"

    account_media = info_object.get("accountMedia", [])
    media_bundles = info_object.get("accountMediaBundles", [])

    def check(item) -> bool:
        if item is None:
            raise ApiError(
                "Media items in response are empty - this is most probably a Fansly API/countermeasure issue."
            )
        return True

    account_media_ids = [media["id"] for media in account_media if check(media)]

    bundle_media_ids = []

    media_id_bundles = [
        bundle["accountMediaIds"] for bundle in media_bundles if check(bundle)
    ]

    # Flatten the ID lists
    for id_list in media_id_bundles:
        bundle_media_ids.extend(id_list)

    all_media_ids = set()

    for id in account_media_ids:
        all_media_ids.add(id)

    for id in bundle_media_ids:
        all_media_ids.add(id)

    return list(all_media_ids)


async def check_page_duplicates(
    config: FanslyConfig,
    page_data: dict[str, Any],
    page_type: str,
    page_id: str | None = None,
    cursor: str | None = None,
    session: AsyncSession | None = None,
) -> None:
    """Check if all posts on a page are already in metadata.

    Args:
        config: FanslyConfig instance
        page_data: Response data containing posts
        page_type: Type of page (e.g., "timeline", "wall")
        page_id: Optional ID of the page (e.g., wall ID)
        cursor: Optional cursor/before value
        session: Optional AsyncSession for database operations

    Raises:
        DuplicatePageError: If all posts are already in metadata
    """
    if not config.use_pagination_duplication:
        return

    if "posts" not in page_data or not page_data["posts"]:
        return

    all_posts_in_metadata = True
    for post in page_data["posts"]:
        # Check if post exists in metadata - only select id to avoid eager loading
        stmt = select(Post.id).where(Post.id == post["id"])
        result = await session.execute(stmt)
        if (
            result.scalar_one_or_none() is None
        ):  # Remove await since scalar_one_or_none() returns directly
            all_posts_in_metadata = False
            break

    if all_posts_in_metadata:
        # If this is a wall, get its metadata for the message
        wall_name = None
        if page_type == "wall" and page_id:
            wall = await session.get(Wall, page_id)
            if wall and wall.name:
                wall_name = wall.name

        await asyncio.sleep(5)  # Sleep before raising to avoid hammering API
        raise DuplicatePageError(page_type, page_id, cursor, wall_name)


def print_download_info(config: FanslyConfig) -> None:
    # starting here: stuff that literally every download mode uses, which should be executed at the very first everytime
    if config.user_agent:
        print_info(
            f"Using user-agent: '{config.user_agent[:28]} [...] {config.user_agent[-35:]}'"
        )

    print_info(
        f"Open download folder when finished, is set to: '{config.open_folder_when_finished}'"
    )
    print_info(
        f"Downloading files marked as preview, is set to: '{config.download_media_previews}'"
    )
    print()

    if config.download_media_previews:
        print_warning(
            "Previews downloading is enabled; repetitive and/or emoji spammed media might be downloaded!"
        )
        print()


async def process_download_accessible_media(
    config: FanslyConfig,
    state: DownloadState,
    media_infos: list[dict],
    post_id: str | None = None,
    session: AsyncSession | None = None,
) -> bool:
    """Filters all media found in posts, messages, ... and downloads them.

    Args:
        config: The downloader configuration.
        state: The state and statistics of what is currently being downloaded.
        media_infos: A list of media informations from posts, timelines, messages, collections and so on.
        post_id: The post ID required for "Single" download mode.

    Returns:
        False as a break indicator for "Timeline" downloads, True otherwise.
    """
    media_items: list[MediaItem] = []

    # Process media info in batches
    batch_size = 15  # Process one timeline page worth of items at a time
    for i in range(0, len(media_infos), batch_size):
        batch = media_infos[i : i + batch_size]
        try:
            # Parse media info for the batch
            for media_info in batch:
                media_items.append(parse_media_info(state, media_info, post_id))

            # Process the entire batch at once
            await process_media_info(
                config,
                {"batch": batch},
                session=session,
            )

        except Exception:
            print_error(
                f"Unexpected error parsing {state.download_type_str()} content;\n{traceback.format_exc()}",
                42,
            )
            input_enter_continue(config.interactive)

    # summarise all scrapable & wanted media
    accessible_media = [
        item
        for item in media_items
        if item.download_url and (not item.is_preview or config.download_media_previews)
    ]

    # Special messages/wall handling
    original_duplicate_threshold = config.DUPLICATE_THRESHOLD

    if state.download_type == DownloadType.MESSAGES:
        state.total_message_items += len(accessible_media)
        # Use 20% of total messages as threshold
        config.DUPLICATE_THRESHOLD = int(0.2 * state.total_message_items)
    elif state.download_type == DownloadType.WALL:
        # Use wall-specific threshold based on content size
        # At least 50, or 30% of wall content
        config.DUPLICATE_THRESHOLD = max(50, int(0.3 * len(accessible_media)))

    # at this point we have already parsed the whole post object and determined what is scrapable with the code above
    print_info(
        f"@{state.creator_name} - amount of media in {state.download_type_str()}: {len(media_infos)} (scrapable: {len(accessible_media)})"
    )

    set_create_directory_for_download(config, state)

    # await process_media_download_accessible(config, state, media_infos=media_infos)

    try:
        # Download media
        await download_media(
            config,
            state,
            accessible_media,
        )

    except DuplicateCountError:
        print_warning(
            f"Already downloaded all possible {state.download_type_str()} content! [Duplicate threshold exceeded {config.DUPLICATE_THRESHOLD}]"
        )
        # Timeline and Wall need a way to break the loop
        if state.download_type in (DownloadType.TIMELINE, DownloadType.WALL):
            return False

    except Exception:
        print_error(
            f"Unexpected error during {state.download_type_str()} download: \n{traceback.format_exc()}",
            43,
        )
        input_enter_continue(config.interactive)

    finally:
        # Reset DUPLICATE_THRESHOLD to the value it was before.
        config.DUPLICATE_THRESHOLD = original_duplicate_threshold

    return True
