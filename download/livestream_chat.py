"""Chat capture for live recordings.

Provides ``ChatRecorder`` (a deduplicated JSONL sink) plus the dedicated
chat WebSocket loop that feeds it.  The daemon runner calls
``route_ws_chat_message`` from its main WS handler so chat events
arriving on the primary connection are also captured; ``ChatRecorder``'s
``_seen_ids`` dedup prevents double-writes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from pydantic import JsonValue
from websockets.exceptions import ConnectionClosed, WebSocketException

from api.websocket import _ChildWebSocket
from api.websocket_protocol import CHAT_URL, MSG_SERVICE_EVENT
from config.fanslyconfig import FanslyConfig
from fileio.livestream import _append_lines


# Seconds before reconnecting the chat WebSocket after a connection error.
_CHAT_WS_RECONNECT_DELAY = 5.0

# Maximum consecutive construction-time failures (constructor / handler
# registration / pre-connect setup) before giving up. Connect-time and
# recv-time failures don't count — those are operational/network and should
# keep retrying forever. Five attempts at the 5 s reconnect cadence is ~25 s,
# long enough to rule out a one-off pre-connect transient but short enough
# to surface a real config/code bug before it spins silently for hours.
_MAX_CONSTRUCTION_FAILURES = 5


# Maps chatRoomId → ChatRecorder for active recordings.
# The runner's WS handler calls route_ws_chat_message() to deliver real-time
# chat events; the key is the integer chatRoomId from the WS payload.
_chat_recorders: dict[int, ChatRecorder] = {}
_chat_recorders_lock = threading.Lock()


class ChatRecorder:
    """Deduplicated JSONL sink for a single livestream chatroom.

    Shared by both the REST-poll loop (``_poll_chat_loop``) and the real-time
    WebSocket path (``route_ws_chat_message``).  Thread-safe dedup via
    ``_seen_ids``; writes are serialised through an asyncio.Lock.
    """

    def __init__(self, chat_path: Path) -> None:
        self.chat_path = chat_path
        self._seen_ids: set[int | str] = set()
        self._lock = asyncio.Lock()

    async def ingest(self, message: dict) -> None:
        """Write *message* to JSONL if not already seen."""
        msg_id = message.get("id")
        if msg_id is None:
            return
        async with self._lock:
            if msg_id in self._seen_ids:
                return
            self._seen_ids.add(msg_id)
        now = datetime.now(UTC).isoformat()
        line = json.dumps({**message, "_recorded_at": now}, ensure_ascii=False) + "\n"
        await asyncio.to_thread(_append_lines, self.chat_path, [line])


def register_chat_recorder(chat_room_id: int, recorder: ChatRecorder) -> None:
    """Register *recorder* so the runner WS path can deliver chat to it."""
    with _chat_recorders_lock:
        _chat_recorders[chat_room_id] = recorder


def unregister_chat_recorder(chat_room_id: int) -> None:
    """Drop the recorder registered for *chat_room_id* if any."""
    with _chat_recorders_lock:
        _chat_recorders.pop(chat_room_id, None)


async def route_ws_chat_message(chat_room_id: int, message: dict) -> None:
    """Forward a WebSocket chat message to the active ChatRecorder, if any.

    Called by the daemon runner's ``_on_service_event`` when it receives a
    SVC_CHAT (serviceId=46) type=10 event.  If no recording is active for
    *chat_room_id*, the message is silently discarded.
    """
    with _chat_recorders_lock:
        recorder = _chat_recorders.get(chat_room_id)
    if recorder is not None:
        await recorder.ingest(message)


async def _chat_ws_loop(
    config: FanslyConfig,
    chat_room_id: int,
    recorder: ChatRecorder,
    stop_event: asyncio.Event,
    log_prefix: str,
    *,
    ws_factory: Callable[[], _ChildWebSocket] | None = None,
) -> None:
    """Maintain a dedicated WebSocket connection for live chat capture.

    Authenticates to ``wss://chatws.fansly.com`` (the **dedicated chat
    endpoint** — ``t=46001`` joins and ``(46, 10)`` chat messages do NOT
    flow on the main ``wsv3.fansly.com`` event bus), joins *chat_room_id*
    via ``MSG_CHAT_ROOM`` (t=46001), then receives ``MSG_SERVICE_EVENT``
    (t=10000) messages with ``serviceId=46, type=10`` and feeds each
    ``chatRoomMessage`` payload into *recorder*.

    Reconnects automatically on any error until *stop_event* fires.  The
    main-WS ``route_ws_chat_message`` path remains active in parallel;
    ``ChatRecorder``'s dedup prevents double-writes.

    Uses ``_ChildWebSocket`` directly (in-process) rather than the
    subprocess-backed ``FanslyWebSocket`` wrapper — chat WS doesn't need
    GIL isolation (no heartbeat deadlines; ``ping_timeout_enabled=False``
    below) and the loop calls ``ws.websocket.recv()`` / ``_handle_message``
    which are ``_ChildWebSocket``-only methods.

    Runs until *stop_event* fires.

    Args:
        ws_factory: Optional zero-arg callable returning a
            :class:`_ChildWebSocket`. When omitted, production constructs
            one against the default URL using ``config.token`` and
            ``config.user_agent``. Tests pass a factory pointing at the
            in-process ``ws_server`` fixture (mirrors the daemon-runner
            ``ws_factory`` injection pattern).
    """
    token = config.token or ""
    user_agent = config.user_agent or ""

    construction_failures = 0

    while not stop_event.is_set():
        ws: _ChildWebSocket | None = None
        connect_attempted = False
        try:
            ws = (
                ws_factory()
                if ws_factory is not None
                else _ChildWebSocket(
                    token=token,
                    user_agent=user_agent,
                    base_url=CHAT_URL,
                    ping_timeout_enabled=False,
                )
            )

            async def _on_service_event(data: JsonValue) -> None:
                try:
                    envelope = json.loads(data) if isinstance(data, str) else data
                    if not isinstance(envelope, dict):
                        return
                    if envelope.get("serviceId") != 46:
                        return
                    raw_event = envelope.get("event")
                    event = (
                        json.loads(raw_event)
                        if isinstance(raw_event, str)
                        else raw_event
                    )
                    if not isinstance(event, dict):
                        return
                    if event.get("type") != 10:
                        return
                    chat_msg = event.get("chatRoomMessage")
                    if isinstance(chat_msg, dict):
                        await recorder.ingest(chat_msg)
                except Exception as exc:
                    logger.debug(
                        "download.livestream_chat: {} chat WS event error: {}",
                        log_prefix,
                        exc,
                    )

            ws.register_handler(MSG_SERVICE_EVENT, _on_service_event)

            connect_attempted = True
            await ws.connect()
            if ws.websocket is None:  # -O-safe narrowing; connect() must set it
                raise RuntimeError("chat WS: websocket is None after connect()")
            construction_failures = 0
            await ws.join_chat_room(chat_room_id)
            logger.info(
                "download.livestream_chat: {} chat WS connected | room={}",
                log_prefix,
                chat_room_id,
            )

            while not stop_event.is_set():
                try:
                    raw = await asyncio.wait_for(ws.websocket.recv(), timeout=5.0)
                    await ws._handle_message(raw)
                except TimeoutError:
                    pass  # re-check stop_event

        except (ConnectionClosed, WebSocketException, OSError) as exc:
            logger.debug(
                "download.livestream_chat: {} chat WS connection error: {}",
                log_prefix,
                exc,
            )
            if not stop_event.is_set():
                await asyncio.sleep(_CHAT_WS_RECONNECT_DELAY)
        except Exception as exc:
            logger.opt(exception=exc).warning(
                "download.livestream_chat: {} chat WS unexpected error: {!r}",
                log_prefix,
                exc,
            )
            if not connect_attempted:
                construction_failures += 1
                if construction_failures >= _MAX_CONSTRUCTION_FAILURES:
                    logger.error(
                        "download.livestream_chat: {} chat WS giving up after {}"
                        " consecutive construction-time failures — likely a code"
                        " or config bug, not a transient",
                        log_prefix,
                        construction_failures,
                    )
                    break
            if not stop_event.is_set():
                await asyncio.sleep(_CHAT_WS_RECONNECT_DELAY)
        finally:
            if ws is not None:
                with contextlib.suppress(Exception):
                    await ws.disconnect()
