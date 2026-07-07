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
import contextlib

import httpx
import pytest
import respx

import daemon.livestream_watcher as watcher_module
from api.fansly import FanslyApi
from daemon.livestream_watcher import _poll_and_diff
from tests.fixtures.api import dump_fansly_calls
from tests.fixtures.utils.test_isolation import snowflake_id


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

        route = respx.get(
            url__startswith=FanslyApi.STREAMING_FOLLOWING_ONLINE_ENDPOINT
        ).mock(
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

        route = respx.get(
            url__startswith=FanslyApi.STREAMING_FOLLOWING_ONLINE_ENDPOINT
        ).mock(
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

        route = respx.get(
            url__startswith=FanslyApi.STREAMING_FOLLOWING_ONLINE_ENDPOINT
        ).mock(side_effect=[httpx.Response(200, json={})])

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


def _online_response(accounts: list[dict]) -> dict:
    """Wrap *accounts* in the followingstreams/online response envelope."""
    return {
        "success": True,
        "response": {
            "streams": [],
            "aggregationData": {"accounts": accounts},
        },
    }


class TestStartLivestreamWatcher:
    """daemon.livestream_watcher.start_livestream_watcher (lines 62-70)."""

    @pytest.mark.asyncio
    async def test_creates_named_task_and_returns_it(self, config_wired, monkeypatch):
        """create_task wraps _watcher_loop, logs the interval, returns the task."""

        async def _noop_loop(_cfg, _ev):
            return None

        monkeypatch.setattr(watcher_module, "_watcher_loop", _noop_loop)
        stop_event = asyncio.Event()

        task = watcher_module.start_livestream_watcher(config_wired, stop_event)
        try:
            assert task.get_name() == "livestream-watcher"
        finally:
            await task
        assert task.done()


class TestStopAllRecordings:
    """daemon.livestream_watcher.stop_all_recordings (lines 80-106)."""

    @pytest.mark.asyncio
    async def test_noop_when_no_active_recordings(self, isolate_active_recordings):
        """Lines 83-84: empty recording set returns immediately."""
        await watcher_module.stop_all_recordings()  # must not raise
        assert watcher_module._active_recordings == {}

    @pytest.mark.asyncio
    async def test_signals_stop_and_awaits_completion(self, isolate_active_recordings):
        """Lines 80-96: each rec_stop is set and the tasks are awaited."""
        rec_stop = asyncio.Event()

        async def _body():
            await rec_stop.wait()

        task = asyncio.create_task(_body())
        with watcher_module._recordings_lock:
            watcher_module._active_recordings[1] = (task, rec_stop)

        await watcher_module.stop_all_recordings()

        assert rec_stop.is_set()
        assert task.done()

    @pytest.mark.asyncio
    async def test_cancels_recordings_that_exceed_timeout(
        self, isolate_active_recordings, monkeypatch
    ):
        """Lines 97-105: a recorder that outlives the shutdown timeout is torn down.

        ``asyncio.gather`` awaits its children's cancellation before propagating
        the timeout, so the straggler ``task.cancel()`` at line 106 is a
        defensive guard that isn't deterministically reachable from here; this
        test covers the timeout → pending-sweep path that precedes it.
        """
        monkeypatch.setattr(watcher_module, "_SHUTDOWN_TIMEOUT", 0.05)
        rec_stop = asyncio.Event()

        async def _stuck():
            await asyncio.sleep(30)  # never observes rec_stop

        task = asyncio.create_task(_stuck())
        with watcher_module._recordings_lock:
            watcher_module._active_recordings[1] = (task, rec_stop)

        await watcher_module.stop_all_recordings()  # times out → cancels stragglers

        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert task.cancelled()


class TestWatcherLoop:
    """daemon.livestream_watcher._watcher_loop (lines 117-142)."""

    @pytest.mark.asyncio
    async def test_runs_a_cycle_then_stops_on_event(self, config_wired, monkeypatch):
        """Salvage → one poll cycle → stop_event ends the loop and finalizes."""

        async def _noop_salvage(_cfg, _ev):
            return None

        monkeypatch.setattr(watcher_module, "_salvage_orphan_segments", _noop_salvage)
        config_wired.monitoring_livestream_poll_interval_seconds = 0.01
        calls = []

        async def _fake_poll(_cfg, ev):
            calls.append(1)
            ev.set()  # end the loop after one cycle

        monkeypatch.setattr(watcher_module, "_poll_and_diff", _fake_poll)

        await watcher_module._watcher_loop(config_wired, asyncio.Event())
        assert calls == [1]

    @pytest.mark.asyncio
    async def test_poll_error_is_logged_and_loop_continues(
        self, config_wired, monkeypatch
    ):
        """Lines 127-132: a poll exception is caught; the loop retries then stops."""

        async def _noop_salvage(_cfg, _ev):
            return None

        monkeypatch.setattr(watcher_module, "_salvage_orphan_segments", _noop_salvage)
        config_wired.monitoring_livestream_poll_interval_seconds = 0.01
        state = {"n": 0}

        async def _flaky_poll(_cfg, ev):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("poll boom")  # caught at 127-132
            ev.set()  # second cycle ends the loop

        monkeypatch.setattr(watcher_module, "_poll_and_diff", _flaky_poll)

        await watcher_module._watcher_loop(config_wired, asyncio.Event())
        assert state["n"] == 2


class TestPollAndDiffBranches:
    """Remaining _poll_and_diff arcs: API failure, non-dict, per-account skips,
    ended-stream signalling, and finished-task reaping."""

    @pytest.mark.asyncio
    async def test_api_error_is_logged_and_returns(
        self, respx_fansly_api, config_wired, isolate_active_recordings
    ):
        """Lines 157-162: an API failure is caught and the cycle returns cleanly."""
        config_wired.monitoring_livestream_recording_enabled = True
        # A connection error is re-raised on every (retried) attempt, so the
        # call ultimately propagates into the except regardless of retry count.
        route = respx.get(
            url__startswith=FanslyApi.STREAMING_FOLLOWING_ONLINE_ENDPOINT
        ).mock(side_effect=httpx.ConnectError("network down"))

        try:
            await _poll_and_diff(config_wired, asyncio.Event())  # must not raise
        finally:
            dump_fansly_calls(route.calls, "poll_and_diff_api_error")

        assert watcher_module._active_recordings == {}

    @pytest.mark.asyncio
    async def test_non_dict_response_returns_without_spawning(
        self, respx_fansly_api, config_wired, isolate_active_recordings
    ):
        """Branch 164->165: a non-dict response body short-circuits."""
        config_wired.monitoring_livestream_recording_enabled = True
        route = respx.get(
            url__startswith=FanslyApi.STREAMING_FOLLOWING_ONLINE_ENDPOINT
        ).mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": []})]
        )

        try:
            await _poll_and_diff(config_wired, asyncio.Event())
        finally:
            dump_fansly_calls(route.calls, "poll_and_diff_nondict")

        assert watcher_module._active_recordings == {}

    @pytest.mark.asyncio
    async def test_skips_accounts_without_valid_live_channel(
        self, respx_fansly_api, config_wired, isolate_active_recordings
    ):
        """Lines 187, 190-195, 197: missing-streaming / malformed / not-live skipped."""
        config_wired.use_following = True  # in scope for anyone live
        config_wired.monitoring_livestream_recording_enabled = True

        no_streaming = {"id": 1, "username": "a"}  # no 'streaming' → 186-187
        malformed = {
            "id": 2,
            "username": "b",
            "streaming": 99999,
        }  # validate fails 190-195
        offline = _streaming_account(snowflake_id(), "c")
        offline["streaming"]["channel"]["status"] = 1  # valid but not live → 197

        route = respx.get(
            url__startswith=FanslyApi.STREAMING_FOLLOWING_ONLINE_ENDPOINT
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=_online_response([no_streaming, malformed, offline]),
                )
            ]
        )

        try:
            await _poll_and_diff(config_wired, asyncio.Event())
        finally:
            dump_fansly_calls(route.calls, "poll_and_diff_skips")

        # None of the three is a valid live in-scope creator → no recordings.
        assert watcher_module._active_recordings == {}

    @pytest.mark.asyncio
    async def test_ended_stream_signals_its_recorder_to_stop(
        self, respx_fansly_api, config_wired, isolate_active_recordings
    ):
        """Lines 232-242: a creator no longer live → its rec_stop is set."""
        config_wired.use_following = True
        config_wired.monitoring_livestream_recording_enabled = True
        gone_id = snowflake_id()
        rec_stop = asyncio.Event()

        async def _body():
            await rec_stop.wait()

        task = asyncio.create_task(_body())
        with watcher_module._recordings_lock:
            watcher_module._active_recordings[gone_id] = (task, rec_stop)

        # Empty live list → gone_id is active but not live → signal stop.
        route = respx.get(
            url__startswith=FanslyApi.STREAMING_FOLLOWING_ONLINE_ENDPOINT
        ).mock(side_effect=[httpx.Response(200, json=_online_response([]))])

        try:
            await _poll_and_diff(config_wired, asyncio.Event())
        finally:
            dump_fansly_calls(route.calls, "poll_and_diff_ended")

        assert rec_stop.is_set()
        await task  # _body returns now that rec_stop is set

    @pytest.mark.asyncio
    async def test_finished_recording_task_is_reaped(
        self, respx_fansly_api, config_wired, isolate_active_recordings
    ):
        """Lines 245-248: a creator whose recording task is done() is removed."""
        config_wired.use_following = True
        config_wired.monitoring_livestream_recording_enabled = True
        done_id = snowflake_id()

        async def _instant():
            return None

        task = asyncio.create_task(_instant())
        await task  # already finished
        with watcher_module._recordings_lock:
            watcher_module._active_recordings[done_id] = (task, asyncio.Event())

        route = respx.get(
            url__startswith=FanslyApi.STREAMING_FOLLOWING_ONLINE_ENDPOINT
        ).mock(side_effect=[httpx.Response(200, json=_online_response([]))])

        try:
            await _poll_and_diff(config_wired, asyncio.Event())
        finally:
            dump_fansly_calls(route.calls, "poll_and_diff_reap")

        with watcher_module._recordings_lock:
            assert done_id not in watcher_module._active_recordings
