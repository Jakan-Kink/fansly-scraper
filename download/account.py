"""Fansly Account Information"""

import asyncio
from typing import Any

import httpx

from config import FanslyConfig
from config.modes import DownloadMode
from errors import ApiAccountInfoError, ApiAuthenticationError, ApiError
from helpers.timer import timing_jitter
from metadata import Account, TimelineStats, process_account_data
from metadata.models import get_store
from textio import json_output, print_error, print_info

from .downloadstate import DownloadState


def _validate_download_mode(config: FanslyConfig, state: DownloadState) -> None:
    """Validate download mode configuration.

    Args:
        config: The program configuration
        state: Current download state

    Raises:
        RuntimeError: If download mode is not set
    """
    if config.download_mode == DownloadMode.NOTSET:
        message = "Internal error getting account info - config download mode not set."
        raise RuntimeError(message)

    # Skip mode check if getting client account info
    if state.creator_name is not None:
        # Collections are independent of creators and
        # single posts may diverge from configured creators
        valid_modes = {
            DownloadMode.MESSAGES,
            DownloadMode.NORMAL,
            DownloadMode.TIMELINE,
            DownloadMode.WALL,
        }
        if config.download_mode not in valid_modes:
            return


async def _get_account_response(
    config: FanslyConfig, state: DownloadState
) -> httpx.Response:
    """Get account information from API.

    Args:
        config: The program configuration
        state: Current download state

    Returns:
        API response

    Raises:
        ApiAccountInfoError: If API returns non-200 status code
        ApiError: For other API errors
    """
    try:
        # Get client account info if no creator name specified
        if state.creator_name is None:
            raw_response = await _make_rate_limited_request(
                config.get_api().get_client_account_info,
                rate_limit_delay=30.0,
            )
        else:
            raw_response = await _make_rate_limited_request(
                config.get_api().get_creator_account_info,
                state.creator_name,
                rate_limit_delay=30.0,
            )

        if raw_response.status_code != 200:
            message = (
                f"API returned status code {raw_response.status_code} (23). "
                f"Please make sure your configuration file is not malformed."
                f"\n  {raw_response.text}"
            )
            raise ApiAccountInfoError(message)

    except httpx.HTTPError as e:
        message = (
            "Error getting account info from fansly API (22). "
            "Please make sure your configuration file is not malformed."
            f"\n  {e!s}"
        )
        raise ApiError(message)
    else:
        return raw_response


def _extract_account_data(
    response: httpx.Response, config: FanslyConfig
) -> dict[str, Any]:
    """Extract account data from API response.

    Args:
        response: API response
        config: The program configuration for error messages

    Returns:
        Account data dictionary

    Raises:
        ApiAuthenticationError: If authentication fails
        ApiError: For other API errors
        ApiAccountInfoError: If creator name is invalid
    """
    try:
        response_data = config.get_api().get_json_response_contents(response)
        # Client account info is wrapped in an 'account' key
        if isinstance(response_data, dict) and "account" in response_data:
            return response_data["account"]
        # Creator account info is in a list
        if isinstance(response_data, list):
            return response_data[0]

    except KeyError as e:
        if response.status_code == 401:
            message = (
                f"API returned unauthorized (24). "
                f"This is most likely because of a wrong authorization "
                f"token in the configuration file."
                f"\n{21 * ' '}Have you surfed Fansly on this browser recently?"
                f"\n{21 * ' '}Used authorization token: '{config.token}'"
                f"\n  {e!s}\n  {response.text}"
            )
            raise ApiAuthenticationError(message)
        message = (
            "Bad response from fansly API (25). Please make sure your configuration file is not malformed."
            f"\n  {e!s}\n  {response.text}"
        )
        raise ApiError(message)

    except IndexError as e:
        message = (
            "Bad response from fansly API (26). Please make sure your configuration file is not malformed; most likely misspelled the creator name."
            f"\n  {e!s}\n  {response.text}"
        )
        raise ApiAccountInfoError(message)
    else:
        return response_data


def _update_state_from_account(
    config: FanslyConfig,
    state: DownloadState,
    account: Account,
) -> None:
    """Update download state from the persisted Account object.

    Args:
        config: The program configuration
        state: Current download state
        account: Account Pydantic object (from identity map after process_account_data)

    Raises:
        ApiAccountInfoError: If timeline stats are missing
    """
    state.creator_id = account.id

    # Store wall IDs from the resolved relationship
    if account.walls:
        state.walls = {wall.id for wall in account.walls}

    # Skip timeline stats for client account info
    if state.creator_name is not None:
        state.following = account.following or False
        state.subscribed = account.subscribed or False

        if not account.timelineStats:
            raise ApiAccountInfoError(
                f"Can not get timelineStats for creator username '{state.creator_name}'; "
                f"you most likely misspelled it! (27)"
            )

        state.total_timeline_pictures = account.timelineStats.imageCount or 0
        state.total_timeline_videos = account.timelineStats.videoCount or 0

        config.DUPLICATE_THRESHOLD = int(
            0.2 * (state.total_timeline_pictures + state.total_timeline_videos)
        )

        print_info(f"Targeted creator: '{state.creator_name}'")


async def get_creator_account_info(
    config: FanslyConfig,
    state: DownloadState,
) -> None:
    """Get and process creator account information."""
    print_info("Getting account information ...")

    _validate_download_mode(config, state)
    response = await _get_account_response(config, state)
    json_output(1, "account_info", response.json())
    account_data = _extract_account_data(response, config)
    json_output(1, "account_data", account_data)

    store = get_store()
    account_id = (
        int(account_data["id"])
        if isinstance(account_data["id"], str)
        else account_data["id"]
    )

    # Capture DB snapshots BEFORE process_account_data merges API data
    # into the identity map. Two independent signals are captured:
    #
    #   db_fetched_at — used by the existing fetched_timeline_duplication
    #     path. Kept for backwards compatibility; noted unreliable because
    #     TimelineStats.fetchedAt is server cache-regeneration metadata
    #     (see project_monitoring_optimizations memory) — we keep the
    #     signal wired but rely on the stronger check below.
    #
    #   db_stats_snapshot / db_wall_signature — capture the preloaded
    #     content state (counts + wall structure). Compared below against
    #     the merged API values to set creator_content_unchanged. These
    #     fields DO change with real creator activity (posts added/removed,
    #     walls added/deleted/renamed/reordered), so the combination is
    #     reliable where fetchedAt alone is not.
    db_fetched_at = None
    if config.use_duplicate_threshold:
        preloaded_stats = store.get_from_cache(TimelineStats, account_id)
        if preloaded_stats:
            db_fetched_at = preloaded_stats.fetchedAt

    db_stats_snapshot: tuple | None = None
    preloaded_stats = store.get_from_cache(TimelineStats, account_id)
    if preloaded_stats:
        db_stats_snapshot = (
            preloaded_stats.imageCount,
            preloaded_stats.videoCount,
            preloaded_stats.bundleCount,
            preloaded_stats.bundleImageCount,
            preloaded_stats.bundleVideoCount,
        )

    db_wall_signature: frozenset | None = None
    preloaded_account = store.get_from_cache(Account, account_id)
    if preloaded_account is not None and preloaded_account.walls:
        db_wall_signature = frozenset(
            (w.id, w.pos, w.name, w.description) for w in preloaded_account.walls
        )

    # process_account_data MERGES API data into preloaded cache
    # (overwrites fetchedAt etc.) — hence the pre-merge snapshots above.
    await process_account_data(config=config, data=account_data, state=state)

    account = await store.get(Account, account_id)
    if account is None:
        raise ApiAccountInfoError(
            f"Failed to persist account data for '{state.creator_name}'"
        )

    _update_state_from_account(config, state, account)

    # Legacy fetchedAt path (kept for backwards compat with downstream
    # flags that look at this field). Unreliable alone — see notes above.
    if (
        db_fetched_at
        and account.timelineStats
        and account.timelineStats.fetchedAt == db_fetched_at
    ):
        state.fetched_timeline_duplication = True

    # Reliable content-unchanged detection: counts match AND wall
    # structure matches. Both conditions required — counts can match
    # while walls change (post moved between walls), and walls can
    # match while counts change (post added to existing wall). Only
    # when BOTH are identical is it safe to skip the timeline+wall scan.
    api_stats_snapshot: tuple | None = None
    if account.timelineStats:
        api_stats_snapshot = (
            account.timelineStats.imageCount,
            account.timelineStats.videoCount,
            account.timelineStats.bundleCount,
            account.timelineStats.bundleImageCount,
            account.timelineStats.bundleVideoCount,
        )

    api_wall_signature: frozenset | None = None
    if account.walls:
        api_wall_signature = frozenset(
            (w.id, w.pos, w.name, w.description) for w in account.walls
        )

    counts_match = (
        db_stats_snapshot is not None and db_stats_snapshot == api_stats_snapshot
    )
    walls_match = (
        db_wall_signature is not None and db_wall_signature == api_wall_signature
    )

    if counts_match and walls_match:
        state.creator_content_unchanged = True


async def _make_rate_limited_request(
    request_func: callable,
    *args: Any,
    rate_limit_delay: float = 30.0,
    **kwargs: Any,
) -> httpx.Response:
    """Make a request with rate limit handling.

    Args:
        request_func: Function to make the request
        rate_limit_delay: Seconds to wait when rate limited
        *args: Positional args for request_func
        **kwargs: Keyword args for request_func

    Returns:
        Response from the request

    Raises:
        ApiError: For non-rate-limit errors
    """
    await asyncio.sleep(0.2)
    while True:
        try:
            response = request_func(*args, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                print_info(f"Rate limited, waiting {rate_limit_delay} seconds...")
                await asyncio.sleep(rate_limit_delay)
                continue
            raise
        else:
            return response


async def _get_following_page(
    config: FanslyConfig,
    state: DownloadState,
    page: int,
    total_fetched: int,
    page_size: int,
    request_delay: float,
) -> tuple[list[dict], int]:
    """Get a single page of following accounts.

    Args:
        config: FanslyConfig instance
        state: DownloadState instance
        page: Current page number
        total_fetched: Total accounts fetched so far
        page_size: Number of accounts per page
        request_delay: Seconds to wait between requests

    Returns:
        Tuple of (account list, number of accounts)
    """
    response = await _make_rate_limited_request(
        config.get_api().get_following_list,
        rate_limit_delay=30.0,
        user_id=state.creator_id,
        limit=page_size,
        offset=total_fetched,
    )
    await asyncio.sleep(timing_jitter(2, 4))

    json_output(1, f"following_list_page_{page}", response.json())
    following_data = config.get_api().get_json_response_contents(response)

    account_ids = [
        item["accountId"]
        for item in following_data
        if isinstance(item, dict) and "accountId" in item
    ]

    if not account_ids:
        return [], 0

    await asyncio.sleep(request_delay)

    account_response = await _make_rate_limited_request(
        config.get_api().get_account_info_by_id,
        account_ids,
        rate_limit_delay=30.0,
    )
    json_output(1, f"account_details_page_{page}", account_response.json())
    account_data = config.get_api().get_json_response_contents(account_response)
    await asyncio.sleep(timing_jitter(2, 4))

    return account_data, len(account_ids)


async def get_following_accounts(
    config: FanslyConfig,
    state: DownloadState,
) -> set[str]:
    """Get and process list of accounts the user is following.

    This function:
    1. Gets the client's following list using pagination
    2. Processes each account's data into the database
    3. Handles errors and authentication issues

    Args:
        config: FanslyConfig instance
        state: DownloadState instance containing client ID
    Returns:
        Set of usernames from the following list

    Raises:
        RuntimeError: If client ID is not set
        ApiAuthenticationError: If authentication fails
        ApiError: If API request fails
    """
    if state.creator_id is None:
        message = "Internal error getting following list - client ID not set."
        raise RuntimeError(message)

    print_info("Getting following list...")
    try:
        # Settings
        page_size = 50  # Smaller size to avoid URL length issues
        request_delay = 2.0  # seconds between requests

        # Get all following accounts with pagination
        following_accounts = []
        page = 0
        total_fetched = 0

        while True:
            accounts, count = await _get_following_page(
                config=config,
                state=state,
                page=page,
                total_fetched=total_fetched,
                page_size=page_size,
                request_delay=request_delay,
            )

            if not accounts:
                break

            following_accounts.extend(accounts)
            total_fetched += count
            page += 1
            print_info(f"Processed following list page {page}")

            # Wait before next page
            await asyncio.sleep(request_delay)

            # If we got fewer results than requested, we've hit the end
            if count < page_size:
                break

        total = len(following_accounts)
        print_info(f"Found {total} followed accounts")

        if config.reverse_order:
            following_accounts = list(reversed(following_accounts))
            print_info("Processing accounts in reverse order")

        usernames = set()

        for i, account_data in enumerate(following_accounts, 1):
            try:
                await process_account_data(
                    config=config, state=state, data=account_data
                )

                # Use the Account object from identity map
                account = Account.model_validate(account_data)
                if account.username:
                    usernames.add(account.username)
                print_info(
                    f"Processing followed account {i}/{total}: "
                    f"{account.username or 'unknown'}"
                )
            except Exception as e:
                username = account_data.get("username", "unknown")
                print_error(f"Error processing account {username}: {e}")
                continue

    except httpx.HTTPError as e:
        if (
            hasattr(e, "response")
            and e.response is not None
            and e.response.status_code == 401
        ):
            message = (
                f"API returned unauthorized while getting following list. "
                f"This is most likely because of a wrong authorization token."
                f"\nUsed authorization token: '{config.token}'"
                f"\n  {e!s}"
            )
            raise ApiAuthenticationError(message)
        message = f"Error getting following list from Fansly API.\n  {e!s}"
        raise ApiError(message)
    else:
        return usernames
