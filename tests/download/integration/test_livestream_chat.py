"""End-to-end integration tests for ``download.livestream_chat``.

Exercises the chat-WS recording pipeline against the real
:class:`FakeFanslyWSServer` over localhost TCP:

1. ``_chat_ws_loop`` opens a ``_ChildWebSocket`` connection, authenticates,
   sends a ``MSG_CHAT_ROOM`` (t=46001) join, and recv-loops until stop.
2. Each ``MSG_SERVICE_EVENT`` with ``serviceId=46, type=10`` is decoded
   and routed into the ``ChatRecorder.ingest`` path.
3. ``ChatRecorder`` writes deduped JSONL to disk (file I/O hits real
   ``tmp_path`` — no mocked filesystem).
4. ``route_ws_chat_message`` + ``register_chat_recorder`` /
   ``unregister_chat_recorder`` deliver chat events from the main WS to
   the same recorder (covers the registry path).

Boundary policy: WS server is a real localhost websockets server (the
existing :class:`FakeFanslyWSServer` fixture); the chat loop uses a
factory-injected ``_ChildWebSocket`` pointing at it via the
production-side ``ws_factory`` parameter (added when this test was
written). Filesystem is real ``tmp_path``. No internal mocks.

Production bug surfaced during test development: ``_chat_ws_loop``
previously constructed the subprocess-backed ``FanslyWebSocket`` wrapper
but called ``connect()`` / ``join_chat_room()`` / ``websocket.recv()``
/ ``_handle_message()`` / ``disconnect()`` — all methods that only
exist on the in-process ``_ChildWebSocket`` class. Production would
have ``AttributeError``-ed on first invocation for any recording with
a ``chatRoomId``. Fixed in the same patch (per
feedback_remove_mocks_fix_bugs Option A): switched to ``_ChildWebSocket``
directly, which also matches the ``ping_timeout_enabled=False`` design
intent (chat WS doesn't need GIL isolation).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from api.websocket import _ChildWebSocket
from config.fanslyconfig import FanslyConfig
from download.livestream_chat import (
    _MAX_CONSTRUCTION_FAILURES,
    ChatRecorder,
    _chat_ws_loop,
    register_chat_recorder,
    route_ws_chat_message,
    unregister_chat_recorder,
)
from tests.fixtures.api import FakeFanslyWSServer, dump_ws_server_state
from tests.fixtures.utils.test_isolation import snowflake_id


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_chat_ws_factory(server_url: str) -> Callable[[], _ChildWebSocket]:
    """Build a zero-arg factory producing a _ChildWebSocket aimed at
    *server_url*. Mirrors the pattern of ``make_ws_factory_for`` but
    returns a no-arg callable (the shape ``_chat_ws_loop`` expects)
    instead of a config-taking one."""

    def _factory() -> _ChildWebSocket:
        return _ChildWebSocket(
            token="test-chat-token",
            user_agent="test-ua",
            cookies={"sess": "abc"},
            base_url=server_url,
            ping_timeout_enabled=False,
        )

    return _factory


async def _read_jsonl_lines(
    path: Path,
    expected: int,
    timeout: float = 5.0,  # noqa: ASYNC109 - test helper; timeout kwarg is idiomatic
) -> list[dict]:
    """Poll *path* until it has at least *expected* JSONL lines, or timeout."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        exists = await asyncio.to_thread(path.exists)
        if exists:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
            lines = [json.loads(line) for line in text.splitlines() if line.strip()]
            if len(lines) >= expected:
                return lines
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(
                f"chat.jsonl never reached {expected} lines within {timeout}s "
                f"(path exists={exists})"
            )
        await asyncio.sleep(0.05)


# ── Tests ──────────────────────────────────────────────────────────────────


class TestChatWsLoopE2E:
    """End-to-end: real WS server + real _ChildWebSocket + real file I/O."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_records_pushed_messages_to_jsonl(
        self,
        ws_server: FakeFanslyWSServer,
        config_wired: FanslyConfig,
        tmp_path: Path,
    ) -> None:
        """Happy path: 3 pushed chat messages → 3 deduped JSONL lines."""
        chat_room_id = snowflake_id()
        chat_path = tmp_path / "chat.jsonl"
        recorder = ChatRecorder(chat_path)
        stop_event = asyncio.Event()

        task = asyncio.create_task(
            _chat_ws_loop(
                config_wired,
                chat_room_id,
                recorder,
                stop_event,
                log_prefix="[test]",
                ws_factory=_make_chat_ws_factory(ws_server.base_url),
            )
        )

        try:
            # Wait for auth + chat-room join to land server-side.
            await ws_server.wait_for_auth(timeout=5.0)
            await ws_server.wait_for_chat_room_joined(chat_room_id, timeout=5.0)

            # Push three distinct chat messages.
            for i in range(3):
                ws_server.push_chat_message(
                    chat_room_id=chat_room_id,
                    message={
                        "id": snowflake_id(),
                        "senderId": str(snowflake_id()),
                        "content": f"hello {i}",
                    },
                )

            lines = await _read_jsonl_lines(chat_path, expected=3, timeout=5.0)
        finally:
            stop_event.set()
            await asyncio.wait_for(task, timeout=5.0)
            dump_ws_server_state(ws_server)

        assert len(lines) == 3
        ids = {line["id"] for line in lines}
        assert len(ids) == 3, f"expected 3 distinct ids, got {ids}"
        contents = sorted(line["content"] for line in lines)
        assert contents == ["hello 0", "hello 1", "hello 2"]
        # Every record carries the ChatRecorder._recorded_at timestamp.
        assert all("_recorded_at" in line for line in lines)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_non_dict_event_payload_is_skipped_not_fatal(
        self,
        ws_server: FakeFanslyWSServer,
        config_wired: FanslyConfig,
        tmp_path: Path,
    ) -> None:
        """A svc=46 event whose decoded payload is a JSON array (not an
        object) must be skipped without killing the loop — regression for the
        pre-JsonValue-tightening ``AttributeError`` on ``event.get`` (now
        guarded by ``isinstance(event, dict)``). A valid chat message pushed
        afterward still records, proving the loop survived the malformed frame.
        """
        chat_room_id = snowflake_id()
        chat_path = tmp_path / "chat.jsonl"
        recorder = ChatRecorder(chat_path)
        stop_event = asyncio.Event()

        task = asyncio.create_task(
            _chat_ws_loop(
                config_wired,
                chat_room_id,
                recorder,
                stop_event,
                log_prefix="[test-malformed]",
                ws_factory=_make_chat_ws_factory(ws_server.base_url),
            )
        )
        try:
            await ws_server.wait_for_chat_room_joined(chat_room_id, timeout=5.0)

            # Malformed envelope: the decoded ``d`` is a JSON array, not an
            # object — covers the isinstance(envelope, dict) guard.
            ws_server.push(
                {"t": 10000, "d": json.dumps([1, 2, 3])}
            )  # 10000=SERVICE_EVENT
            # Malformed event: a svc=46 frame whose event decodes to an array —
            # covers the isinstance(event, dict) guard.
            ws_server.push_service_event(service_id=46, inner=[1, 2, 3])  # type: ignore[arg-type]  # deliberately non-object event

            # Valid chat message after it — only this one should record.
            ws_server.push_chat_message(
                chat_room_id=chat_room_id,
                message={"id": snowflake_id(), "content": "after malformed"},
            )

            lines = await _read_jsonl_lines(chat_path, expected=1, timeout=5.0)
        finally:
            stop_event.set()
            await asyncio.wait_for(task, timeout=5.0)
            dump_ws_server_state(ws_server)

        assert len(lines) == 1
        assert lines[0]["content"] == "after malformed"

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_dedups_duplicate_messages(
        self,
        ws_server: FakeFanslyWSServer,
        config_wired: FanslyConfig,
        tmp_path: Path,
    ) -> None:
        """Same message id pushed 3 times → only 1 line in chat.jsonl."""
        chat_room_id = snowflake_id()
        chat_path = tmp_path / "chat.jsonl"
        recorder = ChatRecorder(chat_path)
        stop_event = asyncio.Event()
        duplicate_id = snowflake_id()

        task = asyncio.create_task(
            _chat_ws_loop(
                config_wired,
                chat_room_id,
                recorder,
                stop_event,
                log_prefix="[test-dedup]",
                ws_factory=_make_chat_ws_factory(ws_server.base_url),
            )
        )

        try:
            await ws_server.wait_for_chat_room_joined(chat_room_id, timeout=5.0)

            # Push the same message id 3 times + 1 distinct.
            for _ in range(3):
                ws_server.push_chat_message(
                    chat_room_id=chat_room_id,
                    message={"id": duplicate_id, "content": "duplicate"},
                )
            ws_server.push_chat_message(
                chat_room_id=chat_room_id,
                message={"id": snowflake_id(), "content": "distinct"},
            )

            lines = await _read_jsonl_lines(chat_path, expected=2, timeout=5.0)
            # Settle: give the loop a beat to NOT write a 3rd line.
            await asyncio.sleep(0.3)
            lines = await _read_jsonl_lines(chat_path, expected=2, timeout=2.0)
        finally:
            stop_event.set()
            await asyncio.wait_for(task, timeout=5.0)

        assert len(lines) == 2, f"expected dedup → 2 lines, got {len(lines)}"
        ids_seen = [line["id"] for line in lines]
        assert duplicate_id in ids_seen
        assert ids_seen.count(duplicate_id) == 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_drops_messages_without_id(
        self,
        ws_server: FakeFanslyWSServer,
        config_wired: FanslyConfig,
        tmp_path: Path,
    ) -> None:
        """Chat messages missing the ``id`` field are silently dropped
        (livestream_chat.py:53-55 — ChatRecorder.ingest's no-id guard)."""
        chat_room_id = snowflake_id()
        chat_path = tmp_path / "chat.jsonl"
        recorder = ChatRecorder(chat_path)
        stop_event = asyncio.Event()

        task = asyncio.create_task(
            _chat_ws_loop(
                config_wired,
                chat_room_id,
                recorder,
                stop_event,
                log_prefix="[test-no-id]",
                ws_factory=_make_chat_ws_factory(ws_server.base_url),
            )
        )

        try:
            await ws_server.wait_for_chat_room_joined(chat_room_id, timeout=5.0)

            # Push two id-less messages + one valid message as a tail
            # marker so we can wait for SOMETHING to land.
            for content in ("no-id-1", "no-id-2"):
                ws_server.push_chat_message(
                    chat_room_id=chat_room_id,
                    message={"content": content},  # NO id field
                )
            tail_id = snowflake_id()
            ws_server.push_chat_message(
                chat_room_id=chat_room_id,
                message={"id": tail_id, "content": "tail"},
            )

            lines = await _read_jsonl_lines(chat_path, expected=1, timeout=5.0)
            # Wait briefly and re-check — the no-id messages should NOT
            # have produced extra lines after the tail.
            await asyncio.sleep(0.3)
            lines = await _read_jsonl_lines(chat_path, expected=1, timeout=2.0)
        finally:
            stop_event.set()
            await asyncio.wait_for(task, timeout=5.0)

        assert len(lines) == 1, (
            f"expected only tail message, got {len(lines)} lines: {lines}"
        )
        assert lines[0]["id"] == tail_id


class TestChatWsConstructionFailureHardStop:
    """Cover the construction-failure counter (Pattern B hard-stop).

    When ``ws_factory`` raises consistently (the v0.13.7-v0.14.3 shape:
    ``TypeError: __init__() got an unexpected keyword argument
    'ping_timeout_enabled'`` on every construction attempt), the loop
    breaks after ``_MAX_CONSTRUCTION_FAILURES`` consecutive attempts
    rather than spinning forever on a non-transient bug.
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(15)
    async def test_breaks_after_max_construction_failures(
        self,
        config_wired: FanslyConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A factory that always raises drives the loop to its hard-stop."""
        # Compress the reconnect delay so the test finishes in <1s instead of
        # waiting _MAX_CONSTRUCTION_FAILURES * 5s = 25s on the default.
        monkeypatch.setattr(
            "download.livestream_chat._CHAT_WS_RECONNECT_DELAY",
            0.01,
        )

        call_count = 0

        def broken_factory() -> _ChildWebSocket:
            nonlocal call_count
            call_count += 1
            raise TypeError("simulated constructor failure")

        chat_room_id = snowflake_id()
        chat_path = tmp_path / "chat.jsonl"
        recorder = ChatRecorder(chat_path)
        stop_event = asyncio.Event()

        task = asyncio.create_task(
            _chat_ws_loop(
                config_wired,
                chat_room_id,
                recorder,
                stop_event,
                log_prefix="[test-break]",
                ws_factory=broken_factory,
            )
        )

        # The loop must self-terminate via break after the cap is hit.
        # stop_event is intentionally NOT set; if the break logic regresses,
        # the task hangs forever and pytest-timeout fires.
        await asyncio.wait_for(task, timeout=5.0)

        assert call_count == _MAX_CONSTRUCTION_FAILURES, (
            f"expected {_MAX_CONSTRUCTION_FAILURES} attempts, got {call_count}"
        )
        # No messages ever made it through — chat.jsonl never gets created.
        assert not chat_path.exists()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_successful_connect_resets_construction_failures(
        self,
        ws_server: FakeFanslyWSServer,
        config_wired: FanslyConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A transient construction failure followed by a successful connect
        resets the counter — so a later burst of construction failures
        gets the full quota again, not the leftover from before."""
        monkeypatch.setattr(
            "download.livestream_chat._CHAT_WS_RECONNECT_DELAY",
            0.01,
        )

        # Sequence: (N-1) failures, then a successful one (resets counter),
        # then (N-1) failures again — total = 2*(N-1) + 1 construction calls,
        # all without ever hitting the break. We set stop_event after the
        # successful connection to terminate cleanly.
        failure_quota = _MAX_CONSTRUCTION_FAILURES - 1
        burst_one_failures = 0
        burst_two_failures = 0
        successful_calls = 0
        successful_factory = _make_chat_ws_factory(ws_server.base_url)

        def flapping_factory() -> _ChildWebSocket:
            nonlocal burst_one_failures, burst_two_failures, successful_calls
            if burst_one_failures < failure_quota:
                burst_one_failures += 1
                raise TypeError("burst one")
            if successful_calls == 0:
                successful_calls += 1
                return successful_factory()
            burst_two_failures += 1
            raise TypeError("burst two")

        chat_room_id = snowflake_id()
        chat_path = tmp_path / "chat.jsonl"
        recorder = ChatRecorder(chat_path)
        stop_event = asyncio.Event()

        task = asyncio.create_task(
            _chat_ws_loop(
                config_wired,
                chat_room_id,
                recorder,
                stop_event,
                log_prefix="[test-reset]",
                ws_factory=flapping_factory,
            )
        )

        try:
            # Wait for the successful connection to land. Once it does, the
            # counter has been reset; we let burst_two start, then stop the
            # loop before burst_two can reach the cap.
            await ws_server.wait_for_chat_room_joined(chat_room_id, timeout=10.0)
        finally:
            # Give burst_two a beat to accumulate some failures (counter
            # reset means none of these reach the cap on their own).
            await asyncio.sleep(0.1)
            stop_event.set()
            await asyncio.wait_for(task, timeout=5.0)

        assert burst_one_failures == failure_quota
        assert successful_calls == 1
        # burst_two ran some attempts but never reached the cap — the reset
        # gave it the full quota again. The exact count is timing-dependent;
        # the load-bearing assertion is that we DIDN'T break early.
        assert burst_two_failures < _MAX_CONSTRUCTION_FAILURES, (
            f"counter didn't reset — burst_two reached {burst_two_failures} "
            f"(>= cap {_MAX_CONSTRUCTION_FAILURES}); break would have fired"
        )


class TestChatRecorderRegistry:
    """Cover register / unregister / route_ws_chat_message — the
    main-WS chat-delivery path (no chat-WS subprocess needed)."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_route_ws_chat_message_dispatches_to_registered_recorder(
        self,
        tmp_path: Path,
    ) -> None:
        """Register a recorder → route delivers; unregister → route drops."""
        chat_room_id = snowflake_id()
        chat_path = tmp_path / "chat.jsonl"
        recorder = ChatRecorder(chat_path)
        register_chat_recorder(chat_room_id, recorder)

        try:
            msg_id_1 = snowflake_id()
            await route_ws_chat_message(
                chat_room_id, {"id": msg_id_1, "content": "via main-ws"}
            )
            unregister_chat_recorder(chat_room_id)
            # Post-unregister: should NOT reach the recorder.
            await route_ws_chat_message(
                chat_room_id, {"id": snowflake_id(), "content": "after-unregister"}
            )
        finally:
            # Always clean up — even if assertions below fail.
            unregister_chat_recorder(chat_room_id)

        text = chat_path.read_text(encoding="utf-8")
        lines = [json.loads(line) for line in text.splitlines() if line.strip()]
        assert len(lines) == 1
        assert lines[0]["id"] == msg_id_1
        assert lines[0]["content"] == "via main-ws"

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_route_ws_chat_message_no_recorder_is_silent(
        self,
        tmp_path: Path,
    ) -> None:
        """No recorder registered for the room → ``route_ws_chat_message``
        returns silently without raising (livestream_chat.py:84-87 — the
        ``if recorder is not None`` guard)."""
        # No register_chat_recorder() call — registry is empty for this id.
        unknown_room = snowflake_id()
        # Should not raise.
        await route_ws_chat_message(unknown_room, {"id": snowflake_id()})
