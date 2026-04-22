"""Unit tests for download/stories.py.

Covers the `_mark_stories_viewed` helper that POSTs to
`/api/v1/mediastory/view` for each saved story, and the `mark_viewed`
gate on `download_stories` that the monitoring daemon toggles to avoid
affecting the user's real Fansly UX.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest
import respx

from download import stories as stories_module
from download.downloadstate import DownloadState
from download.stories import _mark_stories_viewed, download_stories


@dataclass
class _FakeStory:
    """Minimal story object exposing only `.id` as the helper needs."""

    id: int


class _SavedStoryStub:
    """Stands in for a metadata.MediaStory row returned by process_media_stories."""

    def __init__(self, story_id: int) -> None:
        self.id = story_id


@pytest.fixture
def config_with_api(test_config, fansly_api_with_respx):
    """FanslyConfig whose get_api() returns a real httpx-backed FanslyApi.

    Bypasses FanslyConfig.get_api()'s lazy-construction path (which would
    try to read missing auth tokens) by injecting the api directly.
    """
    test_config._api = fansly_api_with_respx
    return test_config


# ---------------------------------------------------------------------------
# _mark_stories_viewed helper — direct RESPX boundary tests
# ---------------------------------------------------------------------------


@respx.mock
def test_mark_stories_viewed_posts_once_per_story(config_with_api):
    """Helper POSTs /mediastory/view once per saved story with the story id."""
    route = respx.post(
        url__startswith="https://apiv3.fansly.com/api/v1/mediastory/view"
    ).mock(
        side_effect=[
            httpx.Response(200, json={"storyId": "111", "accountId": "999"}),
            httpx.Response(200, json={"storyId": "222", "accountId": "999"}),
        ]
    )

    try:
        _mark_stories_viewed(
            config_with_api,
            [_FakeStory(id=111), _FakeStory(id=222)],
        )

        assert route.call_count == 2
        assert route.calls[0].request.read() == b'{"storyId":"111"}'
        assert route.calls[1].request.read() == b'{"storyId":"222"}'
    finally:
        for i, call in enumerate(route.calls):
            print(f"  call[{i}]: {call.request.method} {call.request.url}")


@respx.mock
def test_mark_stories_viewed_swallows_single_failure(config_with_api):
    """One story failing with 500 must not prevent the others from being marked.

    `api.mark_story_viewed` uses `http_session.post` directly (no retry
    transport), so each story gets exactly one HTTP call. A 500 response
    doesn't raise httpx exceptions by default — the helper's broad
    try/except is there to catch connection errors or raise_for_status
    usage in future versions.
    """
    route = respx.post(
        url__startswith="https://apiv3.fansly.com/api/v1/mediastory/view"
    ).mock(
        side_effect=[
            httpx.Response(200, json={"storyId": "111"}),
            httpx.Response(500),
            httpx.Response(200, json={"storyId": "333"}),
        ]
    )

    try:
        _mark_stories_viewed(
            config_with_api,
            [_FakeStory(id=111), _FakeStory(id=222), _FakeStory(id=333)],
        )

        # All three stories are posted exactly once; the 500 for story 222
        # does not prevent story 333 from being attempted.
        assert route.call_count == 3
        request_bodies = [call.request.read() for call in route.calls]
        assert request_bodies == [
            b'{"storyId":"111"}',
            b'{"storyId":"222"}',
            b'{"storyId":"333"}',
        ]
    finally:
        for i, call in enumerate(route.calls):
            print(f"  call[{i}]: {call.request.method} {call.response.status_code}")


@respx.mock
def test_mark_stories_viewed_empty_list_is_noop(config_with_api):
    """Passing an empty list does not hit the network."""
    route = respx.post(
        url__startswith="https://apiv3.fansly.com/api/v1/mediastory/view"
    ).mock(side_effect=[httpx.Response(200)])

    _mark_stories_viewed(config_with_api, [])

    assert route.call_count == 0


# ---------------------------------------------------------------------------
# Gate tests — download_stories(mark_viewed=True|False) branching.
#
# These tests verify the ONE-LINE conditional at the end of download_stories
# that decides whether _mark_stories_viewed fires. Isolation here requires
# stubbing the upstream pipeline (metadata store lookup, API response,
# metadata persistence, media fetch/download) because those aren't under
# test — the subject is the branching logic. We stub them via monkeypatch
# on module-level names, which is the canonical pytest pattern for testing
# "caller dispatches correctly to callable X" without exercising X.
#
# Critical regression coverage: the daemon MUST be able to pass
# mark_viewed=False and have mark_story_viewed never fire, otherwise the
# background daemon would mark the user's stories as watched.
# ---------------------------------------------------------------------------


class _FakeStore:
    """Async store stub — get() always returns None so the creator has no cached state."""

    async def get(self, model, entity_id):
        return None


def _build_canned_stories_response() -> httpx.Response:
    """Build an httpx.Response that satisfies the download_stories parser.

    The request is attached explicitly — `httpx.Response.raise_for_status()`
    raises a generic RuntimeError when constructed without a request, which
    is then swallowed by download_stories's outer try/except and would mask
    the actual gate behaviour we're testing.
    """
    canned_request = httpx.Request(
        "GET", "https://apiv3.fansly.com/api/v1/mediastoriesnew"
    )
    return httpx.Response(
        200,
        json={
            "success": True,
            "response": {
                "mediaStories": [{"id": "777", "accountId": "42"}],
                "aggregationData": {
                    "accountMedia": [
                        {"id": "888", "accountId": "42", "mediaId": "888"}
                    ],
                    "media": [],
                },
            },
        },
        request=canned_request,
    )


@pytest.fixture
def gate_invocations(monkeypatch, config_with_api):
    """Monkeypatch the upstream pipeline so download_stories reaches the gate.

    Returns the list that records `_mark_stories_viewed` invocations.
    """

    monkeypatch.setattr(stories_module, "get_store", _FakeStore)

    canned = _build_canned_stories_response()

    def fake_get_media_stories(creator_id):
        return canned

    monkeypatch.setattr(
        config_with_api._api,
        "get_media_stories",
        fake_get_media_stories,
    )

    async def fake_process_media_stories(config, response):
        return [_SavedStoryStub(story_id=777)]

    monkeypatch.setattr(
        stories_module,
        "process_media_stories",
        fake_process_media_stories,
    )

    async def fake_fetch(config, state, media_ids):
        return {}  # nothing accessible — download pipeline becomes a no-op

    monkeypatch.setattr(stories_module, "fetch_and_process_media", fake_fetch)

    async def fake_process_download(config, state, accessible):
        return True

    monkeypatch.setattr(
        stories_module,
        "process_download_accessible_media",
        fake_process_download,
    )

    invocations: list[tuple] = []

    def spy_mark(config, saved_stories):
        invocations.append((config, list(saved_stories)))

    monkeypatch.setattr(stories_module, "_mark_stories_viewed", spy_mark)

    return invocations


@pytest.mark.asyncio
async def test_download_stories_calls_mark_when_mark_viewed_true(
    gate_invocations, config_with_api
):
    """Default mark_viewed=True → _mark_stories_viewed fires once."""
    state = DownloadState(creator_id=42, creator_name="test_creator")

    await download_stories(config_with_api, state)

    assert len(gate_invocations) == 1
    _, saved = gate_invocations[0]
    assert [s.id for s in saved] == [777]


@pytest.mark.asyncio
async def test_download_stories_skips_mark_when_mark_viewed_false(
    gate_invocations, config_with_api
):
    """mark_viewed=False (the daemon case) → _mark_stories_viewed is NOT called.

    This is the regression guard for the monitoring daemon: if this assertion
    ever fails, the daemon will start marking stories viewed in the user's
    real Fansly account, which is a user-visible bug.
    """
    state = DownloadState(creator_id=42, creator_name="test_creator")

    await download_stories(config_with_api, state, mark_viewed=False)

    assert gate_invocations == []
