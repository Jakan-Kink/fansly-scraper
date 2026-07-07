"""Unit tests for helpers/web.py"""

from unittest.mock import patch

import httpx
import pytest
import respx

from helpers.web import (
    get_file_name_from_url,
    get_flat_qs_dict,
    get_qs_value,
    get_release_info_from_github,
    split_url,
    strip_url_params,
)
from tests.fixtures.api import dump_fansly_calls


GITHUB_RELEASES_URL = (
    "https://api.github.com/repos/prof79/fansly-downloader-ng/releases/latest"
)


class TestGetFileNameFromUrl:
    """Tests for the get_file_name_from_url function."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            pytest.param(
                "https://example.com/path/to/file.txt",
                "file.txt",
                id="simple-path",
            ),
            pytest.param(
                "https://example.com/path/to/directory/",
                "",
                id="trailing-slash-no-file",
            ),
            pytest.param(
                "https://example.com/path/file.txt?key=value&foo=bar",
                "file.txt",
                id="query-string-stripped",
            ),
            pytest.param(
                "https://example.com/path/to/document.pdf?download=true#section1",
                "document.pdf",
                id="query-and-fragment-stripped",
            ),
        ],
    )
    def test_get_file_name_from_url(self, url: str, expected: str) -> None:
        """get_file_name_from_url extracts the basename, ignoring query/fragment."""
        assert get_file_name_from_url(url) == expected


class TestGetQsValue:
    """Tests for the get_qs_value function."""

    def test_get_qs_value_single_parameter(self):
        """Test get_qs_value with single query parameter."""
        url = "https://example.com?key=value"
        result = get_qs_value(url, "key")
        assert result == "value"

    def test_get_qs_value_multiple_parameters(self):
        """Test get_qs_value with multiple query parameters."""
        url = "https://example.com?key1=value1&key2=value2&key3=value3"
        assert get_qs_value(url, "key1") == "value1"
        assert get_qs_value(url, "key2") == "value2"
        assert get_qs_value(url, "key3") == "value3"

    def test_get_qs_value_missing_key(self):
        """Test get_qs_value with missing key returns default."""
        url = "https://example.com?key=value"
        result = get_qs_value(url, "missing_key", default="default_value")
        assert result == "default_value"

    def test_get_qs_value_missing_key_no_default(self):
        """Test get_qs_value with missing key and no default."""
        url = "https://example.com?key=value"
        result = get_qs_value(url, "missing_key")
        assert result is None

    def test_get_qs_value_empty_value(self):
        """Test get_qs_value with empty value."""
        url = "https://example.com?key="
        result = get_qs_value(url, "key")
        # Empty string in query gives empty list, which returns None
        assert result is None

    def test_get_qs_value_no_query_string(self):
        """Test get_qs_value with URL without query string."""
        url = "https://example.com/path/file.txt"
        result = get_qs_value(url, "key", default="default")
        assert result == "default"

    def test_get_qs_value_multiple_values_same_key(self):
        """Test get_qs_value with multiple values for same key (returns first)."""
        url = "https://example.com?key=value1&key=value2"
        result = get_qs_value(url, "key")
        # parse_qs returns list, function returns first element
        assert result == "value1"

    def test_get_qs_value_empty_list_edge_case(self):
        """Test get_qs_value when parse_qs returns empty list (line 58 edge case)."""
        # Mock parse_qs to return a dict with empty list for a key
        with patch("helpers.web.parse_qs") as mock_parse_qs:
            mock_parse_qs.return_value = {"key": []}
            url = "https://example.com?key="
            result = get_qs_value(url, "key")
            # Should return None when list is empty (line 58)
            assert result is None


class TestGetFlatQsDict:
    """Tests for the get_flat_qs_dict function."""

    def test_get_flat_qs_dict_single_parameter(self):
        """Test get_flat_qs_dict with single query parameter."""
        url = "https://example.com?key=value"
        result = get_flat_qs_dict(url)
        assert result == {"key": "value"}

    def test_get_flat_qs_dict_multiple_parameters(self):
        """Test get_flat_qs_dict with multiple parameters."""
        url = "https://example.com?key1=value1&key2=value2&key3=value3"
        result = get_flat_qs_dict(url)
        assert result == {"key1": "value1", "key2": "value2", "key3": "value3"}

    def test_get_flat_qs_dict_empty_value(self):
        """Test get_flat_qs_dict with empty value.

        Note: parse_qs by default doesn't include keys with empty values,
        so the result is an empty dict.
        """
        url = "https://example.com?key="
        result = get_flat_qs_dict(url)
        assert result == {}

    def test_get_flat_qs_dict_no_query_string(self):
        """Test get_flat_qs_dict with URL without query string."""
        url = "https://example.com/path/file.txt"
        result = get_flat_qs_dict(url)
        assert result == {}

    def test_get_flat_qs_dict_multiple_values_same_key(self):
        """Test get_flat_qs_dict with multiple values for same key (returns first)."""
        url = "https://example.com?key=value1&key=value2"
        result = get_flat_qs_dict(url)
        assert result == {"key": "value1"}

    def test_get_flat_qs_dict_empty_list_edge_case(self):
        """Test get_flat_qs_dict when query has empty list value (line 82 edge case)."""
        # Mock parse_qs to return a dict with empty list for a key
        with patch("helpers.web.parse_qs") as mock_parse_qs:
            mock_parse_qs.return_value = {"empty_key": [], "normal_key": ["value"]}
            url = "https://example.com?empty_key=&normal_key=value"
            result = get_flat_qs_dict(url)
            # Should set empty string for empty list (line 82)
            assert result == {"empty_key": "", "normal_key": "value"}


class TestSplitUrl:
    """Tests for the split_url function."""

    @pytest.mark.parametrize(
        ("url", "expected_base", "expected_file"),
        [
            pytest.param(
                "https://example.com/path/to/file.txt",
                "https://example.com/path/to",
                "https://example.com/path/to/file.txt",
                id="basic-path",
            ),
            pytest.param(
                "https://example.com/path/to/file.txt?key=value&foo=bar",
                "https://example.com/path/to",
                "https://example.com/path/to/file.txt",
                id="query-string-stripped",
            ),
            pytest.param(
                "https://example.com/file.txt",
                "https://example.com",
                "https://example.com/file.txt",
                id="root-level-file",
            ),
            pytest.param(
                "https://example.com/path/file.txt#section1",
                "https://example.com/path",
                "https://example.com/path/file.txt",
                id="fragment-stripped",
            ),
        ],
    )
    def test_split_url(self, url: str, expected_base: str, expected_file: str) -> None:
        """split_url yields base_url/file_url with query string and fragment gone."""
        result = split_url(url)
        assert result.base_url == expected_base
        assert result.file_url == expected_file


class TestGetReleaseInfoFromGithub:
    """Tests for the get_release_info_from_github function."""

    def test_get_release_info_all_arms(self):
        """Drive the real ``get_release_info_from_github`` over a single respx
        route on the GitHub releases URL, covering every branch in call order.

        Replaces five ``patch("httpx.get", ...)`` tests (a respx-rule
        violation: HTTP must be intercepted by respx, never by patching httpx
        directly). ``httpx.get`` is sync, so we drive a sync ``respx.mock``
        context here -- the existing async ``respx_*`` fixtures bootstrap a
        Fansly/async client and cannot serve a plain sync GitHub request.

        One route, one ``side_effect`` list, consumed in order:

        1. 200 + JSON       -> returns the parsed release dict
        2. 204              -> passes raise_for_status(), then non-200 guard
                               returns None (a 4xx/5xx would raise first and
                               skip the guard, so a 2xx-non-200 is required to
                               exercise the ``status_code != 200`` branch)
        3. ConnectError     -> network error caught -> None
        4. TimeoutException -> timeout caught -> None
        5. 500              -> raise_for_status() raises HTTPStatusError -> None

        The request itself is asserted via ``route.calls`` (the
        ``user-agent`` header carries the program version).
        """
        release_json = {
            "tag_name": "v1.2.3",
            "name": "Release 1.2.3",
            "body": "Release notes",
        }

        with (
            respx.mock(assert_all_called=True) as respx_mock
        ):  # CCH:respx-mock  # sync httpx.get; async respx_* fixtures serve only Fansly/async clients
            route = respx_mock.get(GITHUB_RELEASES_URL).mock(
                side_effect=[
                    httpx.Response(200, json=release_json),
                    httpx.Response(204),
                    httpx.ConnectError("Network error"),
                    httpx.TimeoutException("Timeout"),
                    httpx.Response(500),
                ]
            )

            try:
                # Arm 1: success
                assert get_release_info_from_github("1.0.0") == release_json
                # Arm 2: non-200 (204) status -- passes raise_for_status()
                assert get_release_info_from_github("1.0.0") is None
                # Arm 3: network error
                assert get_release_info_from_github("1.0.0") is None
                # Arm 4: timeout
                assert get_release_info_from_github("1.0.0") is None
                # Arm 5: HTTP 500 -> raise_for_status raises
                assert get_release_info_from_github("1.0.0") is None
            finally:
                dump_fansly_calls(route.calls, "GitHub release info calls")

            assert route.called
            assert route.call_count == 5
            # Every request carries the version-stamped user-agent header.
            for call in route.calls:
                assert (
                    call.request.headers["user-agent"] == "Fansly Downloader NG 1.0.0"
                )


class TestStripUrlParams:
    """Lines 15-25: strip_url_params removes query string + fragment."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            pytest.param(
                "https://fansly.com/post/123?foo=1&bar=2",
                "https://fansly.com/post/123",
                id="query-string-stripped",
            ),
            pytest.param(
                "https://fansly.com/post/123#section",
                "https://fansly.com/post/123",
                id="fragment-stripped",
            ),
            pytest.param(
                "https://example.com/path?a=1&b=2#frag",
                "https://example.com/path",
                id="query-and-fragment-stripped",
            ),
            pytest.param(
                "https://fansly.com/post/123",
                "https://fansly.com/post/123",
                id="no-params-unchanged",
            ),
            pytest.param(
                "https://cdn.fansly.com/media/abc.mp4?Key-Pair-Id=K123&Signature=xyz",
                "https://cdn.fansly.com/media/abc.mp4",
                id="scheme-and-netloc-preserved",
            ),
        ],
    )
    def test_strip_url_params(self, url: str, expected: str) -> None:
        """strip_url_params removes query string and fragment, keeping the rest."""
        assert strip_url_params(url) == expected
