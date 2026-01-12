"""Unit tests for helpers/browser.py"""

import webbrowser
from unittest.mock import patch

from helpers.browser import open_get_started_url, open_url


class TestOpenUrl:
    """Tests for open_url function."""

    def test_open_url_success(self):
        """Test open_url calls webbrowser.open."""
        with patch("time.sleep"), patch.object(webbrowser, "open") as mock_open:
            open_url("https://example.com")
            mock_open.assert_called_once_with(
                "https://example.com", new=0, autoraise=True
            )

    def test_open_url_exception_suppressed(self):
        """Test open_url suppresses exceptions from webbrowser.open."""
        with (
            patch("time.sleep"),
            patch.object(webbrowser, "open", side_effect=Exception("Browser error")),
        ):
            # Should not raise exception
            open_url("https://example.com")


class TestOpenGetStartedUrl:
    """Tests for open_get_started_url function."""

    def test_open_get_started_url(self):
        """Test open_get_started_url calls open_url with correct URL."""
        with patch("helpers.browser.open_url") as mock_open_url:
            open_get_started_url()
            mock_open_url.assert_called_once_with(
                "https://github.com/prof79/fansly-downloader-ng/wiki/Getting-Started"
            )
