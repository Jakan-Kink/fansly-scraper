"""Coverage tests for download/account.py — proper pattern with real objects.

Uses real FanslyConfig, real httpx.Response, respx at the edge.
NO MagicMock for internal objects.
"""

import httpx
import pytest
import respx

from config.fanslyconfig import FanslyConfig
from download.account import (
    _extract_account_data,
    _get_account_response,
    get_following_accounts,
)
from download.downloadstate import DownloadState
from errors import ApiAccountInfoError, ApiError
from tests.fixtures.api.api_fixtures import dump_fansly_calls
from tests.fixtures.utils import snowflake_id


@pytest.fixture
def real_config(fansly_api):
    """Real FanslyConfig with real FanslyApi."""
    config = FanslyConfig(program_version="0.11.0")
    config._api = fansly_api
    config.token = "a" * 60
    config.user_agent = "a" * 50
    config.check_key = "test-key"
    config.interactive = False
    config.separate_metadata = True
    config.reverse_order = False
    return config


class TestExtractAccountDataKeyErrorNon401:
    """Cover _extract_account_data KeyError with non-401 status (lines 133-137)."""

    def test_key_error_non_401_raises_api_error(self, real_config):
        """KeyError on a non-401 response raises generic ApiError (not ApiAuthenticationError).

        get_json_response_contents raises RuntimeError for non-success responses,
        but the KeyError path triggers when the success response has unexpected structure.
        We construct a response whose JSON will cause the real get_json_response_contents
        to raise KeyError by omitting the 'response' key.
        """
        request = httpx.Request("GET", "https://apiv3.fansly.com/api/v1/account")
        # success=true but no 'response' key → KeyError in get_json_response_contents
        response = httpx.Response(
            status_code=200,
            json={"success": "true"},
            request=request,
        )

        with pytest.raises(ApiError, match="Bad response from fansly API"):
            _extract_account_data(response, real_config)


class TestGetAccountResponseNon200:
    """Cover _get_account_response non-200 status (lines 78-83).

    _make_rate_limited_request calls raise_for_status() which raises for 4xx/5xx.
    To hit line 77 (the explicit != 200 check), we need a 2xx non-200 status
    like 204 that passes raise_for_status() but fails the 200 check.
    """

    @pytest.mark.asyncio
    async def test_non_200_2xx_status_raises(self, real_config):
        """204 No Content passes raise_for_status but fails the != 200 check."""
        state = DownloadState()
        state.creator_name = "testcreator"

        with respx.mock:
            respx.options(url__regex=r".*").mock(side_effect=[httpx.Response(200)])
            route = respx.get(url__regex=r".*account\?usernames=.*").mock(
                side_effect=[httpx.Response(204, text="")]
            )

            try:
                with pytest.raises(
                    ApiAccountInfoError,
                    match="API returned status code 204",
                ):
                    await _get_account_response(real_config, state)
            finally:
                dump_fansly_calls(route.calls)


class TestGetFollowingAccountsEdgeCases:
    """Cover get_following_accounts edge cases (lines 405-407, 427-430)."""

    @pytest.mark.asyncio
    async def test_reverse_order(self, real_config):
        """reverse_order=True reverses the following list (lines 405-407)."""
        real_config.reverse_order = True

        state = DownloadState()
        state.creator_name = "testcreator"
        state.creator_id = snowflake_id()

        cid1 = str(snowflake_id())
        cid2 = str(snowflake_id())

        # _get_following_page makes 2 calls:
        #   1. get_following_list → [{accountId: ...}, ...]
        #   2. get_account_info_by_id → [{id:..., username:...}, ...]
        # Then pagination ends because count(2) < page_size(50)
        following_list_response = [
            {"accountId": cid1},
            {"accountId": cid2},
        ]
        account_details_response = [
            {"id": cid1, "username": "user_first"},
            {"id": cid2, "username": "user_second"},
        ]

        with respx.mock:
            respx.options(url__regex=r".*").mock(side_effect=[httpx.Response(200)] * 10)
            route = respx.get(url__regex=r".*").mock(
                side_effect=[
                    # Call 1: following list
                    httpx.Response(
                        200,
                        json={"success": "true", "response": following_list_response},
                    ),
                    # Call 2: account details
                    httpx.Response(
                        200,
                        json={"success": "true", "response": account_details_response},
                    ),
                ]
            )

            try:
                result = await get_following_accounts(real_config, state)
            finally:
                dump_fansly_calls(route.calls)

        assert result == {"user_first", "user_second"}

    @pytest.mark.asyncio
    async def test_account_processing_error_continues(self, real_config):
        """Exception processing one account continues to next (lines 427-430)."""
        state = DownloadState()
        state.creator_name = "testcreator"
        state.creator_id = snowflake_id()

        good_cid = str(snowflake_id())

        following_list_response = [
            {"accountId": good_cid},
            {"accountId": "bad_id"},
        ]
        # One valid account, one missing required fields → error in model_validate
        account_details_response = [
            {"id": good_cid, "username": "good_user"},
            {"notid": "will_fail_validation"},
        ]

        with respx.mock:
            respx.options(url__regex=r".*").mock(side_effect=[httpx.Response(200)] * 10)
            route = respx.get(url__regex=r".*").mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json={"success": "true", "response": following_list_response},
                    ),
                    httpx.Response(
                        200,
                        json={"success": "true", "response": account_details_response},
                    ),
                ]
            )

            try:
                result = await get_following_accounts(real_config, state)
            finally:
                dump_fansly_calls(route.calls)

        # good_user should still be in results despite error on second account
        assert "good_user" in result
