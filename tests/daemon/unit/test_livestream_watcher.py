"""Unit tests for ``daemon.livestream_watcher._poll_and_diff``.

Pins the v0.14.2 scope filter on the watcher's spawn loop. Pre-fix the
watcher iterated every entry returned by
``/api/v1/streaming/followingstreams/online`` (every followed creator
currently live) and spawned a ``_record_stream`` task per entry — no
filter against ``targeted_creator.usernames`` / ``-u``. The fix
introduces a single ``config.is_username_in_scope(username)`` gate
before the spawn; username comes off the API payload directly, so the
gate stays sync and skips the metadata-store roundtrip.

Test shape: real ``respx_fansly_api`` HTTP boundary (mocks just the
following-streams endpoint), real FanslyConfig + EntityStore, real
``_poll_and_diff`` invocation. The leaf ``_record_stream`` coroutine
is replaced with a recorder so the test never tries to actually mux
livestream segments.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

import daemon.livestream_watcher as watcher_module
from daemon.livestream_watcher import _poll_and_diff
from tests.fixtures.api import dump_fansly_calls


STREAMING_ONLINE_URL = (
    "https://apiv3.fansly.com/api/v1/streaming/followingstreams/online"
)


def _streaming_account(account_id: int, username: str) -> dict:
    """Build a single account entry as it appears under
    ``response.aggregationData.accounts`` in the live-streams payload.

    Mirrors the shape documented in ``project_livestream_ivs.md``:
    ``streaming.channel`` carries the IVS playback URL and a nested
    ``stream`` session block; both ``status=2`` to mark live.
    """
    channel_id = account_id + 10_000
    stream_id = account_id + 20_000
    return {
        "id": account_id,
        "username": username,
        "streaming": {
            "accountId": account_id,
            "enabled": True,
            "channel": {
                "id": channel_id,
                "accountId": account_id,
                "playbackUrl": (
                    f"https://{account_id}.us-east-1.playback.live-video.net"
                    f"/api/video/v1/us-east-1.<aws-acct>.channel.<token>.m3u8"
                ),
                "chatRoomId": account_id + 30_000,
                "status": 2,
                "stream": {
                    "id": stream_id,
                    "channelId": channel_id,
                    "accountId": account_id,
                    "title": f"live broadcast by {username}",
                    "status": 2,
                    "viewerCount": 42,
                    "startedAt": 1_700_000_000_000,
                },
            },
        },
    }


class TestPollAndDiffScopeFilter:
    """Coverage for the watcher's spawn-time scope filter (#94)."""

    @pytest.mark.asyncio
    async def test_only_in_scope_creators_get_recordings_spawned(
        self,
        respx_fansly_api,
        config_wired,
        entity_store,
        isolate_active_recordings,
        monkeypatch,
    ):
        """Two live creators in the API payload — only the one in
        ``user_names`` ends up in ``_active_recordings``.

        Reproduces the #94 reporter's exact configuration: a single
        ``usernames`` entry, ``use_following=False``, and many
        currently-live followed creators. Pre-fix every live creator
        would spawn a recording task; post-fix only the listed
        username does.
        """
        config_wired.use_following = False
        config_wired.user_names = {"alice"}
        config_wired.monitoring_livestream_recording_enabled = True

        alice_id = 1_111_111_111_111_111_111
        bob_id = 2_222_222_222_222_222_222

        # Replace the actual recording coroutine with an awaitable
        # that just returns. The watcher wraps it in
        # ``asyncio.create_task``; we don't want a real recorder
        # firing in the test event loop.
        async def _fake_record_stream(*args, **kwargs):
            return None

        monkeypatch.setattr(watcher_module, "_record_stream", _fake_record_stream)

        route = respx.get(url__startswith=STREAMING_ONLINE_URL).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "streams": [],
                            "aggregationData": {
                                "accounts": [
                                    _streaming_account(alice_id, "alice"),
                                    _streaming_account(bob_id, "bob"),
                                ]
                            },
                        },
                    },
                )
            ]
        )

        stop_event = asyncio.Event()
        try:
            await _poll_and_diff(config_wired, stop_event)
        finally:
            dump_fansly_calls(route.calls, "poll_and_diff_scope")

        with watcher_module._recordings_lock:
            recorded_ids = set(watcher_module._active_recordings.keys())

        assert alice_id in recorded_ids, (
            "in-scope creator alice should have a recording task spawned"
        )
        assert bob_id not in recorded_ids, (
            "out-of-scope creator bob should NOT have a recording task — "
            "this is the #94 livestream-side bug"
        )
        assert recorded_ids == {alice_id}, (
            f"unexpected extras in _active_recordings: {recorded_ids - {alice_id}}"
        )

    @pytest.mark.asyncio
    async def test_use_following_lets_every_live_creator_through(
        self,
        respx_fansly_api,
        config_wired,
        entity_store,
        isolate_active_recordings,
        monkeypatch,
    ):
        """Under ``-uf`` / ``-ufp``, scope short-circuits True and every
        followed creator's live broadcast gets a recording task.

        Pins that the watcher fix doesn't accidentally start filtering
        ``-uf`` mode (where the operator explicitly asked for the
        whole following set).
        """
        config_wired.use_following = True
        config_wired.user_names = None
        config_wired.monitoring_livestream_recording_enabled = True

        alice_id = 3_333_333_333_333_333_333
        bob_id = 4_444_444_444_444_444_444

        async def _fake_record_stream(*args, **kwargs):
            return None

        monkeypatch.setattr(watcher_module, "_record_stream", _fake_record_stream)

        route = respx.get(url__startswith=STREAMING_ONLINE_URL).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": {
                            "streams": [],
                            "aggregationData": {
                                "accounts": [
                                    _streaming_account(alice_id, "alice"),
                                    _streaming_account(bob_id, "bob"),
                                ]
                            },
                        },
                    },
                )
            ]
        )

        stop_event = asyncio.Event()
        try:
            await _poll_and_diff(config_wired, stop_event)
        finally:
            dump_fansly_calls(route.calls, "poll_and_diff_uf")

        with watcher_module._recordings_lock:
            recorded_ids = set(watcher_module._active_recordings.keys())

        assert recorded_ids == {alice_id, bob_id}, (
            f"-uf should let both through; got {recorded_ids}"
        )

    @pytest.mark.asyncio
    async def test_disabled_short_circuits_before_api_call(
        self,
        respx_fansly_api,
        config_wired,
        entity_store,
        isolate_active_recordings,
        monkeypatch,
    ):
        """When ``monitoring_livestream_recording_enabled=False``, the
        watcher returns before hitting the API — no respx call, no
        recordings spawned.

        Pins the early-exit gate; complements the v0.14.0 default
        flip (which only changes the schema default — the runtime
        gate at the watcher itself is what actually suppresses the
        work). Together the gate + default flip ensure the watcher
        is fully off for operators who haven't opted in.
        """
        config_wired.monitoring_livestream_recording_enabled = False

        async def _fake_record_stream(*args, **kwargs):
            return None

        monkeypatch.setattr(watcher_module, "_record_stream", _fake_record_stream)

        route = respx.get(url__startswith=STREAMING_ONLINE_URL).mock(
            side_effect=[httpx.Response(200, json={})]
        )

        stop_event = asyncio.Event()
        try:
            await _poll_and_diff(config_wired, stop_event)
        finally:
            dump_fansly_calls(route.calls, "poll_and_diff_disabled")

        assert route.call_count == 0, (
            "watcher should short-circuit before HTTP when recording is disabled"
        )

        with watcher_module._recordings_lock:
            recorded_ids = set(watcher_module._active_recordings.keys())
        assert recorded_ids == set(), (
            f"no recordings should spawn when disabled; got {recorded_ids}"
        )
