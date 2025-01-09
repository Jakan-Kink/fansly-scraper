#!/usr/bin/env python3

"""Fansly Downloader NG"""

__version__ = "0.10.0"

# TODO: Remove pyffmpeg's "Github Activeness" message
# TODO: Fix in future: audio needs to be properly transcoded from mp4 to mp3, instead of just saved as
# TODO: Rate-limiting fix works but is terribly slow - would be nice to know how to interface with Fansly API properly
# TODO: Check whether messages are rate-limited too or not

import asyncio
import atexit
import base64
import traceback

# from memory_profiler import profile
from datetime import datetime

from alembic.config import Config as AlembicConfig
from config import FanslyConfig, load_config, validate_adjust_config
from config.args import map_args_to_config, parse_args
from config.modes import DownloadMode
from download.core import (
    DownloadState,
    GlobalState,
    download_collections,
    download_messages,
    download_single_post,
    download_timeline,
    download_wall,
    get_creator_account_info,
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
from helpers.timer import Timer
from logging_utils import json_output
from pathio import delete_temporary_pyinstaller_files
from textio import (
    input_enter_close,
    input_enter_continue,
    print_error,
    print_info,
    print_warning,
    set_window_title,
)
from updater import self_update

# tell PIL to be tolerant of files that are truncated
# ImageFile.LOAD_TRUNCATED_IMAGES = True

# turn off for our purpose unnecessary PIL safety features
# Image.MAX_IMAGE_PIXELS = None


def cleanup_database(config: FanslyConfig) -> None:
    """Clean up database connections when the program exits.

    Args:
        config: The program configuration that may contain a database instance.
    """
    if hasattr(config, "_database") and config._database is not None:
        try:
            config._database.close()
            print_info("Database connections closed successfully.")
        except Exception as e:
            print_error(f"Error closing database connections: {e}")


def print_logo() -> None:
    """Prints the Fansly Downloader NG logo."""
    print(
        # Base64 code to display logo in console
        base64.b64decode(
            "CiAg4paI4paI4paI4paI4paI4paI4paI4pWXIOKWiOKWiOKWiOKWiOKWiOKVlyDilojilojilojilZcgICDilojilojilZfilojilojilojilojilojilojilojilZfilojilojilZcgIOKWiOKWiOKVlyAgIOKWiOKWiOKVlyAgICDilojilojilojilZcgICDilojilojilZfilojilojilojilojilojilojilojilZcgICAgIOKWiOKWiOKWiOKWiOKWiOKVlyDilojilojilojilojilojilojilZcg4paI4paI4paI4paI4paI4paI4pWXIAogIOKWiOKWiOKVlOKVkOKVkOKVkOKVkOKVneKWiOKWiOKVlOKVkOKVkOKWiOKWiOKVl+KWiOKWiOKWiOKWiOKVlyAg4paI4paI4pWR4paI4paI4pWU4pWQ4pWQ4pWQ4pWQ4pWd4paI4paI4pWRICDilZrilojilojilZcg4paI4paI4pWU4pWdICAgIOKWiOKWiOKWiOKWiOKVlyAg4paI4paI4pWR4paI4paI4pWU4pWQ4pWQ4pWQ4pWQ4pWdICAgIOKWiOKWiOKVlOKVkOKVkOKWiOKWiOKVl+KWiOKWiOKVlOKVkOKVkOKWiOKWiOKVl+KWiOKWiOKVlOKVkOKVkOKWiOKWiOKVlwogIOKWiOKWiOKWiOKWiOKWiOKVlyAg4paI4paI4paI4paI4paI4paI4paI4pWR4paI4paI4pWU4paI4paI4pWXIOKWiOKWiOKVkeKWiOKWiOKWiOKWiOKWiOKWiOKWiOKVl+KWiOKWiOKVkSAgIOKVmuKWiOKWiOKWiOKWiOKVlOKVnSAgICAg4paI4paI4pWU4paI4paI4pWXIOKWiOKWiOKVkeKWiOKWiOKVkSDilojilojilojilZcgICAg4paI4paI4paI4paI4paI4paI4paI4pWR4paI4paI4paI4paI4paI4paI4pWU4pWd4paI4paI4paI4paI4paI4paI4pWU4pWdCiAg4paI4paI4pWU4pWQ4pWQ4pWdICDilojilojilZTilZDilZDilojilojilZHilojilojilZHilZrilojilojilZfilojilojilZHilZrilZDilZDilZDilZDilojilojilZHilojilojilZEgICAg4pWa4paI4paI4pWU4pWdICAgICAg4paI4paI4pWR4pWa4paI4paI4pWX4paI4paI4pWR4paI4paI4pWRICDilojilojilZEgICAg4paI4paI4pWU4pWQ4pWQ4paI4paI4pWR4paI4paI4pWU4pWQ4pWQ4pWQ4pWdIOKWiOKWiOKVlOKVkOKVkOKVkOKVnSAKICDilojilojilZEgICAgIOKWiOKWiOKVkSAg4paI4paI4pWR4paI4paI4pWRIOKVmuKWiOKWiOKWiOKWiOKVkeKWiOKWiOKWiOKWiOKWiOKWiOKWiOKVkeKWiOKWiOKWiOKWiOKWiOKWiOKWiOKVl+KWiOKWiOKVkSAgICAgICDilojilojilZEg4pWa4paI4paI4paI4paI4pWR4paI4paI4paI4paI4paI4paI4paI4pWRICAgIOKWiOKWiOKVkSAg4paI4paI4pWR4paI4paI4pWRICAgICDilojilojilZEgICAgIAogIOKVmuKVkOKVnSAgICAg4pWa4pWQ4pWdICDilZrilZDilZ3ilZrilZDilZ0gIOKVmuKVkOKVkOKVkOKVneKVmuKVkOKVkOKVkOKVkOKVkOKVkOKVneKVmuKVkOKVkOKVkOKVkOKVkOKVkOKVneKVmuKVkOKVnSAgICAgICDilZrilZDilZ0gIOKVmuKVkOKVkOKVkOKVneKVmuKVkOKVkOKVkOKVkOKVkOKVkOKVnSAgICDilZrilZDilZ0gIOKVmuKVkOKVneKVmuKVkOKVnSAgICAg4pWa4pWQ4pWdICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgZGV2ZWxvcGVkIG9uIGdpdGh1Yi5jb20vcHJvZjc5L2ZhbnNseS1kb3dubG9hZGVyLW5nCg=="
        ).decode("utf-8")
    )
    print(f"{(100 - len(__version__) - 1) // 2 * ' '}v{__version__}\n")


# @profile(precision=2, stream=open('memory_use.log', 'w', encoding='utf-8'))
def main(config: FanslyConfig) -> int:
    """The main logic of the downloader program.

    :param config: The program configuration.
    :type config: FanslyConfig

    :return: The exit code of the program.
    :rtype: int
    """
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

    self_update(config)

    validate_adjust_config(config, download_mode_set)

    if config.user_names is None or config.download_mode == DownloadMode.NOTSET:
        raise RuntimeError(
            "Internal error - user name and download mode should not be empty after validation."
        )

    print()

    # Initialize database first since we need it for deduplication
    from metadata.database import (
        Database,
        get_creator_database_path,
        run_migrations_if_needed,
    )

    alembic_cfg = AlembicConfig("alembic.ini")

    if config.separate_metadata:
        print_info("Using separate metadata databases per creator")
    else:
        print_info(f"Using global metadata database: {config.metadata_db_file}")
        config._database = Database(config)
        # Register cleanup function to ensure database is closed on exit
        atexit.register(cleanup_database, config)
        run_migrations_if_needed(config._database, alembic_cfg)
    print()

    # Print API information
    print_info(f"Token: {config.token}")
    print_info(f"Check Key: {config.check_key}")
    print_info(
        f"Device ID: {config.get_api().device_id} "
        f"({datetime.fromtimestamp(config.get_api().device_id_timestamp / 1000)})"
    )
    print_info(f"Session ID: {config.get_api().session_id}")
    client_user_name = config.get_api().get_client_user_name()
    print_info(f"User ID: {client_user_name}")

    global_download_state = GlobalState()

    # M3U8 fixing interim
    print()
    print_info(
        "Due to important memory usage and video format bugfixes, "
        "existing media items "
        f"\n{' ' * 16} need to be re-hashed (`_hash_`/`_hash1_` to `_hash2_`)."
        f"\n{' ' * 16} Affected files will automatically be renamed in the background."
    )
    print()

    for creator_name in sorted(config.user_names):
        with Timer(creator_name):
            try:
                state = DownloadState(creator_name=creator_name)

                # Initialize database-related variables
                creator_database = None
                orig_db_file = None
                orig_database = None

                # Handle per-creator database if enabled
                if config.separate_metadata:
                    db_path = get_creator_database_path(config, creator_name)
                    print_info(f"Using creator database: {db_path}")
                    # Store original config values
                    orig_db_file = config.metadata_db_file
                    orig_database = config._database
                    # Set up creator database
                    config.metadata_db_file = db_path
                    creator_database = Database(config)
                    config._database = creator_database
                    run_migrations_if_needed(creator_database, alembic_cfg)

                try:
                    from metadata.account import process_account_data

                    # Load client account into the database
                    creator_dict = (
                        config.get_api()
                        .get_creator_account_info(creator_name=client_user_name)
                        .json()["response"][0]
                    )

                    json_output(
                        1,
                        "main - client-account-data",
                        (creator_dict),
                    )

                    process_account_data(
                        config=config,
                        state=state,
                        data=creator_dict,
                    )

                    print_download_info(config)

                    get_creator_account_info(config, state)

                    # Special treatment for deviating folder names later
                    if not config.download_mode == DownloadMode.SINGLE:
                        dedupe_init(config, state)
                        dedupe_init(config, state)

                    # Download mode:
                    # Normal: Downloads Timeline + Messages one after another.
                    # Timeline: Scrapes only the creator's timeline content.
                    # Messages: Scrapes only the creator's messages content.
                    # Wall: Scrapes only the creator's wall content.
                    # Single: Fetch a single post by the post's ID. Click on a post to see its ID in the url bar e.g. ../post/1283493240234
                    # Collection: Download all content listed within the "Purchased Media Collection"

                    print_info(f"Download mode is: {config.download_mode_str()}")
                    print()

                    if config.download_mode == DownloadMode.SINGLE:
                        download_single_post(config, state)

                    elif config.download_mode == DownloadMode.COLLECTION:
                        download_collections(config, state)

                    else:
                        if any(
                            [
                                config.download_mode == DownloadMode.MESSAGES,
                                config.download_mode == DownloadMode.NORMAL,
                            ]
                        ):
                            download_messages(config, state)

                        if any(
                            [
                                config.download_mode == DownloadMode.TIMELINE,
                                config.download_mode == DownloadMode.NORMAL,
                            ]
                        ):
                            download_timeline(config, state)

                        if any(
                            [
                                config.download_mode == DownloadMode.WALL,
                                config.download_mode == DownloadMode.NORMAL,
                            ]
                        ):
                            for wall_id in state.walls:
                                download_wall(config, state, wall_id)

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
                        from stash.processing import StashProcessing

                        # Create processor using factory method
                        stash_processor = StashProcessing.from_config(config, state)

                        # Get or create event loop
                        try:
                            loop = asyncio.get_event_loop()
                        except RuntimeError:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)

                        # Run initial processing and let background tasks continue
                        loop.run_until_complete(
                            stash_processor.start_creator_processing()
                        )

                finally:
                    # Clean up creator database if used
                    if config.separate_metadata and creator_database:
                        creator_database.close()
                        # Restore original config values if they were changed
                        if orig_db_file is not None:
                            config.metadata_db_file = orig_db_file
                        if orig_database is not None:
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
        print_info("Waiting for background tasks to complete...")
        try:
            # Get the current event loop
            loop = asyncio.get_event_loop()
            # Run the tasks in the current loop
            loop.run_until_complete(
                asyncio.gather(*config.get_background_tasks(), return_exceptions=True)
            )
        except Exception as e:
            print_error(f"Error in background tasks: {e}")
        print_info("All background tasks completed.")

    return exit_code


if __name__ == "__main__":
    config = FanslyConfig(program_version=__version__)
    exit_code = EXIT_SUCCESS

    try:
        exit_code = main(config)

    except KeyboardInterrupt:
        print()
        exit_code = EXIT_ABORT

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
            # Try to gracefully complete or cancel background tasks
            if config.get_background_tasks():
                print_warning(
                    "Program stopping. Attempting to complete background tasks..."
                )
                try:
                    loop = asyncio.get_event_loop()
                    # Give tasks a chance to complete with a timeout
                    loop.run_until_complete(
                        asyncio.wait_for(
                            asyncio.gather(
                                *config.get_background_tasks(), return_exceptions=True
                            ),
                            timeout=60.0,
                        )
                    )
                except (TimeoutError, Exception) as e:
                    print_warning(f"Could not complete background tasks: {e}")
                    config.cancel_background_tasks()

            # Ensure database is closed before final input prompt
            cleanup_database(config)

            # Clean up the event loop
            try:
                loop = asyncio.get_event_loop()
                if not loop.is_closed():
                    loop.stop()
                    loop.close()
            except Exception:
                pass
        except Exception as e:
            print_error(f"Error during cleanup: {e}")

        input_enter_close(config.prompt_on_exit)
        exit(exit_code)
