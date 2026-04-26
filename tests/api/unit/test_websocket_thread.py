"""Lifecycle and cross-thread dispatch tests for FanslyWebSocket.

These tests cover the WS thread refactor (api/websocket.py: ``start_in_thread``,
``stop_thread``, ``_thread_main``, ``_dispatch_event``):

* thread spin-up surfaces ``_thread_ready`` only after the loop is alive
* startup exceptions inside the thread are re-raised on the caller
* stop_thread joins cleanly within the configured timeout
* double-start / double-stop are idempotent (warning + no-op)
* registered handlers run on the **main** loop, not the WS thread loop
* same-loop short-circuit in ``_dispatch_event`` (no thread boundary path)

The tests deliberately mock ``_maintain_connection`` so the WS thread loop
runs a controllable coroutine — no real network or websockets handshake.
"""

from __future__ import annotations

import asyncio

import pytest

from api.websocket import FanslyWebSocket


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ws() -> FanslyWebSocket:
    """Build a FanslyWebSocket suitable for thread-lifecycle tests."""
    return FanslyWebSocket(
        token="test-token",  # noqa: S106 — fake token for test fixture
        user_agent="test-ua",
    )


# ---------------------------------------------------------------------------
# start_in_thread / stop_thread lifecycle
# ---------------------------------------------------------------------------


class TestThreadLifecycle:
    """Spin-up, shutdown, and re-entry guarantees of the WS thread."""

    @pytest.mark.asyncio
    async def test_start_then_stop_clean_lifecycle(self, monkeypatch):
        """start_in_thread spins up; stop_thread joins within timeout."""
        ws = _make_ws()

        async def maintain():
            # threading.Event.wait() is blocking; to_thread makes it awaitable
            # without an asyncio.sleep poll loop.
            await asyncio.to_thread(ws._stop_event.wait)

        monkeypatch.setattr(ws, "_maintain_connection", maintain)

        ws.start_in_thread()
        try:
            assert ws._ws_thread is not None
            assert ws._ws_thread.is_alive()
            assert ws._ws_loop is not None
            assert ws._main_loop is asyncio.get_running_loop()
        finally:
            await ws.stop_thread()

        assert ws._ws_thread is None
        # _ws_loop is set None inside _thread_main's finally — give a moment
        # for the thread's cleanup to publish that write.
        for _ in range(20):
            if ws._ws_loop is None:
                break
            await asyncio.sleep(0.01)
        assert ws._ws_loop is None

    @pytest.mark.asyncio
    async def test_double_start_is_no_op(self, monkeypatch):
        """A second start_in_thread call warns and returns the same thread."""
        ws = _make_ws()

        async def maintain():
            await asyncio.to_thread(ws._stop_event.wait)

        monkeypatch.setattr(ws, "_maintain_connection", maintain)

        ws.start_in_thread()
        try:
            first = ws._ws_thread
            assert first is not None
            ws.start_in_thread()  # second call — should warn, no-op
            assert ws._ws_thread is first
        finally:
            await ws.stop_thread()

    @pytest.mark.asyncio
    async def test_double_stop_is_safe(self, monkeypatch):
        """Calling stop_thread twice does not raise."""
        ws = _make_ws()

        async def maintain():
            await asyncio.to_thread(ws._stop_event.wait)

        monkeypatch.setattr(ws, "_maintain_connection", maintain)

        ws.start_in_thread()
        await ws.stop_thread()
        # Second stop on an already-joined thread should warn and return
        await ws.stop_thread()
        assert ws._ws_thread is None

    @pytest.mark.asyncio
    async def test_stop_thread_before_start(self):
        """stop_thread() with no live thread → warning, returns cleanly."""
        ws = _make_ws()
        assert ws._ws_thread is None
        await ws.stop_thread()
        assert ws._ws_thread is None

    @pytest.mark.asyncio
    async def test_thread_exception_re_raises_on_starter(self, monkeypatch):
        """If _maintain_connection raises during startup, start_in_thread re-raises."""
        ws = _make_ws()

        async def boom():
            raise ValueError("simulated thread crash")

        monkeypatch.setattr(ws, "_maintain_connection", boom)

        with pytest.raises(RuntimeError, match="WebSocket thread failed"):
            ws.start_in_thread()


# ---------------------------------------------------------------------------
# _dispatch_event cross-thread / same-loop semantics
# ---------------------------------------------------------------------------


class TestDispatchEvent:
    """Verify handler execution lands on the main loop (not the WS thread)."""

    @pytest.mark.asyncio
    async def test_dispatch_async_handler_runs_on_main_loop(self, monkeypatch):
        """Async handler scheduled via run_coroutine_threadsafe targets main loop."""
        ws = _make_ws()
        main_loop = asyncio.get_running_loop()
        handler_loop: list[asyncio.AbstractEventLoop] = []
        handler_done = asyncio.Event()

        async def handler(_event):
            handler_loop.append(asyncio.get_running_loop())
            handler_done.set()

        ws.register_handler(ws.MSG_SERVICE_EVENT, handler)

        async def maintain():
            # Simulate one inbound service event from the WS thread, then idle
            await ws._dispatch_event(handler, {"serviceId": 1, "event": "test"})
            await asyncio.to_thread(ws._stop_event.wait)

        monkeypatch.setattr(ws, "_maintain_connection", maintain)

        ws.start_in_thread()
        try:
            # Wait up to 1s for the cross-thread dispatch to land on main loop
            await asyncio.wait_for(handler_done.wait(), timeout=1.0)
        finally:
            await ws.stop_thread()

        assert len(handler_loop) == 1
        assert handler_loop[0] is main_loop

    @pytest.mark.asyncio
    async def test_dispatch_sync_handler_runs_on_main_loop(self, monkeypatch):
        """Sync handler scheduled via call_soon_threadsafe targets main loop."""
        ws = _make_ws()
        main_loop = asyncio.get_running_loop()
        handler_loop: list[asyncio.AbstractEventLoop] = []
        handler_called = asyncio.Event()

        def handler(_event):
            handler_loop.append(asyncio.get_running_loop())
            # Schedule the event-set on the loop (we're already on it)
            asyncio.get_running_loop().call_soon(handler_called.set)

        ws.register_handler(ws.MSG_SERVICE_EVENT, handler)

        async def maintain():
            await ws._dispatch_event(handler, {"serviceId": 1, "event": "test"})
            await asyncio.to_thread(ws._stop_event.wait)

        monkeypatch.setattr(ws, "_maintain_connection", maintain)

        ws.start_in_thread()
        try:
            await asyncio.wait_for(handler_called.wait(), timeout=1.0)
        finally:
            await ws.stop_thread()

        assert len(handler_loop) == 1
        assert handler_loop[0] is main_loop

    @pytest.mark.asyncio
    async def test_dispatch_same_loop_short_circuits_to_inline_call(self):
        """When _main_loop is the running loop, dispatch invokes inline.

        Verifies the test-mode fast path: if there's no thread boundary
        (e.g., test calling _handle_message directly), we skip the
        threadsafe scheduling and just await the handler in-place.
        """
        ws = _make_ws()
        ws._main_loop = asyncio.get_running_loop()

        received: list[dict] = []

        async def handler(event):
            received.append(event)

        await ws._dispatch_event(handler, {"k": "v"})
        assert received == [{"k": "v"}]

    @pytest.mark.asyncio
    async def test_dispatch_no_main_loop_invokes_inline(self):
        """When _main_loop is None, dispatch invokes inline (no boundary)."""
        ws = _make_ws()
        assert ws._main_loop is None

        received: list[dict] = []

        def handler(event):
            received.append(event)

        await ws._dispatch_event(handler, {"k": "v"})
        assert received == [{"k": "v"}]
