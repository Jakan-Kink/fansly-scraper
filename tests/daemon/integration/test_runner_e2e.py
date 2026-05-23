"""End-to-end integration test for daemon.runner.run_daemon.

Drives the full poll -> filter -> download -> persist cycle in a single test.

Trade-off note: ``download_timeline``, ``download_stories``, ``download_messages``,
``download_wall``, and ``get_creator_account_info`` are stubbed with AsyncMock at
the ``daemon.runner.*`` import site.  Real download calls require a live Fansly
session; mocking at the module-import boundary (not the definition site) is the
accepted pattern for integration tests that verify the *orchestration* layer, not
the download logic itself.  Coverage for those functions is owned by
``tests/download/`` tests.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, PropertyMock, patch

import httpx
import pytest
import respx
from loguru import logger

from api.fansly import FanslyApi
from daemon.runner import run_daemon
from daemon.simulator import ActivitySimulator
from metadata.models import MonitorState
from tests.fixtures.api import make_ws_factory_for
from tests.fixtures.api.api_fixtures import dump_fansly_calls
from tests.fixtures.utils.test_isolation import snowflake_id


# ---------------------------------------------------------------------------
# URL aliases (url__startswith because ngsw-bypass is appended)
# ---------------------------------------------------------------------------

HOME_TIMELINE_URL = FanslyApi.TIMELINE_HOME_ENDPOINT
STORY_STATES_URL = FanslyApi.MEDIA_STORIES_FOLLOWING_ENDPOINT
TIMELINE_NEW_BASE_URL = FanslyApi.TIMELINE_NEW_ENDPOINT.format("")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# `saved_account`, `config_wired`, and `ws_server` come from the canonical
# fixtures (tests/fixtures/metadata/, tests/fixtures/core/, tests/fixtures/api/
# respectively) via the wildcard import in tests/conftest.py.
# Per Cat L policy: don't redefine here.


# ---------------------------------------------------------------------------
# End-to-end test
# ---------------------------------------------------------------------------


class TestRunDaemonE2E:
    """Full poll -> filter -> download -> persist cycle driven by run_daemon."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(20)
    async def test_poll_to_persist_cycle(
        self,
        config_wired,
        entity_store,
        saved_account,
        ws_server,
    ) -> None:
        """run_daemon completes one full poll -> filter -> download -> persist cycle.

        Setup:
          - One Account pre-seeded in the real PostgreSQL store.
          - HOME_TIMELINE_URL returns one post from that creator (new post —
            not in identity-map cache since the store was just created).
          - TIMELINE_NEW_BASE_URL/{creator_id} returns one non-pinned post
            newer than any baseline (first-run path: no MonitorState row yet
            so should_process_creator returns True immediately).
          - STORY_STATES_URL returns an empty list (no story churn).
          - All download_* functions are stubbed so no real Fansly session
            is needed.  The orchestration path — not the download logic — is
            what this test verifies.

        Assertions:
          - ws_server.auth_event.is_set() (WebSocket authenticated over real TCP).
          - ws_server.connections is empty at end (WebSocket stopped on shutdown).
          - download_timeline stub was called exactly once.
          - MonitorState row exists for the creator with a recent lastCheckedAt
            (set by mark_creator_processed after FullCreatorDownload).
        """
        creator_id: int = saved_account.id
        post_id: int = snowflake_id()

        # ── Diagnostic log capture (exposes mark_creator_processed early returns) ─
        captured_logs: list[str] = []
        sink_id = logger.add(
            lambda msg: captured_logs.append(str(msg)), level="WARNING"
        )

        # ── Spy on download_timeline ──────────────────────────────────────────
        download_called = asyncio.Event()

        async def _spy_download_timeline(*args: Any, **kwargs: Any) -> None:
            download_called.set()

        # ── DB polling: observe MonitorState row appearing ────────────────────
        async def _wait_for_monitor_state() -> MonitorState | None:
            for _ in range(200):  # 20s @ 100ms intervals
                state = await entity_store.get(MonitorState, creator_id)
                if state is not None:
                    return state
                await asyncio.sleep(0.1)
            return None

        # ── Shutdown coordination via injected stop_event ─────────────────────
        stop_event = asyncio.Event()

        home_timeline_route = None
        creator_timeline_route = None
        story_route = None

        async def _run_and_stop() -> None:
            """Run run_daemon and stop it after MonitorState row appears in DB."""
            task = asyncio.create_task(
                run_daemon(
                    config_wired,
                    ws_factory=make_ws_factory_for(ws_server.base_url),
                    stop_event=stop_event,
                )
            )

            # WS is a spawn'd subprocess (api/websocket.py); auth must land
            # before the poll cycle, or shutdown races in-flight handshake.
            try:
                await asyncio.wait_for(ws_server.auth_event.wait(), timeout=10.0)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                for log_line in captured_logs:
                    print(log_line)
                pytest.fail(
                    "WebSocket never authenticated within 10s — "
                    "subprocess spawn / connect / handshake stalled"
                )
                return

            # Poll until MonitorState row appears (real DB write by mark_creator_processed)
            state = await _wait_for_monitor_state()
            if state is None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if home_timeline_route is not None:
                    dump_fansly_calls(home_timeline_route.calls, "home_timeline")
                if creator_timeline_route is not None:
                    dump_fansly_calls(creator_timeline_route.calls, "creator_timeline")
                for log_line in captured_logs:
                    print(log_line)
                pytest.fail(
                    "Timed out waiting for MonitorState row — "
                    "poll->filter->download->persist cycle did not complete"
                )
                return

            # Signal shutdown directly — no asyncio patching required
            stop_event.set()

            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        # ── Patch fast-poll intervals and download stubs ──────────────────────
        try:
            with (
                respx.mock,
                patch.object(
                    ActivitySimulator,
                    "timeline_interval",
                    new_callable=PropertyMock,
                    return_value=0.05,
                ),
                patch.object(
                    ActivitySimulator,
                    "story_interval",
                    new_callable=PropertyMock,
                    return_value=0.05,
                ),
                patch(
                    "daemon.runner.download_timeline",
                    new=_spy_download_timeline,
                ),
                patch(
                    "daemon.runner.download_stories",
                    new=AsyncMock(),
                ),
                patch(
                    "daemon.runner.download_messages",
                    new=AsyncMock(),
                ),
                patch(
                    "daemon.runner.download_wall",
                    new=AsyncMock(),
                ),
                patch(
                    "daemon.runner.get_creator_account_info",
                    new=AsyncMock(),
                ),
            ):
                # ── RESPX routes (inside mock context) ───────────────────────
                respx.options(url__startswith=FanslyApi.BASE_URL).mock(
                    side_effect=[httpx.Response(200)]
                )

                home_timeline_route = respx.get(url__startswith=HOME_TIMELINE_URL).mock(
                    side_effect=[
                        httpx.Response(
                            200,
                            json={
                                "success": True,
                                "response": {
                                    "posts": [
                                        {
                                            "id": post_id,
                                            "accountId": creator_id,
                                            "createdAt": int(
                                                datetime.now(UTC).timestamp() * 1000
                                            ),
                                            "pinned": False,
                                        }
                                    ]
                                },
                            },
                        )
                    ]
                )

                # First run — no MonitorState row → should_process_creator
                # returns True without calling get_timeline at all.
                creator_timeline_route = respx.get(
                    url__startswith=f"{TIMELINE_NEW_BASE_URL}{creator_id}"
                ).mock(
                    side_effect=[
                        httpx.Response(
                            200,
                            json={
                                "success": True,
                                "response": {
                                    "posts": [
                                        {
                                            "id": post_id,
                                            "accountId": creator_id,
                                            "createdAt": int(
                                                datetime.now(UTC).timestamp() * 1000
                                            ),
                                            "pinned": False,
                                        }
                                    ]
                                },
                            },
                        )
                    ]
                )

                story_route = respx.get(url__startswith=STORY_STATES_URL).mock(
                    side_effect=[
                        httpx.Response(
                            200,
                            json={"success": True, "response": []},
                        )
                    ]
                )

                await _run_and_stop()
        finally:
            logger.remove(sink_id)

        # ── Assertions ────────────────────────────────────────────────────────

        # WebSocket lifecycle
        assert ws_server.auth_event.is_set(), (
            "WebSocket never authenticated against the scripted responder"
        )
        assert len(ws_server.connections) == 0, (
            f"WebSocket connections still open at end: {len(ws_server.connections)}"
        )

        # download_timeline was triggered (spy event was set)
        assert download_called.is_set(), (
            "download_timeline was never called — FullCreatorDownload item was "
            "not processed"
        )

        # MonitorState was persisted by mark_creator_processed
        state: MonitorState | None = await entity_store.get(MonitorState, creator_id)
        assert state is not None, (
            f"MonitorState row missing for creator {creator_id} — "
            "mark_creator_processed was never called"
        )
        assert state.lastCheckedAt is not None, (
            "MonitorState.lastCheckedAt is None — mark_creator_processed did "
            "not set the timestamp"
        )
        # lastCheckedAt must be a recent wall-clock time (within last 30 seconds)
        age = (datetime.now(UTC) - state.lastCheckedAt).total_seconds()
        assert age < 30, (
            f"MonitorState.lastCheckedAt is {age:.1f}s old — "
            "expected a value set within this test run"
        )

        # Diagnostic dump (always runs, only visible on failure)
        if home_timeline_route is not None:
            dump_fansly_calls(home_timeline_route.calls, "home_timeline")
        if creator_timeline_route is not None:
            dump_fansly_calls(creator_timeline_route.calls, "creator_timeline")
        if story_route is not None:
            dump_fansly_calls(story_route.calls, "story_states")
