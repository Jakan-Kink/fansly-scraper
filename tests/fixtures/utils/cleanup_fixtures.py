"""Cleanup fixtures to prevent object retention between tests.

These fixtures address potential memory leaks and state persistence that can occur
in xdist parallel test environments, including:
- Rich progress manager and console state
- Loguru handler cleanup
- Global configuration state
- HTTP session cleanup
- Async task cleanup
- Database state cleanup
"""

import asyncio
import contextlib
import gc
from unittest import mock

import httpx
import pytest
from loguru import logger


# NOTE: imports of ``config.logging`` and ``helpers.rich_progress`` MUST stay
# inside the fixture bodies below (lazy import). Hoisting them to module
# scope triggers a circular import: cleanup_fixtures → tests.fixtures
# package init → config.logging → config.config → config.fanslyconfig →
# api.FanslyApi → ImportError. PEP 8 ("imports at top of file") explicitly
# allows this exception when "imports cause circular dependencies".


@pytest.fixture(autouse=True)
def cleanup_rich_progress_state():
    """Clean up Rich progress manager and console state between tests."""
    yield  # Run the test

    try:
        # Lazy import — see top-of-file note about the circular-dependency cycle
        from helpers.rich_progress import _console, _progress_manager

        # Reset progress manager state
        with _progress_manager._lock:
            # Stop any active Live instances
            if _progress_manager.live is not None:
                with contextlib.suppress(Exception):
                    _progress_manager.live.stop()
                _progress_manager.live = None

            # Clear active tasks
            _progress_manager.active_tasks.clear()
            _progress_manager._session_count = 0

        # Reset console state if possible (defensive approach for internal APIs)
        with contextlib.suppress(AttributeError, Exception):
            # Clear any pushed themes (if supported by this Rich version)
            if hasattr(_console, "_theme_stack"):
                theme_stack = getattr(_console, "_theme_stack", None)
                if theme_stack and hasattr(theme_stack, "clear"):
                    theme_stack.clear()

    except ImportError:
        # Rich progress module not available
        pass


def _close_handler_safely(handler):
    """Helper to safely close a handler."""
    if handler and hasattr(handler, "close"):
        with contextlib.suppress(ValueError, OSError, Exception):
            # Check if the handler is already closed before attempting to close it
            if hasattr(handler, "closed") and handler.closed:
                return
            handler.close()


def _get_loguru_handlers():
    """Helper to get loguru handlers safely."""
    with contextlib.suppress(AttributeError, Exception):
        core = getattr(logger, "_core", None)
        if core and hasattr(core, "handlers"):
            return core.handlers.copy()
    return None


@pytest.fixture(autouse=True)
def cleanup_loguru_handlers():
    """Clean up loguru handlers and file handles between tests."""
    yield  # Run the test

    try:
        # Lazy import — see top-of-file note about the circular-dependency cycle
        from config.logging import _handler_ids

        # Remove loguru handlers but don't manually close file handlers
        # Let loguru handle the file closure to avoid double-close issues
        for handler_id in list(_handler_ids.keys()):
            with contextlib.suppress(ValueError):
                logger.remove(handler_id)

        # Clear handler tracking without manual file closes
        _handler_ids.clear()
    except ImportError:
        # Logging module not available
        pass


@pytest.fixture(autouse=True)
def cleanup_http_sessions():
    """Clean up HTTP sessions and connection pools between tests."""
    # Track created sessions during test
    created_sessions = []

    # Patch httpx.AsyncClient and httpx.Client to track instances
    original_async_client_init = None
    original_client_init = None

    try:
        original_async_client_init = httpx.AsyncClient.__init__
        original_client_init = httpx.Client.__init__

        def tracking_async_init(self, *args, **kwargs):
            created_sessions.append(self)
            return original_async_client_init(self, *args, **kwargs)

        def tracking_init(self, *args, **kwargs):
            created_sessions.append(self)
            return original_client_init(self, *args, **kwargs)

        httpx.AsyncClient.__init__ = tracking_async_init
        httpx.Client.__init__ = tracking_init
    except ImportError:
        pass

    yield  # Run the test

    # Clean up tracked sessions
    for session in created_sessions:
        with contextlib.suppress(Exception):
            if hasattr(session, "aclose"):
                # AsyncClient - need to close asynchronously
                with contextlib.suppress(Exception):
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        loop = None
                    if loop is not None:
                        # Loop is running — schedule the close
                        task = loop.create_task(session.aclose())
                        # Store reference to avoid RUF006, though we can't await in cleanup
                        _ = task
                    else:
                        # No running loop — use asyncio.run()
                        asyncio.run(session.aclose())
            elif hasattr(session, "close"):
                # Regular Client - sync close
                session.close()

    # Restore original __init__ methods
    if original_async_client_init:
        with contextlib.suppress(ImportError):
            httpx.AsyncClient.__init__ = original_async_client_init
    if original_client_init:
        with contextlib.suppress(ImportError):
            httpx.Client.__init__ = original_client_init


@pytest.fixture(autouse=True)
def cleanup_global_config_state():
    """Clean up global configuration state between tests."""
    yield  # Run the test

    try:
        # Lazy import — see top-of-file note about the circular-dependency cycle
        import config.logging as _logging_mod

        # Reset global configuration variables
        _logging_mod._config = None
        _logging_mod._debug_enabled = False

    except ImportError:
        pass


def _close_unawaited_coroutines():
    """Helper to close any unawaited coroutines."""
    for obj in gc.get_objects():
        with contextlib.suppress(TypeError, AttributeError, ValueError, RuntimeError):
            # RuntimeError: coroutine ignored GeneratorExit
            if asyncio.iscoroutine(obj):
                obj.close()


@pytest.fixture(autouse=True)
def cleanup_unawaited_coroutines():
    """Clean up any unawaited coroutines after tests."""
    yield  # Run the test

    # Close any coroutines that were created but not awaited
    _close_unawaited_coroutines()


@pytest.fixture(autouse=True)
def cleanup_mock_patches():
    """Clean up unittest.mock patches and stop all active patches.

    This prevents resource leaks from patch objects in parallel test execution.
    Specifically addresses errno 24 (too many open files) errors.
    """
    yield  # Run the test

    # Stop all active patches
    with contextlib.suppress(ImportError, AttributeError):
        # Stop all patches using stopall()
        mock.patch.stopall()


@pytest.fixture(autouse=True)
def cleanup_rate_limiter_displays():
    """Stop ``RateLimiterDisplay`` threads spawned during the test.

    ``api/rate_limiter_display.RateLimiterDisplay.start()`` spawns a
    daemon thread (``name="RateLimiterDisplay"``) that updates Rich
    progress tasks in a polling loop. Tests that exercise ``FanslyConfig``
    setup or rate-limiter wiring create instances; without explicit
    ``.stop()`` the daemon thread accumulates across tests and is alive
    at worker shutdown — racing ``_Py_Finalize.flush_std_files`` on the
    buffered-IO lock.

    Pattern: monkeypatch ``__init__`` to track instances, call ``.stop()``
    on each at teardown, restore original. Same shape as
    ``cleanup_http_sessions`` for ``httpx.Client``.
    """
    tracked: list = []
    original_init = None

    with contextlib.suppress(Exception):
        # Lazy import — RateLimiterDisplay is in api/, which depends on
        # config indirectly; top-level import here triggers the same
        # circular cycle documented at the top of this file.
        from api.rate_limiter_display import RateLimiterDisplay

        original_init = RateLimiterDisplay.__init__

        def tracking_init(self, *args, **kwargs):
            tracked.append(self)
            return original_init(self, *args, **kwargs)

        RateLimiterDisplay.__init__ = tracking_init

    yield  # Run the test

    for instance in tracked:
        with contextlib.suppress(Exception):
            instance.stop()  # sets _stop_event + joins with timeout=1.0

    if original_init is not None:
        with contextlib.suppress(Exception):
            from api.rate_limiter_display import RateLimiterDisplay

            RateLimiterDisplay.__init__ = original_init


@pytest.fixture(autouse=True)
def cleanup_fansly_websockets():
    """Stop ``FanslyWebSocket`` daemon threads spawned during the test.

    ``api/websocket.FanslyWebSocket.start_in_thread()`` spawns a daemon
    thread (``name="fansly-ws"``) running an asyncio event loop that
    maintains the WebSocket connection. Tests that exercise the daemon
    runner, monitoring loop, or WebSocket integration create instances.
    Without explicit ``.stop_thread()`` the daemon thread accumulates
    across tests, racing finalize the same way as RateLimiterDisplay.

    Same instance-tracking pattern as ``cleanup_rate_limiter_displays``.
    """
    tracked: list = []
    original_init = None

    with contextlib.suppress(Exception):
        # Lazy import — same circular-cycle rationale as above.
        from api.websocket import FanslyWebSocket

        original_init = FanslyWebSocket.__init__

        def tracking_init(self, *args, **kwargs):
            tracked.append(self)
            return original_init(self, *args, **kwargs)

        FanslyWebSocket.__init__ = tracking_init

    yield  # Run the test

    # Cross-thread asyncio shutdown: setting ``_stop_event`` alone is not
    # enough because the WS thread's asyncio loop is parked inside
    # ``selector.select(timeout)`` waiting for socket I/O — the ``is_set()``
    # check in ``_maintain_connection`` only runs when select returns.
    # ``call_soon_threadsafe(loop.stop)`` schedules ``loop.stop()`` on the
    # loop's own thread AND wakes the selector via the loop's self-pipe,
    # so ``run_until_complete`` returns and ``_thread_main`` exits.
    for instance in tracked:
        with contextlib.suppress(Exception):
            stop_event = getattr(instance, "_stop_event", None)
            if stop_event is not None:
                stop_event.set()
            ws_loop = getattr(instance, "_ws_loop", None)
            if ws_loop is not None and not ws_loop.is_closed():
                with contextlib.suppress(RuntimeError):
                    ws_loop.call_soon_threadsafe(ws_loop.stop)
            ws_thread = getattr(instance, "_ws_thread", None)
            if ws_thread is not None and ws_thread.is_alive():
                ws_thread.join(timeout=2.0)

    if original_init is not None:
        with contextlib.suppress(Exception):
            from api.websocket import FanslyWebSocket

            FanslyWebSocket.__init__ = original_init


@pytest.fixture(autouse=True)
def cleanup_jspybridge():
    """Tear down JSPyBridge daemon threads + Node subprocess between tests.

    JSPyBridge (the ``javascript`` package, used by ``helpers/checkkey.py``
    for one-shot startup checkKey extraction) spawns daemon threads
    that outlive the test that triggered them:
      - ``connection.com_thread`` reading Node stderr via readline
      - ``connection.stdout_thread`` bare-printing every Node stdout line
      - ``EventLoop.callbackExecutor`` event executor

    These threads accumulate across tests within an xdist worker and
    are alive at worker shutdown, racing ``_Py_Finalize.flush_std_files``
    on the buffered-IO lock and triggering ``_enter_buffered_busy``
    SIGABRTs. Production-side ``helpers/checkkey._shutdown_js_bridge``
    handles the in-flow case; this fixture is the belt-and-suspenders
    for any test path that imports the bridge but doesn't reach the
    production shutdown call.
    """
    yield  # Run the test

    with contextlib.suppress(Exception):
        # Lazy import — see top-of-file note about the circular-dependency cycle
        from helpers.checkkey import _shutdown_js_bridge

        _shutdown_js_bridge()


# Export all cleanup fixtures
__all__ = [
    "cleanup_fansly_websockets",
    "cleanup_global_config_state",
    "cleanup_http_sessions",
    "cleanup_jspybridge",
    "cleanup_loguru_handlers",
    "cleanup_mock_patches",
    "cleanup_rate_limiter_displays",
    "cleanup_rich_progress_state",
    "cleanup_unawaited_coroutines",
]
