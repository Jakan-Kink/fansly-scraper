#!/usr/bin/env python3

"""Fansly Downloader NG"""

__version__ = "0.11.0"

import asyncio
import atexit
import base64
import contextlib
import gc
import signal
import sys
import time
import traceback

# from memory_profiler import profile
from datetime import UTC, datetime
from time import monotonic
from types import FrameType

from config import (
    DownloadMode,
    FanslyConfig,
    load_config,
    update_logging_config,
    validate_adjust_config,
)
from config.args import (  # Keep in args to avoid circular imports
    map_args_to_config,
    parse_args,
)
from download.core import (
    DownloadState,
    GlobalState,
    download_collections,
    download_messages,
    download_single_post,
    download_timeline,
    download_wall,
    get_creator_account_info,
    get_following_accounts,
    print_download_info,
)
from download.statistics import (
    print_global_statistics,
    print_statistics,
    print_timing_statistics,
    update_global_statistics,
)
from errors import (
    API_ERROR,
    CONFIG_ERROR,
    DOWNLOAD_ERROR,
    EXIT_ABORT,
    EXIT_SUCCESS,
    SOME_USERS_FAILED,
    UNEXPECTED_ERROR,
    ApiAccountInfoError,
    ApiError,
    ConfigError,
    DownloadError,
)
from fileio.dedupe import dedupe_init
from helpers.common import open_location
from helpers.timer import Timer, timing_jitter
from metadata.account import process_account_data
from pathio import delete_temporary_pyinstaller_files
from textio import (
    input_enter_continue,
    json_output,
    print_error,
    print_info,
    print_warning,
    set_window_title,
)
from updater import self_update
from utils.semaphore_monitor import cleanup_semaphores, monitor_semaphores


# tell PIL to be tolerant of files that are truncated
# ImageFile.LOAD_TRUNCATED_IMAGES = True

# turn off for our purpose unnecessary PIL safety features
# Image.MAX_IMAGE_PIXELS = None


async def _safe_cleanup_database(config: FanslyConfig) -> None:
    """Safely clean up database with timeout protection.

    Args:
        config: The program configuration that may contain a database instance.
    """
    if not hasattr(config, "_database") or config._database is None:
        print_info("No database to clean up or database already closed.")
        return

    if (
        hasattr(config._database, "_cleanup_done")
        and config._database._cleanup_done.is_set()
    ):
        print_info("Database cleanup already performed, skipping")
        return

    try:
        # Add timeout for database cleanup
        cleanup_timeout = 30  # 30 seconds timeout for cleanup
        try:
            await asyncio.wait_for(config._database.cleanup(), timeout=cleanup_timeout)
            print_info("Database connections closed successfully.")
        except TimeoutError:
            print_error(f"Database cleanup timed out after {cleanup_timeout} seconds")
            try:
                # Force sync cleanup as last resort
                config._database.close_sync()
            except Exception as force_e:
                print_error(f"Forced cleanup also failed: {force_e}")
        except Exception as detail_e:
            print_error(f"Detailed error during database cleanup: {detail_e}")
            try:
                # Try sync cleanup as fallback
                config._database.close_sync()
            except Exception as sync_e:
                print_error(f"Sync cleanup also failed: {sync_e}")
    except Exception as e:
        print_error(f"Error closing database connections: {e}")
        with contextlib.suppress(Exception):
            # One final attempt with sync cleanup
            config._database.close_sync()

    # Always check for leaked semaphores as a safety measure
    with contextlib.suppress(Exception):
        monitor_semaphores(threshold=20)  # Monitor for leaked semaphores


async def cleanup_database(config: FanslyConfig) -> None:
    """Clean up database connections when the program exits.

    Args:
        config: The program configuration that may contain a database instance.
    """
    if hasattr(config, "_database") and config._database is not None:
        try:
            print_info("Closing database connections...")
            await _safe_cleanup_database(config)
        except Exception as e:
            print_error(f"Error during database cleanup: {e}")
    else:
        print_info("No database to clean up or database already closed.")

    # Always check for leaked semaphores
    with contextlib.suppress(Exception):
        monitor_semaphores(threshold=20)


def cleanup_database_sync(config: FanslyConfig) -> None:
    """Synchronous version of cleanup_database for atexit handler.

    Args:
        config: The program configuration that may contain a database instance.
    """
    if hasattr(config, "_database") and config._database is not None:
        if (
            hasattr(config._database, "_cleanup_done")
            and config._database._cleanup_done.is_set()
        ):
            print_info("Database cleanup already performed (sync), skipping")
            return

        try:
            if hasattr(_handle_interrupt, "interrupted"):
                # Skip if already handling interrupt to avoid deadlocks
                return
            # Call close_sync which includes final sync
            config._database.close_sync()
            print_info("Database connections closed successfully.")
        except Exception as e:
            print_error(f"Error closing database connections: {e}")
    monitor_semaphores(threshold=20)  # Report any leaked semaphores
    cleanup_semaphores(r"/mp-.*")  # Clean up multiprocessing semaphores


def print_logo() -> None:
    """Prints the Fansly Downloader NG logo."""
    print(
        # Base64 code to display logo in console
        base64.b64decode(
            "CiAg4paI4paI4paI4paI4paI4paI4paI4pWXIOKWiOKWiOKWiOKWiOKWiOKVlyDilojilojilojilZcgICDilojilojilZfilojilojilojilojilojilojilojilZfilojilojilZcgIOKWiOKWiOKVlyAgIOKWiOKWiOKVlyAgICDilojilojilojilZcgICDilojilojilZfilojilojilojilojilojilojilojilZcgICAgIOKWiOKWiOKWiOKWiOKWiOKVlyDilojilojilojilojilojilojilZcg4paI4paI4paI4paI4paI4paI4pWXIAogIOKWiOKWiOKVlOKVkOKVkOKVkOKVkOKVneKWiOKWiOKVlOKVkOKVkOKWiOKWiOKVl+KWiOKWiOKWiOKWiOKVlyAg4paI4paI4pWR4paI4paI4pWU4pWQ4pWQ4pWQ4pWQ4pWd4paI4paI4pWRICDilZrilojilojilZcg4paI4paI4pWU4pWdICAgIOKWiOKWiOKWiOKWiOKVlyAg4paI4paI4pWR4paI4paI4pWU4pWQ4pWQ4pWQ4pWQ4pWdICAgIOKWiOKWiOKVlOKVkOKVkOKWiOKWiOKVl+KWiOKWiOKVlOKVkOKVkOKWiOKWiOKVl+KWiOKWiOKVlOKVkOKVkOKWiOKWiOKVlwogIOKWiOKWiOKWiOKWiOKWiOKVlyAg4paI4paI4paI4paI4paI4paI4paI4pWR4paI4paI4pWU4paI4paI4pWXIOKWiOKWiOKVkeKWiOKWiOKWiOKWiOKWiOKWiOKWiOKVl+KWiOKWiOKVkSAgIOKVmuKWiOKWiOKWiOKWiOKVlOKVnSAgICAg4paI4paI4pWU4paI4paI4pWXIOKWiOKWiOKVkeKWiOKWiOKVkSDilojilojilojilZcgICAg4paI4paI4paI4paI4paI4paI4paI4pWR4paI4paI4paI4paI4paI4paI4pWU4pWd4paI4paI4paI4paI4paI4paI4pWU4pWdCiAg4paI4paI4pWU4pWQ4pWQ4pWdICDilojilojilZTilZDilZDilojilojilZHilojilojilZHilZrilojilojilZfilojilojilZHilZrilZDilZDilZDilZDilojilojilZHilojilojilZEgICAg4pWa4paI4paI4pWU4pWdICAgICAg4paI4paI4pWR4pWa4paI4paI4pWX4paI4paI4pWR4paI4paI4pWRICDilojilojilZEgICAg4paI4paI4pWU4pWQ4pWQ4paI4paI4pWR4paI4paI4pWU4pWQ4pWQ4pWQ4pWdIOKWiOKWiOKVlOKVkOKVkOKVkOKVnSAKICDilojilojilZEgICAgIOKWiOKWiOKVkSAg4paI4paI4pWR4paI4paI4pWRIOKVmuKWiOKWiOKWiOKWiOKVkeKWiOKWiOKWiOKWiOKWiOKWiOKWiOKVkeKWiOKWiOKWiOKWiOKWiOKWiOKWiOKVl+KWiOKWiOKVkSAgICAgICDilojilojilZEg4pWa4paI4paI4paI4paI4pWR4paI4paI4paI4paI4paI4paI4paI4pWRICAgIOKWiOKWiOKVkSAg4paI4paI4pWR4paI4paI4pWRICAgICDilojilojilZEgICAgIAogIOKVmuKVkOKVnSAgICAg4pWa4pWQ4pWdICDilZrilZDilZ3ilZrilZDilZ0gIOKVmuKVkOKVkOKVkOKVneKVmuKVkOKVkOKVkOKVkOKVkOKVkOKVneKVmuKVkOKVkOKVkOKVkOKVkOKVkOKVneKVmuKVkOKVnSAgICAgICDilZrilZDilZ0gIOKVmuKVkOKVkOKVkOKVneKVmuKVkOKVkOKVkOKVkOKVkOKVkOKVnSAgICDilZrilZDilZ0gIOKVmuKVkOKVneKVmuKVkOKVnSAgICAg4pWa4pWQ4pWdICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgZGV2ZWxvcGVkIG9uIGdpdGh1Yi5jb20vcHJvZjc5L2ZhbnNseS1kb3dubG9hZGVyLW5nCg=="
        ).decode("utf-8")
    )
    print(f"{(100 - len(__version__) - 1) // 2 * ' '}v{__version__}\n")


def _handle_interrupt(signum: int, frame: FrameType | None) -> None:  # noqa: ARG001
    """Handle interrupt signal (Ctrl+C)."""
    print_error("\nInterrupted by user")
    # Set a flag instead of calling sys.exit directly
    if hasattr(_handle_interrupt, "interrupted"):
        # Second interrupt, force exit
        print_error("Second interrupt received, forcing immediate exit!")
        sys.exit(130)  # 128 + SIGINT(2)
    _handle_interrupt.interrupted = True
    # Raise KeyboardInterrupt to break out of blocking operations
    raise KeyboardInterrupt("User interrupted operation")


def increase_file_descriptor_limit() -> None:
    """Increase the file descriptor limit to handle many open files."""
    try:
        import resource  # Unix-only module, not available on Windows

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        # Try to increase to hard limit or 4096, whichever is lower
        new_soft = min(hard, 4096)
        resource.setrlimit(resource.RLIMIT_NOFILE, (new_soft, hard))
        print_info(f"Increased file descriptor limit from {soft} to {new_soft}")
    except Exception as e:
        print_warning(f"Could not increase file descriptor limit: {e}")


async def load_client_account_into_db(
    config: FanslyConfig,
    state: DownloadState,
    client_user_name: str,
) -> None:
    """Load the client account information into the database.

    Args:
        config: The program configuration
        state: Current download state
        client_user_name: Username of the client account
    """
    await asyncio.sleep(timing_jitter(0.4, 0.75))

    try:
        response = config.get_api().get_creator_account_info(
            creator_name=client_user_name
        )
        json = response.json()
        json_output(
            1,
            "main - client-account-data",
            (json),
        )
        creator_dict = json["response"][0]
    except Exception as e:
        print_error(f"Error getting client account info: {e}")
        print_error(f"Error getting client account info: {traceback.format_exc()}")
        raise

    await process_account_data(
        config=config,
        state=state,
        data=creator_dict,
    )


# @profile(precision=2, stream=open('memory_use.log', 'w', encoding='utf-8'))
async def main(config: FanslyConfig) -> int:
    """The main logic of the downloader program.

    :param config: The program configuration.
    :type config: FanslyConfig

    :return: The exit code of the program.
    :rtype: int
    """
    # Check semaphores at start of main
    monitor_semaphores(threshold=20)  # Check what semaphores exist at main start

    exit_code = EXIT_SUCCESS

    timer = Timer("Total")

    timer.start()

    # Update window title with specific downloader version
    set_window_title(f"Fansly Downloader NG v{config.program_version}")

    print_logo()

    delete_temporary_pyinstaller_files()
    load_config(config)

    args = parse_args()
    # Note that due to config._sync_settings(), command-line arguments
    # may overwrite config.ini settings later on during validation
    # when the config may be saved again.
    # Thus a separate config_args.ini will be used for the session.
    download_mode_set = map_args_to_config(args, config)
    update_logging_config(
        config, config.debug
    )  # Update logging with final config state

    self_update(config)

    validate_adjust_config(config, download_mode_set)

    if config.user_names is None or config.download_mode == DownloadMode.NOTSET:
        raise RuntimeError(
            "Internal error - user name and download mode should not be empty after validation."
        )

    print()

    # Check semaphores before database initialization
    monitor_semaphores(threshold=20)  # Check what semaphores exist before DB init

    # Initialize database first since we need it for deduplication
    from metadata.database import Database

    # Initialize database first
    if config.separate_metadata:
        print_info("Using separate metadata databases per creator")
    else:
        print_info(
            f"Using global PostgreSQL database: {config.pg_database} "
            f"at {config.pg_host}:{config.pg_port}"
        )
        config._database = Database(config, creator_name=None)
        # Register cleanup function to ensure database is closed on exit
        atexit.register(cleanup_database_sync, config)
    print()

    # Set up and print API information
    await config.setup_api()
    api = config.get_api()

    print_info(f"Token: {config.token}")
    print_info(f"Check Key: {config.check_key}")
    print_info(
        f"Device ID: {api.device_id} "
        f"({datetime.fromtimestamp(api.device_id_timestamp / 1000, tz=UTC)})"
    )
    print_info(f"Session ID: {api.session_id}")
    client_user_name = api.get_client_user_name()
    print_info(f"User ID: {client_user_name}")
    if client_user_name is None or client_user_name == "":
        raise ConfigError("Could not retrieve client account user name from API")

    # Load client account into global database if not using separate metadata
    if not config.separate_metadata:
        state = DownloadState()
        await load_client_account_into_db(config, state, client_user_name)

    global_download_state = GlobalState()

    print()

    # If no usernames specified or --use-following flag is set, get client account info and following list
    if not config.user_names or config.use_following:
        with Timer("following_list"):
            try:
                # Get client account info first
                state = DownloadState()

                # Skip account data processing if using separate metadata
                if not config.separate_metadata:
                    await get_creator_account_info(config, state)
                    print_info("Creator account info retrieved...")
                await asyncio.sleep(timing_jitter(0.4, 0.75))

                print_info("Getting following list... (from main)")
                # Then get and process following list
                try:
                    usernames = await get_following_accounts(config, state)
                except Exception as e:
                    print_error(f"Error in session scope: {e}")
                    raise
                await asyncio.sleep(timing_jitter(0.4, 0.75))

                if usernames:
                    print_info(f"Following list: {', '.join(usernames)}")
                    config.user_names = usernames
                else:
                    print_error("No usernames found in following list")
                    return 1
            except Exception as e:
                print_error(f"Failed to process following list: {e}")
                return 1

    # Process each creator
    creators_list = sorted(
        config.user_names, key=str.lower, reverse=config.reverse_order
    )
    if config.reverse_order:
        print_info("Processing creators in reverse order")
    for creator_name in creators_list:
        with Timer(creator_name):
            try:
                state = DownloadState(creator_name=creator_name)

                # Initialize database-related variables
                creator_database = None
                orig_database = None

                # Handle per-creator database if enabled
                # Note: With PostgreSQL, this still uses the same database connection
                # but allows for per-creator schema isolation if needed in future
                if config.separate_metadata:
                    print_info(
                        f"Using per-creator metadata for: {creator_name} "
                        f"(PostgreSQL: {config.pg_database} at {config.pg_host}:{config.pg_port})"
                    )
                    # Store original database instance
                    orig_database = config._database
                    # Set up creator database context
                    creator_database = Database(config, creator_name=creator_name)
                    config._database = creator_database
                    # Load client account into separate database
                    await load_client_account_into_db(config, state, client_user_name)

                try:
                    creator_start_monotonic = monotonic()

                    print_download_info(config)

                    await get_creator_account_info(config, state)

                    print_info(f"Download mode is: {config.download_mode_str()}")
                    print()

                    # Special treatment for deviating folder names later
                    if config.download_mode not in (
                        DownloadMode.SINGLE,
                        DownloadMode.STASH_ONLY,
                    ):
                        await dedupe_init(config, state)
                        # await dedupe_init(config, state)

                    # Download mode:
                    # Normal: Downloads Timeline + Messages one after another.
                    # Timeline: Scrapes only the creator's timeline content.
                    # Messages: Scrapes only the creator's messages content.
                    # Wall: Scrapes only the creator's wall content.
                    # Single: Fetch a single post by the post's ID. Click on a post to see its ID in the url bar e.g. ../post/1283493240234
                    # Collection: Download all content listed within the "Purchased Media Collection"
                    # STASH_ONLY: Only process Stash metadata, skip downloading media.

                    if config.download_mode == DownloadMode.SINGLE:
                        await download_single_post(config, state)

                    elif config.download_mode == DownloadMode.COLLECTION:
                        await download_collections(config, state)

                    elif config.download_mode != DownloadMode.STASH_ONLY:
                        if any(
                            [
                                config.download_mode == DownloadMode.MESSAGES,
                                config.download_mode == DownloadMode.NORMAL,
                            ]
                        ):
                            await download_messages(config, state)

                        if any(
                            [
                                config.download_mode == DownloadMode.TIMELINE,
                                config.download_mode == DownloadMode.NORMAL,
                            ]
                        ):
                            await download_timeline(config, state)

                        if any(
                            [
                                config.download_mode == DownloadMode.WALL,
                                config.download_mode == DownloadMode.NORMAL,
                            ]
                        ):
                            for wall_id in state.walls:
                                await download_wall(config, state, wall_id)

                    update_global_statistics(
                        global_download_state, download_state=state
                    )
                    print_statistics(config, state)

                    # open download folder
                    if state.base_path is not None:
                        open_location(
                            state.base_path,
                            config.open_folder_when_finished,
                            config.interactive,
                        )

                    if config.stash_context_conn is not None:
                        from stash import StashProcessing

                        # Create processor using factory method
                        stash_processor = StashProcessing.from_config(config, state)

                        # Run initial processing and wait for background tasks
                        await stash_processor.start_creator_processing()

                        # Wait for background processing to complete
                        if stash_processor._background_task:
                            try:
                                # print_info(
                                #     "Waiting for background processing to complete..."
                                # )
                                await stash_processor._background_task
                                # print_info("Background processing complete")
                            except Exception as e:
                                print_error(f"Background processing failed: {e}")
                                # Continue to next creator even if background processing fails
                                exit_code = SOME_USERS_FAILED

                        # Clean up processor
                        await stash_processor.cleanup()
                    monitor_semaphores(threshold=20)  # Warn if too many semaphores
                    cleanup_semaphores(r"/mp-.*")  # Clean up multiprocessing semaphores

                finally:
                    # Log creator processing time
                    creator_elapsed = monotonic() - creator_start_monotonic
                    print_info(
                        f"Completed processing @{state.creator_name} in {creator_elapsed:.1f}s"
                    )

                    # Restore the original database instance - don't cleanup the database
                    # since stash might still be using the shared memory
                    if config.separate_metadata and orig_database is not None:
                        config._database = orig_database

            # Still continue if one creator failed
            except ApiAccountInfoError as e:
                print_error(str(e))
                input_enter_continue(config.interactive)
                exit_code = SOME_USERS_FAILED

    timer.stop()

    print_timing_statistics(timer)

    print_global_statistics(config, global_download_state)

    # Wait for all background tasks to complete
    if config.get_background_tasks():
        print_info(
            f"Waiting for {len(config.get_background_tasks())} background tasks to complete..."
        )

        try:
            # Categorize tasks to handle Stash processing tasks separately
            stash_tasks = []
            other_tasks = []

            for task in config.get_background_tasks():
                try:
                    # Check if this is a StashProcessing task by examining the coroutine name
                    coro_name = task.get_coro().__qualname__
                    if (
                        "StashProcessing" in coro_name
                        or "_safe_background_processing" in coro_name
                    ):
                        stash_tasks.append(task)
                    else:
                        other_tasks.append(task)
                except Exception:
                    # If we can't determine the task type, treat it as other
                    other_tasks.append(task)

            # Report categorization
            if stash_tasks:
                print_info(f"Found {len(stash_tasks)} Stash processing tasks")
            if other_tasks:
                print_info(f"Found {len(other_tasks)} other background tasks")

            # First, process Stash tasks with a longer timeout
            if stash_tasks:
                stash_timeout = 180  # 3 minutes for Stash tasks
                print_info(
                    f"Waiting up to {stash_timeout} seconds for Stash processing tasks..."
                )

                try:
                    # Wait for Stash tasks with progress reporting
                    pending_stash = list(stash_tasks)

                    # Check progress every second up to the timeout
                    for elapsed in range(stash_timeout):
                        # Check if we're done
                        if not pending_stash:
                            break

                        # Update which tasks are still pending
                        pending_stash = [t for t in pending_stash if not t.done()]

                        # Show progress every 10 seconds
                        if elapsed > 0 and elapsed % 10 == 0:
                            print_info(
                                f"Still waiting on {len(pending_stash)}/{len(stash_tasks)} Stash tasks ({elapsed}s elapsed)"
                            )

                        # Break if all done
                        if not pending_stash:
                            print_info(f"All Stash tasks completed in {elapsed}s")
                            break

                        # Wait a bit before checking again
                        await asyncio.sleep(1)

                    # After waiting, check if any tasks are still pending
                    pending_stash = [t for t in stash_tasks if not t.done()]
                    if pending_stash:
                        print_warning(
                            f"{len(pending_stash)} Stash tasks did not complete within timeout, will cancel them"
                        )
                        for task in pending_stash:
                            if not task.done():
                                task.cancel()

                        # Give them a short time to handle cancellation
                        try:
                            await asyncio.wait(pending_stash, timeout=10)
                        except Exception as e:
                            print_warning(f"Error during Stash task cancellation: {e}")
                    else:
                        print_info("All Stash processing tasks completed successfully")

                except Exception as e:
                    print_error(f"Error waiting for Stash tasks: {e}")
                    # Cancel remaining tasks
                    for task in stash_tasks:
                        if not task.done():
                            task.cancel()

            # Then process other tasks with the original timeout
            if other_tasks:
                other_timeout = 30  # Original 30 seconds for other tasks
                print_info(
                    f"Waiting up to {other_timeout} seconds for other background tasks..."
                )

                try:
                    await asyncio.wait_for(
                        asyncio.gather(*other_tasks, return_exceptions=True),
                        timeout=other_timeout,
                    )
                    print_info("All other background tasks completed successfully")
                except TimeoutError:
                    print_warning(
                        f"Other tasks did not complete within {other_timeout} seconds"
                    )
                    print_warning("Cancelling remaining other tasks...")
                    for task in other_tasks:
                        if not task.done():
                            task.cancel()
                    print_warning("Other background tasks cancelled due to timeout")
                except Exception as e:
                    print_error(f"Error in other background tasks: {e}")
                    for task in other_tasks:
                        if not task.done():
                            task.cancel()

            print_info("All background task processing completed")

        except asyncio.CancelledError:
            print_warning("Background tasks were cancelled by external signal")
            config.cancel_background_tasks()
        except Exception as e:
            print_error(f"Error in background tasks: {e}")
            config.cancel_background_tasks()
        print_info("All background tasks completed or cancelled.")

    monitor_semaphores(threshold=20)  # Warn if too many semaphores
    cleanup_semaphores(r"/mp-.*")  # Clean up multiprocessing semaphores

    return exit_code


async def cleanup_with_global_timeout(config: FanslyConfig) -> None:
    """Perform cleanup with a global timeout to ensure program exits."""
    cleanup_start = time.time()
    max_cleanup_time = 40  # Increased maximum cleanup time to 40 seconds

    # First check and cancel any Stash processing tasks
    try:
        # Look for tasks that belong to StashProcessing
        stash_tasks = []
        for task in config.get_background_tasks():
            with contextlib.suppress(Exception):
                coro_name = task.get_coro().__qualname__
                if (
                    "StashProcessing" in coro_name
                    or "_safe_background_processing" in coro_name
                ):
                    stash_tasks.append(task)

        if stash_tasks:
            print_warning(
                f"Found {len(stash_tasks)} Stash processing tasks to clean up..."
            )
            # Cancel Stash tasks first
            for task in stash_tasks:
                if not task.done():
                    task.cancel()

            # Give Stash tasks a chance to cleanup their resources
            stash_timeout = min(10, max_cleanup_time - (time.time() - cleanup_start))
            if stash_timeout > 0:
                try:
                    await asyncio.wait(stash_tasks, timeout=stash_timeout)
                    print_info("Stash tasks cleanup completed or timed out")
                except Exception as e:
                    print_warning(f"Error waiting for Stash tasks: {e}")
    except Exception as e:
        print_warning(f"Error during Stash task cleanup: {e}")

    # Then handle remaining background tasks
    background_tasks = config.get_background_tasks()
    if background_tasks:
        print_warning(
            f"Cancelling {len(background_tasks)} remaining background tasks..."
        )
        try:
            # Cancel all tasks and wait for them with timeout
            for task in background_tasks:
                if not task.done():
                    task.cancel()

            # Give tasks a chance to cleanup
            try:
                background_timeout = min(
                    10, max_cleanup_time - (time.time() - cleanup_start)
                )
                if background_timeout > 0:
                    await asyncio.wait(background_tasks, timeout=background_timeout)
                    print_info("Background tasks cleanup completed or timed out")
            except TimeoutError:
                print_warning(
                    f"Background task cleanup timed out after {background_timeout} seconds"
                )
            except Exception as e:
                print_warning(f"Error waiting for background tasks: {e}")
        except Exception as e:
            print_warning(f"Could not cancel background tasks: {e}")

    # Now handle database cleanup
    print_info("Starting database cleanup...")
    try:
        # Calculate remaining time for database cleanup
        db_timeout = min(30, max_cleanup_time - (time.time() - cleanup_start))
        if db_timeout <= 0:
            print_warning("No time remaining for database cleanup")
            return

        # Call cleanup directly without a task or timeout
        # The Database.cleanup method now has built-in progress display
        if hasattr(config, "_database") and config._database is not None:
            await cleanup_database(config)
            print_info("Database cleanup completed successfully")
        else:
            print_info("No database to clean up")
    except Exception as db_error:
        print_error(f"Error during database cleanup: {db_error}")

    # Finally clean up any remaining semaphores if time allows
    try:
        remaining_time = max_cleanup_time - (time.time() - cleanup_start)
        if remaining_time > 0:
            print_info("Cleaning up semaphores...")
            monitor_semaphores(threshold=20)
            cleanup_semaphores(r"/mp-.*")
            print_info("Semaphore cleanup completed")
        else:
            print_warning("No time remaining for semaphore cleanup")
    except Exception as e:
        print_warning(f"Error during semaphore cleanup: {e}")

    total_cleanup_time = time.time() - cleanup_start
    print_info(f"Final cleanup complete (took {total_cleanup_time:.2f} seconds)")

    # Request garbage collection as a last attempt to clean up
    with contextlib.suppress(Exception):
        gc.collect()


async def _async_main(config: FanslyConfig) -> int:
    """Async wrapper for main function that handles cleanup."""
    exit_code = EXIT_SUCCESS

    try:
        # Ensure we have an exit handler registered early
        if not hasattr(atexit, "_exithandlers") or not any(
            h[0].__name__ == "cleanup_database_sync" for h in atexit._exithandlers
        ):
            atexit.register(cleanup_database_sync, config)
            print_info("Registered database cleanup exit handler")

        # Run main program
        exit_code = await main(config)
    except KeyboardInterrupt:
        print()
        print_error("Program interrupted by user")
        exit_code = EXIT_ABORT
        # Make sure we don't try to raise KeyboardInterrupt again in cleanup
        if hasattr(_handle_interrupt, "interrupted"):
            print_info("Starting cleanup after interruption...")
    except ApiError as e:
        print()
        print_error(str(e))
        exit_code = API_ERROR
    except ConfigError as e:
        print()
        print_error(str(e))
        exit_code = CONFIG_ERROR
    except DownloadError as e:
        print()
        print_error(str(e))
        exit_code = DOWNLOAD_ERROR
    except Exception as e:
        print()
        print_error(f"An unexpected error occurred: {e}\n{traceback.format_exc()}")
        exit_code = UNEXPECTED_ERROR
    finally:
        try:
            # Run cleanup with global timeout
            print_info("Starting final cleanup process...")
            await cleanup_with_global_timeout(config)
            print_info("Cleanup completed successfully")
        except asyncio.CancelledError:
            print_error("Cleanup was cancelled!")
            sys.exit(1)
        except Exception as e:
            print_error(f"Fatal error during cleanup: {e}")
            print_error(traceback.format_exc())
            sys.exit(1)

    return exit_code


if __name__ == "__main__":
    # Start monitoring semaphores before anything else
    monitor_semaphores(threshold=20)  # Initial baseline check

    # Increase file descriptor limit
    increase_file_descriptor_limit()

    # Create config at top level so it's available for cleanup
    config = FanslyConfig(program_version=__version__)

    # Set up signal handling (for both Unix and Windows)
    signal.signal(signal.SIGINT, _handle_interrupt)
    # Additional Windows-specific signal handling
    if sys.platform == "win32":
        with contextlib.suppress(AttributeError, ValueError):
            # On Windows, CTRL_C_EVENT is more reliable than SIGINT
            signal.signal(signal.CTRL_C_EVENT, _handle_interrupt)  # type: ignore

    try:
        # Get event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Run async main without debug mode to prevent task execution messages
        exit_code = asyncio.run(_async_main(config))

        # Exit with code
        sys.exit(exit_code)

    except Exception as e:
        print_error(f"An unexpected error occurred: {e}")
        print_error(traceback.format_exc())
        sys.exit(UNEXPECTED_ERROR)
    finally:
        # Clean up event loop
        with contextlib.suppress(Exception):
            if not loop.is_closed():
                loop.stop()
                loop.close()

        # # Force exit after 5 seconds
        # print_warning("Forcing program exit in 5 seconds...")
        # time.sleep(5)
        # sys.exit(1)
