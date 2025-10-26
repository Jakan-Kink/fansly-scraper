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


@pytest.fixture(autouse=True)
def cleanup_rich_progress_state():
    """Clean up Rich progress manager and console state between tests."""
    yield  # Run the test

    try:
        # Lazy import to avoid circular dependency
        from helpers.rich_progress import _console, _progress_manager  # noqa: PLC0415

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
        # Lazy import to avoid circular dependency
        from config.logging import _handler_ids  # noqa: PLC0415

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
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # If loop is running, schedule the close
                        asyncio.create_task(session.aclose())
                    else:
                        # If loop is not running, run until complete
                        loop.run_until_complete(session.aclose())
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
        # Lazy import to avoid circular dependency
        import config.logging  # noqa: PLC0415

        # Reset global configuration variables
        config.logging._config = None
        config.logging._debug_enabled = False

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


# Export all cleanup fixtures
__all__ = [
    "cleanup_global_config_state",
    "cleanup_http_sessions",
    "cleanup_loguru_handlers",
    "cleanup_mock_patches",
    "cleanup_rich_progress_state",
    "cleanup_unawaited_coroutines",
]
