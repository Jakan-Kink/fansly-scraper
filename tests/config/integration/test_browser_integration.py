"""Integration tests for browser configuration utilities."""

from pathlib import Path

from config.browser import (
    find_leveldb_folders,
    get_token_from_firefox_db,
    get_token_from_firefox_profile,
)
from tests.fixtures.config import firefox_session_row, write_firefox_store


async def test_firefox_profile_and_db_read_real_store(tmp_path, sqlite_connect_spy):
    """A real Firefox profile tree yields the token via the walker and the direct reader."""
    profile = tmp_path / "firefox" / "profile"
    db_path = write_firefox_store(
        profile / "storage" / "default" / "webappsstore.sqlite",
        firefox_session_row("test-fansly-token"),
    )

    assert await get_token_from_firefox_profile(str(profile)) == "test-fansly-token"
    assert await get_token_from_firefox_db(str(db_path)) == "test-fansly-token"
    assert sqlite_connect_spy.all_closed()


def test_find_leveldb_folders_integration(tmp_path: Path) -> None:
    """Folders holding ``.ldb`` files are found; other folders are not."""
    leveldb_paths = [
        tmp_path / "browser1" / "Default" / "Local Storage" / "leveldb",
        tmp_path / "browser2" / "Profile 1" / "Local Storage" / "leveldb",
    ]
    for path in leveldb_paths:
        path.mkdir(parents=True)
        (path / "000001.ldb").write_text("test")
    (tmp_path / "browser3" / "Other").mkdir(parents=True)
    (tmp_path / "browser3" / "Other" / "notes.txt").write_text("not leveldb")

    found_folders = find_leveldb_folders(str(tmp_path))

    assert len(found_folders) == 2
    for path in leveldb_paths:
        assert any(str(path) in str(found) for found in found_folders)
