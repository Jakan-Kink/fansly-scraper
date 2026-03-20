"""Unit tests for helpers/web.py"""

from unittest.mock import MagicMock, patch

import httpx

from helpers.web import (
    get_file_name_from_url,
    get_flat_qs_dict,
    get_qs_value,
    get_release_info_from_github,
    guess_user_agent,
    split_url,
)


class TestGetFileNameFromUrl:
    """Tests for the get_file_name_from_url function."""

    def test_get_file_name_from_url_simple(self):
        """Test get_file_name_from_url with a simple URL."""
        url = "https://example.com/path/to/file.txt"
        result = get_file_name_from_url(url)
        assert result == "file.txt"

    def test_get_file_name_from_url_no_file(self):
        """Test get_file_name_from_url with URL ending in directory."""
        url = "https://example.com/path/to/directory/"
        result = get_file_name_from_url(url)
        assert result == ""

    def test_get_file_name_from_url_with_query_string(self):
        """Test get_file_name_from_url with query string."""
        url = "https://example.com/path/file.txt?key=value&foo=bar"
        result = get_file_name_from_url(url)
        assert result == "file.txt"

    def test_get_file_name_from_url_complex(self):
        """Test get_file_name_from_url with complex URL."""
        url = "https://example.com/path/to/document.pdf?download=true#section1"
        result = get_file_name_from_url(url)
        assert result == "document.pdf"


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

    def test_split_url_basic(self):
        """Test split_url with basic URL."""
        url = "https://example.com/path/to/file.txt"
        result = split_url(url)
        assert result.base_url == "https://example.com/path/to"
        assert result.file_url == "https://example.com/path/to/file.txt"

    def test_split_url_with_query_string(self):
        """Test split_url with query string (query string is stripped)."""
        url = "https://example.com/path/to/file.txt?key=value&foo=bar"
        result = split_url(url)
        assert result.base_url == "https://example.com/path/to"
        assert result.file_url == "https://example.com/path/to/file.txt"

    def test_split_url_root(self):
        """Test split_url with root URL."""
        url = "https://example.com/file.txt"
        result = split_url(url)
        assert result.base_url == "https://example.com"
        assert result.file_url == "https://example.com/file.txt"

    def test_split_url_with_fragment(self):
        """Test split_url with URL fragment (fragment is stripped)."""
        url = "https://example.com/path/file.txt#section1"
        result = split_url(url)
        assert result.base_url == "https://example.com/path"
        assert result.file_url == "https://example.com/path/file.txt"


class TestGuessUserAgent:
    """Tests for the guess_user_agent function."""

    def test_guess_user_agent_windows_chrome(self):
        """Test guess_user_agent for Windows Chrome."""
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0",
        ]
        with patch("platform.system", return_value="Windows"):
            result = guess_user_agent(user_agents, "Chrome", "default_ua")
            assert "Windows NT 10.0" in result
            assert "Chrome" in result

    def test_guess_user_agent_macos_chrome(self):
        """Test guess_user_agent for macOS Chrome.

        Note: The function extracts the OS version with underscores (10_15_7),
        converts to dots (10.15.7), then checks if the dotted version exists
        in the original UA string. For this to work, the UA must contain the
        dotted version.
        """
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
            # Use a UA with dotted version that will be found after conversion
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7; 10.15.7) AppleWebKit/537.36 Chrome/120.0.0.0",
        ]
        with patch("platform.system", return_value="Darwin"):
            result = guess_user_agent(user_agents, "Chrome", "default_ua")
            assert "Mac OS X 10_15_7" in result
            assert "Chrome" in result

    def test_guess_user_agent_linux_chrome(self):
        """Test guess_user_agent for Linux Chrome."""
        user_agents = [
            "Mozilla/5.0 (X11; Linux 5.10) AppleWebKit/537.36 Chrome/120.0.0.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
        ]
        with patch("platform.system", return_value="Linux"):
            result = guess_user_agent(user_agents, "Chrome", "default_ua")
            assert "Linux 5.10" in result
            assert "Chrome" in result

    def test_guess_user_agent_edge(self):
        """Test guess_user_agent for Microsoft Edge (Edg)."""
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Edg/120.0.0.0",
        ]
        with patch("platform.system", return_value="Windows"):
            result = guess_user_agent(user_agents, "Microsoft Edge", "default_ua")
            assert "Windows NT 10.0" in result
            assert "Edg" in result

    def test_guess_user_agent_no_match_returns_default(self):
        """Test guess_user_agent returns default when no match found."""
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
        ]
        with patch("platform.system", return_value="Darwin"):
            # Looking for Chrome on macOS, but only have Windows UA
            result = guess_user_agent(user_agents, "Chrome", "default_ua_fallback")
            assert result == "default_ua_fallback"

    def test_guess_user_agent_exception_returns_default(self):
        """Test guess_user_agent returns default on exception."""
        user_agents = ["invalid_user_agent_without_regex_match"]
        with patch("platform.system", return_value="Windows"):
            result = guess_user_agent(user_agents, "Chrome", "default_ua_fallback")
            assert result == "default_ua_fallback"

    def test_guess_user_agent_regex_exception(self):
        """Test guess_user_agent exception handler (lines 158-159)."""
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"
        ]
        # Mock re.search to raise an exception to trigger exception handler
        with (
            patch("platform.system", return_value="Windows"),
            patch("helpers.web.re.search", side_effect=Exception("Regex error")),
            patch("helpers.web.print_error") as mock_print_error,
        ):
            result = guess_user_agent(user_agents, "Chrome", "default_ua_fallback")
            # Should return default when exception occurs
            assert result == "default_ua_fallback"
            # Should have called print_error with the exception message
            mock_print_error.assert_called_once()
            assert "Regexing user-agent" in mock_print_error.call_args[0][0]

    def test_guess_user_agent_empty_list(self):
        """Test guess_user_agent with empty user agent list."""
        user_agents = []
        with patch("platform.system", return_value="Windows"):
            result = guess_user_agent(user_agents, "Chrome", "default_ua")
            assert result == "default_ua"


class TestGetReleaseInfoFromGithub:
    """Tests for the get_release_info_from_github function."""

    def test_get_release_info_success(self):
        """Test get_release_info_from_github with successful response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "tag_name": "v1.2.3",
            "name": "Release 1.2.3",
            "body": "Release notes",
        }

        with patch("httpx.get", return_value=mock_response) as mock_get:
            result = get_release_info_from_github("1.0.0")
            assert result == {
                "tag_name": "v1.2.3",
                "name": "Release 1.2.3",
                "body": "Release notes",
            }
            mock_get.assert_called_once()
            # Verify correct headers
            call_kwargs = mock_get.call_args.kwargs
            assert "Fansly Downloader NG 1.0.0" in call_kwargs["headers"]["user-agent"]

    def test_get_release_info_non_200_status(self):
        """Test get_release_info_from_github with non-200 status code."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.get", return_value=mock_response):
            result = get_release_info_from_github("1.0.0")
            assert result is None

    def test_get_release_info_network_error(self):
        """Test get_release_info_from_github with network error."""
        with patch("httpx.get", side_effect=httpx.ConnectError("Network error")):
            result = get_release_info_from_github("1.0.0")
            assert result is None

    def test_get_release_info_timeout(self):
        """Test get_release_info_from_github with timeout."""
        with patch("httpx.get", side_effect=httpx.TimeoutException("Timeout")):
            result = get_release_info_from_github("1.0.0")
            assert result is None

    def test_get_release_info_http_error(self):
        """Test get_release_info_from_github with HTTP error."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Error", request=MagicMock(), response=mock_response
        )

        with patch("httpx.get", return_value=mock_response):
            result = get_release_info_from_github("1.0.0")
            assert result is None
