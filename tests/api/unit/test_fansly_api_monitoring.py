"""Unit tests for FanslyApi monitoring methods.

Tests for get_home_timeline and get_story_states_following, which are used by
the daemon monitor to detect new content and active stories fleet-wide.
"""

import httpx
import pytest
import respx

from tests.fixtures.api.api_fixtures import dump_fansly_calls


HOME_TIMELINE_URL = "https://apiv3.fansly.com/api/v1/timeline/home"
STORY_STATES_URL = "https://apiv3.fansly.com/api/v1/mediastories/following"


class TestGetHomeTimeline:
    """Tests for FanslyApi.get_home_timeline."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_home_timeline_hits_correct_url_and_params(self, fansly_api):
        """get_home_timeline sends GET to the home timeline endpoint with correct params."""
        respx.options(url__startswith=HOME_TIMELINE_URL).mock(
            side_effect=[httpx.Response(200)]
        )
        route = respx.get(url__startswith=HOME_TIMELINE_URL).mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": []})]
        )

        try:
            result = await fansly_api.get_home_timeline()
        finally:
            dump_fansly_calls(route.calls, "get_home_timeline - correct URL/params")

        assert result.status_code == 200
        assert route.called
        assert len(route.calls) == 1

        request = route.calls.last.request
        assert HOME_TIMELINE_URL in str(request.url)

        params = dict(request.url.params)
        assert params["before"] == "0"
        assert params["after"] == "0"
        assert params["mode"] == "0"
        assert params["ngsw-bypass"] == "true"

    @pytest.mark.asyncio
    @respx.mock
    async def test_home_timeline_handles_500_retry(self, fansly_api):
        """get_home_timeline retries on 500 and returns the eventual success response."""
        respx.options(url__startswith=HOME_TIMELINE_URL).mock(
            side_effect=[httpx.Response(200)]
        )
        route = respx.get(url__startswith=HOME_TIMELINE_URL).mock(
            side_effect=[
                httpx.Response(500, json={"success": False}),
                httpx.Response(200, json={"success": True, "response": []}),
            ]
        )

        try:
            result = await fansly_api.get_home_timeline()
        finally:
            dump_fansly_calls(route.calls, "get_home_timeline - 500 retry")

        # RetryTransport retried and eventually got 200
        assert result.status_code == 200
        # At least 2 calls: the initial 500 + 1 retry that succeeded
        assert len(route.calls) >= 2


class TestGetStoryStatesFollowing:
    """Tests for FanslyApi.get_story_states_following."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_story_states_following_hits_correct_url_and_params(
        self, fansly_api
    ):
        """get_story_states_following sends GET to the following endpoint with correct params."""
        respx.options(url__startswith=STORY_STATES_URL).mock(
            side_effect=[httpx.Response(200)]
        )
        route = respx.get(url__startswith=STORY_STATES_URL).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": [
                            {"accountId": "123456789", "hasActiveStories": False}
                        ],
                    },
                )
            ]
        )

        try:
            result = await fansly_api.get_story_states_following()
        finally:
            dump_fansly_calls(
                route.calls, "get_story_states_following - correct URL/params"
            )

        assert result.status_code == 200
        assert route.called
        assert len(route.calls) == 1

        request = route.calls.last.request
        assert STORY_STATES_URL in str(request.url)

        params = dict(request.url.params)
        assert params["limit"] == "100"
        assert params["offset"] == "0"
        assert params["ngsw-bypass"] == "true"

    @pytest.mark.asyncio
    @respx.mock
    async def test_story_states_following_returns_json_body(self, fansly_api):
        """get_story_states_following response is parseable JSON with expected structure."""
        story_state = {"accountId": "987654321", "hasActiveStories": True}
        respx.options(url__startswith=STORY_STATES_URL).mock(
            side_effect=[httpx.Response(200)]
        )
        route = respx.get(url__startswith=STORY_STATES_URL).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": True,
                        "response": [story_state],
                    },
                )
            ]
        )

        try:
            result = await fansly_api.get_story_states_following()
        finally:
            dump_fansly_calls(route.calls, "get_story_states_following - JSON body")

        body = result.json()
        assert body["success"] is True
        assert isinstance(body["response"], list)
        assert len(body["response"]) == 1
        assert body["response"][0]["accountId"] == "987654321"
        assert body["response"][0]["hasActiveStories"] is True
