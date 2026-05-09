"""Tests for download/stories.py — real-pipeline + real mark-viewed flow.

Post-Wave-2.5 rewrite: previously the gate tests used a ``gate_invocations``
fixture that monkeypatched FIVE module-level names —
``stories_module.get_store``, ``api.get_media_stories``,
``stories_module.process_media_stories``,
``stories_module.fetch_and_process_media``,
``stories_module.process_download_accessible_media`` — plus a FakeSpy on
``_mark_stories_viewed`` that recorded calls to a list. The gate
assertion checked the spy list rather than the real HTTP boundary,
which meant the test would pass even if ``_mark_stories_viewed`` itself
was broken (e.g., URL changed, body shape wrong).

Replacement strategy: the gate is a single conditional —
``if mark_viewed: _mark_stories_viewed(config, saved_stories)`` — so
verifying it via the REAL HTTP boundary at ``/api/v1/mediastory/view``
(via ``respx_route.call_count``) is the strongest possible signal. The
real ``_mark_stories_viewed`` must run for the True case and must NOT
run for the False case. If the function is ever renamed, the URL
changed, or the body shape regresses, the gate test fails — protecting
the daemon's "don't mark stories viewed in the background" invariant
from a much wider blast radius.

Edges patched:
- Fansly HTTP via ``respx_fansly_api``
- ``download_media`` (the CDN leaf — patched at both binding sites)
- ``input_enter_continue``, ``asyncio.sleep`` (timing/blocking I/O)

Real code throughout: ``get_store`` returns the test EntityStore;
``process_media_stories`` persists MediaStory + AccountMedia rows;
``fetch_and_process_media`` issues real ``/account/media`` HTTP via
respx; ``process_download_accessible_media`` runs full real
orchestration; ``_mark_stories_viewed`` issues real HTTP POSTs to
``/mediastory/view`` for each saved story.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from api.fansly import FanslyApi
from download.downloadstate import DownloadState
from download.stories import _mark_stories_viewed, download_stories
from download.types import DownloadType
from metadata import Account, MediaStoryState
from tests.fixtures.api import dump_fansly_calls
from tests.fixtures.download import FakeStory
from tests.fixtures.utils.test_isolation import snowflake_id


# FakeStory lives in tests/fixtures/download/story_factories.py.


# ---------------------------------------------------------------------------
# _mark_stories_viewed helper — direct RESPX boundary tests (unchanged)
# ---------------------------------------------------------------------------


@pytest.fixture
def config_with_api(test_config, fansly_api_with_respx):
    """FanslyConfig whose get_api() returns a real httpx-backed FanslyApi.

    Bypasses FanslyConfig.get_api()'s lazy-construction path (which would
    try to read missing auth tokens) by injecting the api directly.
    """
    test_config._api = fansly_api_with_respx
    return test_config


@pytest.mark.asyncio
@respx.mock
async def test_mark_stories_viewed_posts_once_per_story(config_with_api):
    """Helper POSTs /mediastory/view once per saved story with the story id."""
    respx.route(method="OPTIONS", url__startswith=FanslyApi.BASE_URL).mock(
        side_effect=lambda _r: httpx.Response(200)
    )
    route = respx.post(url__startswith=f"{FanslyApi.BASE_URL}mediastory/view").mock(
        side_effect=[
            httpx.Response(200, json={"storyId": "111", "accountId": "999"}),
            httpx.Response(200, json={"storyId": "222", "accountId": "999"}),
        ]
    )

    try:
        await _mark_stories_viewed(
            config_with_api,
            [FakeStory(id=111), FakeStory(id=222)],
        )
    finally:
        dump_fansly_calls(route.calls, label="mark_stories_viewed_posts_once")

    assert route.call_count == 2
    assert route.calls[0].request.read() == b'{"storyId":"111"}'
    assert route.calls[1].request.read() == b'{"storyId":"222"}'


@pytest.mark.asyncio
@respx.mock
async def test_mark_stories_viewed_swallows_single_failure(config_with_api):
    """One story failing with 500 must not prevent the others from being marked.

    ``api.mark_story_viewed`` uses ``http_session.post`` directly (no retry
    transport), so each story gets exactly one HTTP call. A 500 response
    doesn't raise httpx exceptions by default — the helper's broad
    try/except is there to catch connection errors or raise_for_status
    usage in future versions.
    """
    respx.route(method="OPTIONS", url__startswith=FanslyApi.BASE_URL).mock(
        side_effect=lambda _r: httpx.Response(200)
    )
    route = respx.post(url__startswith=f"{FanslyApi.BASE_URL}mediastory/view").mock(
        side_effect=[
            httpx.Response(200, json={"storyId": "111"}),
            httpx.Response(500),
            httpx.Response(200, json={"storyId": "333"}),
        ]
    )

    try:
        await _mark_stories_viewed(
            config_with_api,
            [FakeStory(id=111), FakeStory(id=222), FakeStory(id=333)],
        )
    finally:
        dump_fansly_calls(route.calls, label="mark_stories_viewed_swallows_500")

    assert route.call_count == 3
    request_bodies = [call.request.read() for call in route.calls]
    assert request_bodies == [
        b'{"storyId":"111"}',
        b'{"storyId":"222"}',
        b'{"storyId":"333"}',
    ]


@pytest.mark.asyncio
@respx.mock
async def test_mark_stories_viewed_empty_list_is_noop(config_with_api):
    """Passing an empty list does not hit the network."""
    respx.route(method="OPTIONS", url__startswith=FanslyApi.BASE_URL).mock(
        side_effect=lambda _r: httpx.Response(200)
    )
    route = respx.post(url__startswith=f"{FanslyApi.BASE_URL}mediastory/view").mock(
        side_effect=[httpx.Response(200)]
    )

    await _mark_stories_viewed(config_with_api, [])

    assert route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_mark_stories_viewed_swallows_post_exception(config_with_api):
    """Connection-level exception from POST → caught + logged, others continue.

    Covers lines 106-107 (``except Exception as e: print_warning(...)``).
    Unlike the 500-response case (which doesn't raise from httpx by
    default), a real connection error surfaces as ``httpx.ConnectError``.
    The helper must swallow it and proceed to the next story.
    """
    respx.route(method="OPTIONS", url__startswith=FanslyApi.BASE_URL).mock(
        side_effect=lambda _r: httpx.Response(200)
    )
    route = respx.post(url__startswith=f"{FanslyApi.BASE_URL}mediastory/view").mock(
        side_effect=[
            httpx.Response(200, json={"storyId": "111"}),
            httpx.ConnectError("simulated network drop"),
            httpx.Response(200, json={"storyId": "333"}),
        ]
    )

    try:
        await _mark_stories_viewed(
            config_with_api,
            [FakeStory(id=111), FakeStory(id=222), FakeStory(id=333)],
        )
    finally:
        dump_fansly_calls(route.calls, label="mark_stories_viewed_post_exception")

    # All 3 stories were attempted; the middle one failed but others
    # still succeeded.
    assert route.call_count == 3


# ---------------------------------------------------------------------------
# download_stories gate tests — real pipeline + real /mediastory/view boundary
# ---------------------------------------------------------------------------


def _stories_response(
    *,
    media_stories: list[dict] | None = None,
    account_media: list[dict] | None = None,
    accounts: list[dict] | None = None,
) -> dict:
    """Build a plausible ``/api/v1/mediastoriesnew`` response envelope."""
    return {
        "success": True,
        "response": {
            "mediaStories": media_stories or [],
            "aggregationData": {
                "accountMedia": account_media or [],
                "media": [],
                "accounts": accounts or [],
            },
        },
    }


def _account_media_entry(media_id: int, creator_id: int) -> dict:
    """AccountMedia payload meeting the production-pipeline shape requirements.

    See ``project_fansly_payload_shape_requirements.md`` for why each
    field is needed (nested ``media`` for FK, ``previewId`` to avoid
    KeyError, ``Key-Pair-Id`` in URL to avoid raw input() at
    media/media.py:185).
    """
    return {
        "id": media_id,
        "accountId": creator_id,
        "mediaId": media_id,
        "previewId": None,
        "createdAt": 1700000000,
        "deleted": False,
        "access": True,
        "mimetype": "image/jpeg",
        "media": {
            "id": media_id,
            "accountId": creator_id,
            "mimetype": "image/jpeg",
            "createdAt": 1700000000,
            "locations": [
                {
                    "locationId": "1",
                    "location": (
                        "https://cdn.example.com/img.jpg"
                        "?Policy=abc&Key-Pair-Id=xyz&Signature=def"
                    ),
                }
            ],
        },
    }


async def _seed_creator_account(entity_store, creator_id: int, username: str):
    """Pre-seed an Account row to satisfy FKs in MediaStory + MediaStoryState.

    ``process_media_stories`` saves MediaStory rows (which FK to
    accounts.id via ``mediaStories_accountId_fkey``). MediaStoryState
    has the same FK. Without this seed, the FK violation is caught by
    download_stories's outer try/except and the test silently skips the
    rest of the pipeline including ``_mark_stories_viewed``.
    """

    account = Account.model_validate(
        {"id": creator_id, "username": username, "createdAt": 1700000000}
    )
    await entity_store.save(account)


@pytest.mark.asyncio
async def test_download_stories_calls_mark_when_mark_viewed_true(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Default ``mark_viewed=True`` → real POST to /mediastory/view fires once.

    Asserts via the real HTTP boundary: the ``mark_view`` respx route
    receives one call. This is a stronger guarantee than the previous
    spy-list assertion because it would also fail if
    ``_mark_stories_viewed`` itself regressed (URL change, body shape).

    Real code throughout: ``process_media_stories`` persists the
    MediaStory row to the test DB, ``fetch_and_process_media`` makes
    a real ``/account/media`` HTTP call, ``_mark_stories_viewed`` issues
    a real POST to ``/mediastory/view``.
    """
    config = mock_config
    config.download_directory = tmp_path
    config.interactive = False

    creator_id = snowflake_id()
    story_id = snowflake_id()
    media_id = snowflake_id()

    await _seed_creator_account(entity_store, creator_id, f"st_{creator_id}")

    state = DownloadState(creator_id=creator_id, creator_name=f"st_{creator_id}")

    am_entry = _account_media_entry(media_id, creator_id)

    respx.get(f"{FanslyApi.BASE_URL}mediastoriesnew").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_stories_response(
                    media_stories=[
                        {
                            "id": story_id,
                            "accountId": creator_id,
                            "createdAt": 1700000000,
                        }
                    ],
                    account_media=[am_entry],
                ),
            )
        ]
    )
    respx.get(url__startswith=f"{FanslyApi.BASE_URL}account/media").mock(
        side_effect=[
            httpx.Response(200, json={"success": True, "response": [am_entry]})
        ]
    )

    # The real /mediastory/view boundary — the gate-under-test.
    mark_view_route = respx.post(
        url__startswith=f"{FanslyApi.BASE_URL}mediastory/view"
    ).mock(
        side_effect=[
            httpx.Response(
                200, json={"storyId": str(story_id), "accountId": str(creator_id)}
            )
        ]
    )

    _noop_download = AsyncMock(return_value=None)
    monkeypatch.setattr("download.common.download_media", _noop_download)
    monkeypatch.setattr("download.media.download_media", _noop_download)

    async def _noop(_):
        return None

    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    try:
        await download_stories(config, state)
    finally:
        dump_fansly_calls(respx.calls, label="download_stories_mark_true")

    assert state.download_type == DownloadType.STORIES
    assert mark_view_route.call_count == 1
    assert mark_view_route.calls[0].request.read() == (
        f'{{"storyId":"{story_id}"}}'.encode()
    )


@pytest.mark.asyncio
async def test_download_stories_skips_mark_when_mark_viewed_false(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """``mark_viewed=False`` (the daemon case) → real POST to /mediastory/view NEVER fires.

    Regression guard for the monitoring daemon: if this assertion ever
    fails, the daemon will start marking stories viewed in the user's
    real Fansly account, which is a user-visible bug.

    Asserts via the real HTTP boundary (``mark_view_route.call_count == 0``)
    instead of a spy list — the boundary assertion catches both gate
    regressions AND any future change that bypasses the gate by calling
    the API directly.
    """
    config = mock_config
    config.download_directory = tmp_path
    config.interactive = False

    creator_id = snowflake_id()
    story_id = snowflake_id()
    media_id = snowflake_id()

    await _seed_creator_account(entity_store, creator_id, f"st_{creator_id}")

    state = DownloadState(creator_id=creator_id, creator_name=f"st_{creator_id}")

    am_entry = _account_media_entry(media_id, creator_id)

    respx.get(f"{FanslyApi.BASE_URL}mediastoriesnew").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_stories_response(
                    media_stories=[
                        {
                            "id": story_id,
                            "accountId": creator_id,
                            "createdAt": 1700000000,
                        }
                    ],
                    account_media=[am_entry],
                ),
            )
        ]
    )
    respx.get(url__startswith=f"{FanslyApi.BASE_URL}account/media").mock(
        side_effect=[
            httpx.Response(200, json={"success": True, "response": [am_entry]})
        ]
    )

    # Same route as above — but assert it's NEVER called when gate is closed.
    mark_view_route = respx.post(
        url__startswith=f"{FanslyApi.BASE_URL}mediastory/view"
    ).mock(side_effect=[httpx.Response(200, json={"storyId": str(story_id)})])

    _noop_download = AsyncMock(return_value=None)
    monkeypatch.setattr("download.common.download_media", _noop_download)
    monkeypatch.setattr("download.media.download_media", _noop_download)

    async def _noop(_):
        return None

    monkeypatch.setattr("download.common.input_enter_continue", _noop)
    monkeypatch.setattr("download.media.input_enter_continue", _noop)

    try:
        await download_stories(config, state, mark_viewed=False)
    finally:
        dump_fansly_calls(respx.calls, label="download_stories_mark_false")

    assert state.download_type == DownloadType.STORIES
    # The critical regression guard: zero POSTs to /mediastory/view.
    assert mark_view_route.call_count == 0


# ---------------------------------------------------------------------------
# download_stories edge-branch coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_stories_early_exit_when_no_active_stories_cached(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Cached MediaStoryState with hasActiveStories=False → return without HTTP.

    Covers line 48 (the early-exit when ``story_state.hasActiveStories``
    is False). Pre-seeds a real MediaStoryState into the EntityStore so
    the production ``store.get(MediaStoryState, creator_id)`` returns it.
    """

    config = mock_config
    config.download_directory = tmp_path

    creator_id = snowflake_id()
    state = DownloadState(creator_id=creator_id, creator_name="no_stories")

    # MediaStoryState FKs to accounts.id; seed account first.
    await _seed_creator_account(entity_store, creator_id, "no_stories")

    # Real cache seed: MediaStoryState says no active stories.
    story_state = MediaStoryState.model_validate(
        {"accountId": creator_id, "hasActiveStories": False}
    )
    await entity_store.save(story_state)

    # If the early-exit path is broken, the production code would try to
    # hit /mediastoriesnew. We DON'T register that route — any HTTP call
    # would error (no respx match), which would be visible in the dump.
    try:
        await download_stories(config, state)
    finally:
        dump_fansly_calls(respx.calls, label="download_stories_early_exit")

    # No state.download_type assignment — early-exit happened before line 52.
    # The state.download_type default is NOTSET.
    assert state.download_type == DownloadType.NOTSET


@pytest.mark.asyncio
async def test_download_stories_empty_media_returns_early(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Story present but no accountMedia → "No downloadable media" + return.

    Covers lines 78-79 (``if not media_ids: print_info(...); return``).
    """
    config = mock_config
    config.download_directory = tmp_path

    creator_id = snowflake_id()
    story_id = snowflake_id()
    await _seed_creator_account(entity_store, creator_id, "no_media")
    state = DownloadState(creator_id=creator_id, creator_name="no_media")

    respx.get(f"{FanslyApi.BASE_URL}mediastoriesnew").mock(
        side_effect=[
            httpx.Response(
                200,
                json=_stories_response(
                    media_stories=[
                        {
                            "id": story_id,
                            "accountId": creator_id,
                            "createdAt": 1700000000,
                        }
                    ],
                    account_media=[],  # empty → triggers early return
                ),
            )
        ]
    )

    # No mark-view route registered — should never be hit since we return
    # before reaching the gate.
    mark_view_route = respx.post(
        url__startswith=f"{FanslyApi.BASE_URL}mediastory/view"
    ).mock(side_effect=[httpx.Response(200)])

    await download_stories(config, state)

    assert mark_view_route.call_count == 0
    assert state.download_type == DownloadType.STORIES


@pytest.mark.asyncio
async def test_download_stories_no_stories_in_response(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Empty mediaStories list → "No active stories found" + return.

    Covers lines 61-63 (the ``if not media_stories`` early return).
    """
    config = mock_config
    config.download_directory = tmp_path

    creator_id = snowflake_id()
    state = DownloadState(creator_id=creator_id, creator_name="empty")

    respx.get(f"{FanslyApi.BASE_URL}mediastoriesnew").mock(
        side_effect=[
            httpx.Response(200, json=_stories_response(media_stories=[])),
        ]
    )

    mark_view_route = respx.post(
        url__startswith=f"{FanslyApi.BASE_URL}mediastory/view"
    ).mock(side_effect=[httpx.Response(200)])

    await download_stories(config, state)

    assert mark_view_route.call_count == 0


@pytest.mark.asyncio
async def test_download_stories_no_creator_id_skips_cache_check(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """state.creator_id falsy → skip cache lookup, go straight to API call.

    Covers partial branch 45->50: when ``state.creator_id`` is None/0,
    the cache-lookup block is skipped entirely and execution falls
    through to the ``print_info("Checking for active Stories...")`` line.

    The API call still happens with ``creator_id=None`` — but the test
    just verifies the cache-skip branch fired and the function exits
    cleanly (the empty-stories response means no FK pressure).
    """
    config = mock_config
    config.download_directory = tmp_path

    # creator_id is None — exercises the False branch of `if state.creator_id`.
    state = DownloadState(creator_id=None, creator_name="no_id")

    respx.get(f"{FanslyApi.BASE_URL}mediastoriesnew").mock(
        side_effect=[
            httpx.Response(200, json=_stories_response(media_stories=[])),
        ]
    )

    await download_stories(config, state)

    # The function reached the API call (cache check was skipped).
    assert state.download_type == DownloadType.STORIES


@pytest.mark.asyncio
async def test_download_stories_swallows_outer_exception(
    respx_fansly_api, entity_store, mock_config, tmp_path, monkeypatch
):
    """Non-2xx from /mediastoriesnew → outer ``except Exception`` swallows.

    Covers lines 92-93 (the catch-all ``except Exception as e:
    print_warning(...)``). A 4xx response causes raise_for_status to
    raise HTTPStatusError, caught by the outer handler. The function
    returns normally without raising up.
    """
    config = mock_config
    config.download_directory = tmp_path

    creator_id = snowflake_id()
    state = DownloadState(creator_id=creator_id, creator_name="boom")

    respx.get(f"{FanslyApi.BASE_URL}mediastoriesnew").mock(
        side_effect=[httpx.Response(403, text="Forbidden")]
    )

    # Must not raise.
    await download_stories(config, state)

    # download_type was set before the exception fired.
    assert state.download_type == DownloadType.STORIES
