"""Unit tests for fansly_downloader_ng module."""

import asyncio
import atexit
import logging
import time
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from config import FanslyConfig
from download.downloadstate import DownloadState
from errors import (
    API_ERROR,
    CONFIG_ERROR,
    DOWNLOAD_ERROR,
    EXIT_ABORT,
    EXIT_SUCCESS,
    UNEXPECTED_ERROR,
    ApiAccountInfoError,
    ApiError,
    ConfigError,
    DownloadError,
)
from fansly_downloader_ng import (
    _async_main,
    _check_stash_library_version,
    _handle_interrupt,
    _safe_cleanup_database,
    cleanup_database,
    cleanup_database_sync,
    cleanup_with_global_timeout,
    increase_file_descriptor_limit,
    load_client_account_into_db,
    print_logo,
)
from tests.fixtures.api import dump_fansly_calls
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.fixture(autouse=True)
def _clear_handle_interrupt_flag():
    """Ensure ``_handle_interrupt.interrupted`` is not set at test start.

    The real ``_handle_interrupt`` function sets this attribute as a side
    effect (fansly_downloader_ng.py:211). If a prior test triggered it —
    directly or via a KeyboardInterrupt path — the attribute persists at
    module level, short-circuiting tests that expect it to be absent.
    This autouse fixture clears it before every test as a safety net.
    """
    if hasattr(_handle_interrupt, "interrupted"):
        del _handle_interrupt.interrupted
    yield
    if hasattr(_handle_interrupt, "interrupted"):
        del _handle_interrupt.interrupted


def test_cleanup_database_sync_calls_close_sync(config_with_database):
    """cleanup_database_sync runs real close_sync and sets the cleanup flag.

    Uses real FanslyConfig + PostgresEntityStore-backed Database (via
    config_with_database fixture). Previous version of this test stubbed out
    the function under test and asserted the stub was called — tested nothing.
    """
    config = config_with_database
    # Sanity: the real database exists and is not yet cleaned up.
    assert config._database is not None
    assert not config._database._cleanup_done.is_set()

    cleanup_database_sync(config)

    # Real behavior: _cleanup_done becomes set after close_sync().
    assert config._database._cleanup_done.is_set()


def test_cleanup_database_sync_idempotent(config_with_database, caplog):
    """Second call to cleanup_database_sync logs the skip path and is a no-op."""
    caplog.set_level(logging.INFO)
    config = config_with_database

    cleanup_database_sync(config)  # First call — performs cleanup.
    caplog.clear()

    cleanup_database_sync(config)  # Second call — should short-circuit.

    skip_messages = [
        r.getMessage()
        for r in caplog.records
        if "already performed" in r.getMessage().lower()
    ]
    assert len(skip_messages) == 1, (
        f"Expected one 'already performed' info log, got: {skip_messages}"
    )
    # Still idempotent after multiple calls.
    assert config._database._cleanup_done.is_set()


def test_print_logo(capsys):
    """Test print_logo function outputs correctly."""
    print_logo()
    captured = capsys.readouterr()
    # The logo is ASCII art, so we check for key parts
    assert "███████╗" in captured.out  # Part of the F
    assert "github.com/prof79/fansly-downloader-ng" in captured.out


@pytest.mark.asyncio
async def test_cleanup_database_success(config_with_database, caplog):
    """cleanup_database closes real DB connections and logs the expected path.

    Uses real FanslyConfig + PostgresEntityStore. Verifies that async
    cleanup runs, logs the "Closing database connections..." info message,
    and leaves the Database in a cleaned-up state.
    """
    caplog.set_level(logging.INFO)
    config = config_with_database
    assert config._database is not None
    assert not config._database._cleanup_done.is_set()

    await cleanup_database(config)

    # Real cleanup runs, flag is set.
    assert config._database._cleanup_done.is_set()
    # Info log path was taken (not the "no database" branch).
    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("Closing database connections" in m for m in info_messages), (
        f"Expected 'Closing database connections' info log, got: {info_messages}"
    )
    assert not any("No database to clean up" in m for m in info_messages), (
        "Should not hit the 'no database' branch when a real database exists"
    )


@pytest.mark.asyncio
async def test_cleanup_database_error(config_with_database, caplog, monkeypatch):
    """cleanup_database logs (but does not raise) when the pool cleanup fails.

    Exercises the real except branch in cleanup_database by making
    _safe_cleanup_database raise. The test verifies error-log emission via
    caplog; it does NOT patch print_error (the previous version did, which
    tested the mock, not the real log path).
    """
    caplog.set_level(logging.ERROR)
    config = config_with_database

    async def _raise_boom(_config):
        raise RuntimeError("boom: pool already closed")

    # _safe_cleanup_database is the private helper called inside
    # cleanup_database's try-block; making it raise exercises the except
    # branch on the real cleanup_database path.
    monkeypatch.setattr("fansly_downloader_ng._safe_cleanup_database", _raise_boom)

    # Must not raise — cleanup_database catches and logs.
    await cleanup_database(config)

    error_messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "Error during database cleanup" in m and "boom: pool already closed" in m
        for m in error_messages
    ), f"Expected error log with RuntimeError message, got: {error_messages}"


def test_cleanup_database_no_database():
    """cleanup_database_sync is a no-op when config._database is None.

    Uses a real FanslyConfig with no _database attached — the function
    must gracefully short-circuit without raising.
    """
    config = FanslyConfig(program_version="0.13.0")
    # Explicit: there is no database on a freshly-constructed config.
    assert getattr(config, "_database", None) is None

    cleanup_database_sync(config)  # Must not raise.


# _async_main tests — `main` is patched as the seam for testing the wrapper's
# own exit-code-mapping and cleanup logic; end-to-end coverage of main() itself
# lives in tests/functional/ (Wave 2.2).


def _clear_atexit_cleanup_handlers() -> None:
    """Remove any pre-registered cleanup_database_sync entries from atexit.

    _async_main's atexit-registration check walks `atexit._exithandlers` and
    skips registration if a handler with name `cleanup_database_sync` is
    already there. Repeated test runs can leave stale entries from prior
    tests; clearing them ensures each test observes a fresh state.
    """
    if not hasattr(atexit, "_exithandlers"):
        return
    atexit._exithandlers = [
        h
        for h in atexit._exithandlers
        if getattr(h[0], "__name__", None) != "cleanup_database_sync"
    ]


@pytest.mark.asyncio
async def test_async_main_success_returns_exit_code(config_with_database, caplog):
    """_async_main returns main()'s exit code on success and runs cleanup."""
    caplog.set_level(logging.INFO)
    _clear_atexit_cleanup_handlers()
    config = config_with_database
    received_config: list[FanslyConfig] = []

    async def _fake_main(cfg):
        received_config.append(cfg)
        return EXIT_SUCCESS

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("fansly_downloader_ng.main", _fake_main)
        result = await _async_main(config)

    # Exit code propagated from main().
    assert result == EXIT_SUCCESS
    # _async_main passed the config through to main unchanged.
    assert received_config == [config]
    # Real cleanup_with_global_timeout ran (no patching): DB cleanup flag set.
    assert config._database._cleanup_done.is_set()
    # Finally block's log path was taken.
    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("Starting final cleanup process" in m for m in info_messages)
    assert any("Cleanup completed successfully" in m for m in info_messages)


@pytest.mark.parametrize(
    ("raised", "expected_exit_code", "expected_log_substring"),
    [
        (ApiAccountInfoError("api account boom"), API_ERROR, "api account boom"),
        (ApiError("api boom"), API_ERROR, "api boom"),
        (ConfigError("config boom"), CONFIG_ERROR, "config boom"),
        (DownloadError("download boom"), DOWNLOAD_ERROR, "download boom"),
        (
            RuntimeError("unexpected boom"),
            UNEXPECTED_ERROR,
            "unexpected boom",
        ),
    ],
    ids=[
        "api_account_error_subclass_still_maps_to_API_ERROR",
        "api_error",
        "config_error",
        "download_error",
        "unexpected_exception",
    ],
)
@pytest.mark.asyncio
async def test_async_main_exception_to_exit_code_mapping(
    config_with_database, caplog, raised, expected_exit_code, expected_log_substring
):
    """_async_main maps each exception class from main() to the right exit code.

    Also verifies cleanup still runs (finally block) and the exception message
    is logged at ERROR level — no patching of print_error/logger.
    """
    caplog.set_level(logging.ERROR)
    _clear_atexit_cleanup_handlers()
    config = config_with_database

    async def _fake_main(_cfg):
        raise raised

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("fansly_downloader_ng.main", _fake_main)
        result = await _async_main(config)

    assert result == expected_exit_code, (
        f"{type(raised).__name__} should map to exit code {expected_exit_code}, "
        f"got {result}"
    )
    # Finally always runs cleanup — verify via real DB state.
    assert config._database._cleanup_done.is_set()
    # Error message or traceback excerpt is in the captured error logs.
    error_messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(expected_log_substring in m for m in error_messages), (
        f"Expected error log containing {expected_log_substring!r}, "
        f"got: {error_messages}"
    )


@pytest.mark.asyncio
async def test_async_main_keyboard_interrupt_returns_exit_abort(
    config_with_database, caplog
):
    """KeyboardInterrupt from main() maps to EXIT_ABORT; cleanup still runs."""
    caplog.set_level(logging.ERROR)
    _clear_atexit_cleanup_handlers()
    config = config_with_database

    async def _fake_main(_cfg):
        raise KeyboardInterrupt

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("fansly_downloader_ng.main", _fake_main)
        result = await _async_main(config)

    assert result == EXIT_ABORT
    assert config._database._cleanup_done.is_set()
    error_messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("interrupted by user" in m.lower() for m in error_messages), (
        f"Expected 'interrupted by user' error log, got: {error_messages}"
    )


@pytest.mark.asyncio
async def test_async_main_registers_atexit_handler(config_with_database, caplog):
    """_async_main registers cleanup_database_sync via atexit on first run."""
    caplog.set_level(logging.INFO)
    _clear_atexit_cleanup_handlers()
    config = config_with_database

    async def _fake_main(_cfg):
        return EXIT_SUCCESS

    # Capture atexit.register calls so we don't actually leak handlers into
    # the test process's real atexit chain.
    registered: list[tuple] = []

    def _capture_register(func, *args, **kwargs):
        registered.append((func, args, kwargs))
        return func

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("fansly_downloader_ng.main", _fake_main)
        mp.setattr("atexit.register", _capture_register)
        result = await _async_main(config)

    assert result == EXIT_SUCCESS
    # cleanup_database_sync should have been registered with the config arg.
    handler_matches = [
        entry
        for entry in registered
        if entry[0] is cleanup_database_sync and entry[1] == (config,)
    ]
    assert len(handler_matches) == 1, (
        f"Expected cleanup_database_sync to be registered exactly once, "
        f"got registered entries: {registered}"
    )
    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("Registered database cleanup exit handler" in m for m in info_messages)


@pytest.mark.asyncio
async def test_async_main_cleanup_runs_even_when_main_raises(
    config_with_database, caplog
):
    """Cleanup in the finally block runs even when main() raises an exception."""
    caplog.set_level(logging.INFO)
    _clear_atexit_cleanup_handlers()
    config = config_with_database
    assert not config._database._cleanup_done.is_set()

    async def _fake_main(_cfg):
        raise RuntimeError("main crashed")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("fansly_downloader_ng.main", _fake_main)
        result = await _async_main(config)

    assert result == UNEXPECTED_ERROR
    # Critical guarantee: cleanup ran despite the exception.
    assert config._database._cleanup_done.is_set()
    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("Starting final cleanup process" in m for m in info_messages)
    assert any("Cleanup completed successfully" in m for m in info_messages)


# _check_stash_library_version tests — covers line 93.


def test_check_stash_library_version_raises_on_too_old(monkeypatch):
    """_check_stash_library_version raises when installed version < 0.12.

    Covers line 93: the RuntimeError with upgrade instructions. Patches
    ``fansly_downloader_ng.pkg_version`` to return a too-old version string;
    the function should split, compare, and raise.
    """
    monkeypatch.setattr("fansly_downloader_ng.pkg_version", lambda _name: "0.11.5")
    with pytest.raises(RuntimeError, match=r"0\.11\.5.*>=0\.12\.0 is required"):
        _check_stash_library_version()


def test_check_stash_library_version_passes_on_new_enough(monkeypatch):
    """_check_stash_library_version is a no-op when installed version is new enough."""
    monkeypatch.setattr("fansly_downloader_ng.pkg_version", lambda _name: "0.14.0")
    # Must not raise.
    _check_stash_library_version()


# _safe_cleanup_database tests — covers lines 109-110, 116-117, 124-139.


async def test_safe_cleanup_database_no_database_branch(caplog):
    """_safe_cleanup_database logs and returns when config has no database.

    Covers lines 108-110: the ``if not hasattr(config, "_database") or
    config._database is None`` early-return branch.
    """
    caplog.set_level(logging.INFO)
    config = FanslyConfig(program_version="0.13.0")
    assert getattr(config, "_database", None) is None

    await _safe_cleanup_database(config)

    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "No database to clean up or database already closed" in m for m in info_messages
    ), f"Expected no-database info log, got: {info_messages}"


async def test_safe_cleanup_database_already_done_branch(config_with_database, caplog):
    """_safe_cleanup_database short-circuits when _cleanup_done is already set.

    Covers lines 112-117: the ``hasattr(..., "_cleanup_done") and
    ..._cleanup_done.is_set()`` short-circuit.
    """
    caplog.set_level(logging.INFO)
    config = config_with_database
    config._database._cleanup_done.set()

    await _safe_cleanup_database(config)

    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "Database cleanup already performed, skipping" in m for m in info_messages
    ), f"Expected skip-log, got: {info_messages}"


async def test_safe_cleanup_database_timeout_falls_back_to_close_sync(
    config_with_database, caplog, monkeypatch
):
    """_safe_cleanup_database falls back to close_sync on cleanup TimeoutError.

    Covers lines 124-129: ``except TimeoutError`` branch in the inner try.
    """
    caplog.set_level(logging.ERROR)
    config = config_with_database

    async def _hang_cleanup():
        raise TimeoutError("simulated cleanup timeout")

    monkeypatch.setattr(config._database, "cleanup", _hang_cleanup)

    await _safe_cleanup_database(config)

    error_messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("Database cleanup timed out" in m for m in error_messages), (
        f"Expected timeout-error log, got: {error_messages}"
    )


async def test_safe_cleanup_database_timeout_forced_cleanup_also_fails(
    config_with_database, caplog, monkeypatch
):
    """_safe_cleanup_database logs both failures when close_sync also raises.

    Covers lines 126-129: the inner try/except around ``close_sync()`` —
    both primary cleanup AND forced fallback fail; both errors log.
    """
    caplog.set_level(logging.ERROR)
    config = config_with_database

    async def _hang_cleanup():
        raise TimeoutError("simulated cleanup timeout")

    def _raise_sync():
        raise RuntimeError("forced close boom")

    monkeypatch.setattr(config._database, "cleanup", _hang_cleanup)
    monkeypatch.setattr(config._database, "close_sync", _raise_sync)

    await _safe_cleanup_database(config)  # Must not raise.

    error_messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "Forced cleanup also failed" in m and "forced close boom" in m
        for m in error_messages
    ), f"Expected 'Forced cleanup also failed' log, got: {error_messages}"


async def test_safe_cleanup_database_generic_exception_falls_back(
    config_with_database, caplog, monkeypatch
):
    """_safe_cleanup_database falls back on non-timeout exceptions.

    Covers lines 130-135: ``except Exception as detail_e`` branch.
    """
    caplog.set_level(logging.ERROR)
    config = config_with_database

    async def _raise_boom():
        raise RuntimeError("detailed-error boom")

    monkeypatch.setattr(config._database, "cleanup", _raise_boom)

    await _safe_cleanup_database(config)

    error_messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("Detailed error during database cleanup" in m for m in error_messages), (
        f"Expected detailed-error log, got: {error_messages}"
    )


async def test_safe_cleanup_database_generic_exception_sync_fallback_fails(
    config_with_database, caplog, monkeypatch
):
    """_safe_cleanup_database logs both when close_sync also raises.

    Covers lines 132-135: inner try/except around ``close_sync()`` in the
    detailed-exception branch.
    """
    caplog.set_level(logging.ERROR)
    config = config_with_database

    async def _raise_boom():
        raise RuntimeError("detailed-error boom")

    def _raise_sync():
        raise RuntimeError("sync close also boom")

    monkeypatch.setattr(config._database, "cleanup", _raise_boom)
    monkeypatch.setattr(config._database, "close_sync", _raise_sync)

    await _safe_cleanup_database(config)  # Must not raise.

    error_messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "Sync cleanup also failed" in m and "sync close also boom" in m
        for m in error_messages
    ), f"Expected 'Sync cleanup also failed' log, got: {error_messages}"


async def test_safe_cleanup_database_outer_exception_is_caught(
    config_with_database, caplog, monkeypatch
):
    """_safe_cleanup_database catches exceptions that escape the inner handlers.

    Covers lines 136-139: the outer ``except Exception as e`` branch. This
    branch only fires when something OUTSIDE the inner try-except-except
    raises — otherwise the inner ``except Exception as detail_e`` catches
    everything. To reach it, we:

    1. Make ``asyncio.wait_for`` raise a RuntimeError → caught by the inner
       ``except Exception as detail_e`` at line 130.
    2. Make ``print_error`` raise a separate RuntimeError when called from
       within that inner handler → escapes to the outer ``except Exception``.
    3. Verify the outer handler logs the escape message and still attempts
       the suppressed ``close_sync()`` call.

    The outer handler uses a ``contextlib.suppress(Exception)`` around
    ``close_sync()``, so no further errors propagate.
    """
    caplog.set_level(logging.ERROR)
    config = config_with_database

    async def _raise_inner(_awaitable, *, timeout):  # noqa: ASYNC109 — mirrors asyncio.wait_for signature
        if hasattr(_awaitable, "close"):
            _awaitable.close()
        raise RuntimeError("inner-close boom")

    import fansly_downloader_ng as fdng

    real_print_error = fdng.print_error
    call_count = {"n": 0}

    def _print_error_raises_from_inner(msg, *args, **kwargs):
        # The FIRST print_error call from _safe_cleanup_database's detail
        # branch is "Detailed error during database cleanup: ..." (line 131).
        # We raise there to escape the inner except handler and hit the
        # outer one. Subsequent print_error calls (including the outer
        # handler's own "Error closing database connections") must
        # succeed so the assertion can see the log.
        call_count["n"] += 1
        if call_count["n"] == 1 and "Detailed error" in msg:
            raise RuntimeError("print-error escape boom")
        return real_print_error(msg, *args, **kwargs)

    monkeypatch.setattr("fansly_downloader_ng.asyncio.wait_for", _raise_inner)
    monkeypatch.setattr(fdng, "print_error", _print_error_raises_from_inner)

    await _safe_cleanup_database(config)  # Must not raise.

    error_messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "Error closing database connections" in m and "print-error escape boom" in m
        for m in error_messages
    ), f"Expected outer-exception log, got: {error_messages}"


# cleanup_database_sync interrupted-skip test — covers line 182.


def test_cleanup_database_sync_skips_when_interrupted_flag_set(
    config_with_database, monkeypatch
):
    """cleanup_database_sync returns early when ``_handle_interrupt.interrupted`` is set.

    Covers lines 180-182: the ``if hasattr(_handle_interrupt, "interrupted")``
    check — designed to avoid deadlocks during signal-interrupted cleanup.
    """
    config = config_with_database
    assert not config._database._cleanup_done.is_set()

    monkeypatch.setattr(_handle_interrupt, "interrupted", True, raising=False)

    cleanup_database_sync(config)

    assert not config._database._cleanup_done.is_set(), (
        "Cleanup must be skipped when interrupted flag is set to avoid deadlocks"
    )


# _handle_interrupt tests — covers lines 205-213.


def test_handle_interrupt_first_call_sets_flag_and_raises_keyboard_interrupt(
    caplog, monkeypatch
):
    """_handle_interrupt first invocation sets flag and raises KeyboardInterrupt.

    Covers lines 205, 211-213: first-interrupt path.

    **State cleanup**: ``_handle_interrupt.interrupted`` is a module-level
    attribute that the real function sets on line 211 as a side effect.
    Tests running after this one on the same xdist worker would see the
    flag stuck at True, short-circuiting other code paths. We explicitly
    delete the attribute in teardown to restore module state.
    """
    caplog.set_level(logging.ERROR)
    if hasattr(_handle_interrupt, "interrupted"):
        monkeypatch.delattr(_handle_interrupt, "interrupted")

    try:
        with pytest.raises(KeyboardInterrupt, match="User interrupted operation"):
            _handle_interrupt(2, None)

        assert getattr(_handle_interrupt, "interrupted", False) is True, (
            "First interrupt must set the interrupted flag"
        )
        error_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "ERROR"
        ]
        assert any("Interrupted by user" in m for m in error_messages)
    finally:
        # Clean up the side effect so the module state is pristine for
        # subsequent tests on this worker.
        if hasattr(_handle_interrupt, "interrupted"):
            del _handle_interrupt.interrupted


def test_handle_interrupt_second_call_forces_sys_exit(caplog, monkeypatch):
    """_handle_interrupt second invocation calls sys.exit(130).

    Covers lines 207-210: second-interrupt path.
    """
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(_handle_interrupt, "interrupted", True, raising=False)

    with pytest.raises(SystemExit) as exc_info:
        _handle_interrupt(2, None)

    assert exc_info.value.code == 130, (
        f"Second interrupt must sys.exit(130), got {exc_info.value.code}"
    )
    error_messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "Second interrupt received, forcing immediate exit" in m for m in error_messages
    )


# cleanup_with_global_timeout tests — covers lines 695-803.


async def test_cleanup_with_global_timeout_closes_websocket(
    config_with_database, caplog
):
    """cleanup_with_global_timeout calls config._api.close_websocket() when _api is set.

    Covers lines 694-698: the WebSocket stop path.
    """
    caplog.set_level(logging.INFO)
    config = config_with_database

    close_called = asyncio.Event()

    class _FakeApi:
        async def close_websocket(self):
            close_called.set()

    config._api = _FakeApi()

    await cleanup_with_global_timeout(config)

    assert close_called.is_set(), "close_websocket() must have been called"
    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("Stopping WebSocket connection" in m for m in info_messages)
    assert any("WebSocket stopped successfully" in m for m in info_messages)


async def test_cleanup_with_global_timeout_websocket_close_error_is_logged(
    config_with_database, caplog
):
    """cleanup_with_global_timeout logs warning when close_websocket raises.

    Covers lines 699-700: inner ``except Exception`` around
    ``await config._api.close_websocket()``.
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    class _FailingApi:
        async def close_websocket(self):
            raise RuntimeError("ws close boom")

    config._api = _FailingApi()

    await cleanup_with_global_timeout(config)  # Must not raise.

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any(
        "Error stopping WebSocket" in m and "ws close boom" in m
        for m in warning_messages
    ), f"Expected WS-close warning, got WARNING: {warning_messages}"


async def test_cleanup_with_global_timeout_websocket_outer_exception(
    config_with_database, caplog
):
    """cleanup_with_global_timeout catches outer exceptions from WS shutdown.

    Covers lines 701-702: outer ``except Exception`` around the entire
    WebSocket block.
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    class _RaisingApi:
        def __getattribute__(self, name):
            if name == "close_websocket":
                raise RuntimeError("ws outer boom")
            return super().__getattribute__(name)

    config._api = _RaisingApi()

    await cleanup_with_global_timeout(config)  # Must not raise.

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any(
        "Error during WebSocket shutdown" in m or "Error stopping WebSocket" in m
        for m in warning_messages
    ), f"Expected WS-shutdown warning, got WARNING: {warning_messages}"


async def test_cleanup_with_global_timeout_cancels_stash_tasks(
    config_with_database, caplog
):
    """cleanup_with_global_timeout cancels Stash-routed tasks.

    Covers lines 707-731: stash task identification (by qualname),
    cancellation, and asyncio.wait() grace window.
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    hang_event = asyncio.Event()

    class StashProcessing:
        async def run(self):
            await hang_event.wait()

    sp = StashProcessing()
    stash_task = asyncio.create_task(sp.run())
    config._background_tasks.append(stash_task)

    try:
        await cleanup_with_global_timeout(config)
    finally:
        hang_event.set()

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any(
        "Found 1 Stash processing tasks to clean up" in m for m in warning_messages
    ), f"Expected stash-found warning, got WARNING: {warning_messages}"
    assert stash_task.cancelled() or stash_task.done(), (
        "Stash task must have been cancelled during cleanup"
    )


async def test_cleanup_with_global_timeout_stash_wait_exception(
    config_with_database, caplog, monkeypatch
):
    """cleanup_with_global_timeout handles exceptions from asyncio.wait on stash tasks.

    Covers lines 732-733: ``except Exception as e`` inside the stash-task
    grace-wait block.
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    hang_event = asyncio.Event()

    class StashProcessing:
        async def run(self):
            await hang_event.wait()

    sp = StashProcessing()
    stash_task = asyncio.create_task(sp.run())
    config._background_tasks.append(stash_task)

    async def _raise_on_wait(*_args, **kwargs):
        if kwargs.get("timeout") is not None:
            raise RuntimeError("stash wait boom")

    monkeypatch.setattr("fansly_downloader_ng.asyncio.wait", _raise_on_wait)

    try:
        await cleanup_with_global_timeout(config)
    finally:
        hang_event.set()
        import contextlib

        with contextlib.suppress(Exception):
            stash_task.cancel()

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any(
        "Error waiting for Stash tasks" in m and "stash wait boom" in m
        for m in warning_messages
    ), f"Expected stash-wait exception warning, got WARNING: {warning_messages}"


async def test_cleanup_with_global_timeout_stash_identification_outer_exception(
    config_with_database, caplog, monkeypatch
):
    """cleanup_with_global_timeout logs when the stash-identification block itself raises.

    Covers lines 734-735: outer ``except Exception as e`` around the
    entire stash-cleanup block. Patches config.get_background_tasks to
    raise on first call (inside stash block), succeed on the second
    (remaining-tasks block).
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    call_count = {"n": 0}
    real_get = config.get_background_tasks

    def _raise_once():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("stash identification outer boom")
        return real_get()

    monkeypatch.setattr(config, "get_background_tasks", _raise_once)

    await cleanup_with_global_timeout(config)

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any(
        "Error during Stash task cleanup" in m
        and "stash identification outer boom" in m
        for m in warning_messages
    ), f"Expected stash-cleanup outer warning, got WARNING: {warning_messages}"


async def test_cleanup_with_global_timeout_cancels_remaining_tasks(
    config_with_database, caplog
):
    """cleanup_with_global_timeout cancels non-Stash background tasks.

    Covers lines 740-756: the "remaining background tasks" block.
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    hang_event = asyncio.Event()

    async def _hanging():
        await hang_event.wait()

    task = asyncio.create_task(_hanging())
    config._background_tasks.append(task)

    try:
        await cleanup_with_global_timeout(config)
    finally:
        hang_event.set()

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any(
        "Cancelling 1 remaining background tasks" in m for m in warning_messages
    ), f"Expected remaining-cancel warning, got WARNING: {warning_messages}"
    assert task.cancelled() or task.done()


async def test_cleanup_with_global_timeout_background_task_wait_timeout(
    config_with_database, caplog, monkeypatch
):
    """cleanup_with_global_timeout handles TimeoutError from background tasks wait.

    Covers lines 757-760: ``except TimeoutError`` branch.
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    hang_event = asyncio.Event()

    async def _hanging():
        await hang_event.wait()

    task = asyncio.create_task(_hanging())
    config._background_tasks.append(task)

    async def _raise_timeout(*_args, **_kwargs):
        raise TimeoutError("bg wait timeout")

    monkeypatch.setattr("fansly_downloader_ng.asyncio.wait", _raise_timeout)

    try:
        await cleanup_with_global_timeout(config)
    finally:
        hang_event.set()
        import contextlib

        with contextlib.suppress(Exception):
            task.cancel()

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any("Background task cleanup timed out" in m for m in warning_messages), (
        f"Expected bg-timeout warning, got WARNING: {warning_messages}"
    )


async def test_cleanup_with_global_timeout_background_task_wait_exception(
    config_with_database, caplog, monkeypatch
):
    """cleanup_with_global_timeout handles generic Exception from background tasks wait.

    Covers lines 761-762: ``except Exception`` after background-task wait.
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    hang_event = asyncio.Event()

    async def _hanging():
        await hang_event.wait()

    task = asyncio.create_task(_hanging())
    config._background_tasks.append(task)

    async def _raise_generic(*_args, **_kwargs):
        raise RuntimeError("bg wait generic boom")

    monkeypatch.setattr("fansly_downloader_ng.asyncio.wait", _raise_generic)

    try:
        await cleanup_with_global_timeout(config)
    finally:
        hang_event.set()
        import contextlib

        with contextlib.suppress(Exception):
            task.cancel()

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any(
        "Error waiting for background tasks" in m and "bg wait generic boom" in m
        for m in warning_messages
    ), f"Expected bg-wait generic warning, got WARNING: {warning_messages}"


async def test_cleanup_with_global_timeout_background_task_cancel_outer_exception(
    config_with_database, caplog
):
    """cleanup_with_global_timeout handles exceptions from the cancel-loop itself.

    Covers lines 763-764: outer ``except Exception``. A task-like shim whose
    ``done()`` raises triggers the outer handler.
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    class _BrokenTask:
        def done(self):
            raise RuntimeError("done raised from cancel loop")

        def cancel(self):
            pass

        def get_coro(self):
            return None

    config._background_tasks.append(_BrokenTask())

    await cleanup_with_global_timeout(config)  # Must not raise.

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any("Could not cancel background tasks" in m for m in warning_messages), (
        f"Expected cancel-outer warning, got WARNING: {warning_messages}"
    )


async def test_cleanup_with_global_timeout_no_database_branch(caplog):
    """cleanup_with_global_timeout logs "No database" when config has none.

    Covers lines 780-781: the ``else`` branch of ``if config._database is
    not None``.
    """
    caplog.set_level(logging.INFO)
    config = FanslyConfig(program_version="0.13.0")

    await cleanup_with_global_timeout(config)

    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("No database to clean up" in m for m in info_messages), (
        f"Expected no-database info log, got: {info_messages}"
    )


async def test_cleanup_with_global_timeout_database_exception(
    config_with_database, caplog, monkeypatch
):
    """cleanup_with_global_timeout logs error when cleanup_database raises.

    Covers lines 782-783: ``except Exception as db_error`` branch.
    """
    caplog.set_level(logging.ERROR)
    config = config_with_database

    async def _raise_db(_config):
        raise RuntimeError("db cleanup boom")

    monkeypatch.setattr("fansly_downloader_ng.cleanup_database", _raise_db)

    await cleanup_with_global_timeout(config)

    error_messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "Error during database cleanup" in m and "db cleanup boom" in m
        for m in error_messages
    ), f"Expected db-error log, got: {error_messages}"


async def test_cleanup_with_global_timeout_db_no_time_remaining(
    config_with_database, caplog, monkeypatch
):
    """cleanup_with_global_timeout bails on DB cleanup if no time left.

    Covers lines 771-773: ``if db_timeout <= 0: return`` path.
    Monkeypatches time.time to report an elapsed time already exceeding
    max_cleanup_time (40 seconds) by the DB-cleanup stage.
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    real_time = time.time
    start = real_time()
    call_count = {"n": 0}

    def _fake_time():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return start
        return start + 50.0

    monkeypatch.setattr("fansly_downloader_ng.time.time", _fake_time)

    await cleanup_with_global_timeout(config)

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any(
        "No time remaining for database cleanup" in m for m in warning_messages
    ), f"Expected no-time-for-db warning, got WARNING: {warning_messages}"


async def test_cleanup_with_global_timeout_semaphore_exception(
    config_with_database, caplog, monkeypatch
):
    """cleanup_with_global_timeout catches exceptions from semaphore cleanup.

    Covers lines 795-796: ``except Exception`` around the semaphore block.
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    call_count = {"n": 0}

    def _raise_on_second_call(*_args, **_kwargs):
        # monitor_semaphores is called once in an earlier ``suppress`` block,
        # then again in the semaphore cleanup block at the tail. Raise only
        # on the tail-side call.
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise RuntimeError("semaphore monitor boom")

    monkeypatch.setattr(
        "fansly_downloader_ng.monitor_semaphores", _raise_on_second_call
    )

    await cleanup_with_global_timeout(config)

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any(
        "Error during semaphore cleanup" in m and "semaphore monitor boom" in m
        for m in warning_messages
    ), f"Expected semaphore-error warning, got WARNING: {warning_messages}"


# _async_main partial-branch and finally-exception tests — 812->819, 825, 844-850.


async def test_async_main_skips_atexit_when_already_registered(
    config_with_database, caplog, monkeypatch
):
    """_async_main skips registering cleanup_database_sync if already in atexit chain.

    Covers the 812->819 branch: the ``any(...)`` check short-circuits
    registration when a matching handler is already present.
    """
    caplog.set_level(logging.INFO)
    _clear_atexit_cleanup_handlers()
    config = config_with_database

    captured_registers: list[tuple] = []

    def _capture_register(func, *args, **kwargs):
        captured_registers.append((func, args, kwargs))
        return func

    # Python 3.11+ removed the public ``atexit._exithandlers`` attribute;
    # set it via monkeypatch with ``raising=False`` so the any() check in
    # _async_main's atexit-skip branch has something to iterate over.
    existing = getattr(atexit, "_exithandlers", [])
    monkeypatch.setattr(
        atexit,
        "_exithandlers",
        [*existing, (cleanup_database_sync, (config,), {})],
        raising=False,
    )
    monkeypatch.setattr("atexit.register", _capture_register)

    async def _fake_main(_cfg):
        return EXIT_SUCCESS

    monkeypatch.setattr("fansly_downloader_ng.main", _fake_main)

    result = await _async_main(config)

    assert result == EXIT_SUCCESS
    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert not any(
        "Registered database cleanup exit handler" in m for m in info_messages
    ), "Should have skipped atexit registration"
    assert len(captured_registers) == 0, (
        f"No new atexit.register call expected, got: {captured_registers}"
    )


async def test_async_main_keyboard_interrupt_with_interrupted_flag_logs_cleanup_message(
    config_with_database, caplog, monkeypatch
):
    """_async_main logs "Starting cleanup after interruption..." if the flag is set.

    Covers line 825: the ``if hasattr(_handle_interrupt, "interrupted")``
    branch inside the KeyboardInterrupt handler.
    """
    caplog.set_level(logging.INFO)
    _clear_atexit_cleanup_handlers()
    config = config_with_database

    monkeypatch.setattr(_handle_interrupt, "interrupted", True, raising=False)

    async def _fake_main(_cfg):
        raise KeyboardInterrupt

    monkeypatch.setattr("fansly_downloader_ng.main", _fake_main)

    result = await _async_main(config)

    assert result == EXIT_ABORT
    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("Starting cleanup after interruption" in m for m in info_messages), (
        f"Expected post-interrupt cleanup log, got INFO: {info_messages}"
    )


async def test_async_main_cleanup_cancelled_calls_sys_exit(
    config_with_database, caplog, monkeypatch
):
    """_async_main calls sys.exit(1) when cleanup itself is cancelled.

    Covers lines 844-846: ``except asyncio.CancelledError`` in the finally block.
    """
    caplog.set_level(logging.ERROR)
    _clear_atexit_cleanup_handlers()
    config = config_with_database

    async def _fake_main(_cfg):
        return EXIT_SUCCESS

    async def _cleanup_cancelled(_config):
        raise asyncio.CancelledError

    monkeypatch.setattr("fansly_downloader_ng.main", _fake_main)
    monkeypatch.setattr(
        "fansly_downloader_ng.cleanup_with_global_timeout", _cleanup_cancelled
    )

    with pytest.raises(SystemExit) as exc_info:
        await _async_main(config)

    assert exc_info.value.code == 1, (
        f"Cancelled cleanup must sys.exit(1), got {exc_info.value.code}"
    )
    error_messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("Cleanup was cancelled" in m for m in error_messages), (
        f"Expected cleanup-cancelled error log, got ERROR: {error_messages}"
    )


async def test_async_main_cleanup_exception_calls_sys_exit(
    config_with_database, caplog, monkeypatch
):
    """_async_main calls sys.exit(1) when cleanup raises a generic exception.

    Covers lines 847-850: ``except Exception as e`` in the finally block —
    logs the error + traceback and forces sys.exit(1).
    """
    caplog.set_level(logging.ERROR)
    _clear_atexit_cleanup_handlers()
    config = config_with_database

    async def _fake_main(_cfg):
        return EXIT_SUCCESS

    async def _cleanup_boom(_config):
        raise RuntimeError("cleanup fatal boom")

    monkeypatch.setattr("fansly_downloader_ng.main", _fake_main)
    monkeypatch.setattr(
        "fansly_downloader_ng.cleanup_with_global_timeout", _cleanup_boom
    )

    with pytest.raises(SystemExit) as exc_info:
        await _async_main(config)

    assert exc_info.value.code == 1, (
        f"Fatal cleanup error must sys.exit(1), got {exc_info.value.code}"
    )
    error_messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "Fatal error during cleanup" in m and "cleanup fatal boom" in m
        for m in error_messages
    ), f"Expected fatal-cleanup error log, got ERROR: {error_messages}"


# cleanup_database no-database branch — covers line 158.


async def test_cleanup_database_no_database_branch(caplog):
    """cleanup_database logs the else branch when config has no _database.

    Covers lines 157-158: the ``else: print_info("No database to clean
    up or database already closed.")`` branch.
    """
    caplog.set_level(logging.INFO)
    config = FanslyConfig(program_version="0.13.0")
    # No _database attribute on fresh config.
    assert getattr(config, "_database", None) is None

    await cleanup_database(config)  # Must not raise.

    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "No database to clean up or database already closed" in m for m in info_messages
    ), f"Expected no-database info log, got: {info_messages}"


# cleanup_database_sync exception branch — covers lines 186-187.


def test_cleanup_database_sync_logs_exception(
    config_with_database, caplog, monkeypatch
):
    """cleanup_database_sync logs the error when close_sync raises.

    Covers lines 186-187: ``except Exception as e: print_error(f"Error
    closing database connections: {e}")``.

    Requires ``_handle_interrupt.interrupted`` attribute to NOT be set
    (otherwise line 182's early-return fires first) and ``_cleanup_done``
    to NOT be set (otherwise line 176 fires first).
    """
    caplog.set_level(logging.ERROR)
    # Make sure the interrupted flag is absent — otherwise the early-return
    # at line 182 fires before close_sync is even called.
    if hasattr(_handle_interrupt, "interrupted"):
        monkeypatch.delattr(_handle_interrupt, "interrupted")
    config = config_with_database

    def _raise_sync():
        raise RuntimeError("close_sync boom")

    monkeypatch.setattr(config._database, "close_sync", _raise_sync)

    cleanup_database_sync(config)  # Must not raise.

    error_messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "Error closing database connections" in m and "close_sync boom" in m
        for m in error_messages
    ), f"Expected sync-close-exception log, got: {error_messages}"


# increase_file_descriptor_limit — covers lines 218-227.


def test_increase_file_descriptor_limit_success_on_unix(caplog):
    """increase_file_descriptor_limit runs successfully on Unix-like systems.

    Covers lines 217-225: the try-block with ``import resource``,
    getrlimit, setrlimit, success log. This test runs on macOS/Linux where
    the ``resource`` module is available; on Windows it'd need an ``if``
    guard but since the worktree is on macOS, we can run the real path.
    """
    caplog.set_level(logging.INFO)
    try:
        import resource  # noqa: F401
    except ImportError:
        pytest.skip("resource module not available (Windows)")

    increase_file_descriptor_limit()

    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    # The log fires only when the limit actually changes; if the system
    # already allows 4096+ soft, the function may no-op. Accept either
    # the success log or verify no exception was raised.
    if info_messages:
        assert any("Increased file descriptor limit" in m for m in info_messages), (
            f"Expected FD-limit info log, got: {info_messages}"
        )


def test_increase_file_descriptor_limit_handles_exception(caplog, monkeypatch):
    """increase_file_descriptor_limit logs a warning on exception.

    Covers lines 226-227: ``except Exception as e: print_warning(...)``.
    Patches ``resource.setrlimit`` to raise so the except branch fires.
    """
    caplog.set_level(logging.WARNING)
    try:
        import resource
    except ImportError:
        pytest.skip("resource module not available (Windows)")

    def _raise_setrlimit(*_args, **_kwargs):
        raise ValueError("simulated rlimit boom")

    monkeypatch.setattr(resource, "setrlimit", _raise_setrlimit)

    increase_file_descriptor_limit()  # Must not raise.

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any(
        "Could not increase file descriptor limit" in m for m in warning_messages
    ), f"Expected FD-limit warning, got WARNING: {warning_messages}"


# load_client_account_into_db happy path — pulled forward from the
# deleted tests/core/unit/test_fansly_downloader_ng_extra.py
# (Wave 2 Cat-D #2). Original used MagicMock(spec=FanslyConfig) + fake
# API; this rewrite uses real FanslyConfig + real EntityStore so the
# Account row is actually persisted via process_account_data.


async def test_load_client_account_into_db_persists_real_account(
    respx_fansly_api,
    config_with_database,
    fansly_api,
    entity_store,
    monkeypatch,
):
    """load_client_account_into_db saves the client Account into the real store.

    Real-pipeline rewrite (Wave 2 Cat-D #2): the original was a
    MagicMock-heavy duplicate that asserted only that the patched
    ``process_account_data`` had been awaited with the expected dict.
    This version uses ``respx_fansly_api`` (same pattern established
    by collections + single rewrites): real ``FanslyApi`` from the
    ``fansly_api`` fixture, real ``EntityStore`` for persistence,
    real ``/api/v1/account?usernames=`` HTTP boundary mocked at the
    respx layer. ``asyncio.sleep`` is short-circuited so the test
    doesn't block on the production timing_jitter call.

    Asserting via ``store.get(Account, creator_id)`` exercises the
    full pipeline: HTTP fetch → JSON envelope unwrap → ID coercion
    → process_account_data → identity-map merge → real DB row.
    """
    from metadata.models import Account, get_store

    config = config_with_database
    config._api = fansly_api
    creator_id = snowflake_id()
    creator_name = "real_clientuser"

    # Real /api/v1/account?usernames=... boundary — see
    # mount_empty_creator_pipeline's account-route shape.
    respx.get(url__startswith="https://apiv3.fansly.com/api/v1/account").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "success": True,
                    "response": [
                        {
                            "id": str(creator_id),
                            "username": creator_name,
                            "displayName": creator_name.title(),
                            "createdAt": 1700000000,
                        }
                    ],
                },
            )
        ]
    )
    monkeypatch.setattr(
        "fansly_downloader_ng.asyncio.sleep", AsyncMock(return_value=None)
    )

    state = DownloadState()
    try:
        await load_client_account_into_db(config, state, creator_name)
    finally:
        dump_fansly_calls(respx.calls, label="load_client_account_persists")

    persisted = await get_store().get(Account, creator_id)
    assert persisted is not None, (
        "Real process_account_data should have persisted the client Account"
    )
    assert persisted.username == creator_name


# load_client_account_into_db exception path — covers lines 253-256.


async def test_load_client_account_into_db_reraises_on_api_error(
    config_with_database, caplog, monkeypatch
):
    """load_client_account_into_db logs traceback and re-raises on API errors.

    Covers lines 253-256: ``except Exception as e`` around the API call —
    logs both the short error and the traceback, then ``raise`` re-raises.
    """
    caplog.set_level(logging.ERROR)
    config = config_with_database

    class _FailingApi:
        def get_creator_account_info(self, *, creator_name):
            raise RuntimeError("simulated api boom")

    # config.get_api() returns the stored _api — inject our failing one.
    config._api = _FailingApi()

    state = DownloadState()

    with pytest.raises(RuntimeError, match="simulated api boom"):
        await load_client_account_into_db(config, state, "clientuser")

    error_messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "Error getting client account info" in m and "simulated api boom" in m
        for m in error_messages
    ), f"Expected api-error log, got ERROR: {error_messages}"


# cleanup_with_global_timeout websocket outer exception — covers lines 701-702.


async def test_cleanup_with_global_timeout_websocket_outer_exception_from_print_info(
    config_with_database, caplog, monkeypatch
):
    """cleanup_with_global_timeout's outer WS handler catches escapes from print_info.

    Covers lines 701-702: the outer ``except Exception`` around the WS
    shutdown block fires only when an exception escapes the inner
    ``except Exception`` at 699. Since ``Exception`` is broad, escapes
    happen when one of the ``print_info`` calls (lines 695 or 698) —
    which sit OUTSIDE the inner try — itself raises.

    Patches ``print_info`` to raise on the "Stopping WebSocket connection"
    message (line 695) so the outer handler catches the escape.
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    class _FakeApi:
        async def close_websocket(self):
            return None

    config._api = _FakeApi()

    import fansly_downloader_ng as fdng

    real_print_info = fdng.print_info

    def _print_info_raising(msg, *args, **kwargs):
        # Line 695: "Stopping WebSocket connection..." is OUTSIDE the
        # inner try — an exception here escapes to the outer handler
        # at line 701-702.
        if "Stopping WebSocket connection" in msg:
            raise RuntimeError("ws outer escape boom")
        return real_print_info(msg, *args, **kwargs)

    monkeypatch.setattr(fdng, "print_info", _print_info_raising)

    await cleanup_with_global_timeout(config)  # Must not raise.

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any(
        "Error during WebSocket shutdown" in m and "ws outer escape boom" in m
        for m in warning_messages
    ), f"Expected outer-WS-exception warning, got WARNING: {warning_messages}"


# cleanup_with_global_timeout time-based branches — covers line 794 + partials.


async def test_cleanup_with_global_timeout_no_time_for_semaphore_cleanup(
    config_with_database, caplog, monkeypatch
):
    """cleanup_with_global_timeout logs "No time remaining" for semaphores.

    Covers line 794: the ``else`` branch of ``if remaining_time > 0`` in
    the semaphore cleanup block.

    Requires a time.time() sequence where elapsed is < 40s at the DB
    stage (line 770) but >= 40s at the semaphore stage (line 787). We
    return start, start+10, start+20, start+30, start+50, start+60 for
    the six time.time() call sites — counted in-order at
    ``fansly_downloader_ng.py:688, 727, 752, 770, 787, 798``.
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    real_time = time.time
    start = real_time()
    call_count = {"n": 0}

    # time.time() call sites in cleanup_with_global_timeout, in order:
    # 1) line 688: cleanup_start anchor
    # 2) line 727: stash_timeout calc (only if stash_tasks non-empty)
    # 3) line 752: background_timeout calc (only if background_tasks non-empty)
    # 4) line 770: db_timeout calc
    # 5) line 787: semaphore remaining_time calc (we want this >= 40)
    # 6) line 798: total_cleanup_time (doesn't matter for coverage)
    #
    # With no stash/background tasks, calls 2+3 are skipped. So our
    # sequence must be:
    #   call 1 (line 688): 0 (anchor)
    #   call 2 (line 770): <40 so db_timeout > 0 (enter DB cleanup)
    #   call 3 (line 787): >=40 so remaining_time <= 0 (hit else branch on 793-794)
    elapsed_sequence = [0, 10, 50, 60]

    def _fake_time():
        idx = min(call_count["n"], len(elapsed_sequence) - 1)
        call_count["n"] += 1
        return start + elapsed_sequence[idx]

    monkeypatch.setattr("fansly_downloader_ng.time.time", _fake_time)

    await cleanup_with_global_timeout(config)

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any(
        "No time remaining for semaphore cleanup" in m for m in warning_messages
    ), f"Expected semaphore no-time warning, got WARNING: {warning_messages}"


async def test_cleanup_with_global_timeout_stash_wait_skipped_when_no_time(
    config_with_database, caplog, monkeypatch
):
    """cleanup_with_global_timeout skips the stash-wait when stash_timeout <= 0.

    Covers the 728->738 partial branch: ``if stash_timeout > 0:`` False
    path — no grace wait is performed because time is already gone.

    time.time() sequence: start, start+50 — by the stash-wait check we've
    already burned 50 seconds, so ``stash_timeout = min(10, 40-50) = -10``,
    the ``if stash_timeout > 0`` check is False, and the block is skipped.
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    hang_event = asyncio.Event()

    class StashProcessing:
        async def run(self):
            await hang_event.wait()

    sp = StashProcessing()
    stash_task = asyncio.create_task(sp.run())
    config._background_tasks.append(stash_task)

    real_time = time.time
    start = real_time()
    call_count = {"n": 0}
    # All subsequent calls: elapsed >= 40, so every timeout calculation
    # yields <= 0.
    elapsed_sequence = [0, 50, 60, 70, 80, 90, 100]

    def _fake_time():
        idx = min(call_count["n"], len(elapsed_sequence) - 1)
        call_count["n"] += 1
        return start + elapsed_sequence[idx]

    monkeypatch.setattr("fansly_downloader_ng.time.time", _fake_time)

    try:
        await cleanup_with_global_timeout(config)
    finally:
        hang_event.set()
        import contextlib

        with contextlib.suppress(Exception):
            stash_task.cancel()

    # The "No time remaining for database cleanup" warning fires — proof
    # that time advanced past the 40s mark before the DB check.
    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any(
        "No time remaining for database cleanup" in m for m in warning_messages
    ), f"Expected db no-time warning, got WARNING: {warning_messages}"


async def test_cleanup_with_global_timeout_skips_done_stash_task_in_cancel_loop(
    config_with_database, caplog
):
    """cleanup_with_global_timeout skips done stash tasks in the cancel loop.

    Covers the 723->722 partial branch: ``for task in stash_tasks: if
    not task.done():`` False path when the task is already done before
    the cancel loop reaches it. Seeds one instant-complete task + one
    hanging task; the completed one hits the False branch.
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    hang_event = asyncio.Event()

    class StashProcessing:
        async def run_instant(self):
            return None

        async def run_hanging(self):
            await hang_event.wait()

    sp = StashProcessing()
    done_task = asyncio.create_task(sp.run_instant())
    await asyncio.sleep(0)  # let the instant task complete
    config._background_tasks.append(done_task)
    hang_task = asyncio.create_task(sp.run_hanging())
    config._background_tasks.append(hang_task)

    try:
        await cleanup_with_global_timeout(config)
    finally:
        hang_event.set()

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    assert any(
        "Found 2 Stash processing tasks to clean up" in m for m in warning_messages
    ), f"Expected 2-stash warning, got WARNING: {warning_messages}"


async def test_cleanup_with_global_timeout_skips_bg_wait_when_no_time(
    config_with_database, caplog, monkeypatch
):
    """cleanup_with_global_timeout skips the background-task wait when timeout <= 0.

    Covers the 754->767 partial branch: ``if background_timeout > 0:``
    False path. Makes time.time() advance far enough that
    ``background_timeout = min(10, 40-elapsed)`` is <= 0.
    """
    caplog.set_level(logging.WARNING)
    config = config_with_database

    hang_event = asyncio.Event()

    async def _hanging():
        await hang_event.wait()

    task = asyncio.create_task(_hanging())
    config._background_tasks.append(task)

    real_time = time.time
    start = real_time()
    call_count = {"n": 0}
    # time sequence: 0 at start, then elapsed jumps to 50 for every
    # subsequent check — both stash_timeout and background_timeout are
    # <= 0, and DB also sees >= 40s so it's skipped too.
    elapsed_sequence = [0, 50, 50, 50, 50, 50, 50]

    def _fake_time():
        idx = min(call_count["n"], len(elapsed_sequence) - 1)
        call_count["n"] += 1
        return start + elapsed_sequence[idx]

    monkeypatch.setattr("fansly_downloader_ng.time.time", _fake_time)

    try:
        await cleanup_with_global_timeout(config)
    finally:
        hang_event.set()
        import contextlib

        with contextlib.suppress(Exception):
            task.cancel()

    warning_messages = [
        r.getMessage() for r in caplog.records if r.levelname == "WARNING"
    ]
    # We DO hit the "Cancelling N remaining" warning at the start of the
    # block; the "wait" inside the block is skipped due to time budget.
    assert any("Cancelling 1 remaining background tasks" in m for m in warning_messages)
