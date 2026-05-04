"""Subprocess-isolated WebSocket gateway — Lever 4 GIL isolation.

The in-thread ``FanslyWebSocket`` (api/websocket.py) is correctly placed on
its own asyncio loop in a daemon thread, but the GIL still binds the WS
thread to whichever thread holds it. When the main thread runs CPU-bound
work (image hashing, large JSON decodes, dedupe), the WS thread cannot
acquire the GIL and the spec-mandated 1.2*pingInterval heartbeat misses
its window — observed as "Ping timeout (>30s since last response)" warnings
followed by reconnect storms.

This module wraps ``FanslyWebSocket`` in a child process. Each subprocess
has its own GIL, so the WS heartbeat is fully insulated from main-process
CPU bursts. Communication crosses the process boundary via two
multiprocessing queues: ``cmd_q`` (parent→child commands) and ``evt_q``
(child→parent events).

Opt-in via ``monitoring.websocket_subprocess: true`` in ``config.yaml``
(surfaces as ``FanslyConfig.monitoring_websocket_subprocess``). Default
is the in-thread ``FanslyWebSocket`` so the subprocess path can be
validated separately from any merge/dep-update churn.

Surface compatibility: ``FanslyWebSocketProxy`` mirrors every public
attribute and method that ``api/fansly.py`` and ``daemon/`` read or
call (``connected``, ``session_id``, ``websocket_session_id``,
``account_id``, ``_event_handlers``, ``register_handler``,
``start_in_thread``, ``stop_thread``, ``send_message``), so the existing
call sites don't need to know which implementation is in play.

What does NOT cross the boundary:
  * ``httpx.Client`` — the parent owns the cookie jar; the child receives
    snapshot dicts via ``cmd_q`` and pushes Set-Cookie morsels back via
    ``evt_q`` so the parent's jar stays in sync (the WS→HTTP leg of
    bidirectional cookie sync, see api/websocket.py:_absorb_response_cookies).
  * Callback closures (``on_unauthorized`` / ``on_rate_limited``) — the
    child sends ``{"kind": "auth_error"|"rate_limit"}`` events; the parent
    invokes the locally-stored callbacks.
  * Handler closures (``register_handler``) — the child registers a
    forwarder for each requested message type; the parent dispatches to
    the actual closure (which is bound to ``asyncio.Queue`` and other
    main-loop state and therefore cannot be pickled).
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing as mp
import queue
from collections.abc import Callable
from http.cookies import SimpleCookie
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from .websocket import FanslyWebSocket
from .websocket import FanslyWebSocket as _BaseFWS


# Drain-poll interval for the cmd_q/evt_q blocking get() calls. Short
# enough that shutdown observes cancellation/queue-death promptly, long
# enough that the wakeup overhead is negligible (~2 wakes/sec/thread).
# Without this, run_in_executor(queue.get) blocks indefinitely in C and
# the default ThreadPoolExecutor can't join its workers at process exit
# (RuntimeWarning: "executor did not finishing joining its threads").
_QUEUE_POLL_INTERVAL_S = 0.5


if TYPE_CHECKING:
    from multiprocessing.queues import Queue as MpQueue

    import httpx


# ---------------------------------------------------------------------------
# Child-process entry point — must be a top-level function so the spawn
# start method (macOS / Python 3.8+ default) can pickle ``target=``.
# ---------------------------------------------------------------------------


def _setup_child_logging(log_path: str) -> None:
    """Initialize a single-sink loguru config in the child process.

    The parent's ``config/logging.py`` builds eight enqueued sinks plus a
    Rich console handler. Replicating that across a process boundary
    creates two layers of multiprocessing-process-per-sink workers and
    makes shutdown debugging miserable. The child is small enough that a
    single synchronous file sink is sufficient.

    Args:
        log_path: Filesystem path the child writes its log file to.
    """
    logger.remove()
    logger.add(
        log_path,
        format="{level.icon}   {level.name:>8} | {time:HH:mm:ss.SS} || {name}:{function}:{line} - {message}",
        enqueue=False,
        rotation="50 MB",
        retention=5,
        backtrace=True,
        diagnose=True,
    )


def _run_ws_subprocess(
    init_kwargs: dict[str, Any],
    cookies_initial: dict[str, str],
    cmd_q: MpQueue,
    evt_q: MpQueue,
    log_path: str,
    forward_types: tuple[int, ...],
) -> None:
    """Subprocess entry — runs the WebSocket inside a child Python process.

    Args:
        init_kwargs: Picklable subset of FanslyWebSocket constructor args
            (everything except ``http_client`` and the callbacks, which
            either can't cross the boundary or live in the parent).
        cookies_initial: Cookie snapshot taken from the parent's HTTP jar
            at start time. Subsequent rotations arrive via ``cmd_q``.
        cmd_q: Parent → child command queue. Messages: ``{"cmd": "send",
            "type": int, "data": Any}``, ``{"cmd": "cookies", "data":
            dict}``, ``{"cmd": "stop"}``.
        evt_q: Child → parent event queue. See module docstring for kinds.
        log_path: File path for child's loguru sink.
        forward_types: Message types for which the child registers a
            forwarder. The parent decides which types it has handlers
            for and passes the list at startup; ``MSG_SERVICE_EVENT``
            is always included.
    """
    _setup_child_logging(log_path)

    def _on_unauth() -> None:
        evt_q.put({"kind": "auth_error", "code": 401})

    def _on_rate_limit() -> None:
        evt_q.put({"kind": "rate_limit"})

    ws = FanslyWebSocket(
        cookies=dict(cookies_initial),
        http_client=None,
        on_unauthorized=_on_unauth,
        on_rate_limited=_on_rate_limit,
        **init_kwargs,
    )

    def _forward_response_cookies(response_headers: Any) -> None:
        """Replacement for ``_absorb_response_cookies`` — pushes morsels via evt_q.

        The original method writes Set-Cookie values back into the
        ``http_client`` jar, but in the child ``http_client`` is None. We
        forward each parsed morsel to the parent so it can write to the
        real jar — preserving the WS → HTTP cookie sync leg.
        """
        if response_headers is None:
            return
        get_all = getattr(response_headers, "get_all", None)
        raw_values = (
            get_all("Set-Cookie")
            if callable(get_all)
            else [response_headers.get("Set-Cookie")]
        )
        for raw in raw_values or []:
            if not raw:
                continue
            parsed = SimpleCookie()
            try:
                parsed.load(raw)
            except Exception as exc:
                logger.debug("WS Set-Cookie parse failed in child: {} ({})", raw, exc)
                continue
            for name, morsel in parsed.items():
                evt_q.put(
                    {
                        "kind": "set_cookie",
                        "name": name,
                        "value": morsel.value,
                        "domain": morsel["domain"] or "fansly.com",
                        "path": morsel["path"] or "/",
                    }
                )

    ws._absorb_response_cookies = _forward_response_cookies  # type: ignore[method-assign]

    def _make_forwarder(msg_type: int) -> Callable[[Any], Any]:
        async def _forward(event_data: Any) -> None:
            evt_q.put({"kind": "event", "type": msg_type, "data": event_data})

        return _forward

    types_to_forward = set(forward_types) | {FanslyWebSocket.MSG_SERVICE_EVENT}
    for msg_type in types_to_forward:
        ws.register_handler(msg_type, _make_forwarder(msg_type))

    async def _status_publisher() -> None:
        """Publish (connected, session_id, ...) snapshots whenever they change."""
        last: tuple[bool, str | None, str | None, str | None] = (
            False,
            None,
            None,
            None,
        )
        while True:
            try:
                snapshot = (
                    bool(ws.connected),
                    ws.session_id,
                    ws.websocket_session_id,
                    ws.account_id,
                )
                if snapshot != last:
                    evt_q.put(
                        {
                            "kind": "status",
                            "connected": snapshot[0],
                            "session_id": snapshot[1],
                            "websocket_session_id": snapshot[2],
                            "account_id": snapshot[3],
                        }
                    )
                    last = snapshot
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error("status_publisher error: {}", exc)

    async def _command_consumer() -> bool:
        """Drain cmd_q. Returns True on stop request, False on queue death.

        Uses a polled blocking get with timeout so cancellation can be
        observed promptly and the executor thread can be reaped at exit
        instead of blocking indefinitely in C-level ``Queue.get()``.
        """
        loop = asyncio.get_running_loop()
        while True:
            try:
                msg = await loop.run_in_executor(
                    None, cmd_q.get, True, _QUEUE_POLL_INTERVAL_S
                )
            except queue.Empty:
                continue
            except (EOFError, OSError, BrokenPipeError):
                return False
            cmd = msg.get("cmd")
            if cmd == "stop":
                return True
            if cmd == "send":
                try:
                    await ws.send_message(msg["type"], msg["data"])
                except Exception as exc:
                    logger.error("send_message failed in child: {}", exc)
            elif cmd == "cookies":
                # Atomic attribute reassignment — ``_current_cookies()`` reads
                # ``self.cookies`` and copies, so a torn read is impossible.
                ws.cookies = dict(msg["data"])
            elif cmd == "register":
                msg_type = msg["type"]
                if msg_type not in types_to_forward:
                    types_to_forward.add(msg_type)
                    ws.register_handler(msg_type, _make_forwarder(msg_type))
            else:
                logger.warning("Unknown command in child: {}", cmd)

    async def _supervisor() -> None:
        ws.start_in_thread()
        status_task = asyncio.create_task(_status_publisher())
        try:
            await _command_consumer()
        finally:
            status_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await status_task
            await ws.stop_thread()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_supervisor())


# ---------------------------------------------------------------------------
# Parent-side proxy — drop-in replacement for FanslyWebSocket.
# ---------------------------------------------------------------------------


class FanslyWebSocketProxy:
    """Subprocess-backed drop-in for ``FanslyWebSocket``.

    Mirrors the public surface ``api/fansly.py`` and ``daemon/`` depend on
    so callers don't need to branch on which implementation is running.
    Internally, all WebSocket I/O happens in a child process; this class
    is just the parent-side shim that translates calls into queue
    messages and reflects subprocess state via mirrored attributes.
    """

    MSG_ERROR = _BaseFWS.MSG_ERROR
    MSG_SESSION = _BaseFWS.MSG_SESSION
    MSG_PING = _BaseFWS.MSG_PING
    MSG_SERVICE_EVENT = _BaseFWS.MSG_SERVICE_EVENT
    MSG_BATCH = _BaseFWS.MSG_BATCH
    MSG_CHAT_ROOM = _BaseFWS.MSG_CHAT_ROOM
    PING_INTERVAL_MIN = _BaseFWS.PING_INTERVAL_MIN
    PING_INTERVAL_MAX = _BaseFWS.PING_INTERVAL_MAX

    def __init__(
        self,
        token: str,
        user_agent: str,
        cookies: dict[str, str] | None = None,
        enable_logging: bool = False,
        on_unauthorized: Callable[[], Any] | None = None,
        on_rate_limited: Callable[[], Any] | None = None,
        monitor_events: bool = False,
        base_url: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.token = token
        self.user_agent = user_agent
        self.cookies = dict(cookies) if cookies else {}
        self.enable_logging = enable_logging
        self.on_unauthorized = on_unauthorized
        self.on_rate_limited = on_rate_limited
        self.monitor_events = monitor_events
        self.base_url = base_url
        self.http_client = http_client

        # Mirrored runtime state — populated by the drain task from "status" events.
        self.connected = False
        self.session_id: str | None = None
        self.websocket_session_id: str | None = None
        self.account_id: str | None = None

        # Same shape as FanslyWebSocket._event_handlers so daemon/bootstrap.py's
        # `bootstrap.ws._event_handlers.pop(...)` keeps working.
        self._event_handlers: dict[int, Callable[[Any], Any]] = {}

        self._proc: mp.process.BaseProcess | None = None
        self._cmd_q: MpQueue | None = None
        self._evt_q: MpQueue | None = None
        self._drain_task: asyncio.Task[None] | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def register_handler(
        self,
        message_type: int,
        handler: Callable[[Any], Any],
    ) -> None:
        """Register a parent-side handler for a given message type.

        The handler stays in the parent (handlers close over ``asyncio.Queue``
        and other loop-bound state and cannot cross a process boundary).
        If the subprocess is already running, push a "register" command so
        it knows to forward this message type.
        """
        already_known = message_type in self._event_handlers
        self._event_handlers[message_type] = handler
        if not already_known:
            self._send_cmd({"cmd": "register", "type": message_type})

    # ------------------------------------------------------------------
    # Lifecycle — surface-compatible with FanslyWebSocket
    # ------------------------------------------------------------------

    def start_in_thread(
        self,
        main_loop: asyncio.AbstractEventLoop | None = None,
        ready_timeout: float = 5.0,
    ) -> None:
        """Spawn the WS subprocess and start the parent-side drain task.

        Sync method (matches FanslyWebSocket.start_in_thread). Must be
        called from inside an async context (uses ``get_running_loop()``).

        Args:
            main_loop: The asyncio loop to schedule callbacks on. Defaults
                to the running loop at call time.
            ready_timeout: Unused — kept for signature compatibility with
                FanslyWebSocket. The subprocess publishes its session_id
                via ``evt_q``; callers poll ``self.session_id`` after
                ``start_in_thread()`` (see api/fansly.py:565-573).
        """
        del ready_timeout  # signature compat only
        if self._proc is not None and self._proc.is_alive():
            return

        self._main_loop = main_loop or asyncio.get_running_loop()
        ctx = mp.get_context("spawn")
        self._cmd_q = ctx.Queue()
        self._evt_q = ctx.Queue()

        cookies_initial = self._snapshot_cookies()

        init_kwargs = {
            "token": self.token,
            "user_agent": self.user_agent,
            "enable_logging": self.enable_logging,
            "monitor_events": self.monitor_events,
            "base_url": self.base_url,
        }

        log_dir = Path.cwd() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = str(log_dir / "websocket_subprocess.log")

        forward_types = tuple(self._event_handlers.keys())

        self._proc = ctx.Process(
            target=_run_ws_subprocess,
            args=(
                init_kwargs,
                cookies_initial,
                self._cmd_q,
                self._evt_q,
                log_path,
                forward_types,
            ),
            daemon=True,
            name="fansly-ws-subprocess",
        )
        self._proc.start()

        self._drain_task = asyncio.create_task(self._drain_evt_q())

    async def stop_thread(self, join_timeout: float = 10.0) -> None:
        """Stop the WS subprocess and join.

        Async method (matches FanslyWebSocket.stop_thread). Sends a stop
        command, cancels the drain task, joins the subprocess, and falls
        back to ``terminate()`` if the child overruns the join timeout.
        """
        if self._proc is None:
            return

        self._send_cmd({"cmd": "stop"})

        if self._drain_task is not None:
            self._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._drain_task
            self._drain_task = None

        await asyncio.to_thread(self._proc.join, join_timeout)
        if self._proc.is_alive():
            self._proc.terminate()
            await asyncio.to_thread(self._proc.join, 1.0)

        self._proc = None
        self._cmd_q = None
        self._evt_q = None
        self.connected = False

    async def send_message(self, message_type: int, data: Any) -> None:
        """Send a message through the WS subprocess.

        Raises:
            RuntimeError: If the subprocess is not running.
        """
        if self._proc is None or not self._proc.is_alive():
            raise RuntimeError("WS subprocess not running")
        self._send_cmd({"cmd": "send", "type": message_type, "data": data})

    # ------------------------------------------------------------------
    # Internal — event-queue drain + dispatch
    # ------------------------------------------------------------------

    async def _drain_evt_q(self) -> None:
        """Pull events from the subprocess and dispatch locally.

        Uses a polled blocking get with timeout so the executor thread
        running ``evt_q.get`` can release periodically — without this the
        thread is stuck indefinitely in C-level Queue.get() and the
        default ThreadPoolExecutor warns at process exit when it can't
        join its workers.
        """
        loop = asyncio.get_running_loop()
        try:
            while True:
                if self._evt_q is None or self._proc is None:
                    return
                try:
                    msg = await loop.run_in_executor(
                        None, self._evt_q.get, True, _QUEUE_POLL_INTERVAL_S
                    )
                except queue.Empty:
                    continue
                except (EOFError, OSError, BrokenPipeError, AttributeError):
                    return
                kind = msg.get("kind")
                if kind == "event":
                    await self._dispatch_event(msg["type"], msg["data"])
                elif kind == "status":
                    self._update_status(msg)
                elif kind == "auth_error":
                    await self._dispatch_callback(self.on_unauthorized)
                elif kind == "rate_limit":
                    await self._dispatch_callback(self.on_rate_limited)
                elif kind == "set_cookie":
                    self._absorb_set_cookie(msg)
        except asyncio.CancelledError:
            pass

    async def _dispatch_event(self, msg_type: int, data: Any) -> None:
        handler = self._event_handlers.get(msg_type)
        if handler is None:
            return
        if asyncio.iscoroutinefunction(handler):
            await handler(data)
        else:
            handler(data)

    async def _dispatch_callback(self, cb: Callable[[], Any] | None) -> None:
        if cb is None:
            return
        if asyncio.iscoroutinefunction(cb):
            await cb()
        else:
            cb()

    def _update_status(self, msg: dict[str, Any]) -> None:
        prev_connected = self.connected
        self.connected = msg.get("connected", False)
        self.session_id = msg.get("session_id")
        self.websocket_session_id = msg.get("websocket_session_id")
        self.account_id = msg.get("account_id")

        # Connected → disconnected transition: push a fresh cookie snapshot
        # so the child's next reconnect uses the latest jar contents. This
        # is the HTTP → WS leg of bidirectional cookie sync; without it,
        # cookies that rotated since startup are stale by the next connect.
        if prev_connected and not self.connected:
            self._send_cmd({"cmd": "cookies", "data": self._snapshot_cookies()})

    def _absorb_set_cookie(self, msg: dict[str, Any]) -> None:
        if self.http_client is None:
            self.cookies[msg["name"]] = msg["value"]
            return
        self.http_client.cookies.set(
            msg["name"],
            msg["value"],
            domain=msg.get("domain", "fansly.com"),
            path=msg.get("path", "/"),
        )

    def _snapshot_cookies(self) -> dict[str, str]:
        if self.http_client is None:
            return dict(self.cookies)
        return {c.name: c.value for c in self.http_client.cookies.jar}

    def _send_cmd(self, cmd: dict[str, Any]) -> None:
        if self._cmd_q is None:
            return
        with contextlib.suppress(BrokenPipeError, OSError):
            self._cmd_q.put(cmd)


# ---------------------------------------------------------------------------
# Selection helper — picks Proxy or in-thread implementation based on env.
# ---------------------------------------------------------------------------


def get_websocket_class(use_subprocess: bool = False) -> type:
    """Return ``FanslyWebSocketProxy`` when ``use_subprocess`` is True.

    Driven by ``FanslyConfig.monitoring_websocket_subprocess`` (loaded from
    ``config.yaml`` ``monitoring.websocket_subprocess``). The opt-in keeps
    the subprocess change orthogonal to any in-flight work — flip it on
    in config for a validation run, then promote to default-on in a
    follow-up once stable.

    Args:
        use_subprocess: When True, return the subprocess-isolated proxy.
            When False (default), return the in-thread implementation.

    Returns:
        Either ``FanslyWebSocketProxy`` (subprocess-isolated) or
        ``FanslyWebSocket`` (in-thread). Both expose the same public API.
    """
    if use_subprocess:
        return FanslyWebSocketProxy
    return FanslyWebSocket
