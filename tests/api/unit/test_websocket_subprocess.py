"""Tests for api/websocket_subprocess.py."""

import asyncio
import contextlib
import multiprocessing as mp
import queue
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.websocket import FanslyWebSocket
from api.websocket_subprocess import (
    FanslyWebSocketProxy,
    _run_ws_subprocess,
    _setup_child_logging,
    get_websocket_class,
)
from tests.fixtures.api import (
    build_mock_ws_class,
    make_proxy,
    spawn_ctx_with_mock_process,
)
from tests.fixtures.utils import close_qs


# ── _setup_child_logging ───────────────────────────────────────────────


class TestSetupChildLogging:
    """Lines 93-94: _setup_child_logging configures a single loguru sink."""

    def test_remove_then_add_with_path(self, tmp_path):
        log_path = str(tmp_path / "child.log")
        with patch("api.websocket_subprocess.logger") as mock_logger:
            _setup_child_logging(log_path)

        mock_logger.remove.assert_called_once_with()
        mock_logger.add.assert_called_once()
        args, kwargs = mock_logger.add.call_args
        assert args[0] == log_path
        assert kwargs["rotation"] == "50 MB"
        assert kwargs["retention"] == 5
        assert kwargs["enqueue"] is False
        assert kwargs["backtrace"] is True
        assert kwargs["diagnose"] is True


# ── get_websocket_class ────────────────────────────────────────────────


class TestGetWebsocketClass:
    """Line 582: factory returns FanslyWebSocketProxy when use_subprocess=True."""

    def test_default_returns_in_thread_class(self):
        assert get_websocket_class() is FanslyWebSocket

    def test_explicit_false_returns_in_thread_class(self):
        assert get_websocket_class(False) is FanslyWebSocket

    def test_true_returns_proxy_class(self):
        assert get_websocket_class(True) is FanslyWebSocketProxy


# ── FanslyWebSocketProxy.__init__ ──────────────────────────────────────


class TestProxyInit:
    """Lines 316-340: constructor stores all args and seeds runtime fields."""

    def test_minimal_construction(self):
        proxy = FanslyWebSocketProxy(token="tok", user_agent="ua")  # noqa: S106
        assert proxy.token == "tok"
        assert proxy.user_agent == "ua"
        assert proxy.cookies == {}
        assert proxy.enable_logging is False
        assert proxy.on_unauthorized is None
        assert proxy.on_rate_limited is None
        assert proxy.monitor_events is False
        assert proxy.base_url is None
        assert proxy.http_client is None

    def test_runtime_state_defaults(self):
        proxy = FanslyWebSocketProxy(token="t", user_agent="u")  # noqa: S106
        assert proxy.connected is False
        assert proxy.session_id is None
        assert proxy.websocket_session_id is None
        assert proxy.account_id is None
        assert proxy._event_handlers == {}
        assert proxy._proc is None
        assert proxy._cmd_q is None
        assert proxy._evt_q is None
        assert proxy._drain_task is None
        assert proxy._main_loop is None

    def test_cookies_dict_is_copied(self):
        original = {"k": "v"}
        proxy = FanslyWebSocketProxy(token="t", user_agent="u", cookies=original)  # noqa: S106
        assert proxy.cookies == {"k": "v"}
        assert proxy.cookies is not original

    def test_full_construction(self):
        cb1 = MagicMock()
        cb2 = MagicMock()
        client = MagicMock(name="httpx_client")
        proxy = FanslyWebSocketProxy(
            token="tk",  # noqa: S106
            user_agent="UA",
            cookies={"a": "1"},
            enable_logging=True,
            on_unauthorized=cb1,
            on_rate_limited=cb2,
            monitor_events=True,
            base_url="https://example.invalid",
            http_client=client,
        )
        assert proxy.enable_logging is True
        assert proxy.on_unauthorized is cb1
        assert proxy.on_rate_limited is cb2
        assert proxy.monitor_events is True
        assert proxy.base_url == "https://example.invalid"
        assert proxy.http_client is client


# ── register_handler ───────────────────────────────────────────────────


class TestRegisterHandler:
    """Lines 358-361: stores handler, sends 'register' cmd if subprocess up."""

    def test_new_handler_without_subprocess(self):
        proxy = make_proxy()
        handler = MagicMock()
        proxy.register_handler(42, handler)
        assert proxy._event_handlers[42] is handler

    def test_new_handler_with_subprocess_sends_register(self):
        proxy = make_proxy()
        proxy._cmd_q = mp.Queue()
        try:
            proxy.register_handler(42, MagicMock())
            sent = proxy._cmd_q.get(timeout=1)
            assert sent == {"cmd": "register", "type": 42}
        finally:
            close_qs(proxy._cmd_q)

    def test_replacing_existing_handler_does_not_resend_register(self):
        proxy = make_proxy()
        proxy._cmd_q = mp.Queue()
        try:
            proxy._event_handlers[42] = MagicMock()  # already known
            new_handler = MagicMock()
            proxy.register_handler(42, new_handler)
            assert proxy._event_handlers[42] is new_handler
            with pytest.raises(queue.Empty):
                proxy._cmd_q.get(timeout=0.1)
        finally:
            close_qs(proxy._cmd_q)


# ── _update_status ─────────────────────────────────────────────────────


class TestUpdateStatus:
    """Lines 522-534: mirror state, push cookie snapshot on disconnect."""

    def test_initial_status_update(self):
        proxy = make_proxy()
        proxy._update_status(
            {
                "kind": "status",
                "connected": True,
                "session_id": "sess1",
                "websocket_session_id": "ws1",
                "account_id": "acct1",
            }
        )
        assert proxy.connected is True
        assert proxy.session_id == "sess1"
        assert proxy.websocket_session_id == "ws1"
        assert proxy.account_id == "acct1"

    def test_disconnect_transition_pushes_cookies(self):
        proxy = make_proxy(cookies={"k": "v"})
        proxy._cmd_q = mp.Queue()
        try:
            proxy.connected = True
            proxy._update_status({"connected": False})
            assert proxy.connected is False
            sent = proxy._cmd_q.get(timeout=1)
            assert sent == {"cmd": "cookies", "data": {"k": "v"}}
        finally:
            close_qs(proxy._cmd_q)

    def test_connect_transition_does_not_push_cookies(self):
        proxy = make_proxy()
        proxy._cmd_q = mp.Queue()
        try:
            proxy.connected = False
            proxy._update_status({"connected": True})
            with pytest.raises(queue.Empty):
                proxy._cmd_q.get(timeout=0.1)
        finally:
            close_qs(proxy._cmd_q)

    def test_missing_keys_default_to_none(self):
        proxy = make_proxy()
        proxy._update_status({})
        assert proxy.connected is False
        assert proxy.session_id is None
        assert proxy.websocket_session_id is None
        assert proxy.account_id is None


# ── _absorb_set_cookie ─────────────────────────────────────────────────


class TestAbsorbSetCookie:
    """Lines 536-545: write into proxy.cookies (no http_client) or the jar."""

    def test_writes_to_cookies_dict_when_no_http_client(self):
        proxy = make_proxy()
        proxy.http_client = None
        proxy._absorb_set_cookie({"name": "session", "value": "xyz"})
        assert proxy.cookies["session"] == "xyz"

    def test_writes_to_http_client_jar_when_present(self):
        proxy = make_proxy()
        client = MagicMock(name="httpx_client")
        proxy.http_client = client
        proxy._absorb_set_cookie(
            {
                "name": "session",
                "value": "xyz",
                "domain": "fansly.com",
                "path": "/",
            }
        )
        client.cookies.set.assert_called_once_with(
            "session", "xyz", domain="fansly.com", path="/"
        )

    def test_uses_default_domain_and_path_when_missing(self):
        proxy = make_proxy()
        client = MagicMock(name="httpx_client")
        proxy.http_client = client
        proxy._absorb_set_cookie({"name": "n", "value": "v"})
        client.cookies.set.assert_called_once_with(
            "n", "v", domain="fansly.com", path="/"
        )


# ── _snapshot_cookies ──────────────────────────────────────────────────


class TestSnapshotCookies:
    """Lines 547-550: dict copy when no client; jar items when client present."""

    def test_returns_copy_of_cookies_when_no_http_client(self):
        proxy = make_proxy(cookies={"a": "1", "b": "2"})
        snap = proxy._snapshot_cookies()
        assert snap == {"a": "1", "b": "2"}
        snap["c"] = "3"
        assert "c" not in proxy.cookies

    def test_extracts_from_http_client_jar(self):
        proxy = make_proxy()
        client = MagicMock(name="httpx_client")
        client.cookies.jar = [
            SimpleNamespace(name="x", value="1"),
            SimpleNamespace(name="y", value="2"),
        ]
        proxy.http_client = client
        assert proxy._snapshot_cookies() == {"x": "1", "y": "2"}


# ── _send_cmd ──────────────────────────────────────────────────────────


class TestSendCmd:
    """Lines 552-556: no-op when queue is None; suppresses pipe errors."""

    def test_noop_when_queue_is_none(self):
        proxy = make_proxy()
        proxy._send_cmd({"cmd": "anything"})  # must not raise

    def test_puts_on_queue_when_set(self):
        proxy = make_proxy()
        proxy._cmd_q = mp.Queue()
        try:
            proxy._send_cmd({"cmd": "x", "type": 1})
            assert proxy._cmd_q.get(timeout=1) == {"cmd": "x", "type": 1}
        finally:
            close_qs(proxy._cmd_q)

    def test_suppresses_broken_pipe(self):
        proxy = make_proxy()
        fake_q = MagicMock()
        fake_q.put.side_effect = BrokenPipeError("pipe closed")
        proxy._cmd_q = fake_q
        proxy._send_cmd({"cmd": "x"})  # must not raise

    def test_suppresses_os_error(self):
        proxy = make_proxy()
        fake_q = MagicMock()
        fake_q.put.side_effect = OSError("queue dead")
        proxy._cmd_q = fake_q
        proxy._send_cmd({"cmd": "x"})  # must not raise


# ── _dispatch_event ────────────────────────────────────────────────────


class TestDispatchEvent:
    """Lines 505-512: handler lookup + sync vs async dispatch."""

    async def test_no_handler_registered_is_noop(self):
        proxy = make_proxy()
        await proxy._dispatch_event(99, {"any": "data"})  # must not raise

    async def test_sync_handler_invoked(self):
        proxy = make_proxy()
        called = []
        proxy._event_handlers[1] = called.append
        await proxy._dispatch_event(1, "payload")
        assert called == ["payload"]

    async def test_async_handler_awaited(self):
        proxy = make_proxy()
        seen = []

        async def handler(data):
            seen.append(data)

        proxy._event_handlers[1] = handler
        await proxy._dispatch_event(1, {"x": 1})
        assert seen == [{"x": 1}]


# ── _dispatch_callback ─────────────────────────────────────────────────


class TestDispatchCallback:
    """Lines 514-520: sync vs async callback dispatch; None is noop."""

    async def test_none_callback_is_noop(self):
        proxy = make_proxy()
        await proxy._dispatch_callback(None)  # must not raise

    async def test_sync_callback_invoked(self):
        proxy = make_proxy()
        called = []
        await proxy._dispatch_callback(lambda: called.append("yes"))
        assert called == ["yes"]

    async def test_async_callback_awaited(self):
        proxy = make_proxy()
        seen = []

        async def cb():
            seen.append("yes")

        await proxy._dispatch_callback(cb)
        assert seen == ["yes"]


# ── send_message ───────────────────────────────────────────────────────


class TestSendMessage:
    """Lines 455-463: raises if subprocess not running; routes via _send_cmd."""

    async def test_raises_when_proc_is_none(self):
        proxy = make_proxy()
        with pytest.raises(RuntimeError, match="WS subprocess not running"):
            await proxy.send_message(1, {"hello": "world"})

    async def test_raises_when_proc_is_dead(self):
        proxy = make_proxy()
        dead = MagicMock()
        dead.is_alive.return_value = False
        proxy._proc = dead
        with pytest.raises(RuntimeError, match="WS subprocess not running"):
            await proxy.send_message(1, {})

    async def test_sends_cmd_when_running(self):
        proxy = make_proxy()
        alive = MagicMock()
        alive.is_alive.return_value = True
        proxy._proc = alive
        proxy._cmd_q = mp.Queue()
        try:
            await proxy.send_message(7, {"foo": "bar"})
            sent = proxy._cmd_q.get(timeout=1)
            assert sent == {"cmd": "send", "type": 7, "data": {"foo": "bar"}}
        finally:
            close_qs(proxy._cmd_q)


# ── start_in_thread ────────────────────────────────────────────────────


class TestStartInThread:
    """Lines 367-425: spawn subprocess, create queues, kick off drain task."""

    async def test_noop_when_already_running(self):
        proxy = make_proxy()
        existing = MagicMock()
        existing.is_alive.return_value = True
        proxy._proc = existing
        proxy.start_in_thread()
        # Same process reference is kept; no new spawn happens.
        assert proxy._proc is existing
        existing.start.assert_not_called()

    async def test_spawn_path_creates_queues_and_drain(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # logs/ dir is created in cwd
        proxy = make_proxy()
        mock_proc = MagicMock(name="ws_proc")
        mock_proc.is_alive.return_value = True

        with patch(
            "api.websocket_subprocess.mp.get_context",
            return_value=spawn_ctx_with_mock_process(mock_proc),
        ):
            proxy.start_in_thread()

        try:
            assert proxy._proc is mock_proc
            mock_proc.start.assert_called_once()
            assert proxy._cmd_q is not None
            assert proxy._evt_q is not None
            assert proxy._drain_task is not None
            assert (tmp_path / "logs").is_dir()
        finally:
            proxy._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await proxy._drain_task
            close_qs(proxy._cmd_q, proxy._evt_q)

    async def test_already_running_dead_process_respawns(self, tmp_path, monkeypatch):
        """A previous ``_proc`` that is no longer alive is replaced."""
        monkeypatch.chdir(tmp_path)
        proxy = make_proxy()
        dead = MagicMock(name="dead_proc")
        dead.is_alive.return_value = False
        proxy._proc = dead

        new_proc = MagicMock(name="new_proc")
        new_proc.is_alive.return_value = True

        with patch(
            "api.websocket_subprocess.mp.get_context",
            return_value=spawn_ctx_with_mock_process(new_proc),
        ):
            proxy.start_in_thread()

        try:
            assert proxy._proc is new_proc
        finally:
            proxy._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await proxy._drain_task
            close_qs(proxy._cmd_q, proxy._evt_q)


# ── stop_thread ────────────────────────────────────────────────────────


class TestStopThread:
    """Lines 427-453: send stop, cancel drain task, join, terminate fallback."""

    async def test_noop_when_proc_is_none(self):
        proxy = make_proxy()
        await proxy.stop_thread()  # must not raise

    async def test_clean_shutdown_sends_stop_and_joins(self):
        proxy = make_proxy()
        proc = MagicMock()
        proc.is_alive.return_value = False  # exited cleanly after join
        proxy._proc = proc
        cmd_q = mp.Queue()
        evt_q = mp.Queue()
        proxy._cmd_q = cmd_q
        proxy._evt_q = evt_q
        proxy.connected = True

        async def _idle_drain():
            await asyncio.sleep(60)

        proxy._drain_task = asyncio.create_task(_idle_drain())

        try:
            await proxy.stop_thread(join_timeout=0.1)

            assert proxy._proc is None
            assert proxy._cmd_q is None
            assert proxy._evt_q is None
            assert proxy.connected is False
            assert proxy._drain_task is None
            proc.join.assert_called()
            proc.terminate.assert_not_called()
        finally:
            close_qs(cmd_q, evt_q)

    async def test_terminate_fallback_when_join_overruns(self):
        proxy = make_proxy()
        proc = MagicMock()
        # First is_alive (in stop_thread before join) → True; second (after the
        # initial join) → False after terminate.
        proc.is_alive.side_effect = [True, False]
        proxy._proc = proc
        cmd_q = mp.Queue()
        evt_q = mp.Queue()
        proxy._cmd_q = cmd_q
        proxy._evt_q = evt_q

        try:
            await proxy.stop_thread(join_timeout=0.05)

            proc.terminate.assert_called_once()
            # Two joins: initial timed join, then post-terminate 1.0s join.
            assert proc.join.call_count == 2
        finally:
            close_qs(cmd_q, evt_q)

    async def test_drain_task_none_path(self):
        proxy = make_proxy()
        proc = MagicMock()
        proc.is_alive.return_value = False
        proxy._proc = proc
        cmd_q = mp.Queue()
        evt_q = mp.Queue()
        proxy._cmd_q = cmd_q
        proxy._evt_q = evt_q
        proxy._drain_task = None  # explicitly no drain

        try:
            await proxy.stop_thread(join_timeout=0.05)
            assert proxy._proc is None
        finally:
            close_qs(cmd_q, evt_q)


# ── _drain_evt_q ───────────────────────────────────────────────────────


class TestDrainEvtQ:
    """Lines 469-503: dispatch event/status/auth_error/rate_limit/set_cookie."""

    async def _run_drain_with(self, proxy, msgs, *, run_for=0.4):
        """Pre-load proxy._evt_q with msgs, run drain, stop, return cleanly."""
        for m in msgs:
            proxy._evt_q.put(m)
        task = asyncio.create_task(proxy._drain_evt_q())
        # Yield enough times for the executor thread to drain everything.
        await asyncio.sleep(run_for)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def test_event_dispatch_routes_to_handler(self):
        proxy = make_proxy()
        proxy._proc = MagicMock()
        proxy._evt_q = mp.Queue()
        try:
            seen = []
            proxy._event_handlers[5] = seen.append
            await self._run_drain_with(
                proxy, [{"kind": "event", "type": 5, "data": "hi"}]
            )
            assert seen == ["hi"]
        finally:
            close_qs(proxy._evt_q)

    async def test_status_dispatch_updates_state(self):
        proxy = make_proxy()
        proxy._proc = MagicMock()
        proxy._evt_q = mp.Queue()
        try:
            await self._run_drain_with(
                proxy,
                [
                    {
                        "kind": "status",
                        "connected": True,
                        "session_id": "S",
                        "websocket_session_id": "WS",
                        "account_id": "A",
                    }
                ],
            )
            assert proxy.connected is True
            assert proxy.session_id == "S"
        finally:
            close_qs(proxy._evt_q)

    async def test_auth_error_invokes_callback(self):
        seen = []
        proxy = make_proxy(on_unauthorized=lambda: seen.append("unauth"))
        proxy._proc = MagicMock()
        proxy._evt_q = mp.Queue()
        try:
            await self._run_drain_with(proxy, [{"kind": "auth_error"}])
            assert seen == ["unauth"]
        finally:
            close_qs(proxy._evt_q)

    async def test_rate_limit_invokes_callback(self):
        seen = []
        proxy = make_proxy(on_rate_limited=lambda: seen.append("rl"))
        proxy._proc = MagicMock()
        proxy._evt_q = mp.Queue()
        try:
            await self._run_drain_with(proxy, [{"kind": "rate_limit"}])
            assert seen == ["rl"]
        finally:
            close_qs(proxy._evt_q)

    async def test_set_cookie_writes_into_cookies(self):
        proxy = make_proxy()
        proxy._proc = MagicMock()
        proxy._evt_q = mp.Queue()
        try:
            await self._run_drain_with(
                proxy,
                [
                    {
                        "kind": "set_cookie",
                        "name": "session",
                        "value": "xyz",
                        "domain": "fansly.com",
                        "path": "/",
                    }
                ],
            )
            assert proxy.cookies["session"] == "xyz"
        finally:
            close_qs(proxy._evt_q)

    async def test_returns_when_evt_q_is_none(self):
        proxy = make_proxy()
        proxy._proc = MagicMock()
        proxy._evt_q = None  # the None-guard branch
        await proxy._drain_evt_q()  # returns immediately, no error

    async def test_returns_when_proc_is_none(self):
        proxy = make_proxy()
        proxy._proc = None
        proxy._evt_q = mp.Queue()
        try:
            await proxy._drain_evt_q()  # returns immediately, no error
        finally:
            close_qs(proxy._evt_q)

    async def test_returns_on_eoferror(self):
        proxy = make_proxy()
        proxy._proc = MagicMock()
        fake_q = MagicMock()
        fake_q.get.side_effect = EOFError("queue dead")
        proxy._evt_q = fake_q
        await proxy._drain_evt_q()  # expected to return cleanly

    async def test_loops_through_queue_empty(self):
        """queue.Empty path keeps the drain alive; we cancel it ourselves."""
        proxy = make_proxy()
        proxy._proc = MagicMock()
        proxy._evt_q = mp.Queue()  # always empty → poll loop continues
        try:
            task = asyncio.create_task(proxy._drain_evt_q())
            await asyncio.sleep(0.1)
            assert not task.done()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        finally:
            close_qs(proxy._evt_q)


# ── _run_ws_subprocess (child entry point) ─────────────────────────────


class TestRunWsSubprocessShutdown:
    """Lines 131-277: child entry runs supervisor, processes commands, exits."""

    def test_stop_command_terminates_supervisor(self, tmp_path):
        cmd_q = mp.Queue()
        evt_q = mp.Queue()
        try:
            cmd_q.put({"cmd": "stop"})

            mock_class, instance = build_mock_ws_class()
            with (
                patch("api.websocket_subprocess.FanslyWebSocket", mock_class),
                patch("api.websocket_subprocess._setup_child_logging"),
            ):
                _run_ws_subprocess(
                    init_kwargs={
                        "token": "t",
                        "user_agent": "ua",
                        "enable_logging": False,
                        "monitor_events": False,
                        "base_url": None,
                    },
                    cookies_initial={"sess": "abc"},
                    cmd_q=cmd_q,
                    evt_q=evt_q,
                    log_path=str(tmp_path / "child.log"),
                    forward_types=(31,),
                )

            instance.start_in_thread.assert_called_once()
            instance.stop_thread.assert_awaited()
            # No status event expected — change-on-update path is in
            # test_status_publisher_emits_on_change.
        finally:
            close_qs(cmd_q, evt_q)

    def test_send_command_dispatches_to_ws(self, tmp_path):
        cmd_q = mp.Queue()
        evt_q = mp.Queue()
        try:
            cmd_q.put({"cmd": "send", "type": 7, "data": {"x": 1}})
            cmd_q.put({"cmd": "stop"})

            mock_class, instance = build_mock_ws_class()
            with (
                patch("api.websocket_subprocess.FanslyWebSocket", mock_class),
                patch("api.websocket_subprocess._setup_child_logging"),
            ):
                _run_ws_subprocess(
                    init_kwargs={
                        "token": "t",
                        "user_agent": "ua",
                        "enable_logging": False,
                        "monitor_events": False,
                        "base_url": None,
                    },
                    cookies_initial={},
                    cmd_q=cmd_q,
                    evt_q=evt_q,
                    log_path=str(tmp_path / "child.log"),
                    forward_types=(),
                )

            instance.send_message.assert_awaited_with(7, {"x": 1})
        finally:
            close_qs(cmd_q, evt_q)

    def test_send_command_swallows_send_message_errors(self, tmp_path):
        cmd_q = mp.Queue()
        evt_q = mp.Queue()
        try:
            cmd_q.put({"cmd": "send", "type": 1, "data": "x"})
            cmd_q.put({"cmd": "stop"})

            mock_class, instance = build_mock_ws_class()
            instance.send_message = AsyncMock(side_effect=RuntimeError("boom"))

            with (
                patch("api.websocket_subprocess.FanslyWebSocket", mock_class),
                patch("api.websocket_subprocess._setup_child_logging"),
            ):
                # The supervisor must not propagate the inner error.
                _run_ws_subprocess(
                    init_kwargs={
                        "token": "t",
                        "user_agent": "ua",
                        "enable_logging": False,
                        "monitor_events": False,
                        "base_url": None,
                    },
                    cookies_initial={},
                    cmd_q=cmd_q,
                    evt_q=evt_q,
                    log_path=str(tmp_path / "child.log"),
                    forward_types=(),
                )

            instance.stop_thread.assert_awaited()
        finally:
            close_qs(cmd_q, evt_q)

    def test_cookies_command_replaces_jar(self, tmp_path):
        cmd_q = mp.Queue()
        evt_q = mp.Queue()
        try:
            cmd_q.put({"cmd": "cookies", "data": {"new": "value"}})
            cmd_q.put({"cmd": "stop"})

            mock_class, instance = build_mock_ws_class()
            with (
                patch("api.websocket_subprocess.FanslyWebSocket", mock_class),
                patch("api.websocket_subprocess._setup_child_logging"),
            ):
                _run_ws_subprocess(
                    init_kwargs={
                        "token": "t",
                        "user_agent": "ua",
                        "enable_logging": False,
                        "monitor_events": False,
                        "base_url": None,
                    },
                    cookies_initial={"old": "x"},
                    cmd_q=cmd_q,
                    evt_q=evt_q,
                    log_path=str(tmp_path / "child.log"),
                    forward_types=(),
                )

            # Handler reassigns ws.cookies; final value reflects the cmd payload.
            assert instance.cookies == {"new": "value"}
        finally:
            close_qs(cmd_q, evt_q)

    def test_register_command_adds_forwarder(self, tmp_path):
        cmd_q = mp.Queue()
        evt_q = mp.Queue()
        try:
            cmd_q.put({"cmd": "register", "type": 99})
            cmd_q.put({"cmd": "stop"})

            mock_class, instance = build_mock_ws_class()
            with (
                patch("api.websocket_subprocess.FanslyWebSocket", mock_class),
                patch("api.websocket_subprocess._setup_child_logging"),
            ):
                _run_ws_subprocess(
                    init_kwargs={
                        "token": "t",
                        "user_agent": "ua",
                        "enable_logging": False,
                        "monitor_events": False,
                        "base_url": None,
                    },
                    cookies_initial={},
                    cmd_q=cmd_q,
                    evt_q=evt_q,
                    log_path=str(tmp_path / "child.log"),
                    forward_types=(),
                )

            registered_types = {
                call.args[0] for call in instance.register_handler.call_args_list
            }
            assert 99 in registered_types
            assert FanslyWebSocket.MSG_SERVICE_EVENT in registered_types
        finally:
            close_qs(cmd_q, evt_q)

    def test_register_command_skips_already_known_type(self, tmp_path):
        cmd_q = mp.Queue()
        evt_q = mp.Queue()
        try:
            # forward_types already includes type 8, so register cmd is a noop.
            cmd_q.put({"cmd": "register", "type": 8})
            cmd_q.put({"cmd": "stop"})

            mock_class, instance = build_mock_ws_class()
            with (
                patch("api.websocket_subprocess.FanslyWebSocket", mock_class),
                patch("api.websocket_subprocess._setup_child_logging"),
            ):
                _run_ws_subprocess(
                    init_kwargs={
                        "token": "t",
                        "user_agent": "ua",
                        "enable_logging": False,
                        "monitor_events": False,
                        "base_url": None,
                    },
                    cookies_initial={},
                    cmd_q=cmd_q,
                    evt_q=evt_q,
                    log_path=str(tmp_path / "child.log"),
                    forward_types=(8,),
                )

            # Each forward_type registers exactly once at startup; the
            # duplicate register cmd does NOT add a second entry.
            type_8_registrations = sum(
                1
                for call in instance.register_handler.call_args_list
                if call.args[0] == 8
            )
            assert type_8_registrations == 1
        finally:
            close_qs(cmd_q, evt_q)

    def test_unknown_command_logs_warning_and_continues(self, tmp_path):
        cmd_q = mp.Queue()
        evt_q = mp.Queue()
        try:
            cmd_q.put({"cmd": "bogus"})
            cmd_q.put({"cmd": "stop"})

            mock_class, _instance = build_mock_ws_class()
            with (
                patch("api.websocket_subprocess.FanslyWebSocket", mock_class),
                patch("api.websocket_subprocess._setup_child_logging"),
                patch("api.websocket_subprocess.logger") as mock_logger,
            ):
                _run_ws_subprocess(
                    init_kwargs={
                        "token": "t",
                        "user_agent": "ua",
                        "enable_logging": False,
                        "monitor_events": False,
                        "base_url": None,
                    },
                    cookies_initial={},
                    cmd_q=cmd_q,
                    evt_q=evt_q,
                    log_path=str(tmp_path / "child.log"),
                    forward_types=(),
                )

            assert any(
                "Unknown command" in call.args[0]
                for call in mock_logger.warning.call_args_list
            )
        finally:
            close_qs(cmd_q, evt_q)

    def test_status_publisher_emits_on_change(self, tmp_path):
        """Lines 195-226: status_publisher emits when ws state changes."""
        cmd_q = mp.Queue()
        evt_q = mp.Queue()
        try:
            # No-op cmd first so _command_consumer yields the loop before stop.
            cmd_q.put({"cmd": "register", "type": FanslyWebSocket.MSG_SERVICE_EVENT})
            cmd_q.put({"cmd": "stop"})

            mock_class, instance = build_mock_ws_class()

            # Differ from status_publisher's (False, None, None, None) baseline.
            instance.connected = True
            instance.session_id = "abc"
            instance.websocket_session_id = "ws-abc"
            instance.account_id = "1234"

            with (
                patch("api.websocket_subprocess.FanslyWebSocket", mock_class),
                patch("api.websocket_subprocess._setup_child_logging"),
            ):
                _run_ws_subprocess(
                    init_kwargs={
                        "token": "t",
                        "user_agent": "ua",
                        "enable_logging": False,
                        "monitor_events": False,
                        "base_url": None,
                    },
                    cookies_initial={},
                    cmd_q=cmd_q,
                    evt_q=evt_q,
                    log_path=str(tmp_path / "child.log"),
                    forward_types=(),
                )

            # Drain evt_q and confirm a status event with our values appeared.
            events = []
            with contextlib.suppress(queue.Empty):
                while True:
                    events.append(evt_q.get(timeout=0.05))
            statuses = [e for e in events if e.get("kind") == "status"]
            assert any(s.get("session_id") == "abc" for s in statuses)
        finally:
            close_qs(cmd_q, evt_q)


class TestRunWsSubprocessCookieForwarder:
    """Lines 147-181: _forward_response_cookies — cookies replacement."""

    def _install_forwarder(self, tmp_path, cleanup_qs):
        """Run the supervisor briefly to install the patched cookie forwarder."""
        # Caller registers queues for cleanup so the forwarder closure can
        # keep using evt_q past this method's return.
        cmd_q = mp.Queue()
        evt_q = mp.Queue()
        cleanup_qs.extend([cmd_q, evt_q])
        cmd_q.put({"cmd": "stop"})

        mock_class, instance = build_mock_ws_class()

        with (
            patch("api.websocket_subprocess.FanslyWebSocket", mock_class),
            patch("api.websocket_subprocess._setup_child_logging"),
        ):
            _run_ws_subprocess(
                init_kwargs={
                    "token": "t",
                    "user_agent": "ua",
                    "enable_logging": False,
                    "monitor_events": False,
                    "base_url": None,
                },
                cookies_initial={},
                cmd_q=cmd_q,
                evt_q=evt_q,
                log_path=str(tmp_path / "child.log"),
                forward_types=(),
            )

        forwarder = instance._absorb_response_cookies
        return forwarder, evt_q

    def _drain_evt_q_to_list(self, evt_q):
        out = []
        with contextlib.suppress(queue.Empty):
            while True:
                out.append(evt_q.get(timeout=0.05))
        return out

    def test_none_response_headers_is_noop(self, tmp_path):
        cleanup_qs: list = []
        try:
            forwarder, evt_q = self._install_forwarder(tmp_path, cleanup_qs)
            before = self._drain_evt_q_to_list(evt_q)
            forwarder(None)
            after = self._drain_evt_q_to_list(evt_q)
            assert all(e.get("kind") != "set_cookie" for e in (before + after))
        finally:
            close_qs(*cleanup_qs)

    def test_get_all_callable_yields_set_cookie_events(self, tmp_path):
        cleanup_qs: list = []
        try:
            forwarder, evt_q = self._install_forwarder(tmp_path, cleanup_qs)
            self._drain_evt_q_to_list(evt_q)  # discard startup events

            headers = SimpleNamespace(
                get_all=lambda _key: [
                    "session=xyz; Domain=fansly.com; Path=/",
                    "csrf=abc; Domain=fansly.com; Path=/",
                ]
            )
            forwarder(headers)
            events = self._drain_evt_q_to_list(evt_q)
            cookies = [e for e in events if e.get("kind") == "set_cookie"]
            assert {c["name"] for c in cookies} >= {"session", "csrf"}
        finally:
            close_qs(*cleanup_qs)

    def test_dict_like_headers_uses_get(self, tmp_path):
        cleanup_qs: list = []
        try:
            forwarder, evt_q = self._install_forwarder(tmp_path, cleanup_qs)
            self._drain_evt_q_to_list(evt_q)

            headers = {"Set-Cookie": "single=one; Domain=fansly.com; Path=/"}
            forwarder(headers)
            events = self._drain_evt_q_to_list(evt_q)
            cookies = [e for e in events if e.get("kind") == "set_cookie"]
            assert any(c["name"] == "single" and c["value"] == "one" for c in cookies)
        finally:
            close_qs(*cleanup_qs)

    def test_unparseable_cookie_is_skipped(self, tmp_path):
        cleanup_qs: list = []
        try:
            forwarder, evt_q = self._install_forwarder(tmp_path, cleanup_qs)
            self._drain_evt_q_to_list(evt_q)

            with patch.object(SimpleCookie, "load", side_effect=RuntimeError("bad")):
                forwarder(SimpleNamespace(get_all=lambda _key: ["totally bogus"]))
            events = self._drain_evt_q_to_list(evt_q)
            assert all(e.get("kind") != "set_cookie" for e in events)
        finally:
            close_qs(*cleanup_qs)

    def test_empty_raw_value_is_skipped(self, tmp_path):
        cleanup_qs: list = []
        try:
            forwarder, evt_q = self._install_forwarder(tmp_path, cleanup_qs)
            self._drain_evt_q_to_list(evt_q)

            forwarder(SimpleNamespace(get_all=lambda _key: ["", None]))
            events = self._drain_evt_q_to_list(evt_q)
            assert all(e.get("kind") != "set_cookie" for e in events)
        finally:
            close_qs(*cleanup_qs)

    def test_morsel_with_missing_domain_path_uses_defaults(self, tmp_path):
        cleanup_qs: list = []
        try:
            forwarder, evt_q = self._install_forwarder(tmp_path, cleanup_qs)
            self._drain_evt_q_to_list(evt_q)

            forwarder(SimpleNamespace(get_all=lambda _key: ["bare=value"]))
            events = self._drain_evt_q_to_list(evt_q)
            bare = next(
                (
                    e
                    for e in events
                    if e.get("kind") == "set_cookie" and e.get("name") == "bare"
                ),
                None,
            )
            assert bare is not None
            assert bare["domain"] == "fansly.com"
            assert bare["path"] == "/"
        finally:
            close_qs(*cleanup_qs)


class TestRunWsSubprocessAuthCallbacks:
    """Lines 133-137: _on_unauth and _on_rate_limit push events to evt_q."""

    def test_on_unauth_event_emitted(self, tmp_path):
        cmd_q = mp.Queue()
        evt_q = mp.Queue()
        try:
            cmd_q.put({"cmd": "stop"})

            mock_class, _instance = build_mock_ws_class()

            with (
                patch("api.websocket_subprocess.FanslyWebSocket", mock_class),
                patch("api.websocket_subprocess._setup_child_logging"),
            ):
                _run_ws_subprocess(
                    init_kwargs={
                        "token": "t",
                        "user_agent": "ua",
                        "enable_logging": False,
                        "monitor_events": False,
                        "base_url": None,
                    },
                    cookies_initial={},
                    cmd_q=cmd_q,
                    evt_q=evt_q,
                    log_path=str(tmp_path / "child.log"),
                    forward_types=(),
                )

            # Constructor was called with on_unauthorized + on_rate_limited.
            kwargs = mock_class.call_args.kwargs
            on_unauth = kwargs["on_unauthorized"]
            on_rate_limit = kwargs["on_rate_limited"]

            on_unauth()
            on_rate_limit()

            events = []
            with contextlib.suppress(queue.Empty):
                while True:
                    events.append(evt_q.get(timeout=0.05))
            kinds = [e.get("kind") for e in events]
            assert "auth_error" in kinds
            assert "rate_limit" in kinds
        finally:
            close_qs(cmd_q, evt_q)

    def test_forwarder_emits_event(self, tmp_path):
        """Lines 185-189: per-message forwarder pushes 'event' kind to evt_q."""
        cmd_q = mp.Queue()
        evt_q = mp.Queue()
        try:
            cmd_q.put({"cmd": "stop"})

            mock_class, instance = build_mock_ws_class()

            with (
                patch("api.websocket_subprocess.FanslyWebSocket", mock_class),
                patch("api.websocket_subprocess._setup_child_logging"),
            ):
                _run_ws_subprocess(
                    init_kwargs={
                        "token": "t",
                        "user_agent": "ua",
                        "enable_logging": False,
                        "monitor_events": False,
                        "base_url": None,
                    },
                    cookies_initial={},
                    cmd_q=cmd_q,
                    evt_q=evt_q,
                    log_path=str(tmp_path / "child.log"),
                    forward_types=(42,),
                )

            # Find the forwarder registered for type 42 and invoke it.
            forwarder_for_42 = None
            for call in instance.register_handler.call_args_list:
                if call.args[0] == 42:
                    forwarder_for_42 = call.args[1]
                    break
            assert forwarder_for_42 is not None

            # The forwarder is async; run it to push the event.
            asyncio.run(forwarder_for_42("payload"))

            events = []
            with contextlib.suppress(queue.Empty):
                while True:
                    events.append(evt_q.get(timeout=0.05))
            evt_events = [e for e in events if e.get("kind") == "event"]
            assert any(e["type"] == 42 and e["data"] == "payload" for e in evt_events)
        finally:
            close_qs(cmd_q, evt_q)
