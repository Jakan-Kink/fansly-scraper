"""Unit tests for the m3u8 module."""

from unittest.mock import MagicMock, patch

import ffmpeg
import httpx
import pytest
from m3u8 import M3U8

from config.fanslyconfig import FanslyConfig
from download.m3u8 import (
    _try_direct_download,
    _try_segment_download,
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
        mock_response = MagicMock(spec=httpx.Response)
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
        second_response = MagicMock(spec=httpx.Response)
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
        mock_response.text = """#EXTM3U8
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=0,RESOLUTION=0x0"""

        # Second response - segment playlist
        second_response = MagicMock(spec=httpx.Response)
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


@patch("download.m3u8._try_direct_download")
@patch("download.m3u8._try_segment_download")
class TestDownloadM3U8TwoTierStrategy:
    """Tests for the download_m3u8 function with two-tier strategy."""

    @pytest.fixture
    def mock_config(self):
        """Fixture for a mocked FanslyConfig."""
        config = MagicMock(spec=FanslyConfig)
        mock_api = MagicMock()
        config.get_api.return_value = mock_api
        return config

    def test_download_m3u8_direct_success(
        self,
        mock_segment_download,
        mock_direct_download,
        mock_config,
        tmp_path,
    ):
        """Test M3U8 download when direct download succeeds (fast path)."""
        # Setup
        config = mock_config
        save_path = tmp_path / "video.mp4"
        m3u8_url = (
            "https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def"
        )

        # Mock direct download to succeed
        mock_direct_download.return_value = True

        # Call the function
        result = download_m3u8(
            config=config,
            m3u8_url=m3u8_url,
            save_path=save_path,
        )

        # Assertions
        assert result == save_path.parent / "video.mp4"
        mock_direct_download.assert_called_once()
        mock_segment_download.assert_not_called()  # Should not fallback

    def test_download_m3u8_fallback_to_segments(
        self,
        mock_segment_download,
        mock_direct_download,
        mock_config,
        tmp_path,
    ):
        """Test M3U8 download falls back to segments when direct fails."""
        # Setup
        config = mock_config
        save_path = tmp_path / "video.mp4"
        m3u8_url = (
            "https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def"
        )

        # Mock direct download to fail
        mock_direct_download.return_value = False

        # Mock segment download to succeed
        mock_segment_download.return_value = save_path.parent / "video.mp4"

        # Call the function
        result = download_m3u8(
            config=config,
            m3u8_url=m3u8_url,
            save_path=save_path,
        )

        # Assertions
        assert result == save_path.parent / "video.mp4"
        mock_direct_download.assert_called_once()
        mock_segment_download.assert_called_once()

    @patch("os.utime")
    def test_download_m3u8_with_created_at_direct(
        self,
        mock_utime,
        mock_segment_download,
        mock_direct_download,
        mock_config,
        tmp_path,
    ):
        """Test M3U8 download with timestamp when direct download succeeds."""
        # Setup
        config = mock_config
        save_path = tmp_path / "video.mp4"
        m3u8_url = (
            "https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def"
        )
        created_at = 1633046400  # October 1, 2021

        # Mock direct download to succeed
        mock_direct_download.return_value = True

        # Call the function
        result = download_m3u8(
            config=config,
            m3u8_url=m3u8_url,
            save_path=save_path,
            created_at=created_at,
        )

        # Assertions
        assert result == save_path.parent / "video.mp4"
        mock_direct_download.assert_called_once()
        mock_segment_download.assert_not_called()

        # Check that timestamp was set
        mock_utime.assert_called_once_with(
            save_path.parent / "video.mp4", (created_at, created_at)
        )

    def test_download_m3u8_with_created_at_fallback(
        self,
        mock_segment_download,
        mock_direct_download,
        mock_config,
        tmp_path,
    ):
        """Test M3U8 download with timestamp when falling back to segments."""
        # Setup
        config = mock_config
        save_path = tmp_path / "video.mp4"
        m3u8_url = (
            "https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def"
        )
        created_at = 1633046400  # October 1, 2021

        # Mock direct download to fail
        mock_direct_download.return_value = False

        # Mock segment download to succeed
        mock_segment_download.return_value = save_path.parent / "video.mp4"

        # Call the function
        result = download_m3u8(
            config=config,
            m3u8_url=m3u8_url,
            save_path=save_path,
            created_at=created_at,
        )

        # Assertions
        assert result == save_path.parent / "video.mp4"
        mock_direct_download.assert_called_once()
        mock_segment_download.assert_called_once()

        # Check that created_at was passed to segment download
        _, kwargs = mock_segment_download.call_args
        assert kwargs.get("created_at") == created_at


@patch("download.m3u8.ffmpeg")
@patch("download.m3u8.fetch_m3u8_segment_playlist")
class TestDirectDownload:
    """Tests for the _try_direct_download function."""

    @pytest.fixture
    def mock_config(self):
        """Fixture for a mocked FanslyConfig."""
        config = MagicMock(spec=FanslyConfig)
        mock_api = MagicMock()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_api.get_with_ngsw.return_value.__enter__.return_value = mock_response
        config.get_api.return_value = mock_api
        return config, mock_api, mock_response

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    def test_direct_download_success(
        self,
        mock_stat,
        mock_exists,
        mock_fetch_playlist,
        mock_ffmpeg,
        mock_config,
        tmp_path,
    ):
        """Test successful direct HLS download using ffmpeg."""
        # Setup
        config, _mock_api, mock_response = mock_config
        output_path = tmp_path / "video.mp4"
        cookies = {"CloudFront-Policy": "abc", "CloudFront-Key-Pair-Id": "xyz"}

        # Mock master playlist response
        mock_response.text = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080
video_1080.m3u8"""

        # Mock segment playlist
        mock_playlist = MagicMock()
        mock_fetch_playlist.return_value = mock_playlist

        # Mock ffmpeg chain
        mock_stream = MagicMock()
        mock_stream.get_args.return_value = ["ffmpeg", "-i", "input.m3u8", "output.mp4"]
        mock_stream.run.return_value = None
        mock_ffmpeg.input.return_value.output.return_value.overwrite_output.return_value = mock_stream

        # Mock file existence and size check
        mock_exists.return_value = True
        mock_stat_result = MagicMock()
        mock_stat_result.st_size = 1000000
        mock_stat.return_value = mock_stat_result

        # Call the function
        result = _try_direct_download(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies=cookies,
        )

        # Assertions
        assert result is True
        mock_ffmpeg.input.assert_called_once()
        mock_stream.run.assert_called_once()

    @patch("pathlib.Path.exists")
    def test_direct_download_ffmpeg_error(
        self,
        mock_exists,
        mock_fetch_playlist,
        mock_ffmpeg,
        mock_config,
        tmp_path,
    ):
        """Test direct download handles ffmpeg errors and returns False."""
        # Setup
        config, _mock_api, mock_response = mock_config
        output_path = tmp_path / "video.mp4"
        cookies = {"CloudFront-Policy": "abc"}

        # Mock master playlist response
        mock_response.text = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080
video_1080.m3u8"""

        # Mock ffmpeg to raise an error
        mock_stream = MagicMock()
        mock_stream.run.side_effect = ffmpeg.Error("ffmpeg", b"", b"Error message")
        mock_ffmpeg.input.return_value.output.return_value.overwrite_output.return_value = mock_stream
        mock_ffmpeg.Error = ffmpeg.Error

        # Call the function
        result = _try_direct_download(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies=cookies,
        )

        # Assertions
        assert result is False  # Should return False, not raise
        mock_stream.run.assert_called_once()


@patch("download.m3u8.ffmpeg")
@patch("download.m3u8.fetch_m3u8_segment_playlist")
@patch("download.m3u8.concurrent.futures.ThreadPoolExecutor")
@patch("download.m3u8.get_m3u8_progress")
class TestSegmentDownload:
    """Tests for the _try_segment_download function."""

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
    @patch("pathlib.Path.stat")
    def test_segment_download_success(
        self,
        mock_stat,
        mock_unlink,
        mock_exists,
        mock_open,
        mock_progress,
        mock_thread_executor,
        mock_fetch_playlist,
        mock_ffmpeg,
        mock_segment_playlist,
        mock_config,
        tmp_path,
    ):
        """Test successful segment download and concatenation."""
        # Setup
        config = mock_config
        output_path = tmp_path / "video.mp4"
        cookies = {"CloudFront-Policy": "abc"}

        # Mock file operations
        mock_exists.return_value = True
        mock_stat_result = MagicMock()
        mock_stat_result.st_size = 1000000
        mock_stat.return_value = mock_stat_result

        # Set up mock thread executor
        mock_executor = MagicMock()
        mock_thread_executor.return_value.__enter__.return_value = mock_executor

        # Set up mock progress bar
        mock_progress_bar = MagicMock()
        mock_progress.return_value = mock_progress_bar

        # Set up mock playlist
        mock_fetch_playlist.return_value = mock_segment_playlist

        # Mock ffmpeg concat
        mock_stream = MagicMock()
        mock_stream.get_args.return_value = ["ffmpeg", "-f", "concat", "-i", "list.ffc"]
        mock_stream.run.return_value = None
        mock_ffmpeg.input.return_value.output.return_value.overwrite_output.return_value = mock_stream

        # Call the function
        result = _try_segment_download(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies=cookies,
        )

        # Assertions
        assert result == output_path
        mock_fetch_playlist.assert_called_once()
        mock_thread_executor.assert_called_once()
        mock_executor.map.assert_called_once()
        mock_stream.run.assert_called_once()
        mock_unlink.assert_called()  # Cleanup

    @patch("download.m3u8.open", create=True)
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.unlink")
    def test_segment_download_missing_segments(
        self,
        mock_unlink,
        mock_exists,
        mock_open,
        mock_progress,
        mock_thread_executor,
        mock_fetch_playlist,
        mock_ffmpeg,
        mock_segment_playlist,
        mock_config,
        tmp_path,
    ):
        """Test segment download handles missing segments."""
        # Setup
        config = mock_config
        output_path = tmp_path / "video.mp4"
        cookies = {"CloudFront-Policy": "abc"}

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
            _try_segment_download(
                config=config,
                m3u8_url="https://example.com/video.m3u8",
                output_path=output_path,
                cookies=cookies,
            )

        assert "Stream segments failed to download" in str(excinfo.value)
        mock_fetch_playlist.assert_called_once()
        mock_thread_executor.assert_called_once()
        mock_executor.map.assert_called_once()
