"""Stash-only mode: no Fansly contact, creators from the database, files from disk."""

import logging

import httpx
import pytest
import respx

from config.logging import init_logging_config
from config.modes import DownloadMode
from errors import EXIT_SUCCESS, SOME_USERS_FAILED
from fansly_downloader_ng import _run_stash_only
from metadata import Media
from tests.fixtures.api import run_main_and_cleanup
from tests.fixtures.metadata.metadata_factories import AccountFactory
from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.utils.test_isolation import snowflake_id


_GRAPHQL_URL = "http://localhost:9999/graphql"
_STASH = {"scheme": "http", "host": "localhost", "port": 9999, "apikey": ""}


@pytest.mark.asyncio
async def test_main_stash_only_never_contacts_fansly(main_integration_env, caplog):
    """A stash-only run needs no Fansly credentials and sends Fansly nothing.

    The config carries no token, user agent or check key, and no Stash
    connection either, so the run ends after saying what is missing.
    """
    env = main_integration_env
    env.config.download_mode = DownloadMode.STASH_ONLY
    env.config.token = None
    env.config.user_agent = None
    env.config.check_key = None
    init_logging_config(env.config)
    caplog.set_level(logging.WARNING)

    result = await run_main_and_cleanup(env.config)

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert result == EXIT_SUCCESS
    assert respx.calls.call_count == 0
    assert env.fake_ws.sent == []
    assert any("needs a stash_context" in message for message in warnings)


@pytest.mark.asyncio
async def test_stash_only_picks_creators_from_the_database(
    entity_store, respx_stash_processor, tmp_path, caplog
):
    """Named creators must be in the database; with no names, followed ones are used.

    Nothing here reaches Stash: one name is unknown, and the creators that are
    known have no folder on disk.
    """
    caplog.set_level(logging.INFO)
    config = respx_stash_processor.config
    config.download_mode = DownloadMode.STASH_ONLY
    config.stash_context_conn = _STASH
    config.download_directory = tmp_path
    config.use_following = False
    await entity_store.save(
        AccountFactory.build(id=snowflake_id(), username="followed_one", following=True)
    )
    await entity_store.save(
        AccountFactory.build(
            id=snowflake_id(), username="not_followed", following=False
        )
    )

    config.user_names = {"missing_creator", "Not_Followed"}
    named = await _run_stash_only(config)
    config.user_names = set()
    followed = await _run_stash_only(config)

    messages = [r.getMessage() for r in caplog.records]
    no_folder = [m for m in messages if "has no folder on disk" in m]
    assert named == SOME_USERS_FAILED
    assert followed == EXIT_SUCCESS
    assert any("@missing_creator is not in the database" in m for m in messages)
    assert [m.split()[0] for m in no_folder] == ["@not_followed", "@followed_one"]
    assert respx.calls.call_count == 0


@pytest.mark.asyncio
async def test_stash_only_scans_the_folder_before_the_sync(
    entity_store, respx_stash_processor, tmp_path, valid_mp4_file
):
    """The creator's folder is scanned first, and a failed sync fails only that creator.

    The file on disk has no record in the database. Stash refuses the first
    request, so the run reports the creator as failed without raising, and the
    scan's record of the file is already saved.
    """
    config = respx_stash_processor.config
    config.download_mode = DownloadMode.STASH_ONLY
    config.stash_context_conn = _STASH
    config.download_directory = tmp_path
    config.use_folder_suffix = False
    config.use_following = False
    config.user_names = {"disk_creator"}
    account_id = snowflake_id()
    await entity_store.save(
        AccountFactory.build(id=account_id, username="disk_creator")
    )
    media_id = snowflake_id()
    name = f"2024-03-05_at_12-30_UTC_id_{media_id}.mp4"
    videos = tmp_path / "disk_creator" / "Timeline" / "Videos"
    videos.mkdir(parents=True)
    (videos / name).write_bytes(valid_mp4_file.read_bytes())

    route = respx.post(_GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json={"errors": [{"message": "Stash refused"}]})
        ]
    )
    try:
        result = await _run_stash_only(config)
    finally:
        dump_graphql_calls(route.calls, "stash_only_scans_the_folder_before_the_sync")

    media = await entity_store.get(Media, media_id)
    assert result == SOME_USERS_FAILED
    assert route.call_count == 1
    assert media is not None
    assert media.local_filename == name
    assert media.accountId == account_id
