"""Integration tests for daemon.runner.run_daemon.

Architecture:
  - Real EntityStore via ``entity_store`` fixture (PostgreSQL isolation)
  - FakeWS stub injected via ``ws_factory`` kwarg to bypass real WebSocket
  - run_daemon terminated via asyncio.wait_for with short timeouts

Shared stubs (FakeWS, _fake_ws_factory, fake_ws, config_wired) are
provided by tests/daemon/conftest.py.

These tests exercise the FULL daemon loop lifecycle -- startup through
clean shutdown -- rather than individual component wiring.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from daemon.runner import run_daemon
from errors import DAEMON_UNRECOVERABLE, EXIT_SUCCESS, DaemonUnrecoverableError
from tests.daemon.conftest import _fake_ws_factory
from tests.fixtures.utils.test_isolation import snowflake_id


# ---------------------------------------------------------------------------
# Test: SIGINT / cancel -> all tasks stopped, ws closed
# ---------------------------------------------------------------------------


class TestCleanShutdown:
    """Daemon shuts down cleanly on SIGINT."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_sigint_stops_daemon_and_closes_ws(
        self, config_wired, entity_store, fake_ws
    ):
        """SIGINT causes run_daemon to stop all tasks and stop the WS.

        Uses the stop_event injection instead of patching asyncio's
        global get_running_loop — the patch approach leaked the mock
        loop across modules (any caller of asyncio.get_running_loop got
        the MagicMock) and broke code that legitimately needs real
        loop.time() / loop.call_soon().
        """
        stop_event = asyncio.Event()

        daemon_task = asyncio.create_task(
            run_daemon(
                config_wired,
                ws_factory=_fake_ws_factory(fake_ws),
                stop_event=stop_event,
            )
        )

        # Allow daemon to start
        await asyncio.sleep(0.05)

        # Simulate SIGINT — same effect as the real _sigint_handler
        stop_event.set()

        try:
            result = await asyncio.wait_for(daemon_task, timeout=5.0)
        except TimeoutError:
            daemon_task.cancel()
            await asyncio.gather(daemon_task, return_exceptions=True)
            result = None

        assert fake_ws.started, "WebSocket should have been started"
        assert fake_ws.stopped, "WebSocket should have been stopped on shutdown"
        assert result == EXIT_SUCCESS, f"Expected EXIT_SUCCESS (0), got {result!r}"

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_run_daemon_returns_exit_success_on_sigint(
        self, config_wired, entity_store, fake_ws
    ):
        """run_daemon returns EXIT_SUCCESS (0) on clean SIGINT shutdown."""
        stop_event = asyncio.Event()

        daemon_task = asyncio.create_task(
            run_daemon(
                config_wired,
                ws_factory=_fake_ws_factory(fake_ws),
                stop_event=stop_event,
            )
        )

        await asyncio.sleep(0.05)
        stop_event.set()

        try:
            exit_code = await asyncio.wait_for(daemon_task, timeout=5.0)
        except TimeoutError:
            daemon_task.cancel()
            await asyncio.gather(daemon_task, return_exceptions=True)
            exit_code = None

        assert exit_code == EXIT_SUCCESS, (
            f"Expected EXIT_SUCCESS ({EXIT_SUCCESS}), got {exit_code!r}"
        )


# ---------------------------------------------------------------------------
# Test: worker drains queue before shutdown completes
# ---------------------------------------------------------------------------


class TestWorkerDrainsOnShutdown:
    """Worker processes all in-flight items before shutdown completes."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(15)
    async def test_worker_drains_3_items_on_sigint(
        self, config_wired, entity_store, saved_account, fake_ws
    ):
        """Put 3 items in the queue, send SIGINT — all 3 are processed.

        The daemon's finally block cancels pollers first, then waits up to
        30 seconds for the worker to drain. We pre-load the queue before
        shutdown and verify all items were processed.
        """
        processed_ids: list[int] = []
        stop_event = asyncio.Event()

        creator_ids = [saved_account.id, snowflake_id(), snowflake_id()]

        async def _spy_handle_work_item(config, item):
            if hasattr(item, "creator_id"):
                processed_ids.append(item.creator_id)

        with (
            patch("daemon.runner._handle_work_item", side_effect=_spy_handle_work_item),
            patch("daemon.runner.mark_creator_processed", new=AsyncMock()),
            patch("daemon.runner._refresh_following", new=AsyncMock()),
        ):
            daemon_task = asyncio.create_task(
                run_daemon(
                    config_wired,
                    ws_factory=_fake_ws_factory(fake_ws),
                    stop_event=stop_event,
                )
            )

            # Wait for daemon to start
            await asyncio.sleep(0.05)

            # Fire 3 WS events (subscription confirmed for 3 creators) —
            # each enqueues a work item on the daemon's internal queue.
            for cid in creator_ids:
                await fake_ws.fire(
                    service_id=15,
                    event_type=5,
                    inner={"subscription": {"accountId": cid, "status": 3}},
                )

            # Short delay to let items land in the queue
            await asyncio.sleep(0.05)

            # Trigger shutdown directly via the injected stop_event
            stop_event.set()

            try:
                await asyncio.wait_for(daemon_task, timeout=10.0)
            except TimeoutError:
                daemon_task.cancel()
                await asyncio.gather(daemon_task, return_exceptions=True)

        # All 3 creator IDs should have been processed
        for cid in creator_ids:
            assert cid in processed_ids, (
                f"Creator {cid} was not processed before shutdown; "
                f"processed: {processed_ids}"
            )


# ---------------------------------------------------------------------------
# Test: DaemonUnrecoverableError -> run_daemon returns DAEMON_UNRECOVERABLE
# ---------------------------------------------------------------------------


class TestUnrecoverableExitCode:
    """DaemonUnrecoverableError causes run_daemon to return DAEMON_UNRECOVERABLE."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_unrecoverable_error_returns_daemon_unrecoverable(
        self, config_wired, entity_store, fake_ws
    ):
        """When DaemonUnrecoverableError propagates, run_daemon returns -8.

        We patch _timeline_poll_loop to raise DaemonUnrecoverableError immediately,
        bypassing the poll-interval wait. This simulates an auth failure (e.g. HTTP
        401 tripping the ErrorBudget) without waiting for the real poll interval.
        """

        async def _raise_unrecoverable(*args, **kwargs):
            raise DaemonUnrecoverableError("Simulated fatal auth failure")

        with patch(
            "daemon.runner._timeline_poll_loop", side_effect=_raise_unrecoverable
        ):
            daemon_task = asyncio.create_task(
                run_daemon(config_wired, ws_factory=_fake_ws_factory(fake_ws))
            )

            try:
                exit_code = await asyncio.wait_for(daemon_task, timeout=8.0)
            except TimeoutError:
                daemon_task.cancel()
                await asyncio.gather(daemon_task, return_exceptions=True)
                pytest.fail(
                    "run_daemon timed out — DaemonUnrecoverableError did not "
                    "propagate to the top-level gather"
                )
                return

        assert exit_code == DAEMON_UNRECOVERABLE, (
            f"Expected DAEMON_UNRECOVERABLE ({DAEMON_UNRECOVERABLE}), got {exit_code!r}"
        )
        assert fake_ws.stopped, "WebSocket should be stopped even on unrecoverable exit"
