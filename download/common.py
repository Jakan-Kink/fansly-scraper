"""Common Download Functions"""

import asyncio
from typing import Any

from config import FanslyConfig
from errors import ApiError, DuplicateCountError, DuplicatePageError
from metadata import Media, Post, Wall
from metadata.models import get_store
from pathio import set_create_directory_for_download
from textio import input_enter_continue, print_error, print_info, print_warning

from .downloadstate import DownloadState
from .media import download_media
from .types import DownloadType


def get_unique_media_ids(info_object: dict[str, Any]) -> list[int]:
    """Extracts a unique list of media IDs from `accountMedia` and
    `accountMediaBundles` of prominent Fansly API objects.
    """
    account_media = info_object.get("accountMedia", [])
    media_bundles = info_object.get("accountMediaBundles", [])

    def check(item: Any) -> bool:
        if item is None:
            raise ApiError(
                "Media items in response are empty - this is most probably a Fansly API/countermeasure issue."
            )
        return True

    account_media_ids = [int(media["id"]) for media in account_media if check(media)]

    bundle_media_ids = []
    for id_list in [
        bundle["accountMediaIds"] for bundle in media_bundles if check(bundle)
    ]:
        bundle_media_ids.extend(int(mid) for mid in id_list)

    all_media_ids: set[int] = set()
    for media_id in account_media_ids:
        all_media_ids.add(media_id)
    for media_id in bundle_media_ids:
        all_media_ids.add(media_id)

    return list(all_media_ids)


async def check_page_duplicates(
    config: FanslyConfig,
    page_data: dict[str, Any],
    page_type: str,
    page_id: int | str | None = None,
    cursor: int | str | None = None,
) -> None:
    """Check if all posts on a page are already in metadata.

    Raises:
        DuplicatePageError: If all posts are already in metadata
    """
    if not config.use_pagination_duplication:
        return

    if "posts" not in page_data or not page_data["posts"]:
        return

    store = get_store()

    # With preload at store init, all existing Posts are in the identity map.
    # Cache check is O(1) per post — no DB queries needed.
    all_posts_in_metadata = True
    for post in page_data["posts"]:
        if store.get_from_cache(Post, post["id"]) is None:
            all_posts_in_metadata = False
            break

    if all_posts_in_metadata:
        wall_name = None
        if page_type == "wall" and page_id:
            wall = await store.get(Wall, int(page_id))
            if wall and wall.name:
                wall_name = wall.name

        await asyncio.sleep(5)
        raise DuplicatePageError(page_type, page_id, cursor, wall_name)


def print_download_info(config: FanslyConfig) -> None:
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

    if config.download_media_previews:
        print_warning(
            "Previews downloading is enabled; repetitive and/or emoji spammed media might be downloaded!"
        )


async def process_download_accessible_media(
    config: FanslyConfig,
    state: DownloadState,
    accessible_media: list[Media],
) -> bool:
    """Download accessible media items.

    Handles duplicate threshold adjustment for messages/walls,
    prints stats, and delegates to download_media.

    Returns:
        False as a break indicator for "Timeline"/"Wall" downloads, True otherwise.
    """
    # Special messages/wall threshold handling
    original_duplicate_threshold = config.DUPLICATE_THRESHOLD

    if state.download_type == DownloadType.MESSAGES:
        state.total_message_items += len(accessible_media)
        config.DUPLICATE_THRESHOLD = int(0.2 * state.total_message_items)
    elif state.download_type == DownloadType.WALL:
        config.DUPLICATE_THRESHOLD = max(50, int(0.3 * len(accessible_media)))

    print_info(
        f"@{state.creator_name} - amount of media in "
        f"{state.download_type_str()}: scrapable: {len(accessible_media)}"
    )

    set_create_directory_for_download(config, state)

    try:
        await download_media(config, state, accessible_media)

    except DuplicateCountError:
        print_warning(
            f"Already downloaded all possible {state.download_type_str()} content! "
            f"[Duplicate threshold exceeded {config.DUPLICATE_THRESHOLD}]"
        )
        if state.download_type in (DownloadType.TIMELINE, DownloadType.WALL):
            return False

    except Exception:
        import traceback

        print_error(
            f"Unexpected error during {state.download_type_str()} download: \n{traceback.format_exc()}",
            43,
        )
        input_enter_continue(config.interactive)

    finally:
        config.DUPLICATE_THRESHOLD = original_duplicate_threshold

    return True
