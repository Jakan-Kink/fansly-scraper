"""Fansly Account Information"""

import asyncio
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from config import FanslyConfig, with_database_session
from config.modes import DownloadMode
from errors import ApiAccountInfoError, ApiAuthenticationError, ApiError
from helpers.timer import timing_jitter
from metadata import Base, TimelineStats, process_account_data
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

        return raw_response

    except httpx.HTTPError as e:
        message = (
            "Error getting account info from fansly API (22). "
            "Please make sure your configuration file is not malformed."
            f"\n  {e!s}"
        )
        raise ApiError(message)


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
        return response_data

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


def _update_state_from_account(
    config: FanslyConfig,
    state: DownloadState,
    account: dict[str, Any],
) -> None:
    """Update download state with account information.

    Args:
        config: The program configuration
        state: Current download state
        account: Account data dictionary

    Raises:
        ApiAccountInfoError: If timeline stats are missing
    """
    state.creator_id = account["id"]

    # Store wall IDs in DownloadState if they exist
    if "walls" in account:
        state.walls = {wall["id"] for wall in account["walls"]}

    # Skip timeline stats for client account info
    if state.creator_name is not None:
        state.following = account.get("following", False)
        state.subscribed = account.get("subscribed", False)

        try:
            state.total_timeline_pictures = account["timelineStats"]["imageCount"]
        except KeyError:
            raise ApiAccountInfoError(
                f"Can not get timelineStats for creator username '{state.creator_name}'; you most likely misspelled it! (27)"
            )

        state.total_timeline_videos = account["timelineStats"]["videoCount"]

        # overwrite base dup threshold with custom 20% of total timeline content
        config.DUPLICATE_THRESHOLD = int(
            0.2 * int(state.total_timeline_pictures + state.total_timeline_videos)
        )

        print_info(f"Targeted creator: '{state.creator_name}'")
        print()


@with_database_session(async_session=True)
async def get_creator_account_info(
    config: FanslyConfig,
    state: DownloadState,
    session: AsyncSession | None = None,
) -> None:
    """Get and process creator account information.

    Args:
        config: The program configuration
        state: Current download state
        session: Optional AsyncSession for database operations

    Raises:
        RuntimeError: If download mode is not set
        ApiAccountInfoError: If API returns non-200 status code or creator name is invalid
        ApiAuthenticationError: If authentication fails
        ApiError: For other API errors
    """
    print_info("Getting account information ...")

    _validate_download_mode(config, state)
    response = await _get_account_response(config, state)
    json_output(1, "account_info", response.json())
    account = _extract_account_data(response, config)
    json_output(1, "account_data", account)
    _update_state_from_account(config, state, account)

    # Check for timeline duplication if enabled
    if (
        config.use_duplicate_threshold
        and "timelineStats" in account
        and "fetchedAt" in account["timelineStats"]
    ):
        # Get current TimelineStats from db
        stmt = select(TimelineStats).where(TimelineStats.accountId == state.creator_id)
        result = await session.execute(stmt)
        if current_stats := await result.scalar_one_or_none():
            # Convert API timestamp to datetime
            stats_data = {"fetchedAt": account["timelineStats"]["fetchedAt"]}
            Base.convert_timestamps(stats_data, ("fetchedAt",))
            api_fetched_at = stats_data["fetchedAt"]
            # Compare with db fetchedAt
            if current_stats.fetchedAt == api_fetched_at:
                state.fetched_timeline_duplication = True


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
            return response
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:  # Rate limited
                print_info(f"Rate limited, waiting {rate_limit_delay} seconds...")
                await asyncio.sleep(rate_limit_delay)
                continue
            raise


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
    # Get list of account IDs
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

    # Extract account IDs
    account_ids = [
        item["accountId"]
        for item in following_data
        if isinstance(item, dict) and "accountId" in item
    ]

    if not account_ids:
        return [], 0

    # Wait before next request
    await asyncio.sleep(request_delay)

    # Get account details
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
        session: Optional SQLAlchemy session. If not provided, a new session will be created.

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

        # Reverse list if requested
        if config.reverse_order:
            following_accounts = list(reversed(following_accounts))
            print_info("Processing accounts in reverse order")

        # Process accounts and collect usernames
        usernames = set()

        # Process all accounts in a single session
        async with config._database.async_session_scope() as session:
            # Process each account
            for i, account in enumerate(following_accounts, 1):
                username = account.get("username")
                if username:
                    usernames.add(username)
                print_info(
                    f"Processing followed account {i}/{total}: {username or 'unknown'}"
                )

                # Process account data in main DB if NOT using separate metadata
                if not config.separate_metadata:
                    try:
                        await process_account_data(
                            config=config,
                            state=state,
                            data=account,
                            session=session,  # Pass the session
                        )
                        # Flush to ensure data is written
                        await session.flush()
                    except Exception as e:
                        print_error(f"Error processing account {username}: {e}")
                        # Don't fail completely if one account fails
                        continue

        return usernames

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
