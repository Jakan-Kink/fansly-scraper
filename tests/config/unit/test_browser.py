"""Unit tests for browser configuration utilities."""

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
from tests.fixtures.config import (
    FakeBrowserProcess,
    firefox_session_row,
    hold_exclusive_lock,
    leveldb_session_value,
    write_firefox_store,
    write_leveldb_store,
    write_non_utf8_store,
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


def test_get_browser_config_paths_windows(monkeypatch):
    """Windows paths are built from the real APPDATA / LOCALAPPDATA env vars."""
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", "C:\\Users\\test\\AppData\\Roaming")
    monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\test\\AppData\\Local")

    paths = get_browser_config_paths()

    assert len(paths) == 6
    assert any("Chrome" in path for path in paths)
    assert any("Firefox" in path for path in paths)
    assert any("Edge" in path for path in paths)
    assert any("Opera" in path for path in paths)
    assert any("Opera GX" in path for path in paths)
    assert any("Brave" in path for path in paths)


def test_get_browser_config_paths_windows_no_env(monkeypatch):
    """Missing AppData env vars on Windows raise."""
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    with pytest.raises(RuntimeError):
        get_browser_config_paths()


def test_unsupported_os_has_no_browser_paths_and_closes_nothing(
    browser_processes, monkeypatch, scaled_sync_sleep_recording
):
    """An OS outside Windows/macOS/Linux yields no paths and never kills processes."""
    monkeypatch.setattr("platform.system", lambda: "FreeBSD")
    monkeypatch.setattr("config.browser.sleep", scaled_sync_sleep_recording)
    firefox = FakeBrowserProcess("firefox")
    browser_processes.append(firefox)

    assert get_browser_config_paths() == []
    close_browser_by_name("Firefox")

    assert not firefox.killed
    assert not firefox.terminated
    assert scaled_sync_sleep_recording.calls == []


async def test_get_auth_token_from_leveldb_without_plyvel_raises(monkeypatch):
    """Without plyvel-ci the LevelDB reader refuses with an install hint."""
    monkeypatch.setattr("config.browser.PLYVEL_AVAILABLE", False)

    with pytest.raises(ImportError, match="poetry install --with browser-auth"):
        await get_auth_token_from_leveldb_folder("unused")


def test_get_browser_config_paths_macos(monkeypatch, tmp_path):
    """macOS paths hang off the real ``Path.home()`` (HOME)."""
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setenv("HOME", str(tmp_path))

    paths = get_browser_config_paths()

    assert len(paths) == 6
    assert all(path.startswith(str(tmp_path / "Library")) for path in paths)
    assert any("Chrome" in path for path in paths)
    assert any("Firefox" in path for path in paths)
    assert any("Microsoft Edge" in path for path in paths)
    assert any("Opera" in path for path in paths)
    assert any("OperaGX" in path for path in paths)
    assert any("BraveSoftware" in path for path in paths)


def test_get_browser_config_paths_linux(monkeypatch, tmp_path):
    """Linux paths hang off the real ``Path.home()`` (HOME)."""
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setenv("HOME", str(tmp_path))

    paths = get_browser_config_paths()

    assert len(paths) == 5
    assert all(path.startswith(str(tmp_path)) for path in paths)
    assert any("chrome" in path for path in paths)
    assert any("snap/firefox" in path for path in paths)
    assert any("opera" in path for path in paths)
    assert any("Brave" in path for path in paths)


async def test_get_token_from_firefox_profile_walks_storage_dirs(
    tmp_path, sqlite_connect_spy
):
    """Real ``os.walk`` over real profiles; only ``storage`` dirs are read.

    Covers: no ``storage`` dir, ``storage`` without ``.sqlite`` files, a DB
    without the session row, a deeply nested DB, and first-match-wins across
    two storage dirs. Every connection the reader opens is closed.
    """
    no_storage = tmp_path / "no_storage"
    (no_storage / "other").mkdir(parents=True)
    (no_storage / "other" / "file.txt").write_text("not a db")
    assert await get_token_from_firefox_profile(str(no_storage)) is None

    no_sqlite = tmp_path / "no_sqlite"
    (no_sqlite / "storage").mkdir(parents=True)
    (no_sqlite / "storage" / "data.json").write_text("{}")
    assert await get_token_from_firefox_profile(str(no_sqlite)) is None

    no_session = tmp_path / "no_session"
    write_firefox_store(
        no_session / "storage" / "webappsstore.sqlite",
        ("unrelated_key", None, None, None, None, b'{"data":"x"}'),
    )
    assert await get_token_from_firefox_profile(str(no_session)) is None

    nested = tmp_path / "nested"
    write_firefox_store(
        nested / "folder1" / "storage" / "subdir" / "webappsstore.sqlite",
        firefox_session_row("deep-token"),
    )
    assert await get_token_from_firefox_profile(str(nested)) == "deep-token"

    first_match = tmp_path / "first_match"
    write_firefox_store(
        first_match / "storage1" / "webappsstore.sqlite",
        firefox_session_row("tokenA"),
    )
    write_firefox_store(
        first_match / "storage2" / "webappsstore.sqlite",
        ("unrelated_key", None, None, None, None, b"{}"),
    )
    assert await get_token_from_firefox_profile(str(first_match)) == "tokenA"

    assert len(sqlite_connect_spy.opened) >= 3
    assert sqlite_connect_spy.all_closed()


@pytest.mark.parametrize(
    ("build_db", "expected", "error_log"),
    [
        pytest.param(
            lambda p: write_firefox_store(p, firefox_session_row("test-token")),
            "test-token",
            None,
            id="with_token",
        ),
        pytest.param(
            lambda p: write_firefox_store(
                p, (b"other_key", None, None, None, None, b'{"data":"test"}')
            ),
            None,
            None,
            id="no_token",
        ),
        pytest.param(write_non_utf8_store, None, None, id="non_utf8_operational_error"),
        pytest.param(
            lambda p: p.write_bytes(b"not a database at all " * 50),
            None,
            "Unexpected Error processing SQLite file",
            id="not_a_database",
        ),
        pytest.param(
            lambda p: write_firefox_store(
                p, ("session_active_session", None, None, None, None, b"{not json")
            ),
            None,
            "Unexpected Error parsing Firefox SQLite databases",
            id="malformed_session_json",
        ),
    ],
)
async def test_get_token_from_firefox_db_outcomes(
    tmp_path, sqlite_connect_spy, caplog, build_db, expected, error_log
):
    """Real SQLite files drive every reader outcome; the connection always closes.

    ``not_a_database`` raises a real ``sqlite3.DatabaseError`` (logged);
    ``non_utf8_operational_error`` raises a real ``OperationalError`` (skipped
    silently); ``malformed_session_json`` hits the generic ``except``.
    """
    db_path = tmp_path / "webappsstore.sqlite"
    build_db(db_path)
    sqlite_connect_spy.opened.clear()

    result = await get_token_from_firefox_db(str(db_path), interactive=False)

    assert result == expected
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    if error_log is None:
        assert errors == []
    else:
        assert len(errors) == 1
        assert error_log in errors[0]
    assert len(sqlite_connect_spy.opened) == 1
    assert sqlite_connect_spy.all_closed()


async def test_firefox_locked_non_interactive_skips_without_prompt(
    tmp_path, sqlite_connect_spy, fake_prompt_session, browser_processes
):
    """A real lock held by a running Firefox is skipped in non-interactive mode.

    No prompt, no browser kill; the profile walker forwards ``interactive``.
    """
    profile = tmp_path / "firefox" / "profile"
    db_path = write_firefox_store(
        profile / "storage" / "default" / "webappsstore.sqlite",
        firefox_session_row("locked-token"),
    )
    firefox = FakeBrowserProcess("firefox")
    browser_processes.append(firefox)

    with hold_exclusive_lock(db_path):
        sqlite_connect_spy.opened.clear()
        result = await get_token_from_firefox_profile(str(profile), interactive=False)

    assert result is None
    assert len(sqlite_connect_spy.opened) == 1
    assert not firefox.killed
    assert not firefox.terminated
    assert fake_prompt_session == []
    assert sqlite_connect_spy.all_closed()


async def test_firefox_locked_interactive_prompts_closes_and_retries(
    tmp_path,
    sqlite_connect_spy,
    tty,
    fake_prompt_session,
    browser_processes,
    monkeypatch,
    scaled_sync_sleep_recording,
    caplog,
):
    """A real ``database is locked`` reaches the prompt-close-retry flow.

    The lock surfaces as ``sqlite3.OperationalError``; the reader must route
    it to the Firefox-locked branch instead of the non-UTF-8 skip. Closing the
    browser releases the lock, so the retry reads the token.
    """
    profile = tmp_path / "firefox" / "profile"
    db_path = write_firefox_store(
        profile / "storage" / "default" / "webappsstore.sqlite",
        firefox_session_row("unlocked-token"),
    )
    fake_prompt_session.append("")
    monkeypatch.setattr("config.browser.sleep", scaled_sync_sleep_recording)

    with hold_exclusive_lock(db_path) as release:
        firefox = FakeBrowserProcess("firefox", on_exit=release)
        browser_processes.append(firefox)
        sqlite_connect_spy.opened.clear()

        result = await get_token_from_firefox_profile(str(profile), interactive=True)

    assert result == "unlocked-token"
    assert firefox.killed or firefox.terminated
    assert fake_prompt_session == []
    assert scaled_sync_sleep_recording.calls == [3]
    messages = [r.getMessage() for r in caplog.records]
    assert any("Firefox browser is open" in m for m in messages)
    assert len(sqlite_connect_spy.opened) == 2
    assert sqlite_connect_spy.all_closed()


@pytest.mark.parametrize(
    ("system", "process_name", "browser_arg", "expect_terminate"),
    [
        pytest.param("Windows", "msedge.exe", "Microsoft Edge", True, id="windows"),
        pytest.param("Darwin", "firefox", "Firefox", False, id="unix"),
        pytest.param("Windows", "opera", "Opera Gx", True, id="opera_gx"),
    ],
)
def test_close_browser_by_name(
    browser_processes,
    monkeypatch,
    scaled_sync_sleep_recording,
    system,
    process_name,
    browser_arg,
    expect_terminate,
):
    """Matching processes are terminated (Windows) or killed (Unix), then a 3 s settle."""
    proc = FakeBrowserProcess(process_name)
    browser_processes.append(proc)
    monkeypatch.setattr("platform.system", lambda: system)
    monkeypatch.setattr("config.browser.sleep", scaled_sync_sleep_recording)

    close_browser_by_name(browser_arg)

    assert proc.terminated is expect_terminate
    assert proc.killed is not expect_terminate
    assert scaled_sync_sleep_recording.calls == [3]


def test_close_browser_by_name_no_process(
    browser_processes, monkeypatch, scaled_sync_sleep_recording
):
    """No matching browser process → nothing closed, no settle sleep."""
    unrelated = FakeBrowserProcess("python")
    browser_processes.append(unrelated)
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("config.browser.sleep", scaled_sync_sleep_recording)

    close_browser_by_name("Firefox")

    assert not unrelated.terminated
    assert scaled_sync_sleep_recording.calls == []


@pytest.mark.skipif(not HAS_PLYVEL, reason="plyvel not installed")
async def test_get_auth_token_from_leveldb_outcomes(tmp_path, browser_processes):
    """Real LevelDB databases drive every reader outcome.

    Covers: token present, token absent, malformed session JSON (generic
    ``except``), and a real ``LOCK`` held by a running browser in
    non-interactive mode (no prompt, no kill).
    """
    root = tmp_path / "Google" / "Chrome" / "Default" / "Local Storage"
    folders = {
        name: str(write_leveldb_store(root / name / "leveldb", value))
        for name, value in (
            ("with_token", leveldb_session_value("test-token")),
            ("no_token", None),
            ("malformed", b"\x01{not json"),
        )
    }

    assert (
        await get_auth_token_from_leveldb_folder(folders["with_token"]) == "test-token"
    )
    assert await get_auth_token_from_leveldb_folder(folders["no_token"]) is None
    assert await get_auth_token_from_leveldb_folder(folders["malformed"]) is None

    chrome = FakeBrowserProcess("chrome")
    browser_processes.append(chrome)
    holder = plyvel.DB(folders["with_token"])
    try:
        result = await get_auth_token_from_leveldb_folder(
            folders["with_token"], interactive=False
        )
    finally:
        holder.close()

    assert result is None
    assert not chrome.killed
    assert not chrome.terminated


@pytest.mark.skipif(not HAS_PLYVEL, reason="plyvel not installed")
async def test_get_auth_token_from_leveldb_interactive_browser_locked(
    tmp_path,
    tty,
    fake_prompt_session,
    browser_processes,
    monkeypatch,
    scaled_sync_sleep_recording,
):
    """A real LevelDB ``LOCK`` prompts, closes the browser, and the retry reads the token."""
    folder = write_leveldb_store(
        tmp_path / "Google" / "Chrome" / "Default" / "Local Storage" / "leveldb",
        leveldb_session_value("t"),
    )
    holder = plyvel.DB(str(folder))

    chrome = FakeBrowserProcess("chrome", on_exit=holder.close)
    browser_processes.append(chrome)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("config.browser.sleep", scaled_sync_sleep_recording)
    fake_prompt_session.append("")

    try:
        result = await get_auth_token_from_leveldb_folder(str(folder), interactive=True)
    finally:
        if not holder.closed:
            holder.close()

    assert result == "t"
    assert chrome.killed
    assert fake_prompt_session == []
    assert scaled_sync_sleep_recording.calls == [3]
