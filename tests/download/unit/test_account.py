"""Unit tests for the account module."""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from api.fansly import FanslyApi
from config.modes import DownloadMode
from download.account import (
    _extract_account_data,
    _get_account_response,
    _make_rate_limited_request,
    _update_state_from_account,
    _validate_download_mode,
    get_creator_account_info,
    get_following_accounts,
)
from download.downloadstate import DownloadState
from errors import ApiAccountInfoError, ApiAuthenticationError, ApiError
from metadata import Account, FollowEvent, TimelineStats, Wall
from metadata.subscriptions import _access_changed_accounts
from tests.fixtures.api import build_creator_account_info_response, dump_fansly_calls
from tests.fixtures.utils import scaled_async_sleep, snowflake_id


class TestValidateDownloadMode:
    """Tests for the _validate_download_mode function."""

    def test_valid_mode_with_creator(self, mock_config):
        """Test with a creator name and valid mode."""
        mock_config.download_mode = DownloadMode.TIMELINE
        state = DownloadState()
        state.creator_name = "testuser"

        # Should not raise an exception
        _validate_download_mode(mock_config, state)

    def test_invalid_mode_with_creator(self, mock_config):
        """Test with a creator name and invalid mode."""
        mock_config.download_mode = (
            DownloadMode.COLLECTION
        )  # Not in valid modes for creator
        state = DownloadState()
        state.creator_name = "testuser"

        # Should not raise an exception (mode check skipped)
        _validate_download_mode(mock_config, state)

    def test_no_mode_set(self, mock_config):
        """Test when download mode is not set."""
        mock_config.download_mode = DownloadMode.NOTSET
        state = DownloadState()

        # Should raise RuntimeError
        with pytest.raises(RuntimeError) as excinfo:
            _validate_download_mode(mock_config, state)

        assert "config download mode not set" in str(excinfo.value)

    def test_client_account_any_mode(self, mock_config):
        """Test with client account (no creator) and any mode."""
        mock_config.download_mode = DownloadMode.COLLECTION  # Any mode should be fine
        state = DownloadState()
        state.creator_name = None  # Client account

        # Should not raise an exception
        _validate_download_mode(mock_config, state)


class TestGetAccountResponse:
    """Tests for the _get_account_response function."""

    @pytest.mark.asyncio
    async def test_get_client_account_info(self, respx_fansly_api, mock_config):
        """Test getting client account info."""
        state = DownloadState()
        state.creator_name = None  # Client account

        route = respx.get(url__startswith=FanslyApi.ACCOUNT_ME_ENDPOINT).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": "true",
                        "response": {"account": {"id": "client123"}},
                    },
                )
            ]
        )

        try:
            response = await _get_account_response(mock_config, state)
        finally:
            dump_fansly_calls(route.calls, "test_get_client_account_info")

        assert response.status_code == 200
        assert response.json() == {
            "success": "true",
            "response": {"account": {"id": "client123"}},
        }

    @pytest.mark.asyncio
    async def test_get_creator_account_info(self, respx_fansly_api, mock_config):
        """Test getting creator account info."""
        state = DownloadState()
        state.creator_name = "testcreator"

        route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_USERNAME_ENDPOINT.format(
                "testcreator"
            )
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"success": "true", "response": [{"id": "creator123"}]},
                )
            ]
        )

        try:
            response = await _get_account_response(mock_config, state)
        finally:
            dump_fansly_calls(route.calls, "test_get_creator_account_info")

        assert response.status_code == 200
        assert response.json() == {
            "success": "true",
            "response": [{"id": "creator123"}],
        }

    @pytest.mark.asyncio
    async def test_get_account_error_response(self, respx_fansly_api, mock_config):
        """Test handling error response."""
        state = DownloadState()
        state.creator_name = "testcreator"

        route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_USERNAME_ENDPOINT.format(
                "testcreator"
            )
        ).mock(side_effect=[httpx.Response(400, text="Bad Request")])

        try:
            with pytest.raises(ApiError) as excinfo:
                await _get_account_response(mock_config, state)
        finally:
            dump_fansly_calls(route.calls, "test_get_account_error_response")

        assert "Error getting account info from fansly API" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_non_200_2xx_status_raises(self, respx_fansly_api, mock_config):
        """204 No Content passes raise_for_status but fails the != 200 check.

        _make_rate_limited_request calls raise_for_status() which raises for
        4xx/5xx. To hit the explicit != 200 check, we need a 2xx non-200 status
        like 204 that passes raise_for_status() but fails the 200 check.
        """
        state = DownloadState()
        state.creator_name = "testcreator"

        route = respx.get(url__startswith=f"{FanslyApi.BASE_URL}account").mock(
            side_effect=[httpx.Response(204, text="")]
        )

        try:
            with pytest.raises(
                ApiAccountInfoError,
                match="API returned status code 204",
            ):
                await _get_account_response(mock_config, state)
        finally:
            dump_fansly_calls(route.calls)

    @pytest.mark.asyncio
    async def test_get_account_request_exception(self, respx_fansly_api, mock_config):
        """Test handling request exception."""
        state = DownloadState()
        state.creator_name = "testcreator"

        # Mock _make_rate_limited_request to raise httpx.HTTPError (not requests.RequestException)
        mock_exception = httpx.HTTPError("Connection error")
        with patch(
            "download.account._make_rate_limited_request",
            AsyncMock(side_effect=mock_exception),
        ) as mock_rate_limited:
            # Should raise ApiError
            with pytest.raises(ApiError) as excinfo:
                await _get_account_response(mock_config, state)

            assert "Error getting account info from fansly API" in str(excinfo.value)
            assert "Connection error" in str(excinfo.value)
            mock_rate_limited.assert_called_once()


_CREATOR_ACCOUNT_ITEM = {
    "id": "creator123",
    "username": "creatoruser",
    "displayName": "Creator User",
    "following": True,
    "subscribed": True,
    "timelineStats": {
        "imageCount": 100,
        "videoCount": 50,
    },
}


class TestExtractAccountData:
    """Tests for the _extract_account_data function.

    Real httpx Responses through the real JSON pipeline
    (``get_json_response_contents``); no internal patches.
    """

    @pytest.mark.parametrize(
        (
            "endpoint",
            "status_code",
            "body_kwargs",
            "token",
            "expected",
            "raises",
            "match_substrings",
        ),
        [
            # Client account info is wrapped in an 'account' key → unwrapped.
            pytest.param(
                "me",
                200,
                {
                    "json": {
                        "success": "true",
                        "response": {
                            "account": {
                                "id": "client123",
                                "username": "clientuser",
                                "displayName": "Client User",
                            }
                        },
                    }
                },
                None,
                {
                    "id": "client123",
                    "username": "clientuser",
                    "displayName": "Client User",
                },
                None,
                [],
                id="client-account-dict-unwrapped",
            ),
            # Creator account info is a list → first item returned.
            pytest.param(
                "username",
                200,
                {"json": {"success": "true", "response": [_CREATOR_ACCOUNT_ITEM]}},
                None,
                _CREATOR_ACCOUNT_ITEM,
                None,
                [],
                id="creator-first-list-item",
            ),
            # Real 401 → HTTPStatusError → ApiAuthenticationError with token.
            pytest.param(
                "username",
                401,
                {"json": {"error": "Unauthorized"}, "text": "Unauthorized"},
                "invalid_token",
                None,
                ApiAuthenticationError,
                ["API returned unauthorized", "invalid_token"],
                id="unauthorized-401-auth-error",
            ),
            # Empty response list → IndexError → misspelled-creator error.
            pytest.param(
                "username",
                200,
                {"json": {"success": "true", "response": []}},
                None,
                None,
                ApiAccountInfoError,
                ["Bad response from fansly API", "misspelled the creator name"],
                id="empty-list-missing-creator",
            ),
            # Non-standard shape (no 'account' key, not a list) → returned as-is.
            pytest.param(
                "username",
                200,
                {"json": {"success": "true", "response": {"invalid": "data"}}},
                None,
                {"invalid": "data"},
                None,
                [],
                id="malformed-shape-returned-as-is",
            ),
            # success=true but no 'response' key → KeyError on a non-401 →
            # generic ApiError (not the 401 auth arm).
            pytest.param(
                "username",
                200,
                {"json": {"success": "true"}},
                None,
                None,
                ApiError,
                ["Bad response from fansly API"],
                id="key-error-non-401-api-error",
            ),
        ],
    )
    def test_extract_account_data(
        self,
        respx_fansly_api,
        mock_config,
        endpoint,
        status_code,
        body_kwargs,
        token,
        expected,
        raises,
        match_substrings,
    ):
        """``raises`` None → result must equal ``expected``; else that error
        type must raise with every ``match_substrings`` entry in its message.
        ``token`` overrides config.token when the message must echo it."""
        url = (
            FanslyApi.ACCOUNT_ME_ENDPOINT
            if endpoint == "me"
            else FanslyApi.ACCOUNT_BY_USERNAME_ENDPOINT.format("")
        )
        request = httpx.Request("GET", url)
        response = httpx.Response(status_code, request=request, **body_kwargs)
        if token is not None:
            mock_config.token = token

        if raises is None:
            assert _extract_account_data(response, mock_config) == expected
        else:
            with pytest.raises(raises) as excinfo:
                _extract_account_data(response, mock_config)
            for substring in match_substrings:
                assert substring in str(excinfo.value)


class TestUpdateStateFromAccount:
    """Tests for the _update_state_from_account function."""

    def test_update_client_state(self, mock_config):
        """Test updating state for client account."""
        account_id = snowflake_id()
        wall_id_1 = snowflake_id()
        wall_id_2 = snowflake_id()

        account = Account(
            id=account_id,
            username="clientuser",
            walls=[
                Wall(id=wall_id_1, accountId=account_id),
                Wall(id=wall_id_2, accountId=account_id),
            ],
        )

        state = DownloadState()
        state.creator_name = None  # Client account

        _update_state_from_account(mock_config, state, account)

        assert state.creator_id == account_id
        assert state.walls == {wall_id_1, wall_id_2}
        # These should not be set for client
        assert state.following is False
        assert state.subscribed is False
        assert state.total_timeline_pictures == 0
        assert state.total_timeline_videos == 0

    def test_update_creator_state(self, mock_config):
        """Test updating state for creator account."""
        account_id = snowflake_id()
        wall_id_1 = snowflake_id()
        wall_id_2 = snowflake_id()

        account = Account(
            id=account_id,
            username="creatoruser",
            following=True,
            subscribed=True,
            timelineStats=TimelineStats(
                accountId=account_id,
                imageCount=100,
                videoCount=50,
            ),
            walls=[
                Wall(id=wall_id_1, accountId=account_id),
                Wall(id=wall_id_2, accountId=account_id),
            ],
        )

        mock_config.DUPLICATE_THRESHOLD = 10  # Initial value

        state = DownloadState()
        state.creator_name = "creatoruser"  # Creator account

        _update_state_from_account(mock_config, state, account)

        assert state.creator_id == account_id
        assert state.following is True
        assert state.subscribed is True
        assert state.total_timeline_pictures == 100
        assert state.total_timeline_videos == 50
        assert state.walls == {wall_id_1, wall_id_2}

        # Custom duplicate threshold - 20% of timeline content
        assert int(0.2 * (100 + 50)) == mock_config.DUPLICATE_THRESHOLD

    def test_update_creator_missing_timeline_stats(self, mock_config):
        """Test error when timeline stats are missing for creator."""
        account = Account(
            id=snowflake_id(),
            username="creatoruser",
            following=True,
            subscribed=True,
        )

        state = DownloadState()
        state.creator_name = "creatoruser"  # Creator account

        with pytest.raises(ApiAccountInfoError) as excinfo:
            _update_state_from_account(mock_config, state, account)

        assert "Can not get timelineStats for creator" in str(excinfo.value)
        assert "creatoruser" in str(excinfo.value)


class TestMakeRateLimitedRequest:
    """Tests for the _make_rate_limited_request function.

    Drives real ``api.get_client_account_info`` (which makes a real httpx
    GET) as ``request_func``, with respx intercepting at the HTTP edge.
    No MagicMock — the rate-limiter wrapper runs against real httpx
    Response objects and real ``raise_for_status`` behavior.
    """

    @pytest.mark.asyncio
    async def test_successful_request(self, respx_fansly_api, mock_config):
        """200 response → returned as-is, no retry."""
        route = respx.get(url__startswith=respx_fansly_api.ACCOUNT_ME_ENDPOINT).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"success": "true", "response": {"account": {"id": "1"}}},
                )
            ]
        )

        try:
            with patch("asyncio.sleep", scaled_async_sleep):
                result = await _make_rate_limited_request(
                    request_func=respx_fansly_api.get_client_account_info,
                    rate_limit_delay=1.0,
                )
        finally:
            dump_fansly_calls(route.calls, "test_successful_request")

        assert result.status_code == 200
        assert route.call_count == 1

    @pytest.mark.asyncio
    async def test_rate_limited_request(
        self, respx_fansly_api, mock_config, scaled_async_sleep_recording
    ):
        """429 then 200 → real raise_for_status raises, wrapper retries, returns 200."""
        route = respx.get(url__startswith=respx_fansly_api.ACCOUNT_ME_ENDPOINT).mock(
            side_effect=[
                httpx.Response(429, json={"error": "Rate limited"}),
                httpx.Response(
                    200,
                    json={"success": "true", "response": {"account": {"id": "1"}}},
                ),
            ]
        )

        try:
            with patch("asyncio.sleep", scaled_async_sleep_recording):
                result = await _make_rate_limited_request(
                    request_func=respx_fansly_api.get_client_account_info,
                    rate_limit_delay=1.0,
                )
        finally:
            dump_fansly_calls(route.calls, "test_rate_limited_request")

        assert result.status_code == 200
        assert route.call_count == 2
        # asyncio.sleep called: once at top of _make_rate_limited_request (0.2s),
        # once on rate-limit retry (1.0s). Verify the 1.0s rate-limit sleep happened.
        assert 1.0 in scaled_async_sleep_recording.calls

    @pytest.mark.asyncio
    async def test_non_rate_limit_error(self, respx_fansly_api, mock_config):
        """404 → real raise_for_status raises HTTPStatusError, wrapper re-raises."""
        route = respx.get(url__startswith=respx_fansly_api.ACCOUNT_ME_ENDPOINT).mock(
            side_effect=[httpx.Response(404, json={"error": "Not Found"})]
        )

        try:
            with (
                patch("asyncio.sleep", scaled_async_sleep),
                pytest.raises(httpx.HTTPStatusError) as excinfo,
            ):
                await _make_rate_limited_request(
                    request_func=respx_fansly_api.get_client_account_info,
                    rate_limit_delay=1.0,
                )
        finally:
            dump_fansly_calls(route.calls, "test_non_rate_limit_error")

        assert excinfo.value.response.status_code == 404
        assert route.call_count == 1


class TestGetCreatorAccountInfo:
    """Tests for the get_creator_account_info function.

    Uses respx to mock HTTP at the edge and entity_store for real DB.
    No internal functions are patched — the full code path runs end-to-end.
    """

    @pytest.mark.asyncio
    async def test_get_creator_account_info_success(
        self, respx_fansly_api, mock_config, entity_store
    ):
        """Test successful retrieval of creator account info."""
        creator_id = snowflake_id()

        state = DownloadState()
        state.creator_name = "testcreator"
        mock_config.download_mode = DownloadMode.TIMELINE

        route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_USERNAME_ENDPOINT.format(
                "testcreator"
            )
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": "true",
                        "response": [
                            {
                                "id": str(creator_id),
                                "username": "testcreator",
                                "following": True,
                                "subscribed": True,
                                "timelineStats": {
                                    "accountId": str(creator_id),
                                    "imageCount": 100,
                                    "videoCount": 50,
                                },
                            }
                        ],
                    },
                )
            ]
        )

        try:
            await get_creator_account_info(mock_config, state)
        finally:
            dump_fansly_calls(route.calls, "test_get_creator_account_info_success")

        # Verify real _update_state_from_account ran
        assert state.creator_id == creator_id
        assert state.following is True
        assert state.subscribed is True
        assert state.total_timeline_pictures == 100
        assert state.total_timeline_videos == 50

        # Account persisted to real store
        account = await entity_store.get(Account, creator_id)
        assert account is not None
        assert account.username == "testcreator"

    @pytest.mark.asyncio
    async def test_get_creator_account_info_timeline_duplication(
        self, respx_fansly_api, mock_config, entity_store
    ):
        """Test timeline duplication detection via store cache."""
        creator_id = snowflake_id()
        fetched_at = datetime.fromtimestamp(1633046400, UTC)

        mock_config.use_duplicate_threshold = True
        mock_config.download_mode = DownloadMode.TIMELINE

        state = DownloadState()
        state.creator_name = "testcreator"

        # Pre-seed store: Account must exist before TimelineStats (FK constraint)
        await entity_store.save(Account(id=creator_id, username="testcreator"))
        await entity_store.save(
            TimelineStats(accountId=creator_id, fetchedAt=fetched_at)
        )

        route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_USERNAME_ENDPOINT.format(
                "testcreator"
            )
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": "true",
                        "response": [
                            {
                                "id": str(creator_id),
                                "username": "testcreator",
                                "following": True,
                                "subscribed": True,
                                "timelineStats": {
                                    "accountId": str(creator_id),
                                    "imageCount": 100,
                                    "videoCount": 50,
                                    "fetchedAt": 1633046400000,
                                },
                            }
                        ],
                    },
                )
            ]
        )

        try:
            await get_creator_account_info(mock_config, state)
        finally:
            dump_fansly_calls(
                route.calls, "test_get_creator_account_info_timeline_duplication"
            )

        assert state.fetched_timeline_duplication is True


class TestGetFollowingAccounts:
    """Tests for the get_following_accounts function.

    Drives HTTP via respx at the edge — no internal-layer mocks. The single
    exception is test_get_following_accounts_request_error, which patches
    _make_rate_limited_request to raise a connection-level httpx.HTTPError
    (respx is transport-level and can't simulate that cleanly).

    Each test uses ONE wide GET route over the API base with a SIZED
    side_effect list — the list size is the contract: deviation surfaces as
    StopIteration (overflow) or an unconsumed entry (undershoot, observable
    in dump). Wrapping the call in try/finally + dump_fansly_calls preserves
    diagnostic evidence even when the assertion or function raises.
    """

    @pytest.mark.asyncio
    async def test_get_following_accounts_success(
        self, respx_fansly_api, mock_config, entity_store
    ):
        creator1_id = snowflake_id()
        creator2_id = snowflake_id()

        state = DownloadState()
        state.creator_id = snowflake_id()

        following_route = respx.get(
            url__startswith=respx_fansly_api.FOLLOWING_ENDPOINT.format(state.creator_id)
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": "true",
                        "response": [
                            {"accountId": str(creator1_id)},
                            {"accountId": str(creator2_id)},
                        ],
                    },
                ),
            ]
        )
        details_route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_ID_ENDPOINT.format("")
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": "true",
                        "response": [
                            {"id": str(creator1_id), "username": "creator1user"},
                            {"id": str(creator2_id), "username": "creator2user"},
                        ],
                    },
                ),
            ]
        )

        try:
            with patch("asyncio.sleep", scaled_async_sleep):
                result = await get_following_accounts(mock_config, state)
        finally:
            dump_fansly_calls(following_route.calls, "following_success")
            dump_fansly_calls(details_route.calls, "details_success")

        assert result == {"creator1user", "creator2user"}
        account1 = await entity_store.get(Account, creator1_id)
        assert account1 is not None
        assert account1.username == "creator1user"

    @pytest.mark.asyncio
    async def test_get_following_accounts_empty(self, respx_fansly_api, mock_config):
        state = DownloadState()
        state.creator_id = 123456

        following_route = respx.get(
            url__startswith=respx_fansly_api.FOLLOWING_ENDPOINT.format(state.creator_id)
        ).mock(
            side_effect=[
                httpx.Response(200, json={"success": "true", "response": []}),
            ]
        )

        try:
            with patch("asyncio.sleep", scaled_async_sleep):
                result = await get_following_accounts(mock_config, state)
        finally:
            dump_fansly_calls(following_route.calls, "following_empty")

        assert result == set()

    @pytest.mark.asyncio
    async def test_get_following_accounts_no_client_id(self, mock_config):
        state = DownloadState()
        state.creator_id = None

        with pytest.raises(RuntimeError) as excinfo:
            await get_following_accounts(mock_config, state)

        assert "client ID not set" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_get_following_accounts_unauthorized(
        self, respx_fansly_api, mock_config
    ):
        mock_config.token = "invalid_token"
        state = DownloadState()
        state.creator_id = 123456

        route = respx.get(
            url__startswith=respx_fansly_api.FOLLOWING_ENDPOINT.format(state.creator_id)
        ).mock(
            side_effect=[
                httpx.Response(401, json={"error": "Unauthorized"}),
            ]
        )

        try:
            with (
                patch("asyncio.sleep", scaled_async_sleep),
                pytest.raises(ApiAuthenticationError) as excinfo,
            ):
                await get_following_accounts(mock_config, state)
        finally:
            dump_fansly_calls(route.calls, "test_get_following_accounts_unauthorized")

        assert "API returned unauthorized while getting following list" in str(
            excinfo.value
        )
        assert "invalid_token" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_get_following_accounts_request_error(
        self, respx_fansly_api, mock_config
    ):
        """Connection-level httpx.HTTPError — patches _make_rate_limited_request.

        Mirrors the precedent set by test_get_account_request_exception in
        TestGetAccountResponse: respx is transport-level and can't simulate
        connection failures cleanly, so the rate-limited wrapper is the
        practical seam for this single case.
        """
        state = DownloadState()
        state.creator_id = 123456

        with (
            patch(
                "download.account._make_rate_limited_request",
                AsyncMock(side_effect=httpx.HTTPError("Connection error")),
            ),
            pytest.raises(ApiError) as excinfo,
        ):
            await get_following_accounts(mock_config, state)

        assert "Error getting following list from Fansly API" in str(excinfo.value)
        assert "Connection error" in str(excinfo.value)

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_following_accounts_pagination(
        self, respx_fansly_api, mock_config, entity_store
    ):
        """Page 1 (50 items, full page → continue) + Page 2 (2 items, short → stop).

        13 GETs in this exact order:
          1. following list page 1
          2-11. account details page 1 (50 ids in 10 chunks of
                account_ids_batch_size=5)
          12. following list page 2
          13. account details page 2 (2 ids, 1 chunk)

        Sized side_effect lists — a 14th GET (any unintended retry or
        duplicate poll) would StopIteration and surface in the dump.
        """
        state = DownloadState()
        state.creator_id = snowflake_id()

        creator_ids = [snowflake_id() for _ in range(52)]
        first_page_accounts = [{"accountId": str(cid)} for cid in creator_ids[:50]]
        first_page_details = [
            {"id": str(cid), "username": f"user_{cid}"} for cid in creator_ids[:50]
        ]
        second_page_accounts = [{"accountId": str(cid)} for cid in creator_ids[50:]]
        second_page_details = [
            {"id": str(cid), "username": f"user_{cid}"} for cid in creator_ids[50:]
        ]
        batch_size = mock_config.account_ids_batch_size
        first_page_detail_responses = [
            httpx.Response(
                200,
                json={
                    "success": "true",
                    "response": first_page_details[start : start + batch_size],
                },
            )
            for start in range(0, len(first_page_details), batch_size)
        ]

        following_route = respx.get(
            url__startswith=respx_fansly_api.FOLLOWING_ENDPOINT.format(state.creator_id)
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"success": "true", "response": first_page_accounts},
                ),
                httpx.Response(
                    200,
                    json={"success": "true", "response": second_page_accounts},
                ),
            ]
        )
        details_route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_ID_ENDPOINT.format("")
        ).mock(
            side_effect=[
                *first_page_detail_responses,
                httpx.Response(
                    200,
                    json={"success": "true", "response": second_page_details},
                ),
            ]
        )

        try:
            with patch("asyncio.sleep", scaled_async_sleep):
                result = await get_following_accounts(mock_config, state)
        finally:
            dump_fansly_calls(following_route.calls, "following_pagination")
            dump_fansly_calls(details_route.calls, "details_pagination")

        expected_usernames = {f"user_{cid}" for cid in creator_ids}
        assert result == expected_usernames

    @pytest.mark.asyncio
    async def test_reverse_order_processes_accounts_in_reverse(
        self, respx_fansly_api, mock_config, entity_store, caplog
    ):
        caplog.set_level(logging.INFO)
        creator_a = snowflake_id()
        creator_b = snowflake_id()
        creator_c = snowflake_id()

        mock_config.reverse_order = True

        state = DownloadState()
        state.creator_id = snowflake_id()

        following_route = respx.get(
            url__startswith=respx_fansly_api.FOLLOWING_ENDPOINT.format(state.creator_id)
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": "true",
                        "response": [
                            {"accountId": str(creator_a)},
                            {"accountId": str(creator_b)},
                            {"accountId": str(creator_c)},
                        ],
                    },
                ),
            ]
        )
        details_route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_ID_ENDPOINT.format("")
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": "true",
                        "response": [
                            {"id": str(creator_a), "username": "alpha"},
                            {"id": str(creator_b), "username": "bravo"},
                            {"id": str(creator_c), "username": "charlie"},
                        ],
                    },
                ),
            ]
        )

        try:
            with patch("asyncio.sleep", scaled_async_sleep):
                result = await get_following_accounts(mock_config, state)
        finally:
            dump_fansly_calls(following_route.calls, "following_reverse")
            dump_fansly_calls(details_route.calls, "details_reverse")

        assert result == {"alpha", "bravo", "charlie"}
        info_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "INFO"
        ]
        assert any("Processing accounts in reverse order" in m for m in info_messages)

    @pytest.mark.asyncio
    async def test_per_account_exception_logged_loop_continues(
        self, respx_fansly_api, mock_config, entity_store, caplog
    ):
        """Per-account exception is logged and loop continues — real exception path.

        Triggers the loop's ``except Exception`` branch organically via a
        non-numeric id on one entry: ``Account`` declares ``id: int``, and
        ``convert_ids_to_int`` falls back to the raw string when int()
        fails, so ``Account.model_validate({"id": "badguy-id-x", ...})``
        raises ``ValidationError`` inside the loop — no internal-function
        monkeypatch needed.
        """
        caplog.set_level(logging.ERROR)
        creator_good = snowflake_id()

        state = DownloadState()
        state.creator_id = snowflake_id()

        following_route = respx.get(
            url__startswith=respx_fansly_api.FOLLOWING_ENDPOINT.format(state.creator_id)
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": "true",
                        "response": [
                            {"accountId": str(creator_good)},
                            {"accountId": "badguy-id-x"},
                        ],
                    },
                ),
            ]
        )
        details_route = respx.get(
            url__startswith=respx_fansly_api.ACCOUNT_BY_ID_ENDPOINT.format("")
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "success": "true",
                        "response": [
                            {"id": str(creator_good), "username": "goodguy"},
                            {"id": "badguy-id-x", "username": "badguy"},
                        ],
                    },
                ),
            ]
        )

        try:
            with patch("asyncio.sleep", scaled_async_sleep):
                result = await get_following_accounts(mock_config, state)
        finally:
            dump_fansly_calls(following_route.calls, "following_per_account_err")
            dump_fansly_calls(details_route.calls, "details_per_account_err")

        assert "goodguy" in result
        assert "badguy" not in result


@pytest.mark.asyncio
async def test_creator_access_changed_priority_full_pass_over_registry_over_follow(
    respx_fansly_api, mock_config, entity_store
):
    """Three triggers can fire on the same creator in one run; the resolver
    checks them in order — full-pass beats registry beats follow-transition.
    A single state object only gets one reason set, so the priority is
    visible by configuring each scenario in turn against the same creator.
    """
    creator_id = snowflake_id()
    username = f"prio_{creator_id}"

    # Pre-seed Account with following=False so the API following=True is
    # detected as a transition by record_follow_observation.
    await entity_store.save(Account(id=creator_id, username=username, following=False))

    route = respx.get(
        url__startswith=respx_fansly_api.ACCOUNT_BY_USERNAME_ENDPOINT.format(username)
    ).mock(
        side_effect=[
            httpx.Response(
                200,
                json=build_creator_account_info_response(creator_id, username),
            )
            for _ in range(3)
        ]
    )

    # --- Scenario 1: full-pass wins over a registry entry. ---
    mock_config.monitoring_session_baseline = datetime(2000, 1, 1, tzinfo=UTC)
    _access_changed_accounts[creator_id] = "sub-activated"

    state = DownloadState()
    state.creator_name = username
    try:
        await get_creator_account_info(mock_config, state)
    finally:
        dump_fansly_calls(route.calls, "priority_full_pass")

    assert state.creator_access_changed is True
    assert state.creator_access_change_reason == "full-pass"
    # Registry entry is NOT consumed when full-pass wins.
    assert _access_changed_accounts.get(creator_id) == "sub-activated"

    # --- Scenario 2: registry wins over follow-transition. ---
    mock_config.monitoring_session_baseline = None

    state = DownloadState()
    state.creator_name = username
    await get_creator_account_info(mock_config, state)

    assert state.creator_access_changed is True
    assert state.creator_access_change_reason == "sub-activated"
    assert creator_id not in _access_changed_accounts

    # --- Scenario 3: only follow-transition. Earlier successful runs
    # merged following=True into the cached Account, so flip the cached
    # row back to False to make a fresh False→True transition visible.
    cached = entity_store.get_from_cache(Account, creator_id)
    cached.following = False
    await entity_store.save(cached)
    for ev in entity_store.filter(
        FollowEvent, lambda e, c=creator_id: e.accountId == c
    ):
        await entity_store.delete(ev)

    state = DownloadState()
    state.creator_name = username
    await get_creator_account_info(mock_config, state)

    assert state.creator_access_changed is True
    assert state.creator_access_change_reason == "follow-transition"


@pytest.mark.asyncio
async def test_follow_event_appended_only_on_transition(
    respx_fansly_api, mock_config, entity_store
):
    """A FollowEvent row is added only when the new observation differs
    from the most recent recorded state. Same-state observations are a
    no-op so the audit log stays scoped to actual transitions."""
    creator_id = snowflake_id()
    username = f"follow_{creator_id}"

    await entity_store.save(Account(id=creator_id, username=username, following=True))
    now = datetime.now(UTC)
    await entity_store.save(
        FollowEvent(
            accountId=creator_id,
            observed_at=now - timedelta(days=1),
            following_state=True,
        )
    )

    route = respx.get(
        url__startswith=respx_fansly_api.ACCOUNT_BY_USERNAME_ENDPOINT.format(username)
    ).mock(
        side_effect=[
            httpx.Response(
                200,
                json=build_creator_account_info_response(
                    creator_id, username, following=True
                ),
            ),
            httpx.Response(
                200,
                json=build_creator_account_info_response(
                    creator_id, username, following=False
                ),
            ),
        ]
    )

    state = DownloadState()
    state.creator_name = username
    try:
        await get_creator_account_info(mock_config, state)
    finally:
        dump_fansly_calls(route.calls, "follow_steady_state")

    events = entity_store.filter(FollowEvent, lambda e, c=creator_id: e.accountId == c)
    assert len(events) == 1

    state = DownloadState()
    state.creator_name = username
    await get_creator_account_info(mock_config, state)

    events = entity_store.filter(FollowEvent, lambda e, c=creator_id: e.accountId == c)
    assert len(events) == 2
    assert sorted(e.following_state for e in events) == [False, True]
