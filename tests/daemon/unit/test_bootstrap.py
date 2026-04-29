"""Unit tests for daemon.bootstrap — pre-sync WS handler attachment + backfill drain.

Covers the previously-uncovered branches:
- bootstrap_daemon_ws: WS-not-available early return + warning log
- drain_backfill: empty queue, happy-path drain, exception bookkeeping
- shutdown_bootstrap: ws_started=False early return
- _unused_import_keeper: re-export keeper

These tests exercise daemon.bootstrap directly and patch the re-exported
``daemon.bootstrap._handle_work_item`` seam (explicitly sanctioned by the
docstring at daemon/bootstrap.py:48-55: "Re-export internal runner
helpers so the test suite can patch them"). All other behavior runs real
code: ``DaemonBootstrap`` is the real dataclass, ``ActivitySimulator`` is
real, the asyncio.Queue is real.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from daemon.bootstrap import (
    DaemonBootstrap,
    _unused_import_keeper,
    bootstrap_daemon_ws,
    drain_backfill,
    shutdown_bootstrap,
)
from daemon.handlers import DownloadTimelineOnly, FullCreatorDownload
from daemon.simulator import ActivitySimulator


def _make_bootstrap(
    items: list | None = None, *, ws_started: bool = False
) -> DaemonBootstrap:
    """Construct a DaemonBootstrap with optional pre-seeded queue items."""
    queue: asyncio.Queue = asyncio.Queue()
    if items:
        for item in items:
            queue.put_nowait(item)
    return DaemonBootstrap(
        ws=None,
        queue=queue,
        simulator=ActivitySimulator(),
        baseline_consumed=set(),
        ws_started=ws_started,
    )


# ---------------------------------------------------------------------------
# bootstrap_daemon_ws — WS-not-available branch (lines 109-120)
# ---------------------------------------------------------------------------


class TestBootstrapDaemonWsNoWebSocket:
    """Lines 109-120: when API has no WS or it's disconnected, return inactive."""

    @pytest.mark.asyncio
    async def test_no_api_attached_returns_inactive(self, config, caplog):
        """config._api is None → DaemonBootstrap with ws=None, ws_started=False."""
        caplog.set_level(logging.WARNING)
        # config fixture has no _api attached by default.
        config._api = None

        result = await bootstrap_daemon_ws(config)

        assert result.ws is None
        assert result.ws_started is False
        assert isinstance(result.queue, asyncio.Queue)
        assert result.queue.empty()
        assert isinstance(result.simulator, ActivitySimulator)
        assert result.baseline_consumed == set()

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("API WebSocket not available" in m for m in warnings)

    @pytest.mark.asyncio
    async def test_ws_disconnected_returns_inactive(self, config, caplog):
        """ws.connected is False → same inactive return path."""
        caplog.set_level(logging.WARNING)

        # Build a minimal API stub that exposes _websocket_client but it's not connected.
        class _ApiStub:
            def __init__(self) -> None:
                self._websocket_client = type(
                    "_WsStub", (), {"connected": False, "session_id": None}
                )()

        config._api = _ApiStub()

        result = await bootstrap_daemon_ws(config)

        assert result.ws is None
        assert result.ws_started is False
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("API WebSocket not available" in m for m in warnings)


# ---------------------------------------------------------------------------
# drain_backfill — lines 164-196 (the actual draining loop + bookkeeping)
# ---------------------------------------------------------------------------


class TestDrainBackfillEmptyQueue:
    """Line 168-170: empty queue → debug log + return 0 without entering loop."""

    @pytest.mark.asyncio
    async def test_empty_queue_returns_zero_no_handler_calls(self, config, caplog):
        caplog.set_level(logging.DEBUG)
        bootstrap = _make_bootstrap(items=[])

        with patch(
            "daemon.bootstrap._handle_work_item", new_callable=AsyncMock
        ) as mock_handle:
            result = await drain_backfill(config, bootstrap)

        assert result == 0
        mock_handle.assert_not_awaited()
        debug_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "DEBUG"
        ]
        assert any("no delta items captured" in m for m in debug_messages)


class TestDrainBackfillHappyPath:
    """Lines 172-196: drain N items, all succeed, return N."""

    @pytest.mark.asyncio
    async def test_processes_all_items_and_returns_count(self, config, caplog):
        caplog.set_level(logging.INFO)
        items = [
            DownloadTimelineOnly(creator_id=100),
            DownloadTimelineOnly(creator_id=200),
            FullCreatorDownload(creator_id=300),
        ]
        bootstrap = _make_bootstrap(items=items)

        handled: list = []

        async def _capturing_handler(_config, item) -> None:
            handled.append(item)

        with patch(
            "daemon.bootstrap._handle_work_item", side_effect=_capturing_handler
        ) as mock_handle:
            result = await drain_backfill(config, bootstrap)

        assert result == 3
        assert mock_handle.await_count == 3
        # Items processed in FIFO order.
        assert handled == items
        # Queue is fully drained.
        assert bootstrap.queue.empty()

        info_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "INFO"
        ]
        assert any("draining 3 delta item" in m for m in info_messages)
        assert any(
            "backfill complete" in m and "3 processed" in m and "0 failed" in m
            for m in info_messages
        )


class TestDrainBackfillExceptionBookkeeping:
    """Lines 181-187: handler raises → counted as failure, error logged, loop continues."""

    @pytest.mark.asyncio
    async def test_failure_counted_separately_from_success(self, config, caplog):
        caplog.set_level(logging.ERROR)
        items = [
            DownloadTimelineOnly(creator_id=100),  # will succeed
            DownloadTimelineOnly(creator_id=200),  # will raise
            DownloadTimelineOnly(creator_id=300),  # will succeed
        ]
        bootstrap = _make_bootstrap(items=items)

        async def _flaky_handler(_config, item) -> None:
            if getattr(item, "creator_id", None) == 200:
                raise RuntimeError("simulated downstream failure")

        with patch("daemon.bootstrap._handle_work_item", side_effect=_flaky_handler):
            result = await drain_backfill(config, bootstrap)

        # Successful items counted; failed item NOT counted; loop did not abort.
        assert result == 2
        assert bootstrap.queue.empty()

        error_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "ERROR"
        ]
        assert any(
            "backfill error" in m
            and "DownloadTimelineOnly" in m
            and "simulated downstream failure" in m
            for m in error_messages
        )

    @pytest.mark.asyncio
    async def test_all_fail_returns_zero_no_partial_count(self, config, caplog):
        caplog.set_level(logging.INFO)
        items = [DownloadTimelineOnly(creator_id=cid) for cid in (10, 20, 30)]
        bootstrap = _make_bootstrap(items=items)

        async def _always_raises(_config, _item) -> None:
            raise ValueError("boom")

        with patch("daemon.bootstrap._handle_work_item", side_effect=_always_raises):
            result = await drain_backfill(config, bootstrap)

        assert result == 0
        info_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "INFO"
        ]
        assert any(
            "backfill complete" in m and "0 processed" in m and "3 failed" in m
            for m in info_messages
        )


# ---------------------------------------------------------------------------
# shutdown_bootstrap — line 211-212 (early return when no WS attached)
# ---------------------------------------------------------------------------


class TestShutdownBootstrapNoWebSocket:
    """Line 211-212: ws_started=False or ws=None → return without touching anything."""

    @pytest.mark.asyncio
    async def test_ws_started_false_is_noop(self):
        bootstrap = _make_bootstrap(ws_started=False)
        # Must not raise even though bootstrap.ws is None.
        await shutdown_bootstrap(bootstrap)

    @pytest.mark.asyncio
    async def test_ws_none_is_noop_even_when_started_flag_true(self):
        # Pathological state: ws_started True but ws is None (shouldn't happen
        # in production but the guard handles it). Verifies the OR short-circuit.
        bootstrap = DaemonBootstrap(
            ws=None,
            queue=asyncio.Queue(),
            simulator=ActivitySimulator(),
            baseline_consumed=set(),
            ws_started=True,
        )
        await shutdown_bootstrap(bootstrap)


# ---------------------------------------------------------------------------
# _unused_import_keeper — line 219-221 (re-export keeper)
# ---------------------------------------------------------------------------


def test_unused_import_keeper_returns_handle_work_item():
    """Line 221: returns the re-exported _handle_work_item callable."""
    from daemon.runner import _handle_work_item as runner_handle

    assert _unused_import_keeper() is runner_handle
