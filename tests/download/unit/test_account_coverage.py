"""Coverage tests for download/account.py — proper pattern with real objects.

Uses real FanslyConfig (via ``config_wired``), real httpx.Response, respx at
the edge. NO MagicMock for internal objects.
"""

import httpx
import pytest
import respx

from api.fansly import FanslyApi
from download.account import (
    _extract_account_data,
    _get_account_response,
)
from download.downloadstate import DownloadState
from errors import ApiAccountInfoError, ApiError
from tests.fixtures.api.api_fixtures import dump_fansly_calls


class TestExtractAccountDataKeyErrorNon401:
    """Cover _extract_account_data KeyError with non-401 status (lines 133-137)."""

    def test_key_error_non_401_raises_api_error(self, config_wired):
        """KeyError on a non-401 response raises generic ApiError (not ApiAuthenticationError).

        get_json_response_contents raises RuntimeError for non-success responses,
        but the KeyError path triggers when the success response has unexpected structure.
        We construct a response whose JSON will cause the real get_json_response_contents
        to raise KeyError by omitting the 'response' key.
        """
        request = httpx.Request("GET", f"{FanslyApi.BASE_URL}account")
        # success=true but no 'response' key → KeyError in get_json_response_contents
        response = httpx.Response(
            status_code=200,
            json={"success": "true"},
            request=request,
        )

        with pytest.raises(ApiError, match="Bad response from fansly API"):
            _extract_account_data(response, config_wired)


class TestGetAccountResponseNon200:
    """Cover _get_account_response non-200 status (lines 78-83).

    _make_rate_limited_request calls raise_for_status() which raises for 4xx/5xx.
    To hit line 77 (the explicit != 200 check), we need a 2xx non-200 status
    like 204 that passes raise_for_status() but fails the 200 check.
    """

    @pytest.mark.asyncio
    async def test_non_200_2xx_status_raises(self, config_wired):
        """204 No Content passes raise_for_status but fails the != 200 check."""
        state = DownloadState()
        state.creator_name = "testcreator"

        with respx.mock:
            respx.options(url__startswith=f"{FanslyApi.BASE_URL}account").mock(
                side_effect=[httpx.Response(200)]
            )
            route = respx.get(url__startswith=f"{FanslyApi.BASE_URL}account").mock(
                side_effect=[httpx.Response(204, text="")]
            )

            try:
                with pytest.raises(
                    ApiAccountInfoError,
                    match="API returned status code 204",
                ):
                    await _get_account_response(config_wired, state)
            finally:
                dump_fansly_calls(route.calls)
