"""Unit tests for the m3u8 module."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from m3u8 import M3U8
from requests import Response

from config.fanslyconfig import FanslyConfig
from download.m3u8 import (
    download_m3u8,
    fetch_m3u8_segment_playlist,
    get_m3u8_cookies,
    get_m3u8_progress,
)
from errors import M3U8Error


class TestM3U8Cookies:
    """Tests for the get_m3u8_cookies function."""

    def test_get_m3u8_cookies_all_values(self):
        """Test getting CloudFront cookies from a complete M3U8 URL."""
        test_url = "https://media.example.com/hls/video.m3u8?Policy=abc123&Key-Pair-Id=xyz789&Signature=def456"
        cookies = get_m3u8_cookies(test_url)

        assert cookies == {
            "CloudFront-Key-Pair-Id": "xyz789",
            "CloudFront-Policy": "abc123",
            "CloudFront-Signature": "def456",
        }

    def test_get_m3u8_cookies_missing_values(self):
        """Test handling missing values in M3U8 URL."""
        test_url = "https://media.example.com/hls/video.m3u8?Policy=abc123"
        cookies = get_m3u8_cookies(test_url)

        assert cookies == {
            "CloudFront-Key-Pair-Id": "",  # Empty string for missing value
            "CloudFront-Policy": "abc123",  # Present value
            "CloudFront-Signature": "",  # Empty string for missing value
        }

    def test_get_m3u8_cookies_no_values(self):
        """Test handling M3U8 URL with no query parameters."""
        test_url = "https://media.example.com/hls/video.m3u8"
        cookies = get_m3u8_cookies(test_url)

        assert cookies == {
            "CloudFront-Key-Pair-Id": "",
            "CloudFront-Policy": "",
            "CloudFront-Signature": "",
        }


class TestM3U8Progress:
    """Tests for the get_m3u8_progress function."""

    def test_progress_bar_enabled(self):
        """Test progress bar is enabled when not disabled."""
        progress = get_m3u8_progress(disable_loading_bar=False)
        assert progress.disable is False
        assert progress.expand is True
        assert progress.transient is True

    def test_progress_bar_disabled(self):
        """Test progress bar is disabled when requested."""
        progress = get_m3u8_progress(disable_loading_bar=True)
        assert progress.disable is True


class TestFetchM3U8SegmentPlaylist:
    """Tests for the fetch_m3u8_segment_playlist function."""

    @pytest.fixture
    def mock_config(self):
        """Fixture for a mocked FanslyConfig."""
        config = MagicMock(spec=FanslyConfig)
        mock_api = MagicMock()
        mock_response = MagicMock(spec=Response)
        mock_api.get_with_ngsw.return_value.__enter__.return_value = mock_response
        config.get_api.return_value = mock_api
        return config, mock_api, mock_response

    def test_fetch_m3u8_segment_playlist_endlist_vod(self, mock_config):
        """Test fetching an M3U8 playlist that is an endlist VOD."""
        config, _, mock_response = mock_config
        mock_response.status_code = 200
        mock_response.text = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment1.ts
#EXTINF:8.0,
segment2.ts
#EXT-X-ENDLIST"""

        result = fetch_m3u8_segment_playlist(
            config=config,
            m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
        )

        assert isinstance(result, M3U8)
        assert result.is_endlist is True
        assert result.playlist_type == "vod"
        assert len(result.segments) == 2

    def test_fetch_m3u8_segment_playlist_select_highest_resolution(self, mock_config):
        """Test fetching an M3U8 master playlist selects highest resolution."""
        config, mock_api, mock_response = mock_config

        # First response - master playlist
        mock_response.status_code = 200
        mock_response.text = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=640x360
video_360.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
video_720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080
video_1080.m3u8"""

        # Second response - segment playlist
        second_response = MagicMock(spec=Response)
        second_response.status_code = 200
        second_response.text = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment1.ts
#EXTINF:8.0,
segment2.ts
#EXT-X-ENDLIST"""

        # Setup for second request
        mock_api.get_with_ngsw.return_value.__enter__.side_effect = [
            mock_response,  # First call returns master playlist
            second_response,  # Second call returns segment playlist
        ]

        result = fetch_m3u8_segment_playlist(
            config=config,
            m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
        )

        # Verify the result and that the highest resolution was selected
        assert isinstance(result, M3U8)
        assert result.is_endlist is True
        assert result.playlist_type == "vod"
        assert len(result.segments) == 2

        # Check that the API was called with the correct URL for the highest resolution
        calls = mock_api.get_with_ngsw.call_args_list
        assert len(calls) == 2
        _, kwargs = calls[1]
        assert "video_1080.m3u8" in kwargs["url"]

    def test_fetch_m3u8_segment_playlist_empty_playlist(self, mock_config):
        """Test handling an empty M3U8 playlist."""
        config, mock_api, mock_response = mock_config

        # First response - empty playlist
        mock_response.status_code = 200
        mock_response.text = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=0,RESOLUTION=0x0"""

        # Second response - segment playlist
        second_response = MagicMock(spec=Response)
        second_response.status_code = 200
        second_response.text = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment1.ts
#EXTINF:8.0,
segment2.ts
#EXT-X-ENDLIST"""

        # Setup for second request
        mock_api.get_with_ngsw.return_value.__enter__.side_effect = [
            mock_response,  # First call returns empty playlist
            second_response,  # Second call returns segment playlist
        ]

        result = fetch_m3u8_segment_playlist(
            config=config,
            m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
        )

        assert isinstance(result, M3U8)
        assert result.is_endlist is True
        assert result.playlist_type == "vod"
        assert len(result.segments) == 2

        # Check that the API was called with the correct URL for the 1080p fallback
        calls = mock_api.get_with_ngsw.call_args_list
        assert len(calls) == 2
        _, kwargs = calls[1]
        assert "_1080.m3u8" in kwargs["url"]

    def test_fetch_m3u8_segment_playlist_http_error(self, mock_config):
        """Test handling HTTP error when fetching M3U8 playlist."""
        config, _, mock_response = mock_config
        mock_response.status_code = 404
        mock_response.text = "Not Found"

        with pytest.raises(M3U8Error) as excinfo:
            fetch_m3u8_segment_playlist(
                config=config,
                m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
            )

        assert "Failed downloading M3U8 playlist" in str(excinfo.value)
        assert "404" in str(excinfo.value)


@patch("download.m3u8.fetch_m3u8_segment_playlist")
@patch("download.m3u8.concurrent.futures.ThreadPoolExecutor")
@patch("download.m3u8.get_m3u8_progress")
@patch("download.m3u8.run_ffmpeg")
class TestDownloadM3U8:
    """Tests for the download_m3u8 function."""

    @pytest.fixture
    def mock_config(self):
        """Fixture for a mocked FanslyConfig."""
        config = MagicMock(spec=FanslyConfig)
        mock_api = MagicMock()
        config.get_api.return_value = mock_api
        return config

    @pytest.fixture
    def mock_segment_playlist(self):
        """Fixture for a mocked M3U8 segment playlist."""
        playlist = MagicMock(spec=M3U8)

        # Create mock segments
        segment1 = MagicMock()
        segment1.absolute_uri = "https://example.com/segment1.ts"
        segment2 = MagicMock()
        segment2.absolute_uri = "https://example.com/segment2.ts"

        playlist.segments = [segment1, segment2]
        return playlist

    @patch("download.m3u8.open", create=True)
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.unlink")
    @patch("download.m3u8.print_debug")
    def test_download_m3u8_success(
        self,
        mock_print_debug,
        mock_unlink,
        mock_exists,
        mock_open,
        mock_run_ffmpeg,
        mock_progress,
        mock_thread_executor,
        mock_fetch_playlist,
        mock_segment_playlist,
        mock_config,
        tmp_path,
    ):
        """Test successful download of M3U8 content."""
        # Setup
        config = mock_config
        save_path = tmp_path / "video.mp4"

        # Mock file operations
        mock_exists.return_value = True

        # Set up mock thread executor
        mock_executor = MagicMock()
        mock_thread_executor.return_value.__enter__.return_value = mock_executor

        # Set up mock progress bar
        mock_progress_bar = MagicMock()
        mock_progress.return_value = mock_progress_bar

        # Set up mock playlist
        mock_fetch_playlist.return_value = mock_segment_playlist

        # Call the function
        result = download_m3u8(
            config=config,
            m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
            save_path=save_path,
        )

        # Assertions
        assert result == save_path.parent / "video.mp4"
        mock_fetch_playlist.assert_called_once()
        mock_thread_executor.assert_called_once()
        mock_executor.map.assert_called_once()
        mock_run_ffmpeg.assert_called_once()
        mock_unlink.assert_called()  # Cleanup of segment files

    @patch("download.m3u8.open", create=True)
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.unlink")
    @patch("download.m3u8.print_debug")
    def test_download_m3u8_missing_segments(
        self,
        mock_print_debug,
        mock_unlink,
        mock_exists,
        mock_open,
        mock_run_ffmpeg,
        mock_progress,
        mock_thread_executor,
        mock_fetch_playlist,
        mock_segment_playlist,
        mock_config,
        tmp_path,
    ):
        """Test handling missing segments during M3U8 download."""
        # Setup
        config = mock_config
        save_path = tmp_path / "video.mp4"

        # Mock file operations - segments don't exist
        mock_exists.return_value = False

        # Set up mock thread executor
        mock_executor = MagicMock()
        mock_thread_executor.return_value.__enter__.return_value = mock_executor

        # Set up mock progress bar
        mock_progress_bar = MagicMock()
        mock_progress.return_value = mock_progress_bar

        # Set up mock playlist
        mock_fetch_playlist.return_value = mock_segment_playlist

        # Call the function - should raise an error
        with pytest.raises(M3U8Error) as excinfo:
            download_m3u8(
                config=config,
                m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
                save_path=save_path,
            )

        assert "Stream segments failed to download" in str(excinfo.value)
        mock_fetch_playlist.assert_called_once()
        mock_thread_executor.assert_called_once()
        mock_executor.map.assert_called_once()
        mock_run_ffmpeg.assert_not_called()  # FFMPEG should not be called

    @patch("download.m3u8.open", create=True)
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.unlink")
    @patch("download.m3u8.print_debug")
    def test_download_m3u8_ffmpeg_error(
        self,
        mock_print_debug,
        mock_unlink,
        mock_exists,
        mock_open,
        mock_run_ffmpeg,
        mock_progress,
        mock_thread_executor,
        mock_fetch_playlist,
        mock_segment_playlist,
        mock_config,
        tmp_path,
    ):
        """Test handling FFMPEG error during M3U8 download."""
        # Setup
        config = mock_config
        save_path = tmp_path / "video.mp4"

        # Mock file operations
        mock_exists.return_value = True

        # Set up mock thread executor
        mock_executor = MagicMock()
        mock_thread_executor.return_value.__enter__.return_value = mock_executor

        # Set up mock progress bar
        mock_progress_bar = MagicMock()
        mock_progress.return_value = mock_progress_bar

        # Set up mock playlist
        mock_fetch_playlist.return_value = mock_segment_playlist

        # Set up ffmpeg to raise an error
        from subprocess import CalledProcessError

        mock_error = CalledProcessError(1, "ffmpeg", stderr=b"FFMPEG error")
        mock_run_ffmpeg.side_effect = mock_error

        # Call the function - should raise an error
        with pytest.raises(M3U8Error) as excinfo:
            download_m3u8(
                config=config,
                m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
                save_path=save_path,
            )

        assert "Error running ffmpeg" in str(excinfo.value)
        mock_fetch_playlist.assert_called_once()
        mock_thread_executor.assert_called_once()
        mock_executor.map.assert_called_once()
        mock_run_ffmpeg.assert_called_once()
        mock_unlink.assert_called()  # Cleanup should still happen

    @patch("download.m3u8.open", create=True)
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.unlink")
    @patch("download.m3u8.print_debug")
    @patch("os.utime")
    def test_download_m3u8_with_created_at(
        self,
        mock_utime,
        mock_print_debug,
        mock_unlink,
        mock_exists,
        mock_open,
        mock_run_ffmpeg,
        mock_progress,
        mock_thread_executor,
        mock_fetch_playlist,
        mock_segment_playlist,
        mock_config,
        tmp_path,
    ):
        """Test M3U8 download with timestamp setting."""
        # Setup
        config = mock_config
        save_path = tmp_path / "video.mp4"
        created_at = 1633046400  # October 1, 2021

        # Mock file operations
        mock_exists.return_value = True

        # Set up mock thread executor
        mock_executor = MagicMock()
        mock_thread_executor.return_value.__enter__.return_value = mock_executor

        # Set up mock progress bar
        mock_progress_bar = MagicMock()
        mock_progress.return_value = mock_progress_bar

        # Set up mock playlist
        mock_fetch_playlist.return_value = mock_segment_playlist

        # Call the function
        result = download_m3u8(
            config=config,
            m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
            save_path=save_path,
            created_at=created_at,
        )

        # Assertions
        assert result == save_path.parent / "video.mp4"
        mock_fetch_playlist.assert_called_once()
        mock_thread_executor.assert_called_once()
        mock_executor.map.assert_called_once()
        mock_run_ffmpeg.assert_called_once()
        mock_unlink.assert_called()  # Cleanup of segment files

        # Check that timestamp was set
        mock_utime.assert_called_once_with(
            save_path.parent / "video.mp4", (created_at, created_at)
        )
