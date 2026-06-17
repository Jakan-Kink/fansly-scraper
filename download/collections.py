"""Download Fansly Collections"""

from config import FanslyConfig
from helpers.common import expect_dict, expect_list
from metadata.account import process_account_data
from metadata.media import process_media_info
from textio import input_enter_continue, json_output, print_error, print_info

from .common import process_download_accessible_media
from .downloadstate import DownloadState
from .media import fetch_and_process_media
from .types import DownloadType


async def download_collections(config: FanslyConfig, state: DownloadState) -> None:
    """Downloads Fansly purchased item collections."""

    print_info("Starting Collections sequence. Buckle up and enjoy the ride!")

    state.download_type = DownloadType.COLLECTIONS

    collections_response = await config.get_api().get_media_collections()

    if collections_response.status_code == 200:
        json_output(1, "Download Collections", collections_response.json())
        collections = expect_dict(
            config.get_api().get_json_response_contents(collections_response),
            "collections response",
        )

        # Process accounts present in the collections response
        for account in expect_list(collections.get("accounts", []), "accounts"):
            await process_account_data(config, data=expect_dict(account, "account"))

        # Process accountMedia metadata (variants, locations) before download
        account_media = expect_list(collections.get("accountMedia", []), "accountMedia")
        if account_media:
            batch_size = 15
            for i in range(0, len(account_media), batch_size):
                batch = account_media[i : i + batch_size]
                await process_media_info(config, {"batch": batch})

        account_media_orders = expect_list(
            collections.get("accountMediaOrders", []), "accountMediaOrders"
        )
        media_ids = [
            int(str(expect_dict(order, "media order")["accountMediaId"]))
            for order in account_media_orders
        ]

        # fetch_and_process_media handles batching, persistence, and variant selection
        accessible = await fetch_and_process_media(config, state, media_ids)

        await process_download_accessible_media(config, state, accessible)

        if (
            state.duplicate_count > 0
            and config.show_downloads
            and not config.show_skipped_downloads
        ):
            print_info(
                f"Skipped {state.duplicate_count} already downloaded media "
                f"item{'' if state.duplicate_count == 1 else 's'}."
            )

    else:
        print_error(
            f"Collections download failed. Response code: "
            f"{collections_response.status_code}\n{collections_response.text}",
            23,
        )
        await input_enter_continue(config.interactive)
