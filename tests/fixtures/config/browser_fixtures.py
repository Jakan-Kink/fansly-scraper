"""Real SQLite stores and edge stand-ins for ``config.browser`` tests.

Firefox token extraction reads ``webappsstore.sqlite``; these helpers build
real store files in ``tmp_path``, hold real SQLite locks the way a running
Firefox does, and stand in for ``psutil`` process entries.
"""

from __future__ import annotations

import json
import sqlite3
import webbrowser
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil
import pytest


try:
    import plyvel
except ImportError:  # browser-auth group not installed; LevelDB tests skip
    plyvel = None  # type: ignore[assignment]  # optional third-party module


def firefox_session_row(token: str) -> tuple[object, ...]:
    """Row carrying a Fansly ``session_active_session`` entry for ``token``."""
    value = json.dumps({"token": token}).encode("utf-8")
    return ("session_active_session", None, None, None, None, value)


def write_firefox_store(sqlite_path: Path, *rows: tuple[object, ...]) -> Path:
    """Create a Firefox-style ``webappsstore.sqlite`` holding ``rows``.

    The reader scans every table; column 0 is the storage key and column 5
    the JSON value as UTF-8 bytes.
    """
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(sqlite_path)) as conn:
        conn.execute(
            "CREATE TABLE webappsstore2 "
            "(scope TEXT, c1 TEXT, c2 TEXT, c3 TEXT, c4 TEXT, value BLOB)"
        )
        conn.executemany("INSERT INTO webappsstore2 VALUES (?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
    return sqlite_path


def write_non_utf8_store(sqlite_path: Path) -> Path:
    """Create a store whose TEXT column holds invalid UTF-8 (non-Firefox cookie DBs)."""
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(sqlite_path)) as conn:
        conn.execute("CREATE TABLE cookies (name TEXT, value TEXT)")
        conn.execute("INSERT INTO cookies VALUES ('k', CAST(x'fffe' AS TEXT))")
        conn.commit()
    return sqlite_path


LEVELDB_SESSION_KEY = b"_https://fansly.com\x00\x01session_active_session"


def leveldb_session_value(token: str) -> bytes:
    """Chromium Local Storage value carrying a Fansly session for ``token``."""
    return b"\x01" + json.dumps({"token": token}).encode("utf-8")


def write_leveldb_store(folder: Path, value: bytes | None) -> Path:
    """Create a Chromium-style Local Storage LevelDB; ``None`` writes no session."""
    folder.mkdir(parents=True, exist_ok=True)
    with plyvel.DB(str(folder), create_if_missing=True) as db:
        if value is not None:
            db.put(LEVELDB_SESSION_KEY, value)
    return folder


@pytest.fixture
def opened_urls(
    monkeypatch: pytest.MonkeyPatch, scaled_sync_sleep_recording: Any
) -> list[str]:
    """URLs passed to ``webbrowser.open``; ``open_url``'s 10 s pause is scaled down."""
    urls: list[str] = []

    def open_url(url: str, *_args: object, **_kwargs: object) -> bool:
        urls.append(url)
        return True

    monkeypatch.setattr(webbrowser, "open", open_url)
    monkeypatch.setattr("helpers.browser.sleep", scaled_sync_sleep_recording)
    return urls


@contextmanager
def hold_exclusive_lock(sqlite_path: Path) -> Iterator[Callable[[], None]]:
    """Hold an EXCLUSIVE lock on ``sqlite_path`` like a running Firefox.

    Yields a ``release`` callable; the lock is also released on exit.
    """
    conn = sqlite3.connect(sqlite_path)
    conn.execute("BEGIN EXCLUSIVE")
    released = False

    def release() -> None:
        nonlocal released
        if not released:
            conn.rollback()
            released = True

    try:
        yield release
    finally:
        release()
        conn.close()


class FakeBrowserProcess:
    """``psutil.process_iter`` entry; ``kill``/``terminate`` run ``on_exit``.

    ``on_exit`` models the browser releasing its files when it exits.
    """

    def __init__(self, name: str, on_exit: Callable[[], None] | None = None) -> None:
        self.info = {"name": name}
        self.killed = False
        self.terminated = False
        self._on_exit = on_exit

    def _exit(self) -> None:
        if self._on_exit is not None:
            self._on_exit()

    def kill(self) -> None:
        self.killed = True
        self._exit()

    def terminate(self) -> None:
        self.terminated = True
        self._exit()


@pytest.fixture
def browser_processes(monkeypatch: pytest.MonkeyPatch) -> list[FakeBrowserProcess]:
    """Process table served by ``psutil.process_iter``; tests append entries."""
    procs: list[FakeBrowserProcess] = []

    def process_iter(**_kwargs: object) -> list[FakeBrowserProcess]:
        return list(procs)

    monkeypatch.setattr(psutil, "process_iter", process_iter)
    return procs


@pytest.fixture
def linux_browser_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Report Linux and point ``HOME`` at an empty dir; returns that home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setenv("HOME", str(home))
    return home


@dataclass
class SqliteConnectSpy:
    """Connections opened through ``sqlite3.connect`` during a test."""

    opened: list[sqlite3.Connection] = field(default_factory=list)

    def all_closed(self) -> bool:
        """True when every recorded connection has been closed."""
        for conn in self.opened:
            try:
                conn.execute("SELECT 1")
            except sqlite3.ProgrammingError:
                continue
            return False
        return True


@pytest.fixture
def sqlite_connect_spy(monkeypatch: pytest.MonkeyPatch) -> SqliteConnectSpy:
    """Record every ``sqlite3.connect`` and scale the 5 s busy timeout to 50 ms.

    The real connect and the real lock wait still run; only the wait is shorter.
    """
    spy = SqliteConnectSpy()
    real_connect = sqlite3.connect

    def connect(database: str | Path, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        kwargs.setdefault("timeout", 0.05)
        conn = real_connect(database, *args, **kwargs)
        spy.opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    return spy
