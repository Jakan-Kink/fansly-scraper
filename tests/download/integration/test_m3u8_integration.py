"""Integration tests for the m3u8 module."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import ffmpeg as ffmpeg_lib
import httpx
import pytest
import respx

from config.fanslyconfig import FanslyConfig
from download.m3u8 import download_m3u8, fetch_m3u8_segment_playlist
from errors import M3U8Error


class TestM3U8Integration:
    """Integration tests for the m3u8 module."""

    @pytest.fixture
    def mock_config(self, fansly_api):
        """Fixture for a FanslyConfig with real API and respx mocking."""
        config = MagicMock(spec=FanslyConfig)
        # Use real API from fixture, respx will mock HTTP at edge
        config.get_api.return_value = fansly_api
        return config

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdirname:
            yield Path(tmpdirname)

    @respx.mock
    @patch("download.m3u8.ffmpeg")
    @patch("download.m3u8._try_direct_download")
    def test_full_m3u8_download_workflow(
        self, mock_direct_download, mock_ffmpeg, mock_config, temp_dir
    ):
        """Test the full M3U8 download workflow with segment download fallback - mocks HTTP at edge."""
        config = mock_config

        # Mock direct download to fail (forces segment download)
        mock_direct_download.return_value = False

        # First response - master playlist
        master_playlist = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=640x360
video_360.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
video_720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080
video_1080.m3u8"""

        # Second response - segment playlist
        segment_playlist = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment1.ts
#EXTINF:8.0,
segment2.ts
#EXT-X-ENDLIST"""

        # Segment content - just some dummy data
        segment_content = b"DUMMY_TS_SEGMENT_DATA"

        # Mock HTTP responses at edge using respx
        # OPTIONS requests (preflight)
        respx.options(url__regex=r"https://example\.com/.*").mock(
            return_value=httpx.Response(200)
        )

        # Master playlist request
        respx.get("https://example.com/video.m3u8").mock(
            return_value=httpx.Response(200, text=master_playlist)
        )

        # Segment playlist request (highest quality)
        respx.get("https://example.com/video_1080.m3u8").mock(
            return_value=httpx.Response(200, text=segment_playlist)
        )

        # Segment requests
        respx.get("https://example.com/segment1.ts").mock(
            return_value=httpx.Response(200, content=segment_content)
        )
        respx.get("https://example.com/segment2.ts").mock(
            return_value=httpx.Response(200, content=segment_content)
        )

        # Mock ffmpeg concat
        mock_stream = MagicMock()
        mock_stream.get_args.return_value = ["ffmpeg", "-f", "concat", "-i", "list.ffc"]
        mock_stream.run.return_value = None
        mock_ffmpeg.input.return_value.output.return_value.overwrite_output.return_value = mock_stream
        mock_ffmpeg.Error = ffmpeg_lib.Error

        # Create directory for test (before patching)
        save_path = temp_dir / "video.ts"
        save_path.parent.mkdir(exist_ok=True)

        # Mock exists check for segments and stat check for output file
        mock_stat = MagicMock()
        mock_stat.st_size = 1024  # Mock file size
        mock_stat.st_mode = 33188  # Regular file (S_IFREG | 0644)
        with patch("pathlib.Path.exists", return_value=True), patch("pathlib.Path.stat", return_value=mock_stat):

            # Mock temp file operations for segments and ffmpeg list file
            with patch("builtins.open", create=True):
                # Run the download
                result = download_m3u8(
                    config=config,
                    m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
                    save_path=save_path,
                )

                # Verify results
                assert result == save_path.parent / "video.mp4"
                mock_direct_download.assert_called_once()  # Tried direct first
                mock_stream.run.assert_called_once()  # Then fell back to segment concat

    @respx.mock
    @patch("download.m3u8.ffmpeg")
    @patch("download.m3u8._try_direct_download")
    def test_m3u8_download_with_error_handling(
        self, mock_direct_download, mock_ffmpeg, mock_config, temp_dir
    ):
        """Test M3U8 download with error handling for missing segments - mocks HTTP at edge."""
        config = mock_config

        # Mock direct download to fail (forces segment download)
        mock_direct_download.return_value = False

        # Set up ffmpeg Error class for exception handling
        mock_ffmpeg.Error = ffmpeg_lib.Error

        # Segment playlist with segment links
        segment_playlist = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment1.ts
#EXTINF:8.0,
segment2.ts
#EXT-X-ENDLIST"""

        # Segment content - just some dummy data
        segment_content = b"DUMMY_TS_SEGMENT_DATA"

        # Mock HTTP responses at edge using respx
        # OPTIONS requests (preflight)
        respx.options(url__regex=r"https://example\.com/.*").mock(
            return_value=httpx.Response(200)
        )

        # Segment playlist request
        respx.get("https://example.com/video.m3u8").mock(
            return_value=httpx.Response(200, text=segment_playlist)
        )

        # First segment succeeds
        respx.get("https://example.com/segment1.ts").mock(
            return_value=httpx.Response(200, content=segment_content)
        )

        # Second segment fails
        respx.get("https://example.com/segment2.ts").mock(
            return_value=httpx.Response(404, text="Not Found")
        )

        # Create directory for test (before patching)
        save_path = temp_dir / "video.ts"
        save_path.parent.mkdir(exist_ok=True)

        # Create a custom mock for exists() that makes segments appear to not exist
        exists_mock = MagicMock()
        def exists_side_effect(*args, **kwargs):
            # When called as path.exists(), self is the first argument
            if args:
                path_obj = args[0]
                if hasattr(path_obj, 'name'):
                    # Make segment files appear to not exist to trigger error
                    if path_obj.name.endswith(".ts"):
                        return False
            # Default to True for other paths (like directories, temp files)
            return True
        exists_mock.side_effect = exists_side_effect

        # Setup for the test
        with patch("pathlib.Path.exists", exists_mock):

            # Mock temp file operations for segments and ffmpeg list file
            with patch("builtins.open", create=True):
                # Run the download - should raise M3U8Error for missing segments
                with pytest.raises(M3U8Error) as excinfo:
                    download_m3u8(
                        config=config,
                        m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
                        save_path=save_path,
                    )

                # Verify error was raised due to download failure (segments or output file missing)
                assert "Failed to download HLS video" in str(excinfo.value)
                mock_direct_download.assert_called_once()

    @respx.mock
    @patch("download.m3u8.ffmpeg")
    @patch("download.m3u8._try_direct_download")
    def test_m3u8_download_with_ffmpeg_error(
        self, mock_direct_download, mock_ffmpeg, mock_config, temp_dir
    ):
        """Test M3U8 download with error handling for FFMPEG failure - mocks HTTP at edge."""
        config = mock_config

        # Mock direct download to fail (forces segment download)
        mock_direct_download.return_value = False

        # Segment playlist with segment links
        segment_playlist = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment1.ts
#EXTINF:8.0,
segment2.ts
#EXT-X-ENDLIST"""

        # Segment content - just some dummy data
        segment_content = b"DUMMY_TS_SEGMENT_DATA"

        # Mock HTTP responses at edge using respx
        # OPTIONS requests (preflight)
        respx.options(url__regex=r"https://example\.com/.*").mock(
            return_value=httpx.Response(200)
        )

        # Segment playlist request
        respx.get("https://example.com/video.m3u8").mock(
            return_value=httpx.Response(200, text=segment_playlist)
        )

        # Segment requests
        respx.get("https://example.com/segment1.ts").mock(
            return_value=httpx.Response(200, content=segment_content)
        )
        respx.get("https://example.com/segment2.ts").mock(
            return_value=httpx.Response(200, content=segment_content)
        )

        # Set up ffmpeg to raise an error
        mock_stream = MagicMock()
        mock_stream.run.side_effect = ffmpeg_lib.Error("ffmpeg", b"", b"FFMPEG error")
        mock_ffmpeg.input.return_value.output.return_value.overwrite_output.return_value = mock_stream
        mock_ffmpeg.Error = ffmpeg_lib.Error

        # Mock exists check for segments
        with patch("pathlib.Path.exists", return_value=True):
            # Create directory for test
            save_path = temp_dir / "video.ts"
            save_path.parent.mkdir(exist_ok=True)

            # Mock temp file operations for segments and ffmpeg list file
            with patch("builtins.open", create=True):
                # Run the download - should raise M3U8Error for FFMPEG failure
                with pytest.raises(M3U8Error) as excinfo:
                    download_m3u8(
                        config=config,
                        m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
                        save_path=save_path,
                    )

                assert "Error running ffmpeg" in str(excinfo.value)
                mock_stream.run.assert_called_once()

    @respx.mock
    def test_m3u8_error_propagation(self, mock_config):
        """Test proper error propagation when API returns an error - mocks HTTP at edge."""
        config = mock_config

        # Mock HTTP responses at edge using respx
        # OPTIONS requests (preflight)
        respx.options(url__regex=r"https://example\.com/.*").mock(
            return_value=httpx.Response(200)
        )

        # Mock failed API response at edge
        respx.get("https://example.com/video.m3u8").mock(
            return_value=httpx.Response(403, text="Forbidden")
        )

        # Call function and check error propagation
        with pytest.raises(M3U8Error) as excinfo:
            fetch_m3u8_segment_playlist(
                config=config,
                m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
            )

        assert "Failed downloading M3U8 playlist" in str(excinfo.value)
        assert "403" in str(excinfo.value)

    @respx.mock
    @patch("download.m3u8.ffmpeg")
    @patch("download.m3u8._try_direct_download")
    def test_m3u8_with_timestamp_setting(
        self, mock_direct_download, mock_ffmpeg, mock_config, temp_dir
    ):
        """Test M3U8 download with timestamp setting - mocks HTTP at edge."""
        config = mock_config

        # Mock direct download to fail (forces segment download)
        mock_direct_download.return_value = False

        # Segment playlist
        segment_playlist = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment1.ts
#EXTINF:8.0,
segment2.ts
#EXT-X-ENDLIST"""

        # Segment content
        segment_content = b"DUMMY_TS_SEGMENT_DATA"

        # Mock HTTP responses at edge using respx
        # OPTIONS requests (preflight)
        respx.options(url__regex=r"https://example\.com/.*").mock(
            return_value=httpx.Response(200)
        )

        # Segment playlist request
        respx.get("https://example.com/video.m3u8").mock(
            return_value=httpx.Response(200, text=segment_playlist)
        )

        # Segment requests
        respx.get("https://example.com/segment1.ts").mock(
            return_value=httpx.Response(200, content=segment_content)
        )
        respx.get("https://example.com/segment2.ts").mock(
            return_value=httpx.Response(200, content=segment_content)
        )

        # Mock ffmpeg concat
        mock_stream = MagicMock()
        mock_stream.get_args.return_value = ["ffmpeg", "-f", "concat", "-i", "list.ffc"]
        mock_stream.run.return_value = None
        mock_ffmpeg.input.return_value.output.return_value.overwrite_output.return_value = mock_stream
        mock_ffmpeg.Error = ffmpeg_lib.Error

        # Created timestamp
        created_at = 1633046400  # October 1, 2021

        # Create directory for test (before patching)
        save_path = temp_dir / "video.ts"
        save_path.parent.mkdir(exist_ok=True)

        # Mock exists check for segments and stat check for output file
        mock_stat = MagicMock()
        mock_stat.st_size = 1024  # Mock file size
        mock_stat.st_mode = 33188  # Regular file (S_IFREG | 0644)
        with patch("pathlib.Path.exists", return_value=True), patch("pathlib.Path.stat", return_value=mock_stat):

            # Mock temp file operations and utime
            with patch("builtins.open", create=True), patch("os.utime") as mock_utime:
                # Run the download
                result = download_m3u8(
                    config=config,
                    m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
                    save_path=save_path,
                    created_at=created_at,
                )

                # Verify results
                assert result == save_path.parent / "video.mp4"
                mock_stream.run.assert_called_once()

                # Check that timestamp was set
                mock_utime.assert_called_once_with(
                    save_path.parent / "video.mp4", (created_at, created_at)
                )

    @patch("download.m3u8._try_direct_download")
    @patch("download.m3u8._try_segment_download")
    def test_m3u8_direct_download_success(
        self, mock_segment_download, mock_direct_download, mock_config, temp_dir
    ):
        """Test M3U8 download when direct download succeeds (fast path)."""
        # Setup config
        config = mock_config

        # Mock direct download to succeed
        mock_direct_download.return_value = True

        # Create directory for test
        save_path = temp_dir / "video.ts"
        save_path.parent.mkdir(exist_ok=True)

        # Run the download
        result = download_m3u8(
            config=config,
            m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
            save_path=save_path,
        )

        # Verify results
        assert result == save_path.parent / "video.mp4"
        mock_direct_download.assert_called_once()  # Tried direct
        mock_segment_download.assert_not_called()  # Did NOT fall back
