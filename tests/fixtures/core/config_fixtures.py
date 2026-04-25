"""Pytest fixtures for FanslyConfig testing.

This module provides pytest fixtures unique to the core config testing layer.

Fixtures previously duplicated in this module — `test_config`, `temp_config_dir`,
`config_parser`, `mock_config_file`, `valid_api_config` — were shadowed by root
`tests/conftest.py` (which wins by pytest discovery order) and have been removed.
Use the root-conftest versions of those fixtures directly.
"""

import argparse

import pytest


@pytest.fixture
def complete_args():
    """Create a complete argparse.Namespace with all required attributes for main().

    This fixture provides a fully-populated Namespace object with all attributes
    that are created by parse_args() in config/args.py.

    Returns:
        argparse.Namespace: Complete args object for testing main()

    Usage:
        def test_main_function(config, complete_args):
            complete_args.use_following = True
            result = await main(config)
    """
    return argparse.Namespace(
        # Essential Options
        use_following=False,
        use_following_with_pagination=False,
        reverse_order=False,
        users=None,
        download_directory=None,
        token=None,
        user_agent=None,
        check_key=None,
        # Download Modes
        download_mode_normal=False,
        download_mode_messages=False,
        download_mode_timeline=False,
        download_mode_collection=False,
        download_mode_single=None,
        # Other Options
        non_interactive=False,
        no_prompt_on_exit=False,
        no_folder_suffix=False,
        no_media_previews=False,
        hide_downloads=False,
        hide_skipped_downloads=False,
        no_open_folder=False,
        no_separate_messages=False,
        no_separate_timeline=False,
        separate_previews=False,
        use_duplicate_threshold=False,
        use_pagination_duplication=False,
        timeline_retries=None,
        timeline_delay_seconds=None,
        api_max_retries=None,
        pg_host=None,
        pg_port=None,
        pg_database=None,
        pg_user=None,
        pg_password=None,
        temp_folder=None,
        stash_only=False,
        # Stash Options
        stash_scheme=None,
        stash_host=None,
        stash_port=None,
        stash_apikey=None,
        # Developer/troubleshooting
        debug=False,
        updated_to=None,
    )


__all__ = [
    "complete_args",
]
