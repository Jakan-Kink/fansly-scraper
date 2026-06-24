"""Integration tests for daemon.runner.run_daemon.

Architecture:
  - Real EntityStore via ``entity_store`` fixture (PostgreSQL isolation)
  - FakeWS stub injected via ``ws_factory`` kwarg to bypass real WebSocket
  - run_daemon terminated via asyncio.wait_for with short timeouts

The ``fake_ws`` and ``config_wired`` pytest fixtures are provided by
``tests/daemon/conftest.py``; ``FakeWS`` and ``make_fake_ws_factory``
are imported directly from ``tests.fixtures.api``.

These tests exercise the FULL daemon loop lifecycle -- startup through
clean shutdown -- rather than individual component wiring.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from loguru import logger as _logger

from daemon.runner import run_daemon
from errors import DAEMON_UNRECOVERABLE, EXIT_SUCCESS, DaemonUnrecoverableError
from tests.fixtures.api import (
    dump_ws_server_state,
    make_fake_ws_factory,
    make_ws_factory_for,
)
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
                ws_factory=make_fake_ws_factory(fake_ws),
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
                ws_factory=make_fake_ws_factory(fake_ws),
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
    @pytest.mark.timeout(20)
    async def test_worker_drains_3_items_on_sigint(
        self, config_wired, entity_store, saved_account, ws_server
    ):
        """Put 3 items in the queue, send SIGINT — all 3 are processed.

        Drives real ``(15, 5) status=3`` subscription-confirmed events
        through the scripted-responder WS server (analogous to respx for
        HTTP). The full WS connect / triple-JSON decode / dispatch path
        runs against an in-process server; no synthetic handler call.
        """
        processed_ids: list[int] = []
        stop_event = asyncio.Event()
        # Capture WS subprocess + runner logs for diagnosis if auth times out.
        captured_logs: list[str] = []
        sink_id = _logger.add(lambda m: captured_logs.append(str(m)), level="DEBUG")

        creator_ids = [saved_account.id, snowflake_id(), snowflake_id()]

        async def _spy_handle_work_item(config, item):
            if hasattr(item, "creator_id"):
                processed_ids.append(item.creator_id)

        try:
            with (
                patch(
                    "daemon.runner._handle_work_item", side_effect=_spy_handle_work_item
                ),
                patch("daemon.runner.mark_creator_processed", new=AsyncMock()),
                patch("daemon.runner._refresh_following", new=AsyncMock()),
            ):
                daemon_task = asyncio.create_task(
                    run_daemon(
                        config_wired,
                        ws_factory=make_ws_factory_for(
                            ws_server.base_url, enable_logging=True
                        ),
                        stop_event=stop_event,
                    )
                )

                # Wait for WS to authenticate against the scripted server
                try:
                    await ws_server.wait_for_auth(timeout=10.0)
                except TimeoutError:
                    dump_ws_server_state(
                        ws_server,
                        daemon_task=daemon_task,
                        captured_logs=captured_logs,
                    )
                    stop_event.set()
                    daemon_task.cancel()
                    await asyncio.gather(daemon_task, return_exceptions=True)
                    raise

                # Push 3 real (15, 5) status=3 subscription-confirmed events
                # over the wire. Each one becomes a FullCreatorDownload work
                # item on the daemon's internal queue after triple-JSON decode
                # and dispatch.
                for cid in creator_ids:
                    ws_server.push_subscription_event(
                        subscription_id=str(snowflake_id()),
                        history_id=str(snowflake_id()),
                        subscriber_id=str(saved_account.id),
                        creator_id=str(cid),
                        status=3,
                        price_mills=10000,
                    )

                # Short delay to let items traverse the wire + worker drain start
                await asyncio.sleep(0.2)

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
        finally:
            _logger.remove(sink_id)


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
                run_daemon(
                    config_wired,
                    ws_factory=make_fake_ws_factory(fake_ws),
                )
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
