"""Integration tests for the m3u8 module."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from m3u8 import M3U8

from config.fanslyconfig import FanslyConfig
from download.m3u8 import download_m3u8, fetch_m3u8_segment_playlist
from errors import M3U8Error


class TestM3U8Integration:
    """Integration tests for the m3u8 module."""

    @pytest.fixture
    def mock_config(self):
        """Fixture for a mocked FanslyConfig with working API."""
        config = MagicMock(spec=FanslyConfig)
        mock_api = MagicMock()

        # Create a mock response for get_with_ngsw
        mock_response = MagicMock(spec=requests.Response)
        mock_response.status_code = 200
        mock_api.get_with_ngsw.return_value.__enter__.return_value = mock_response

        config.get_api.return_value = mock_api
        return config, mock_api, mock_response

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdirname:
            yield Path(tmpdirname)

    @patch("download.m3u8.run_ffmpeg")
    def test_full_m3u8_download_workflow(self, mock_run_ffmpeg, mock_config, temp_dir):
        """Test the full M3U8 download workflow with mocked segments."""
        # Setup config and mock responses
        config, mock_api, mock_response = mock_config

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

        # Set up mock api response sequence
        # 1. Master playlist, 2. Segment playlist, 3-4. Segments
        mock_master_response = MagicMock(spec=requests.Response)
        mock_master_response.status_code = 200
        mock_master_response.text = master_playlist

        mock_segment_playlist_response = MagicMock(spec=requests.Response)
        mock_segment_playlist_response.status_code = 200
        mock_segment_playlist_response.text = segment_playlist

        mock_segment_response1 = MagicMock(spec=requests.Response)
        mock_segment_response1.status_code = 200
        mock_segment_response1.iter_content.return_value = [segment_content]

        mock_segment_response2 = MagicMock(spec=requests.Response)
        mock_segment_response2.status_code = 200
        mock_segment_response2.iter_content.return_value = [segment_content]

        # Configure the API mock to return the sequence of responses
        mock_api.get_with_ngsw.return_value.__enter__.side_effect = [
            mock_master_response,  # 1. Master playlist
            mock_segment_playlist_response,  # 2. Segment playlist
            mock_segment_response1,  # 3. First segment
            mock_segment_response2,  # 4. Second segment
        ]

        # Mock exists check for segments
        with patch("pathlib.Path.exists", return_value=True):
            # Create directory for test
            save_path = temp_dir / "video.ts"
            save_path.parent.mkdir(exist_ok=True)

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
                mock_run_ffmpeg.assert_called_once()

                # Check API calls
                assert (
                    mock_api.get_with_ngsw.call_count >= 3
                )  # At minimum we should call for master, playlist, and segments

                # Verify the segment response was handled correctly
                mock_segment_response1.iter_content.assert_called_once()
                mock_segment_response2.iter_content.assert_called_once()

    @patch("download.m3u8.run_ffmpeg")
    def test_m3u8_download_with_error_handling(
        self, mock_run_ffmpeg, mock_config, temp_dir
    ):
        """Test M3U8 download with error handling for missing segments."""
        # Setup config and mock responses
        config, mock_api, mock_response = mock_config

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

        # Set up segment playlist response
        mock_segment_playlist_response = MagicMock(spec=requests.Response)
        mock_segment_playlist_response.status_code = 200
        mock_segment_playlist_response.text = segment_playlist

        # First segment succeeds
        mock_segment_response1 = MagicMock(spec=requests.Response)
        mock_segment_response1.status_code = 200
        mock_segment_response1.iter_content.return_value = [segment_content]

        # Second segment fails
        mock_segment_response2 = MagicMock(spec=requests.Response)
        mock_segment_response2.status_code = 404
        mock_segment_response2.text = "Not Found"

        # Configure the API mock to return the sequence of responses
        mock_api.get_with_ngsw.return_value.__enter__.side_effect = [
            mock_segment_playlist_response,  # Segment playlist
            mock_segment_response1,  # First segment
            mock_segment_response2,  # Second segment (fails)
        ]

        # Create a patched version of exists that returns True for first segment but False for second
        original_exists = Path.exists

        def mocked_exists(path_obj):
            if path_obj.name == "segment1.ts":
                return True
            if path_obj.name == "segment2.ts":
                return False
            return original_exists(path_obj)

        # Setup for the test
        with patch("pathlib.Path.exists", side_effect=mocked_exists):
            # Create directory for test
            save_path = temp_dir / "video.ts"
            save_path.parent.mkdir(exist_ok=True)

            # Mock temp file operations for segments and ffmpeg list file
            with patch("builtins.open", create=True):
                # Run the download - should raise M3U8Error for missing segment
                with pytest.raises(M3U8Error) as excinfo:
                    download_m3u8(
                        config=config,
                        m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
                        save_path=save_path,
                    )

                assert "Stream segments failed to download" in str(excinfo.value)
                mock_run_ffmpeg.assert_not_called()  # FFMPEG should not be called

    @patch("download.m3u8.run_ffmpeg")
    def test_m3u8_download_with_ffmpeg_error(
        self, mock_run_ffmpeg, mock_config, temp_dir
    ):
        """Test M3U8 download with error handling for FFMPEG failure."""
        # Setup config and mock responses
        config, mock_api, mock_response = mock_config

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

        # Set up segment playlist response
        mock_segment_playlist_response = MagicMock(spec=requests.Response)
        mock_segment_playlist_response.status_code = 200
        mock_segment_playlist_response.text = segment_playlist

        # Segments succeed
        mock_segment_response1 = MagicMock(spec=requests.Response)
        mock_segment_response1.status_code = 200
        mock_segment_response1.iter_content.return_value = [segment_content]

        mock_segment_response2 = MagicMock(spec=requests.Response)
        mock_segment_response2.status_code = 200
        mock_segment_response2.iter_content.return_value = [segment_content]

        # Configure the API mock to return the sequence of responses
        mock_api.get_with_ngsw.return_value.__enter__.side_effect = [
            mock_segment_playlist_response,  # Segment playlist
            mock_segment_response1,  # First segment
            mock_segment_response2,  # Second segment
        ]

        # Set up ffmpeg to raise an error
        from subprocess import CalledProcessError

        mock_error = CalledProcessError(1, "ffmpeg", stderr=b"FFMPEG error")
        mock_run_ffmpeg.side_effect = mock_error

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
                mock_run_ffmpeg.assert_called_once()

    def test_m3u8_error_propagation(self, mock_config):
        """Test proper error propagation when API returns an error."""
        # Setup config and mock responses
        config, mock_api, mock_response = mock_config

        # Set up failed API response
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        # Call function and check error propagation
        with pytest.raises(M3U8Error) as excinfo:
            fetch_m3u8_segment_playlist(
                config=config,
                m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
            )

        assert "Failed downloading M3U8 playlist" in str(excinfo.value)
        assert "403" in str(excinfo.value)
        mock_api.get_with_ngsw.assert_called_once()

    @patch("download.m3u8.run_ffmpeg")
    def test_m3u8_with_timestamp_setting(self, mock_run_ffmpeg, mock_config, temp_dir):
        """Test M3U8 download with timestamp setting."""
        # Setup config and mock responses
        config, mock_api, mock_response = mock_config

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

        # Set up segment playlist response
        mock_segment_playlist_response = MagicMock(spec=requests.Response)
        mock_segment_playlist_response.status_code = 200
        mock_segment_playlist_response.text = segment_playlist

        # Segments succeed
        mock_segment_response1 = MagicMock(spec=requests.Response)
        mock_segment_response1.status_code = 200
        mock_segment_response1.iter_content.return_value = [segment_content]

        mock_segment_response2 = MagicMock(spec=requests.Response)
        mock_segment_response2.status_code = 200
        mock_segment_response2.iter_content.return_value = [segment_content]

        # Configure the API mock to return the sequence of responses
        mock_api.get_with_ngsw.return_value.__enter__.side_effect = [
            mock_segment_playlist_response,  # Segment playlist
            mock_segment_response1,  # First segment
            mock_segment_response2,  # Second segment
        ]

        # Created timestamp
        created_at = 1633046400  # October 1, 2021

        # Mock exists check for segments
        with patch("pathlib.Path.exists", return_value=True):
            # Create directory for test
            save_path = temp_dir / "video.ts"
            save_path.parent.mkdir(exist_ok=True)

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
                mock_run_ffmpeg.assert_called_once()

                # Check that timestamp was set
                mock_utime.assert_called_once_with(
                    save_path.parent / "video.mp4", (created_at, created_at)
                )
