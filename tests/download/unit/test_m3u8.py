"""Unit tests for the m3u8 module."""

from unittest.mock import MagicMock, patch

import ffmpeg
import httpx
import pytest
from m3u8 import M3U8

from config.fanslyconfig import FanslyConfig
from download.m3u8 import (
    _try_direct_download_ffmpeg,
    _try_direct_download_pyav,
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
            "CloudFront-Key-Pair-Id": None,  # None for missing value
            "CloudFront-Policy": "abc123",  # Present value
            "CloudFront-Signature": None,  # None for missing value
        }

    def test_get_m3u8_cookies_no_values(self):
        """Test handling M3U8 URL with no query parameters."""
        test_url = "https://media.example.com/hls/video.m3u8"
        cookies = get_m3u8_cookies(test_url)

        assert cookies == {
            "CloudFront-Key-Pair-Id": None,
            "CloudFront-Policy": None,
            "CloudFront-Signature": None,
        }


class TestM3U8Progress:
    """Tests for the get_m3u8_progress function."""

    def test_progress_bar_enabled(self):
        """Test progress bar is enabled when not disabled."""
        progress = get_m3u8_progress(disable_loading_bar=False)
        assert progress.disable is False
        assert progress.expand is True
        # Note: transient is a constructor parameter but not exposed as an attribute

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
        # get_with_ngsw returns httpx.Response directly, not a context manager
        mock_api.get_with_ngsw.return_value = mock_response
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

        master_playlist_text = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=640x360
video_360.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
video_720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080
video_1080.m3u8"""

        # First response - master playlist (by fetch_m3u8_segment_playlist)
        mock_response.status_code = 200
        mock_response.text = master_playlist_text

        # Second response - master playlist again (by _get_highest_quality_variant_url)
        second_response = MagicMock(spec=httpx.Response)
        second_response.status_code = 200
        second_response.text = master_playlist_text

        # Third response - segment playlist (recursive call)
        third_response = MagicMock(spec=httpx.Response)
        third_response.status_code = 200
        third_response.text = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment1.ts
#EXTINF:8.0,
segment2.ts
#EXT-X-ENDLIST"""

        mock_api.get_with_ngsw.side_effect = [
            mock_response,  # fetch_m3u8_segment_playlist (master)
            second_response,  # _get_highest_quality_variant_url (master again)
            third_response,  # fetch_m3u8_segment_playlist (segments)
        ]

        result = fetch_m3u8_segment_playlist(
            config=config,
            m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
        )

        assert isinstance(result, M3U8)
        assert result.is_endlist is True
        assert result.playlist_type == "vod"
        assert len(result.segments) == 2

        # Third call should target the highest resolution variant
        calls = mock_api.get_with_ngsw.call_args_list
        assert len(calls) == 3
        _, kwargs = calls[2]
        assert "video_1080.m3u8" in kwargs["url"]

    def test_fetch_m3u8_segment_playlist_empty_playlist(self, mock_config):
        """Test handling an empty M3U8 playlist."""
        config, mock_api, mock_response = mock_config

        empty_playlist_text = """#EXTM3U8
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=0,RESOLUTION=0x0"""

        # First response - empty playlist (by fetch_m3u8_segment_playlist)
        mock_response.status_code = 200
        mock_response.text = empty_playlist_text

        # Second response - empty playlist again (by _get_highest_quality_variant_url)
        second_response = MagicMock(spec=httpx.Response)
        second_response.status_code = 200
        second_response.text = empty_playlist_text

        # Third response - segment playlist (recursive call with guessed 1080p URL)
        third_response = MagicMock(spec=httpx.Response)
        third_response.status_code = 200
        third_response.text = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment1.ts
#EXTINF:8.0,
segment2.ts
#EXT-X-ENDLIST"""

        mock_api.get_with_ngsw.side_effect = [
            mock_response,  # fetch_m3u8_segment_playlist (empty master)
            second_response,  # _get_highest_quality_variant_url (empty again)
            third_response,  # fetch_m3u8_segment_playlist (segments via guessed URL)
        ]

        result = fetch_m3u8_segment_playlist(
            config=config,
            m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
        )

        assert isinstance(result, M3U8)
        assert result.is_endlist is True
        assert result.playlist_type == "vod"
        assert len(result.segments) == 2

        # Third call should use the 1080p fallback URL
        calls = mock_api.get_with_ngsw.call_args_list
        assert len(calls) == 3
        _, kwargs = calls[2]
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


@patch("download.m3u8._try_direct_download_pyav")
@patch("download.m3u8._try_direct_download_ffmpeg")
@patch("download.m3u8._try_segment_download")
class TestDownloadM3U8ThreeTierStrategy:
    """Tests for the download_m3u8 function with three-tier strategy."""

    @pytest.fixture
    def mock_config(self):
        """Fixture for a mocked FanslyConfig."""
        config = MagicMock(spec=FanslyConfig)
        mock_api = MagicMock()
        config.get_api.return_value = mock_api
        return config

    def test_download_m3u8_pyav_success(
        self,
        mock_segment_download,
        mock_ffmpeg_download,
        mock_pyav_download,
        mock_config,
        tmp_path,
    ):
        """Test M3U8 download when PyAV download succeeds (fastest path)."""
        config = mock_config
        save_path = tmp_path / "video.mp4"
        m3u8_url = (
            "https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def"
        )

        mock_pyav_download.return_value = True

        result = download_m3u8(
            config=config,
            m3u8_url=m3u8_url,
            save_path=save_path,
        )

        assert result == save_path.parent / "video.mp4"
        mock_pyav_download.assert_called_once()
        mock_ffmpeg_download.assert_not_called()
        mock_segment_download.assert_not_called()

    def test_download_m3u8_ffmpeg_fallback(
        self,
        mock_segment_download,
        mock_ffmpeg_download,
        mock_pyav_download,
        mock_config,
        tmp_path,
    ):
        """Test M3U8 download falls back to FFmpeg when PyAV fails."""
        config = mock_config
        save_path = tmp_path / "video.mp4"
        m3u8_url = (
            "https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def"
        )

        mock_pyav_download.return_value = False
        mock_ffmpeg_download.return_value = True

        result = download_m3u8(
            config=config,
            m3u8_url=m3u8_url,
            save_path=save_path,
        )

        assert result == save_path.parent / "video.mp4"
        mock_pyav_download.assert_called_once()
        mock_ffmpeg_download.assert_called_once()
        mock_segment_download.assert_not_called()

    def test_download_m3u8_segment_fallback(
        self,
        mock_segment_download,
        mock_ffmpeg_download,
        mock_pyav_download,
        mock_config,
        tmp_path,
    ):
        """Test M3U8 download falls back to segments when both direct fail."""
        config = mock_config
        save_path = tmp_path / "video.mp4"
        m3u8_url = (
            "https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def"
        )

        mock_pyav_download.return_value = False
        mock_ffmpeg_download.return_value = False
        mock_segment_download.return_value = save_path.parent / "video.mp4"

        result = download_m3u8(
            config=config,
            m3u8_url=m3u8_url,
            save_path=save_path,
        )

        assert result == save_path.parent / "video.mp4"
        mock_pyav_download.assert_called_once()
        mock_ffmpeg_download.assert_called_once()
        mock_segment_download.assert_called_once()

    @patch("os.utime")
    def test_download_m3u8_with_created_at_pyav(
        self,
        mock_utime,
        mock_segment_download,
        mock_ffmpeg_download,
        mock_pyav_download,
        mock_config,
        tmp_path,
    ):
        """Test M3U8 download with timestamp when PyAV succeeds."""
        config = mock_config
        save_path = tmp_path / "video.mp4"
        m3u8_url = (
            "https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def"
        )
        created_at = 1633046400

        mock_pyav_download.return_value = True

        result = download_m3u8(
            config=config,
            m3u8_url=m3u8_url,
            save_path=save_path,
            created_at=created_at,
        )

        assert result == save_path.parent / "video.mp4"
        mock_pyav_download.assert_called_once()
        mock_utime.assert_called_once_with(
            save_path.parent / "video.mp4", (created_at, created_at)
        )

    def test_download_m3u8_with_created_at_segment_fallback(
        self,
        mock_segment_download,
        mock_ffmpeg_download,
        mock_pyav_download,
        mock_config,
        tmp_path,
    ):
        """Test M3U8 download with timestamp when falling back to segments."""
        config = mock_config
        save_path = tmp_path / "video.mp4"
        m3u8_url = (
            "https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def"
        )
        created_at = 1633046400

        mock_pyav_download.return_value = False
        mock_ffmpeg_download.return_value = False
        mock_segment_download.return_value = save_path.parent / "video.mp4"

        result = download_m3u8(
            config=config,
            m3u8_url=m3u8_url,
            save_path=save_path,
            created_at=created_at,
        )

        assert result == save_path.parent / "video.mp4"
        mock_pyav_download.assert_called_once()
        mock_ffmpeg_download.assert_called_once()
        mock_segment_download.assert_called_once()

        # created_at passed as 5th positional arg
        args, _ = mock_segment_download.call_args
        assert args[4] == created_at


@patch("download.m3u8.ffmpeg")
@patch("download.m3u8._get_highest_quality_variant_url")
class TestDirectDownloadFFmpeg:
    """Tests for the _try_direct_download_ffmpeg function."""

    @pytest.fixture
    def mock_config(self):
        """Fixture for a mocked FanslyConfig."""
        config = MagicMock(spec=FanslyConfig)
        mock_api = MagicMock()
        config.get_api.return_value = mock_api
        return config

    @patch("helpers.rich_progress.ffmpeg_progress")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    @patch("pathlib.Path.unlink")
    def test_ffmpeg_download_success(
        self,
        mock_unlink,
        mock_stat,
        mock_exists,
        mock_ffmpeg_progress,
        mock_variant_url,
        mock_ffmpeg,
        mock_config,
        tmp_path,
    ):
        """Test successful direct HLS download using ffmpeg subprocess."""
        config = mock_config
        output_path = tmp_path / "video.mp4"
        cookies = {"CloudFront-Policy": "abc", "CloudFront-Key-Pair-Id": "xyz"}

        mock_variant_url.return_value = "https://example.com/video_1080.m3u8"

        # Mock ffmpeg chain
        mock_stream = MagicMock()
        mock_stream.get_args.return_value = ["ffmpeg", "-i", "input.m3u8", "output.mp4"]
        mock_stream.run.return_value = None
        mock_stream.global_args.return_value = mock_stream
        mock_ffmpeg.input.return_value.output.return_value.overwrite_output.return_value = mock_stream

        # Mock ffmpeg_progress context manager
        mock_ffmpeg_progress.return_value.__enter__ = MagicMock(
            return_value="progress.txt"
        )
        mock_ffmpeg_progress.return_value.__exit__ = MagicMock(return_value=False)

        # Mock probe for duration detection (best-effort)
        mock_ffmpeg.probe.return_value = {"format": {"duration": "120.5"}}

        # Mock file existence and size check
        mock_exists.return_value = True
        mock_stat_result = MagicMock()
        mock_stat_result.st_size = 1000000
        mock_stat.return_value = mock_stat_result

        # Mock probe for stream verification
        mock_ffmpeg.probe.return_value = {
            "format": {"duration": "120.5"},
            "streams": [
                {"codec_type": "video"},
                {"codec_type": "audio"},
            ],
        }

        result = _try_direct_download_ffmpeg(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies=cookies,
        )

        assert result is True
        mock_ffmpeg.input.assert_called_once()

    @patch("helpers.rich_progress.ffmpeg_progress")
    def test_ffmpeg_download_error(
        self,
        mock_ffmpeg_progress,
        mock_variant_url,
        mock_ffmpeg,
        mock_config,
        tmp_path,
    ):
        """Test FFmpeg download handles errors and returns False."""
        config = mock_config
        output_path = tmp_path / "video.mp4"
        cookies = {"CloudFront-Policy": "abc"}

        mock_variant_url.return_value = "https://example.com/video_1080.m3u8"

        # Mock ffmpeg to raise an error
        mock_stream = MagicMock()
        mock_stream.global_args.return_value = mock_stream
        mock_stream.run.side_effect = ffmpeg.Error("ffmpeg", b"", b"Error message")
        mock_ffmpeg.input.return_value.output.return_value.overwrite_output.return_value = mock_stream
        mock_ffmpeg.Error = ffmpeg.Error
        mock_ffmpeg.probe.side_effect = Exception("probe failed")

        # Mock ffmpeg_progress context manager
        mock_ffmpeg_progress.return_value.__enter__ = MagicMock(
            return_value="progress.txt"
        )
        mock_ffmpeg_progress.return_value.__exit__ = MagicMock(return_value=False)

        result = _try_direct_download_ffmpeg(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies=cookies,
        )

        assert result is False


@patch("download.m3u8.av")
@patch("download.m3u8._get_highest_quality_variant_url")
class TestDirectDownloadPyAV:
    """Tests for the _try_direct_download_pyav function."""

    @pytest.fixture
    def mock_config(self):
        """Fixture for a mocked FanslyConfig."""
        config = MagicMock(spec=FanslyConfig)
        return config

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.stat")
    def test_pyav_download_success(
        self,
        mock_stat,
        mock_exists,
        mock_variant_url,
        mock_av,
        mock_config,
        tmp_path,
    ):
        """Test successful direct HLS download using PyAV."""
        config = mock_config
        output_path = tmp_path / "video.mp4"
        cookies = {"CloudFront-Policy": "abc"}

        mock_variant_url.return_value = "https://example.com/video_1080.m3u8"

        # Mock PyAV containers
        mock_input = MagicMock()
        mock_output = MagicMock()
        mock_av.open.side_effect = [mock_input, mock_output]

        # Mock streams with codec_context
        mock_stream = MagicMock()
        mock_stream.codec_context = MagicMock()
        mock_input.streams = [mock_stream]

        mock_output_stream = MagicMock()
        mock_output.add_stream.return_value = mock_output_stream

        # Mock demux packets
        mock_packet = MagicMock()
        mock_packet.stream = mock_stream
        mock_input.demux.return_value = [mock_packet]

        # Mock file verification
        mock_exists.return_value = True
        mock_stat_result = MagicMock()
        mock_stat_result.st_size = 500000
        mock_stat.return_value = mock_stat_result

        result = _try_direct_download_pyav(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies=cookies,
        )

        assert result is True
        assert mock_av.open.call_count == 2
        mock_output.mux.assert_called_once()

    def test_pyav_download_error(
        self,
        mock_variant_url,
        mock_av,
        mock_config,
        tmp_path,
    ):
        """Test PyAV download handles errors and returns False."""
        import av as real_av

        config = mock_config
        output_path = tmp_path / "video.mp4"
        cookies = {"CloudFront-Policy": "abc"}

        mock_variant_url.return_value = "https://example.com/video_1080.m3u8"
        mock_av.open.side_effect = Exception("Connection refused")
        mock_av.error.FFmpegError = real_av.error.FFmpegError

        result = _try_direct_download_pyav(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies=cookies,
        )

        assert result is False


@patch("download.m3u8._mux_segments_with_pyav")
@patch("download.m3u8._mux_segments_with_ffmpeg")
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

        segment1 = MagicMock()
        segment1.absolute_uri = "https://example.com/segment1.ts"
        segment2 = MagicMock()
        segment2.absolute_uri = "https://example.com/segment2.ts"

        playlist.segments = [segment1, segment2]
        return playlist

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.unlink")
    @patch("pathlib.Path.stat")
    def test_segment_download_pyav_mux_success(
        self,
        mock_stat,
        mock_unlink,
        mock_exists,
        mock_progress,
        mock_thread_executor,
        mock_fetch_playlist,
        mock_ffmpeg_mux,
        mock_pyav_mux,
        mock_segment_playlist,
        mock_config,
        tmp_path,
    ):
        """Test successful segment download with PyAV muxing."""
        config = mock_config
        output_path = tmp_path / "video.mp4"
        cookies = {"CloudFront-Policy": "abc"}

        mock_exists.return_value = True
        mock_stat_result = MagicMock()
        mock_stat_result.st_size = 1000000
        mock_stat.return_value = mock_stat_result

        mock_executor = MagicMock()
        mock_thread_executor.return_value.__enter__.return_value = mock_executor

        mock_progress_bar = MagicMock()
        mock_progress.return_value = mock_progress_bar

        mock_fetch_playlist.return_value = mock_segment_playlist

        # PyAV mux succeeds
        mock_pyav_mux.return_value = True

        result = _try_segment_download(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies=cookies,
        )

        assert result == output_path
        mock_fetch_playlist.assert_called_once()
        mock_pyav_mux.assert_called_once()
        mock_ffmpeg_mux.assert_not_called()  # PyAV succeeded, no ffmpeg fallback

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.unlink")
    @patch("pathlib.Path.stat")
    def test_segment_download_ffmpeg_mux_fallback(
        self,
        mock_stat,
        mock_unlink,
        mock_exists,
        mock_progress,
        mock_thread_executor,
        mock_fetch_playlist,
        mock_ffmpeg_mux,
        mock_pyav_mux,
        mock_segment_playlist,
        mock_config,
        tmp_path,
    ):
        """Test segment download falls back to ffmpeg concat when PyAV fails."""
        config = mock_config
        output_path = tmp_path / "video.mp4"
        cookies = {"CloudFront-Policy": "abc"}

        mock_exists.return_value = True
        mock_stat_result = MagicMock()
        mock_stat_result.st_size = 1000000
        mock_stat.return_value = mock_stat_result

        mock_executor = MagicMock()
        mock_thread_executor.return_value.__enter__.return_value = mock_executor

        mock_progress_bar = MagicMock()
        mock_progress.return_value = mock_progress_bar

        mock_fetch_playlist.return_value = mock_segment_playlist

        # PyAV mux fails, ffmpeg succeeds
        mock_pyav_mux.return_value = False
        mock_ffmpeg_mux.return_value = True

        result = _try_segment_download(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies=cookies,
        )

        assert result == output_path
        mock_pyav_mux.assert_called_once()
        mock_ffmpeg_mux.assert_called_once()

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.unlink")
    def test_segment_download_missing_segments(
        self,
        mock_unlink,
        mock_exists,
        mock_progress,
        mock_thread_executor,
        mock_fetch_playlist,
        mock_ffmpeg_mux,
        mock_pyav_mux,
        mock_segment_playlist,
        mock_config,
        tmp_path,
    ):
        """Test segment download handles missing segments."""
        config = mock_config
        output_path = tmp_path / "video.mp4"
        cookies = {"CloudFront-Policy": "abc"}

        mock_exists.return_value = False

        mock_executor = MagicMock()
        mock_thread_executor.return_value.__enter__.return_value = mock_executor

        mock_progress_bar = MagicMock()
        mock_progress.return_value = mock_progress_bar

        mock_fetch_playlist.return_value = mock_segment_playlist

        with pytest.raises(M3U8Error) as excinfo:
            _try_segment_download(
                config=config,
                m3u8_url="https://example.com/video.m3u8",
                output_path=output_path,
                cookies=cookies,
            )

        assert "Stream segments failed to download" in str(excinfo.value)
        mock_fetch_playlist.assert_called_once()
        mock_pyav_mux.assert_not_called()  # Should fail before muxing
