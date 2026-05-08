"""Message Downloading"""

from asyncio import sleep

from config import FanslyConfig
from helpers.timer import timing_jitter
from metadata import get_store, process_groups_response, process_messages_metadata
from metadata.models import Account
from textio import input_enter_continue, print_error, print_info, print_warning

from .common import get_unique_media_ids, process_download_accessible_media
from .downloadstate import DownloadState
from .media import fetch_and_process_media
from .types import DownloadType


async def download_messages(config: FanslyConfig, state: DownloadState) -> None:
    """Discover the creator's DM group by ``state.creator_id`` and download it.

    Used by the batch download path where the targeted creator's identity is
    already on ``state``. For the daemon path (group_id known, creator info
    possibly not yet resolved), use :func:`download_messages_for_group`.
    """
    state.download_type = DownloadType.MESSAGES

    print_info("Initiating Messages procedure. Standby for results.")

    groups_response = await config.get_api().get_group()

    if groups_response.status_code != 200:
        print_error(
            f"Failed Messages download. Response code: "
            f"{groups_response.status_code}\n{groups_response.text}",
            31,
        )
        input_enter_continue(config.interactive)
        return

    groups_data = config.get_api().get_json_response_contents(groups_response)
    await process_groups_response(config, state, groups_data)
    groups_list = groups_data["aggregationData"]["groups"]

    # Find the group whose member list includes the targeted creator.
    group_id: str | None = None
    for group in groups_list:
        for user in group["users"]:
            if user["userId"] == state.creator_id:
                group_id = group["id"]
                break
        if group_id:
            break

    if group_id is None:
        print_warning(
            f"Could not find a chat history with "
            f"{state.creator_name}; skipping messages download ..."
        )
        return

    await _download_group_message_loop(config, state, group_id)


async def download_messages_for_group(
    config: FanslyConfig,
    state: DownloadState,
    group_id: int | str,
) -> None:
    """Download messages for a specific DM group (daemon entry point).

    Unlike :func:`download_messages`, this variant takes ``group_id`` directly
    rather than discovering it from ``state.creator_id``. If
    ``state.creator_id`` / ``state.creator_name`` are not already populated,
    they are inferred from the group's ``users`` list (first non-self user
    we can parse) and the EntityStore-cached ``Account`` respectively — so
    the download path (output directory, log lines) has correct creator
    context.
    """
    state.download_type = DownloadType.MESSAGES
    group_id_str = str(group_id)

    groups_response = await config.get_api().get_group()
    if groups_response.status_code != 200:
        print_error(
            f"Failed to fetch groups for group {group_id_str}. Response code: "
            f"{groups_response.status_code}\n{groups_response.text}",
            31,
        )
        return

    groups_data = config.get_api().get_json_response_contents(groups_response)
    await process_groups_response(config, state, groups_data)
    groups_list = groups_data["aggregationData"]["groups"]

    target_group = next((g for g in groups_list if str(g["id"]) == group_id_str), None)
    if target_group is None:
        print_warning(
            f"Group {group_id_str} not found in available DM groups; "
            f"skipping messages download ..."
        )
        return

    # Populate creator info from the group when the caller didn't supply it.
    # For 1:1 DMs, pick any user with a parseable ID — when state.creator_id
    # is already set by the caller (daemon senderId path), we trust it.
    if state.creator_id is None:
        for user in target_group["users"]:
            try:
                state.creator_id = int(user["userId"])
                break
            except (KeyError, TypeError, ValueError):
                continue

    if state.creator_name is None and state.creator_id is not None:
        account = get_store().get_from_cache(Account, state.creator_id)
        if account is not None and account.username:
            state.creator_name = account.username

    if state.creator_id is None:
        print_warning(
            f"Could not identify a creator in group {group_id_str}; "
            f"skipping messages download ..."
        )
        return

    await _download_group_message_loop(config, state, group_id_str)


async def _download_group_message_loop(
    config: FanslyConfig,
    state: DownloadState,
    group_id: str,
) -> None:
    """Paginate through a DM group's messages and download attachments.

    Shared by :func:`download_messages` (batch path) and
    :func:`download_messages_for_group` (daemon path).
    """
    msg_cursor: str = "0"

    while True:
        starting_duplicates = state.duplicate_count

        params = {"groupId": group_id, "limit": "25", "ngsw-bypass": "true"}
        if msg_cursor != "0":
            params["before"] = msg_cursor

        messages_response = await config.get_api().get_message(params)

        if messages_response.status_code != 200:
            print_error(
                f"Failed messages download. messages_req failed with "
                f"response code: {messages_response.status_code}\n"
                f"{messages_response.text}",
                30,
            )
            return

        # Object contains: messages, accountMedia, accountMediaBundles, tips, tipGoals, stories
        messages = config.get_api().get_json_response_contents(messages_response)

        await process_messages_metadata(config, state, messages)

        all_media_ids = get_unique_media_ids(messages)
        accessible = await fetch_and_process_media(config, state, all_media_ids)
        await process_download_accessible_media(config, state, accessible)

        # Print info on skipped downloads if show_skipped_downloads is enabled
        skipped_downloads = state.duplicate_count - starting_duplicates
        if (
            skipped_downloads > 1
            and config.show_downloads
            and not config.show_skipped_downloads
        ):
            print_info(
                f"Skipped {skipped_downloads} already downloaded media item"
                f"{'' if skipped_downloads == 1 else 's'}."
            )

        # Advance cursor for next page
        try:
            # Fansly rate-limiting fix
            await sleep(timing_jitter(2, 4))
            msg_cursor = messages["messages"][-1]["id"]
        except IndexError:
            return  # end of history
