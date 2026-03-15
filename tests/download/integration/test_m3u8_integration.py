"""Integration tests for the m3u8 module."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    @patch("download.m3u8._mux_segments_with_ffmpeg")
    @patch("download.m3u8._mux_segments_with_pyav")
    @patch("download.m3u8._try_direct_download_ffmpeg")
    @patch("download.m3u8._try_direct_download_pyav")
    def test_full_m3u8_download_workflow(
        self,
        mock_pyav_direct,
        mock_ffmpeg_direct,
        mock_pyav_mux,
        mock_ffmpeg_mux,
        mock_config,
        temp_dir,
    ):
        """Test the full M3U8 download workflow with segment download fallback."""
        config = mock_config

        # Both direct downloads fail (forces segment download)
        mock_pyav_direct.return_value = False
        mock_ffmpeg_direct.return_value = False

        # PyAV mux succeeds
        mock_pyav_mux.return_value = True

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
        respx.options(url__regex=r"https://example\.com/.*").mock(
            return_value=httpx.Response(200)
        )
        respx.get("https://example.com/video.m3u8").mock(
            return_value=httpx.Response(200, text=master_playlist)
        )
        respx.get("https://example.com/video_1080.m3u8").mock(
            return_value=httpx.Response(200, text=segment_playlist)
        )
        respx.get("https://example.com/segment1.ts").mock(
            return_value=httpx.Response(200, content=segment_content)
        )
        respx.get("https://example.com/segment2.ts").mock(
            return_value=httpx.Response(200, content=segment_content)
        )

        # Create directory for test
        save_path = temp_dir / "video.ts"
        save_path.parent.mkdir(exist_ok=True)

        # Mock exists check for segments and stat check for output file
        mock_stat = MagicMock()
        mock_stat.st_size = 1024
        mock_stat.st_mode = 33188
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=mock_stat),
            patch("builtins.open", create=True),
        ):
            result = download_m3u8(
                config=config,
                m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
                save_path=save_path,
            )

            assert result == save_path.parent / "video.mp4"
            mock_pyav_direct.assert_called_once()
            mock_ffmpeg_direct.assert_called_once()
            mock_pyav_mux.assert_called_once()
            mock_ffmpeg_mux.assert_not_called()  # PyAV mux succeeded

    @respx.mock
    @patch("download.m3u8._mux_segments_with_ffmpeg")
    @patch("download.m3u8._mux_segments_with_pyav")
    @patch("download.m3u8._try_direct_download_ffmpeg")
    @patch("download.m3u8._try_direct_download_pyav")
    def test_m3u8_download_with_error_handling(
        self,
        mock_pyav_direct,
        mock_ffmpeg_direct,
        mock_pyav_mux,
        mock_ffmpeg_mux,
        mock_config,
        temp_dir,
    ):
        """Test M3U8 download with error handling for missing segments."""
        config = mock_config

        mock_pyav_direct.return_value = False
        mock_ffmpeg_direct.return_value = False

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

        segment_content = b"DUMMY_TS_SEGMENT_DATA"

        respx.options(url__regex=r"https://example\.com/.*").mock(
            return_value=httpx.Response(200)
        )
        respx.get("https://example.com/video.m3u8").mock(
            return_value=httpx.Response(200, text=segment_playlist)
        )
        respx.get("https://example.com/segment1.ts").mock(
            return_value=httpx.Response(200, content=segment_content)
        )
        respx.get("https://example.com/segment2.ts").mock(
            return_value=httpx.Response(404, text="Not Found")
        )

        save_path = temp_dir / "video.ts"
        save_path.parent.mkdir(exist_ok=True)

        def exists_side_effect(*args, **kwargs):
            if args:
                path_obj = args[0]
                if hasattr(path_obj, "name") and path_obj.name.endswith(".ts"):
                    return False
            return True

        exists_mock = MagicMock()
        exists_mock.side_effect = exists_side_effect

        with (
            patch("pathlib.Path.exists", exists_mock),
            patch("builtins.open", create=True),
            pytest.raises(M3U8Error) as excinfo,
        ):
            download_m3u8(
                config=config,
                m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
                save_path=save_path,
            )

        assert "Failed to download HLS video" in str(excinfo.value)
        mock_pyav_direct.assert_called_once()
        mock_ffmpeg_direct.assert_called_once()

    @respx.mock
    @patch("download.m3u8._mux_segments_with_ffmpeg")
    @patch("download.m3u8._mux_segments_with_pyav")
    @patch("download.m3u8._try_direct_download_ffmpeg")
    @patch("download.m3u8._try_direct_download_pyav")
    def test_m3u8_download_both_mux_fail(
        self,
        mock_pyav_direct,
        mock_ffmpeg_direct,
        mock_pyav_mux,
        mock_ffmpeg_mux,
        mock_config,
        temp_dir,
    ):
        """Test M3U8 download when both PyAV and FFmpeg muxing fail."""
        config = mock_config

        mock_pyav_direct.return_value = False
        mock_ffmpeg_direct.return_value = False
        mock_pyav_mux.return_value = False
        mock_ffmpeg_mux.return_value = False

        segment_playlist = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment1.ts
#EXTINF:8.0,
segment2.ts
#EXT-X-ENDLIST"""

        segment_content = b"DUMMY_TS_SEGMENT_DATA"

        respx.options(url__regex=r"https://example\.com/.*").mock(
            return_value=httpx.Response(200)
        )
        respx.get("https://example.com/video.m3u8").mock(
            return_value=httpx.Response(200, text=segment_playlist)
        )
        respx.get("https://example.com/segment1.ts").mock(
            return_value=httpx.Response(200, content=segment_content)
        )
        respx.get("https://example.com/segment2.ts").mock(
            return_value=httpx.Response(200, content=segment_content)
        )

        save_path = temp_dir / "video.ts"
        save_path.parent.mkdir(exist_ok=True)

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("builtins.open", create=True),
            pytest.raises(M3U8Error) as excinfo,
        ):
            download_m3u8(
                config=config,
                m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
                save_path=save_path,
            )

        assert "Both PyAV and FFmpeg muxing failed" in str(excinfo.value)
        mock_pyav_mux.assert_called_once()
        mock_ffmpeg_mux.assert_called_once()

    @respx.mock
    def test_m3u8_error_propagation(self, mock_config):
        """Test proper error propagation when API returns an error."""
        config = mock_config

        respx.options(url__regex=r"https://example\.com/.*").mock(
            return_value=httpx.Response(200)
        )
        respx.get("https://example.com/video.m3u8").mock(
            return_value=httpx.Response(403, text="Forbidden")
        )

        with pytest.raises(M3U8Error) as excinfo:
            fetch_m3u8_segment_playlist(
                config=config,
                m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
            )

        assert "Failed downloading M3U8 playlist" in str(excinfo.value)
        assert "403" in str(excinfo.value)

    @respx.mock
    @patch("download.m3u8._mux_segments_with_ffmpeg")
    @patch("download.m3u8._mux_segments_with_pyav")
    @patch("download.m3u8._try_direct_download_ffmpeg")
    @patch("download.m3u8._try_direct_download_pyav")
    def test_m3u8_with_timestamp_setting(
        self,
        mock_pyav_direct,
        mock_ffmpeg_direct,
        mock_pyav_mux,
        mock_ffmpeg_mux,
        mock_config,
        temp_dir,
    ):
        """Test M3U8 download with timestamp setting."""
        config = mock_config

        mock_pyav_direct.return_value = False
        mock_ffmpeg_direct.return_value = False
        mock_pyav_mux.return_value = True

        segment_playlist = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment1.ts
#EXTINF:8.0,
segment2.ts
#EXT-X-ENDLIST"""

        segment_content = b"DUMMY_TS_SEGMENT_DATA"

        respx.options(url__regex=r"https://example\.com/.*").mock(
            return_value=httpx.Response(200)
        )
        respx.get("https://example.com/video.m3u8").mock(
            return_value=httpx.Response(200, text=segment_playlist)
        )
        respx.get("https://example.com/segment1.ts").mock(
            return_value=httpx.Response(200, content=segment_content)
        )
        respx.get("https://example.com/segment2.ts").mock(
            return_value=httpx.Response(200, content=segment_content)
        )

        created_at = 1633046400  # October 1, 2021

        save_path = temp_dir / "video.ts"
        save_path.parent.mkdir(exist_ok=True)

        mock_stat = MagicMock()
        mock_stat.st_size = 1024
        mock_stat.st_mode = 33188
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=mock_stat),
            patch("builtins.open", create=True),
            patch("os.utime") as mock_utime,
        ):
            result = download_m3u8(
                config=config,
                m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
                save_path=save_path,
                created_at=created_at,
            )

            assert result == save_path.parent / "video.mp4"
            mock_pyav_mux.assert_called_once()

            mock_utime.assert_called_once_with(
                save_path.parent / "video.mp4", (created_at, created_at)
            )

    @patch("download.m3u8._try_direct_download_pyav")
    @patch("download.m3u8._try_direct_download_ffmpeg")
    @patch("download.m3u8._try_segment_download")
    def test_m3u8_pyav_direct_success(
        self,
        mock_segment_download,
        mock_ffmpeg_direct,
        mock_pyav_direct,
        mock_config,
        temp_dir,
    ):
        """Test M3U8 download when PyAV direct download succeeds (fastest path)."""
        config = mock_config

        mock_pyav_direct.return_value = True

        save_path = temp_dir / "video.ts"
        save_path.parent.mkdir(exist_ok=True)

        result = download_m3u8(
            config=config,
            m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
            save_path=save_path,
        )

        assert result == save_path.parent / "video.mp4"
        mock_pyav_direct.assert_called_once()
        mock_ffmpeg_direct.assert_not_called()
        mock_segment_download.assert_not_called()
