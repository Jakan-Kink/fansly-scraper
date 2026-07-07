"""Common Download Functions"""

import asyncio
import traceback

from pydantic import JsonValue

from config import FanslyConfig
from errors import ApiError, DuplicateCountError, DuplicatePageError
from helpers.common import JsonDict, expect_dict, expect_int, expect_list
from metadata import Media, Message, Post, Wall
from metadata.models import get_store
from pathio import set_create_directory_for_download
from textio import input_enter_continue, print_error, print_info, print_warning

from .downloadstate import DownloadState
from .media import download_media
from .types import DownloadType


def get_unique_media_ids(info_object: JsonDict) -> list[int]:
    """Extracts a unique list of media IDs from `accountMedia` and
    `accountMediaBundles` of prominent Fansly API objects.
    """
    account_media = expect_list(info_object.get("accountMedia", []), "accountMedia")
    media_bundles = expect_list(
        info_object.get("accountMediaBundles", []), "accountMediaBundles"
    )

    def check(item: JsonValue) -> bool:
        if item is None:
            raise ApiError(
                "Media items in response are empty - this is most probably a Fansly API/countermeasure issue."
            )
        return True

    account_media_ids = [
        expect_int(expect_dict(media, "accountMedia")["id"], "id")
        for media in account_media
        if check(media)
    ]

    bundle_media_ids: list[int] = []
    for bundle in media_bundles:
        if not check(bundle):
            continue
        id_list = expect_list(
            expect_dict(bundle, "accountMediaBundle")["accountMediaIds"],
            "accountMediaIds",
        )
        bundle_media_ids.extend(expect_int(mid, "accountMediaId") for mid in id_list)

    all_media_ids: set[int] = set()
    for media_id in account_media_ids:
        all_media_ids.add(media_id)
    for media_id in bundle_media_ids:
        all_media_ids.add(media_id)

    return list(all_media_ids)


_PAGE_TYPE_CONFIG: dict[str, tuple[str, type]] = {
    "timeline": ("posts", Post),
    "wall": ("posts", Post),
    "messages": ("messages", Message),
}


async def check_page_duplicates(
    config: FanslyConfig,
    page_data: JsonDict,
    page_type: str,
    page_id: int | str | None = None,
    cursor: int | str | None = None,
    bypass: bool = False,
) -> None:
    """Check if every item on a page is already in metadata.

    Args:
        bypass: When True, never raise — caller signals an access-change
            that invalidates the dedup short-circuit (re-paginate fully).

    Raises:
        DuplicatePageError: If every item is already cached and bypass is False.
    """
    if bypass or not config.use_pagination_duplication:
        return

    page_config = _PAGE_TYPE_CONFIG.get(page_type)
    if page_config is None:
        return
    data_key, model_class = page_config

    items_raw = page_data.get(data_key)
    if not items_raw:
        return
    items = expect_list(items_raw, data_key)

    store = get_store()

    all_in_metadata = all(
        store.get_from_cache(
            model_class, expect_int(expect_dict(item, data_key)["id"], "id")
        )
        is not None
        for item in items
    )

    if all_in_metadata:
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
        print_error(
            f"Unexpected error during {state.download_type_str()} download: \n{traceback.format_exc()}",
            43,
        )
        await input_enter_continue(config.interactive)

    finally:
        config.DUPLICATE_THRESHOLD = original_duplicate_threshold

    return True
