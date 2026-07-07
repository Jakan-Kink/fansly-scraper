"""Unit tests for browser configuration utilities."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# plyvel is optional - skip tests if not available
try:
    import plyvel

    HAS_PLYVEL = True
except ImportError:
    HAS_PLYVEL = False
    plyvel = None  # type: ignore[assignment]

from config.browser import (
    close_browser_by_name,
    get_auth_token_from_leveldb_folder,
    get_browser_config_paths,
    get_token_from_firefox_db,
    get_token_from_firefox_profile,
    parse_browser_from_string,
)


@pytest.mark.parametrize(
    ("input_name", "expected"),
    [
        ("Firefox", "Firefox"),
        ("firefox", "Firefox"),
        ("Mozilla Firefox", "Firefox"),
        ("Brave", "Brave"),
        ("brave-browser", "Brave"),
        ("Opera GX", "Opera GX"),
        ("Opera GX Browser", "Opera GX"),
        ("Opera", "Opera"),
        ("Google Chrome", "Chrome"),
        ("chrome", "Chrome"),
        ("Edge", "Edge"),  # Just Edge without Microsoft
        ("Microsoft Edge", "Microsoft Edge"),  # Both edge and microsoft
        ("Unknown Browser", "Unknown"),
        ("Safari", "Unknown"),  # Not supported
    ],
)
def test_parse_browser_from_string(input_name, expected):
    """Test browser name parsing from various strings."""
    assert parse_browser_from_string(input_name) == expected


@patch("platform.system")
@patch("os.path.expanduser")
@patch("os.getenv")
def test_get_browser_config_paths_windows(mock_getenv, mock_expanduser, mock_platform):
    """Test browser config paths resolution on Windows."""
    mock_platform.return_value = "Windows"
    mock_getenv.side_effect = lambda x: {  # noqa: PLW0108
        "APPDATA": "C:\\Users\\test\\AppData\\Roaming",
        "LOCALAPPDATA": "C:\\Users\\test\\AppData\\Local",
    }.get(x)

    paths = get_browser_config_paths()

    assert len(paths) == 6  # Should return 6 browser paths
    assert any("Chrome" in path for path in paths)
    assert any("Firefox" in path for path in paths)
    assert any("Edge" in path for path in paths)
    assert any("Opera" in path for path in paths)
    assert any("Opera GX" in path for path in paths)
    assert any("Brave" in path for path in paths)


@patch("platform.system")
@patch("os.path.expanduser")
def test_get_browser_config_paths_macos(mock_expanduser, mock_platform):
    """Test browser config paths resolution on macOS."""
    mock_platform.return_value = "Darwin"
    mock_expanduser.return_value = "/Users/testuser"

    paths = get_browser_config_paths()

    assert len(paths) == 6  # Should return 6 browser paths
    assert any("Chrome" in path for path in paths)
    assert any("Firefox" in path for path in paths)
    assert any("Microsoft Edge" in path for path in paths)
    assert any("Opera" in path for path in paths)
    assert any("OperaGX" in path for path in paths)
    assert any("BraveSoftware" in path for path in paths)


@patch("platform.system")
@patch("os.path.expanduser")
def test_get_browser_config_paths_linux(mock_expanduser, mock_platform):
    """Test browser config paths resolution on Linux."""
    mock_platform.return_value = "Linux"
    mock_expanduser.return_value = "/home/testuser"

    paths = get_browser_config_paths()

    assert len(paths) == 5  # Should return 5 browser paths
    assert any("chrome" in path for path in paths)
    assert any("firefox" in path or "snap/firefox" in path for path in paths)
    assert any("opera" in path for path in paths)
    assert any("Brave" in path for path in paths)


def _write_firefox_store(sqlite_path: Path, token: str | None) -> None:
    """Build a real Firefox-style webappsstore.sqlite at ``sqlite_path``.

    Mirrors the on-disk shape the production reader (``get_token_from_firefox_db``)
    walks: every table is scanned, each row's column 0 is the storage key and
    column 5 is the JSON-encoded value as UTF-8 bytes. The reader pulls
    ``json.loads(row[5])["token"]`` when ``row[0] == "session_active_session"``.

    When ``token`` is None, only an unrelated key is written so the real reader
    returns None for that DB.
    """
    conn = sqlite3.connect(str(sqlite_path))
    try:
        conn.execute(
            "CREATE TABLE webappsstore2 "
            "(scope TEXT, c1 TEXT, c2 TEXT, c3 TEXT, c4 TEXT, value BLOB)"
        )
        if token is not None:
            value = json.dumps({"token": token}).encode("utf-8")
            conn.execute(
                "INSERT INTO webappsstore2 VALUES (?, ?, ?, ?, ?, ?)",
                ("session_active_session", None, None, None, None, value),
            )
        else:
            conn.execute(
                "INSERT INTO webappsstore2 VALUES (?, ?, ?, ?, ?, ?)",
                ("unrelated_key", None, None, None, None, b'{"data":"x"}'),
            )
        conn.commit()
    finally:
        conn.close()


async def test_get_token_from_firefox_profile_no_storage(tmp_path):
    """No ``storage`` folder anywhere in the profile tree → None.

    Real filesystem walk over a real profile dir; the production ``os.walk``
    never matches the ``"storage" in root`` predicate, so no sqlite read runs.
    """
    profile = tmp_path / "profile"
    (profile / "other").mkdir(parents=True)
    (profile / "other" / "file.txt").write_text("not a db")

    result = await get_token_from_firefox_profile(str(profile))
    assert result is None


async def test_get_token_from_firefox_profile_deeply_nested_storage(tmp_path):
    """Token in a deeply nested ``storage`` dir is found via the real sqlite read.

    Builds a real ``webappsstore.sqlite`` under ``root/folder1/storage`` and
    lets the real ``os.walk`` + real ``get_token_from_firefox_db`` + real
    ``sqlite3`` edge resolve the token end-to-end (covers the profile-loop
    token-found return at browser.py:47-48).
    """
    storage = tmp_path / "root" / "folder1" / "storage" / "subdir"
    storage.mkdir(parents=True)
    _write_firefox_store(storage / "webappsstore.sqlite", "deep-token")

    result = await get_token_from_firefox_profile(str(tmp_path / "root"))
    assert result == "deep-token"


async def test_get_token_from_firefox_profile_multiple_storage_first_match_wins(
    tmp_path,
):
    """Multiple ``storage`` dirs: search stops at the first DB carrying a token.

    Two real storage dirs each hold a real sqlite DB; only one has the session
    row. The real reader returns that token and short-circuits the walk.
    """
    s1 = tmp_path / "storage1"
    s2 = tmp_path / "storage2"
    s1.mkdir()
    s2.mkdir()
    _write_firefox_store(s1 / "webappsstore.sqlite", "tokenA")
    _write_firefox_store(s2 / "webappsstore.sqlite", None)

    result = await get_token_from_firefox_profile(str(tmp_path))
    assert result == "tokenA"


async def test_get_token_from_firefox_profile_no_sqlite_in_storage(tmp_path):
    """``storage`` exists but holds no ``.sqlite`` files → None (no read attempted)."""
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "other.txt").write_text("x")
    (storage / "data.json").write_text("{}")

    result = await get_token_from_firefox_profile(str(tmp_path))
    assert result is None


async def test_get_token_from_firefox_profile_storage_db_without_session(tmp_path):
    """``storage`` sqlite exists but lacks the session row → None.

    Exercises the real reader returning None (no ``session_active_session``
    key) so the profile loop falls through to its final ``return None``.
    """
    storage = tmp_path / "storage"
    storage.mkdir()
    _write_firefox_store(storage / "webappsstore.sqlite", None)

    result = await get_token_from_firefox_profile(str(tmp_path))
    assert result is None


@patch("sqlite3.connect")
async def test_get_token_from_firefox_db_with_token(mock_connect):
    """Test extracting token from Firefox SQLite database when token exists."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.side_effect = [
        [("table1",)],  # Table names
        [
            (
                "session_active_session",
                None,
                None,
                None,
                None,
                b'{"token":"test-token"}',
            )
        ],  # Row data with key in first column
    ]
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn

    result = await get_token_from_firefox_db("test.sqlite")
    assert result == "test-token"


@patch("sqlite3.connect")
async def test_get_token_from_firefox_db_no_token(mock_connect):
    """Test extracting token from Firefox SQLite database when no token exists."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.side_effect = [
        [("table1",)],  # Table names
        [(b"other_key", None, None, None, None, b'{"data":"test"}')],  # Row data
    ]
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn

    result = await get_token_from_firefox_db("test.sqlite")
    assert result is None


@patch("sqlite3.connect")
async def test_get_token_from_firefox_db_utf8_decode_error(mock_connect):
    """Test handling UTF-8 decode errors in Firefox SQLite database."""
    mock_connect.side_effect = sqlite3.OperationalError(
        "Could not decode to UTF-8 column 'value' with text"
    )

    result = await get_token_from_firefox_db("test.sqlite")
    assert result is None


@patch("sqlite3.connect")
async def test_get_token_from_firefox_db_locked_non_interactive(mock_connect):
    """Test handling locked database in non-interactive mode."""
    mock_connect.side_effect = sqlite3.Error("database is locked")

    result = await get_token_from_firefox_db("firefox.sqlite", interactive=False)
    assert result is None


@patch("sqlite3.connect")
@patch("config.browser.await_for_enter", new_callable=AsyncMock)
@patch("config.browser.close_browser_by_name")
async def test_get_token_from_firefox_db_locked_interactive(
    mock_close_browser, mock_await_enter, mock_connect
):
    """Test handling locked database in interactive mode."""
    mock_connect.side_effect = [
        sqlite3.Error("database is locked"),  # First attempt fails
        MagicMock(  # Second attempt succeeds
            __enter__=MagicMock(
                return_value=MagicMock(
                    cursor=MagicMock(
                        return_value=MagicMock(
                            fetchall=MagicMock(
                                side_effect=[
                                    [("table1",)],
                                    [
                                        (
                                            "session_active_session",
                                            None,
                                            None,
                                            None,
                                            None,
                                            b'{"token":"test-token"}',
                                        )
                                    ],
                                ]
                            )
                        )
                    )
                )
            )
        ),
    ]

    result = await get_token_from_firefox_db("firefox.sqlite", interactive=True)

    assert result == "test-token"
    mock_close_browser.assert_called_once_with("firefox")
    mock_await_enter.assert_called_once()


@patch("sqlite3.connect")
@patch("config.browser.textio_logger")
@patch("traceback.format_exc", return_value="Traceback: some other error")
async def test_get_token_from_firefox_db_other_sqlite_error(
    mock_traceback,
    mock_logger,
    mock_connect,
):
    """Test handling other SQLite errors."""
    mock_connect.side_effect = sqlite3.Error("some other error")

    result = await get_token_from_firefox_db("test.sqlite")

    assert result is None
    mock_logger.error.assert_called_once_with(
        "Unexpected Error processing SQLite file:\nTraceback: some other error"
    )


@patch("sqlite3.connect")
async def test_get_token_from_firefox_db_generic_exception(mock_connect):
    """Test handling generic exceptions."""
    mock_connect.side_effect = Exception("unexpected error")

    result = await get_token_from_firefox_db("test.sqlite")
    assert result is None


@patch("platform.system")
@patch("psutil.process_iter")
def test_close_browser_by_name_windows(
    mock_process_iter, mock_platform, monkeypatch, scaled_sync_sleep_recording
):
    """Test closing browser on Windows."""
    mock_platform.return_value = "Windows"
    mock_process = MagicMock()
    mock_process.info = {"name": "msedge.exe"}
    mock_process_iter.return_value = [mock_process]

    monkeypatch.setattr("config.browser.sleep", scaled_sync_sleep_recording)
    close_browser_by_name("Microsoft Edge")

    mock_process.terminate.assert_called_once()
    assert scaled_sync_sleep_recording.calls == [3]


@patch("platform.system")
@patch("psutil.process_iter")
def test_close_browser_by_name_unix(
    mock_process_iter, mock_platform, monkeypatch, scaled_sync_sleep_recording
):
    """Test closing browser on Unix-like systems."""
    mock_platform.return_value = "Darwin"
    mock_process = MagicMock()
    mock_process.info = {"name": "firefox"}
    mock_process_iter.return_value = [mock_process]

    monkeypatch.setattr("config.browser.sleep", scaled_sync_sleep_recording)
    close_browser_by_name("Firefox")

    mock_process.kill.assert_called_once()
    assert scaled_sync_sleep_recording.calls == [3]


@patch("platform.system")
@patch("psutil.process_iter")
def test_close_browser_by_name_opera_gx(
    mock_process_iter, mock_platform, monkeypatch, scaled_sync_sleep_recording
):
    """Test closing Opera GX browser (special case where process name differs)."""
    mock_platform.return_value = "Windows"
    mock_process = MagicMock()
    mock_process.info = {"name": "opera"}
    mock_process_iter.return_value = [mock_process]

    # Production code expects "Opera Gx" with lowercase x
    monkeypatch.setattr("config.browser.sleep", scaled_sync_sleep_recording)
    close_browser_by_name("Opera Gx")

    mock_process.terminate.assert_called_once()
    assert scaled_sync_sleep_recording.calls == [3]


@patch("platform.system")
@patch("psutil.process_iter")
def test_close_browser_by_name_no_process(
    mock_process_iter, mock_platform, monkeypatch, scaled_sync_sleep_recording
):
    """Test when no matching browser process is found."""
    mock_platform.return_value = "Windows"
    mock_process_iter.return_value = []  # No processes found

    monkeypatch.setattr("config.browser.sleep", scaled_sync_sleep_recording)
    close_browser_by_name("Firefox")

    assert scaled_sync_sleep_recording.calls == []


@pytest.mark.skipif(not HAS_PLYVEL, reason="plyvel not installed")
@patch("plyvel.DB")
async def test_get_auth_token_from_leveldb_success(mock_db_class):
    """Test successfully getting auth token from LevelDB.

    Production uses ``with plyvel.DB(...) as db:`` — wire __enter__ to
    return the same mock so the as-bound name resolves to ``mock_db``.
    Without this, MagicMock's default __enter__ returns a child mock and
    the ``with`` block sees a different object than the test configured.
    """
    mock_db = MagicMock()
    mock_db_class.return_value = mock_db
    mock_db.__enter__.return_value = mock_db
    mock_db.get.return_value = b'{"token":"test-token"}'

    result = await get_auth_token_from_leveldb_folder("test/path")

    assert result == "test-token"


@pytest.mark.skipif(not HAS_PLYVEL, reason="plyvel not installed")
@patch("plyvel.DB")
async def test_get_auth_token_from_leveldb_no_token(mock_db_class):
    """Test when no token is found in LevelDB."""
    mock_db = MagicMock()
    mock_db_class.return_value = mock_db
    mock_db.__enter__.return_value = mock_db
    mock_db.get.return_value = None

    result = await get_auth_token_from_leveldb_folder("test/path")

    assert result is None


@pytest.mark.skipif(not HAS_PLYVEL, reason="plyvel not installed")
@patch("plyvel.DB")
async def test_get_auth_token_from_leveldb_browser_locked(mock_db_class):
    """Test handling browser lock error in LevelDB access."""
    mock_db_class.side_effect = plyvel._plyvel.IOError(
        "Resource temporarily unavailable"
    )

    result = await get_auth_token_from_leveldb_folder("test/path", interactive=False)

    assert result is None


@pytest.mark.skipif(not HAS_PLYVEL, reason="plyvel not installed")
@patch("plyvel.DB")
@patch("config.browser.await_for_enter", new_callable=AsyncMock)
@patch("config.browser.close_browser_by_name")
async def test_get_auth_token_from_leveldb_interactive_browser_locked(
    mock_close_browser, mock_await_enter, mock_db_class
):
    """Test interactive handling of browser lock error in LevelDB access."""
    # Second-attempt mock supports the ``with`` context manager: __enter__
    # returns the same mock so ``db.get(...)`` hits the configured bytes.
    second_db = MagicMock()
    second_db.__enter__.return_value = second_db
    second_db.get.return_value = b'{"token":"test-token"}'
    mock_db_class.side_effect = [
        plyvel._plyvel.IOError(
            "Resource temporarily unavailable"
        ),  # First attempt fails
        second_db,  # Second attempt succeeds
    ]

    result = await get_auth_token_from_leveldb_folder("test/path", interactive=True)

    assert result == "test-token"
    mock_close_browser.assert_called_once()
    mock_await_enter.assert_awaited_once()


@pytest.mark.skipif(not HAS_PLYVEL, reason="plyvel not installed")
@patch("plyvel.DB")
async def test_get_auth_token_from_leveldb_generic_exception(mock_db_class):
    """Generic exception during LevelDB access returns None (lines 338-339)."""
    mock_db_class.side_effect = RuntimeError("unexpected db error")

    result = await get_auth_token_from_leveldb_folder("test/path")

    assert result is None


@patch("platform.system")
@patch("os.getenv")
def test_get_browser_config_paths_windows_no_env(mock_getenv, mock_platform):
    """Test Windows browser paths with missing environment variables."""
    mock_platform.return_value = "Windows"
    mock_getenv.return_value = None

    with pytest.raises(RuntimeError):
        get_browser_config_paths()
