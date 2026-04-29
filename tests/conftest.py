"""Master test configuration and fixtures.

This is the single source of truth for all test fixtures and configuration.
All tests can access fixtures defined here or in the tests/fixtures/ modules.

Organization:
- Auto-use fixtures (logging, cleanup) - Always run
- Database fixtures - From tests/fixtures/database_fixtures.py
- Factory fixtures - FactoryBoy factories for creating test data
- Stash fixtures - From tests/fixtures/stash_fixtures.py
- Test data fixtures - JSON data loaders, sample objects
- Config fixtures - Configuration objects for testing
"""

import asyncio
import faulthandler
import gc
import logging
import os
import shutil
import sys
import tempfile
import threading
import traceback
from configparser import ConfigParser
from contextlib import contextmanager, suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, sleep, time


# ---------------------------------------------------------------------------
# Pre-import side effects (must run before project imports — see comments)
# ---------------------------------------------------------------------------

# Run BEFORE any project import that might transitively evaluate loguru
# sink configuration. ``config/logging.py::init_logging_config`` reads
# ``TESTING`` to decide ``enqueue=False`` (synchronous, safe) vs the
# default ``enqueue=True`` (spawns a background worker thread per sink).
#
# In production ``enqueue=True`` lets log emission happen off the main
# thread, but during pytest-xdist worker teardown the main thread enters
# ``_Py_Finalize → flush_std_files`` while a loguru worker thread can
# still be mid-``_bufferedwriter_flush_unlocked`` — that race trips
# ``_enter_buffered_busy`` and the worker dies with SIGABRT (sometimes
# SIGSEGV/SIGBUS depending on which buffer is corrupted at finalization).
#
# ``setdefault`` preserves an explicit ``TESTING=0 pytest ...`` override
# for anyone deliberately exercising the production path.
os.environ.setdefault("TESTING", "1")

# Dump Python tracebacks of every thread on SIGSEGV/SIGFPE/SIGABRT/SIGBUS/
# SIGILL before the process dies. Without this, xdist subprocess crashes
# from C extensions (asyncpg, Pillow, leveldb, etc.) leave only the C
# stack in the ``.ips`` report — no signal back to the Python code that
# triggered them. Writes to the original stderr fd so it survives even
# when pytest's capture is teardown-broken.
faulthandler.enable(file=sys.stderr, all_threads=True)

# Project + third-party imports follow. The E402 noqa is intentional:
# the side-effect block above MUST run before these imports, because some
# of them (config.*, anything that touches loguru) evaluate the
# ``TESTING`` env at runtime via paths that can be triggered at
# fixture-collection time.
import psutil  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from loguru import logger  # noqa: E402

from config import FanslyConfig  # noqa: E402
from config.fanslyconfig import FanslyConfig as FanslyConfigClass  # noqa: E402
from config.modes import DownloadMode  # noqa: E402
from download.core import DownloadState  # noqa: E402

# Import all factories and fixtures using wildcard.
# This pulls in the cleanup_* autouse fixtures from
# tests/fixtures/utils/cleanup_fixtures.py — they fire on every test.
from tests.fixtures import *  # noqa: F403,E402
from tests.fixtures import DownloadStateFactory, FanslyConfigFactory  # noqa: E402
from tests.fixtures.utils.test_isolation import snowflake_id  # noqa: E402


# ============================================================================
# Pytest Hooks
# ============================================================================


# Known-vendored thread fingerprints — bottom-of-stack file path substrings.
# Only includes leaks we have NO fix path for (pure third-party plugins
# whose threads can't be torn down from a fixture). Anything we've actively
# fixed (JSPyBridge via _shutdown_js_bridge, fansly-ws via call_soon_threadsafe,
# _do_watch_progress via stop_event, etc.) is INTENTIONALLY EXCLUDED so a
# regression that bypasses our fix triggers the dump — silent on success,
# loud when our fix breaks.
_VENDORED_THREAD_PATHS: tuple[str, ...] = (
    "/site-packages/pytest_rerunfailures",  # rerunfailures TCP server + per-worker connections
)


def pytest_unconfigure(config):
    """Flush stdout/stderr + dump UNFAMILIAR live-thread frames before xdist worker shutdown.

    Python 3.13 on macOS has a race in _Py_Finalize: if a daemon thread
    holds the stdout buffer lock when the main thread calls flush_std_files(),
    the interpreter hits _enter_buffered_busy → SIGABRT.

    The faulthandler ``all_threads=True`` we install at conftest import
    time CANNOT show those daemon threads' Python frames in the abort path,
    because by the time faulthandler fires (inside ``fatal_error`` inside
    ``_Py_Finalize``), the daemon threads have already been detached via
    ``PyThread_hang_thread`` — removed from ``interp->threads.head`` so
    ``_Py_DumpTracebackThreads`` can't enumerate them.

    This hook runs *before* finalize starts, while every live thread is
    still in the interpreter's thread state list. We use ``sys._current_frames()``
    (which works regardless of GIL state) to capture each thread's Python
    stack and emit it to stderr. If a daemon thread is mid-``builtin_print``
    or mid-FFmpeg-progress-readline, that's where the racing call site shows.

    Threads from known-vendored sources (``_VENDORED_THREAD_PATHS``) are
    counted but not dumped — pytest_rerunfailures' run_server + per-worker
    run_connection threads are unfixable from a fixture and would be pure
    noise. The dump fires only when an UNFAMILIAR live thread is found,
    so any regression that re-introduces a fansly-ws / JSPyBridge events.loop /
    _do_watch_progress / loguru-enqueue / RateLimiterDisplay / etc. leak is
    loud, while the known-clean state is silent. JSPyBridge is intentionally
    NOT vendored — we have a working fix (_shutdown_js_bridge), so a leak
    return means the fix broke or got bypassed, which we want to know.
    """
    with suppress(Exception):
        sys.stdout.flush()
    with suppress(Exception):
        sys.stderr.flush()

    with suppress(Exception):
        main_id = threading.main_thread().ident
        live = [t for t in threading.enumerate() if t.ident != main_id]
        if not live:
            return

        frames = sys._current_frames()

        # Classify each thread by walking ALL frames in its stack for a
        # vendored-path match. We can't use just the deepest frame because
        # that's always ``threading.py:_bootstrap`` for thread-spawned code.
        # We can't use just the top frame either because the thread's parked
        # call site (e.g. ``socket.py:accept``) is below the vendored frame.
        # The vendored marker (e.g. pytest_rerunfailures.py:466 ``run_server``)
        # sits in the middle of the stack — scan every frame for it.
        unfamiliar: list[tuple[threading.Thread, object]] = []
        vendored_count = 0
        for thread in live:
            frame = frames.get(thread.ident)
            is_vendored = False
            if frame is not None:
                walker = frame
                while walker is not None:
                    if any(
                        token in walker.f_code.co_filename
                        for token in _VENDORED_THREAD_PATHS
                    ):
                        is_vendored = True
                        break
                    walker = walker.f_back
            if is_vendored:
                vendored_count += 1
            else:
                unfamiliar.append((thread, frame))

        # Steady-state silent path: only known-vendored threads alive → no output.
        if not unfamiliar:
            return

        print(
            f"\n=== pytest_unconfigure: {len(unfamiliar)} unfamiliar non-main thread(s) "
            f"alive at shutdown ({vendored_count} vendored ignored) "
            f"— dumping Python frames before _Py_Finalize ===",
            file=sys.stderr,
        )
        for thread, frame in unfamiliar:
            print(
                f"\n--- thread name={thread.name!r} ident={thread.ident} "
                f"daemon={thread.daemon} alive={thread.is_alive()} ---",
                file=sys.stderr,
            )
            if frame is None:
                print("  (no Python frame — pure C-extension thread)", file=sys.stderr)
            else:
                traceback.print_stack(frame, file=sys.stderr)
        print(
            "=== end pytest_unconfigure thread dump ===\n",
            file=sys.stderr,
        )
        sys.stderr.flush()


def pytest_collection_modifyitems(config, items):
    """Hook to validate fixture usage and add markers.

    Ensures that any test using Stash fixtures also uses stash_cleanup_tracker
    for proper test isolation and cleanup.

    This enforcement applies to:
    - stash_client: Direct access to StashClient
    - real_stash_processor: StashProcessing with real Docker Stash
    - stash_context: StashContext that creates stash_client internally

    Even if a test mocks methods, if the mock is incorrectly configured, it could
    make real connections to the Stash server and leave behind objects.

    Tests that violate this requirement are marked with xfail(strict=True),
    which means:
    - If the test would pass, it fails (enforcement)
    - If the test would fail naturally, it fails (expected)
    - If Stash is unavailable and test skips, it skips (expected)

    EXCEPTIONS:
    - respx_stash_client: Uses respx to mock HTTP, no cleanup needed
    - respx_stash_processor: Uses respx to mock HTTP, no cleanup needed
    - Tests in tests/stash/types/: Unit tests for data conversion only
    """
    for item in items:
        if hasattr(item, "fixturenames"):
            # Check if test uses any Stash fixture that requires cleanup
            uses_stash_fixture = (
                "stash_client" in item.fixturenames
                or "real_stash_processor" in item.fixturenames
                or "stash_context" in item.fixturenames
            )

            # Exception: respx fixtures use HTTP mocking, no real Stash calls
            uses_respx = (
                "respx_stash_processor" in item.fixturenames
                or "respx_stash_client" in item.fixturenames
            )

            # Exception: tests/stash/types/ are unit tests for data models
            is_types_test = "tests/stash/types/" in str(item.fspath)

            has_cleanup = "stash_cleanup_tracker" in item.fixturenames

            # Enforce cleanup requirement
            if (
                uses_stash_fixture
                and not uses_respx
                and not is_types_test
                and not has_cleanup
            ):
                item.add_marker(
                    pytest.mark.xfail(
                        reason="Tests using Stash fixtures (stash_client, real_stash_processor, "
                        "stash_context) MUST also use stash_cleanup_tracker for test isolation "
                        "and cleanup. See tests/stash/CLEANUP_ENFORCEMENT_SUMMARY.md",
                        strict=True,
                    )
                )

            # Pin all ``message_media_generator`` consumers to a single
            # xdist worker. The fixture picks images out of the shared
            # Docker Stash pool; under multi-worker concurrency, two
            # workers can race on the same image row — one creates a
            # gallery + links it, the other reads the link, then the
            # first worker's cleanup deletes the gallery before the
            # second worker's ``imageUpdate`` can fire, and Stash rejects
            # the now-stale gallery_id. Single Docker container, no per-
            # worker isolation possible, so serial execution for this
            # cluster is the only realistic mitigation. ``--dist=loadgroup``
            # in ``pyproject.toml`` honors this marker.
            if "message_media_generator" in item.fixturenames:
                item.add_marker(pytest.mark.xdist_group(name="docker_stash_shared"))


# ============================================================================
# Helper Functions
# ============================================================================


def clean_model_data(data_dict):
    """Remove problematic fields from dict before creating model instances.

    This prevents issues with _dirty_attrs and other internal fields
    that might cause problems with mock objects in tests.

    Args:
        data_dict: Dictionary of model data

    Returns:
        Cleaned dictionary
    """
    if not isinstance(data_dict, dict):
        return data_dict

    # Remove internal attributes that could cause issues
    clean_dict = {
        k: v
        for k, v in data_dict.items()
        if not k.startswith("_") and k != "client_mutation_id"
    }
    return clean_dict


# ============================================================================
# Auto-use Fixtures (Always Applied)
# ============================================================================


@pytest_asyncio.fixture(autouse=True)
async def cleanup_tasks():
    """Cleanup leaked async tasks after each test.

    Only cancels tasks that were created during the test — not internal
    framework tasks (asyncpg pool management, etc.) that may still be
    needed during fixture teardown.
    """
    pre_existing = asyncio.all_tasks()
    yield
    for task in asyncio.all_tasks():
        if (
            task not in pre_existing
            and not task.done()
            and task != asyncio.current_task()
        ):
            task.cancel()
            with suppress(asyncio.CancelledError, RuntimeError):
                await task


# NOTE: cleanup_rich_progress_state, cleanup_loguru_handlers,
# cleanup_global_config_state, cleanup_http_sessions, cleanup_mock_patches,
# and cleanup_unawaited_coroutines all live in
# ``tests/fixtures/utils/cleanup_fixtures.py`` (per CLAUDE.md fixture
# location policy) and are wired into this conftest via the
# ``from tests.fixtures import *`` wildcard at the top.


@pytest.fixture(autouse=True)
def setup_test_logging():
    """Set up logging for tests and clean up after.

    This fixture is automatically used in all tests to:
    1. Set up logging to a temporary directory
    2. Clean up log files after tests
    3. Properly close all file handlers
    """
    # Create a temporary directory for logs (manual management for proper cleanup)
    temp_dir = tempfile.mkdtemp()
    temp_path = Path(temp_dir)

    # Store original handlers to restore later
    original_handlers = logging.root.handlers.copy()

    handler_id = None
    try:
        # Remove all existing handlers
        logging.root.handlers.clear()

        # Configure logger to use temp directory
        logger.remove()  # Remove default loguru handler
        handler_id = logger.add(
            temp_path / "test.log",
            rotation="1 MB",
            retention=1,
            level="DEBUG",
        )

        yield temp_path

    finally:
        # CRITICAL: Remove loguru handlers FIRST to close file handles
        # This must happen before temp directory cleanup
        with suppress(ValueError, Exception):
            if handler_id is not None:
                logger.remove(handler_id)
            else:
                logger.remove()  # Remove all if handler_id wasn't captured

        # Restore original logging handlers
        logging.root.handlers.clear()
        for handler in original_handlers:
            logging.root.addHandler(handler)

        # NOW safe to clean up temp directory (all files are closed)
        with suppress(Exception):
            shutil.rmtree(temp_dir, ignore_errors=True)


# ============================================================================
# Test Data Fixtures
# ============================================================================


# ============================================================================
# Sample Data Fixtures
# ============================================================================


@pytest.fixture
def sample_account():
    """Create a sample account for testing.

    Returns:
        dict: Sample account data
    """
    return {
        "id": str(snowflake_id()),
        "username": "test_user",
        "displayName": "Test User",
        "about": "Test account for testing",
        "location": "Test Location",
    }


@pytest.fixture
def sample_post(sample_account):
    """Create a sample post for testing.

    Returns:
        dict: Sample post data
    """
    return {
        "id": str(snowflake_id()),
        "accountId": sample_account["id"],
        "content": "Test post content #test",
        "likeCount": 5,
        "replyCount": 1,
        "mediaLikeCount": 3,
    }


@pytest.fixture
def sample_message(sample_account):
    """Create a sample message for testing.

    Returns:
        dict: Sample message data
    """
    return {
        "id": str(snowflake_id()),
        "groupId": str(snowflake_id()),
        "senderId": sample_account["id"],
        "content": "Test message content",
    }


# ============================================================================
# Config Fixtures (consolidated from tests/config/conftest.py)
# ============================================================================


# NOTE: The main 'config' fixture is defined in tests/fixtures/database_fixtures.py
# It creates a UUID-based PostgreSQL database for perfect test isolation.
# This legacy fixture is kept as 'mock_config' for tests that don't need real databases.


@pytest.fixture
def mock_config():
    """Create a basic FanslyConfig instance without database setup.

    Use this for tests that mock the database or don't need real database access.
    For tests that need a real database, use the 'config' fixture from database_fixtures.py

    Note: Sets pg_database to a non-existent name to prevent accidental connections
    to the production database.
    """
    config = FanslyConfig(program_version="0.13.0")
    # Override database name to prevent accidental connections to production database
    config.pg_database = "test_mock_should_not_connect"
    return config


@pytest.fixture
def test_config():
    """Alias for mock_config fixture for backwards compatibility."""
    config = FanslyConfig(program_version="0.13.0")
    # Override database name to prevent accidental connections to production database
    config.pg_database = "test_mock_should_not_connect"
    return config


@pytest.fixture
def temp_config_dir():
    """Create a temporary directory and change to it for config file testing."""

    with tempfile.TemporaryDirectory() as temp_dir:
        original_cwd = Path.cwd()
        os.chdir(temp_dir)
        try:
            yield Path(temp_dir)
        finally:
            os.chdir(original_cwd)

            # Clean up logging handlers (loguru keeps file handles open)
            with suppress(Exception):
                logger.remove()  # Remove all handlers to close log files

            # Force garbage collection to close any open file handles
            gc.collect()
            sleep(0.1)  # Brief delay to ensure handles are released

            # Try to remove any remaining files manually
            with suppress(Exception):
                for item in Path(temp_dir).iterdir():
                    if item.is_file():
                        item.unlink(missing_ok=True)
                    elif item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)


@pytest.fixture
def config_parser():
    """Create a ConfigParser instance for raw config manipulation."""
    return ConfigParser(interpolation=None)


@pytest.fixture
def mock_config_file(temp_config_dir, request):
    """Create a mock config file with specified content."""
    config_path = temp_config_dir / "config.ini"

    config_content = getattr(request, "param", None)
    if config_content is None:
        config_content = """
        [Options]
        download_mode = Normal
        interactive = True
        download_directory = Local_directory
        """

    with config_path.open("w") as f:
        f.write(config_content)

    return config_path


@pytest.fixture
def mock_download_dir(temp_config_dir):
    """Create a mock download directory for testing."""
    download_dir = temp_config_dir / "downloads"
    download_dir.mkdir()
    return download_dir


@pytest.fixture
def mock_metadata_dir(temp_config_dir):
    """Create a mock metadata directory for testing."""
    metadata_dir = temp_config_dir / "metadata"
    metadata_dir.mkdir()
    return metadata_dir


@pytest.fixture
def mock_temp_dir(temp_config_dir):
    """Create a mock temporary directory for testing."""
    temp_dir = temp_config_dir / "temp"
    temp_dir.mkdir()
    return temp_dir


@pytest.fixture
def valid_api_config(mock_config_file):
    """Create a config file with valid API credentials."""
    with mock_config_file.open("w") as f:
        f.write(
            """
        [MyAccount]
        Authorization_Token = test_token_long_enough_to_be_valid_token_here_more_chars
        User_Agent = test_user_agent_long_enough_to_be_valid_agent_here_more
        Check_Key = test_check_key

        [Options]
        interactive = True
        download_mode = Normal
        download_directory = Local_directory
        """
        )
    return mock_config_file


@pytest.fixture(scope="session")
def download_modes():
    """Get all available download modes."""
    return list(DownloadMode)


@pytest.fixture
def temp_db_dir():
    """Create a temporary directory for database testing."""
    with TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


# ============================================================================
# Download Fixtures (consolidated from tests/download/conftest.py)
# ============================================================================


@pytest.fixture
def mock_download_config():
    """Create a FanslyConfig for download testing using real factory.

    Note: Now uses FanslyConfigFactory instead of MagicMock.
    For tests that need database access, use the 'config' fixture instead.
    """
    return FanslyConfigFactory(
        download_path=Path("/test/download/path"),
        program_version="0.0.0-test",
    )


# Note: download_state fixture already uses real DownloadState
# It's defined in tests/fixtures/download/download_fixtures.py and imported via wildcard
# Keeping this alias here for backward compatibility
@pytest.fixture
def download_state():
    """Create a real DownloadState for testing."""
    state = DownloadState()
    state.creator_name = "test_creator"
    return state


@pytest.fixture
def mock_download_state():
    """Create a DownloadState for testing using real factory.

    Note: Now uses DownloadStateFactory instead of MagicMock.
    """
    creator_id = snowflake_id()
    return DownloadStateFactory(
        creator_id=creator_id,
        creator_name="test_user",
        messages_enabled=True,
        verbose_logs=False,
    )


@pytest.fixture
def test_downloads_dir(tmp_path):
    """Create a temporary downloads directory."""
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    return downloads_dir


# ============================================================================
# Performance Fixtures (consolidated from tests/performance/conftest.py)
# ============================================================================


@pytest.fixture(scope="session")
def performance_log_dir():
    """Create a directory for performance test logs."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture(scope="session")
def performance_threshold():
    """Define performance thresholds for tests."""
    return {
        "max_memory_mb": 512,
        "max_cpu_percent": 80,
        "max_response_time": 2.0,
        "max_download_time": 30.0,
    }


@pytest.fixture
def performance_tracker(performance_log_dir, request):
    """Fixture to track and log performance metrics."""

    @contextmanager
    def track_performance():
        start_time = perf_counter()
        process = psutil.Process()
        start_memory = process.memory_info().rss / 1024 / 1024

        metrics = {
            "start_time": time(),
            "start_memory": start_memory,
            "max_memory": start_memory,
            "max_cpu": 0.0,
            "duration": 0.0,
            "memory_change": 0.0,
            "end_memory": start_memory,
            "end_time": time(),
        }

        try:
            yield metrics
        finally:
            end_time = perf_counter()
            end_memory = process.memory_info().rss / 1024 / 1024

            metrics["duration"] = end_time - start_time
            metrics["memory_change"] = end_memory - start_memory
            metrics["end_memory"] = end_memory
            metrics["end_time"] = time()
            metrics["max_memory"] = max(metrics["max_memory"], end_memory)

    class PerformanceContextManager:
        def __init__(self, operation_name: str):
            self.operation_name = operation_name
            self.log_file = (
                performance_log_dir / f"{request.node.name}_{operation_name}.log"
            )
            self.metrics = None
            self.perf_context = None

        def __enter__(self):
            self.perf_context = track_performance()
            self.metrics = self.perf_context.__enter__()
            return self.metrics

        def __exit__(self, exc_type, exc_val, exc_tb):
            result = self.perf_context.__exit__(exc_type, exc_val, exc_tb)

            with self.log_file.open("a") as f:
                f.write(f"Performance metrics for {self.operation_name}:\n")
                f.write(f"Duration: {self.metrics['duration']:.3f} seconds\n")
                f.write(f"Memory change: {self.metrics['memory_change']:.2f} MB\n")
                f.write(f"Max memory: {self.metrics['max_memory']:.2f} MB\n")
                f.write(f"Max CPU: {self.metrics['max_cpu']:.1f}%\n")
                f.write("-" * 50 + "\n")

            return result

        # Add async context manager support
        async def __aenter__(self):
            return self.__enter__()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return self.__exit__(exc_type, exc_val, exc_tb)

    return PerformanceContextManager


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    "clean_model_data",
    "cleanup_tasks",
    "mock_download_state",
    "performance_log_dir",
    "performance_threshold",
    "performance_tracker",
    "sample_account",
    "sample_message",
    "sample_post",
    "setup_test_logging",
    "temp_config_dir",
    "temp_db_dir",
    "test_config",
    "test_downloads_dir",
    # Note: All factories and fixtures from tests.fixtures are automatically imported
    # via wildcard import and are available in all tests
]
