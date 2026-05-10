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
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from websockets.exceptions import ConnectionClosed, WebSocketException

from api.websocket import FanslyWebSocket
from config.fanslyconfig import FanslyConfig
from fileio.livestream import _append_lines


# Seconds before reconnecting the chat WebSocket after a connection error.
_CHAT_WS_RECONNECT_DELAY = 5.0


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
) -> None:
    """Maintain a dedicated WebSocket connection for live chat capture.

    Authenticates to ``wss://wsv3.fansly.com``, joins *chat_room_id* via
    ``MSG_CHAT_ROOM`` (t=46001), then receives ``MSG_SERVICE_EVENT``
    (t=10000) messages with ``serviceId=46, type=10`` and feeds each
    ``chatRoomMessage`` payload into *recorder*.

    Reconnects automatically on any error until *stop_event* fires.  The
    main-WS ``route_ws_chat_message`` path remains active in parallel;
    ``ChatRecorder``'s dedup prevents double-writes.

    Runs until *stop_event* fires.
    """
    token = config.token or ""
    user_agent = config.user_agent or ""

    while not stop_event.is_set():
        ws = FanslyWebSocket(
            token=token,
            user_agent=user_agent,
            ping_timeout_enabled=False,
        )

        async def _on_service_event(data: dict | str) -> None:
            try:
                envelope = json.loads(data) if isinstance(data, str) else data
                if envelope.get("serviceId") != 46:
                    return
                raw_event = envelope.get("event")
                event = (
                    json.loads(raw_event)
                    if isinstance(raw_event, str)
                    else (raw_event or {})
                )
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

        ws.register_handler(ws.MSG_SERVICE_EVENT, _on_service_event)

        try:
            await ws.connect()
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
            logger.warning(
                "download.livestream_chat: {} chat WS unexpected error: {}",
                log_prefix,
                exc,
            )
            if not stop_event.is_set():
                await asyncio.sleep(_CHAT_WS_RECONNECT_DELAY)
        finally:
            with contextlib.suppress(Exception):
                await ws.disconnect()
