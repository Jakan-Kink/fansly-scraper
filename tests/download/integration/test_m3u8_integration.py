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
from tests.fixtures.api.api_fixtures import dump_fansly_calls


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

        # Mock HTTP responses at edge using respx. Each URL is fetched once
        # per download path: master playlist once, 1080p variant playlist once,
        # each segment once. CORS preflight fires once per unique GET URL
        # (3 unique GETs: video.m3u8, video_1080.m3u8, segment1.ts, segment2.ts
        # = 4 preflights).
        # Verified call counts from dump_fansly_calls probe run:
        #   GET master playlist          — 2x (once for variant selection,
        #                                  once inside fetch_m3u8_segment_playlist
        #                                  when tier-3 starts the segment flow
        #                                  from the original URL)
        #   GET 1080p variant playlist   — 1x
        #   GET segment1.ts              — 1x
        #   GET segment2.ts              — 1x
        #   CORS OPTIONS preflight       — 5x (one per GET)
        #
        # `url__startswith` is used instead of exact URLs because `get_with_ngsw`
        # appends `?ngsw-bypass=true` to the URL before sending.
        options_route = respx.options(url__startswith="https://example.com/").mock(
            side_effect=[httpx.Response(200)] * 5
        )
        master_route = respx.get(url__startswith="https://example.com/video.m3u8").mock(
            side_effect=[httpx.Response(200, text=master_playlist)] * 2
        )
        variant_route = respx.get(
            url__startswith="https://example.com/video_1080.m3u8"
        ).mock(side_effect=[httpx.Response(200, text=segment_playlist)])
        seg1_route = respx.get(url__startswith="https://example.com/segment1.ts").mock(
            side_effect=[httpx.Response(200, content=segment_content)]
        )
        seg2_route = respx.get(url__startswith="https://example.com/segment2.ts").mock(
            side_effect=[httpx.Response(200, content=segment_content)]
        )

        # Real tmp_path-based directory (replaces PathMock + mock_open that were
        # here previously). download_m3u8 writes the muxed output file via the
        # mocked _mux_segments_with_pyav; segments are written to real disk.
        save_path = temp_dir / "video.ts"
        save_path.parent.mkdir(exist_ok=True)

        # Simulate the mux succeeding by having it write a placeholder output.
        # The 4 wrapper patches above (`_try_direct_download_pyav`,
        # `_try_direct_download_ffmpeg`, `_mux_segments_with_pyav`,
        # `_mux_segments_with_ffmpeg`) are legitimate for this integration test
        # because each wrapper has dedicated unit-test coverage elsewhere:
        #   - `_try_direct_download_ffmpeg` → TestDirectDownloadFFmpeg
        #     (tests/download/unit/test_m3u8.py:412)
        #   - `_try_direct_download_pyav` → TestDirectDownloadPyAV
        #     (tests/download/unit/test_m3u8.py:528)
        #   - `_mux_segments_with_pyav` / `_mux_segments_with_ffmpeg`
        #     → TestSegmentDownload (tests/download/unit/test_m3u8.py:623)
        # This test verifies the orchestration (tier1→tier2→tier3 cascade with
        # tier3's mux path succeeding); the wrappers' internal behavior is
        # covered by the above dedicated unit tests.
        def _fake_mux_write(segments, output_path, *args, **kwargs):
            Path(output_path).write_bytes(b"MUXED_MP4_OUTPUT")
            return True

        mock_pyav_mux.side_effect = _fake_mux_write

        try:
            result = download_m3u8(
                config=config,
                m3u8_url="https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def",
                save_path=save_path,
            )
        finally:
            dump_fansly_calls(
                master_route.calls
                + variant_route.calls
                + seg1_route.calls
                + seg2_route.calls,
                "test_full_m3u8_download_workflow",
            )

        # Functional assertions
        assert result == save_path.parent / "video.mp4"
        assert result.exists(), "Muxed output file should exist on disk"
        assert result.read_bytes() == b"MUXED_MP4_OUTPUT"

        # Exact call counts for each route
        assert len(master_route.calls) == 2, (
            f"Master playlist fetched twice (variant selection + tier-3 "
            f"segment flow re-fetch), got {len(master_route.calls)}"
        )
        assert len(variant_route.calls) == 1, (
            f"1080p variant playlist should be fetched once, "
            f"got {len(variant_route.calls)}"
        )
        assert len(seg1_route.calls) == 1
        assert len(seg2_route.calls) == 1
        # 5 GETs total (2 master + variant + 2 segments) → 5 CORS preflights
        assert len(options_route.calls) == 5, (
            f"CORS preflight should fire once per GET (5 expected), "
            f"got {len(options_route.calls)}"
        )

        # Verify the fallback cascade ran as designed
        mock_pyav_direct.assert_called_once()
        mock_ffmpeg_direct.assert_called_once()
        mock_pyav_mux.assert_called_once()
        mock_ffmpeg_mux.assert_not_called()  # PyAV mux succeeded

        # Verify segment content written to disk (segments are fetched then
        # passed to mux, which wrote the fake MUXED output above).
        for seg_route in (seg1_route, seg2_route):
            assert seg_route.calls[0].response.content == segment_content

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
            # CORS preflight fires once per unique GET URL; pad for multiple calls.
            side_effect=[httpx.Response(200)] * 10
        )
        respx.get("https://example.com/video.m3u8").mock(
            # Padded — 3-tier download strategy may call multiple times.
            side_effect=[httpx.Response(200, text=segment_playlist)] * 5
        )
        respx.get("https://example.com/segment1.ts").mock(
            # Padded — 3-tier download strategy may call multiple times.
            side_effect=[httpx.Response(200, content=segment_content)] * 5
        )
        respx.get("https://example.com/segment2.ts").mock(
            side_effect=[httpx.Response(404, text="Not Found")]
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
            # CORS preflight fires once per unique GET URL; pad for multiple calls.
            side_effect=[httpx.Response(200)] * 10
        )
        respx.get("https://example.com/video.m3u8").mock(
            # Padded — 3-tier download strategy may call multiple times.
            side_effect=[httpx.Response(200, text=segment_playlist)] * 5
        )
        respx.get("https://example.com/segment1.ts").mock(
            # Padded — 3-tier download strategy may call multiple times.
            side_effect=[httpx.Response(200, content=segment_content)] * 5
        )
        respx.get("https://example.com/segment2.ts").mock(
            # Padded — 3-tier download strategy may call multiple times.
            side_effect=[httpx.Response(200, content=segment_content)] * 5
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
            # CORS preflight fires once per unique GET URL; pad for multiple calls.
            side_effect=[httpx.Response(200)] * 10
        )
        respx.get("https://example.com/video.m3u8").mock(
            side_effect=[httpx.Response(403, text="Forbidden")]
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
            # CORS preflight fires once per unique GET URL; pad for multiple calls.
            side_effect=[httpx.Response(200)] * 10
        )
        respx.get("https://example.com/video.m3u8").mock(
            # Padded — 3-tier download strategy may call multiple times.
            side_effect=[httpx.Response(200, text=segment_playlist)] * 5
        )
        respx.get("https://example.com/segment1.ts").mock(
            # Padded — 3-tier download strategy may call multiple times.
            side_effect=[httpx.Response(200, content=segment_content)] * 5
        )
        respx.get("https://example.com/segment2.ts").mock(
            # Padded — 3-tier download strategy may call multiple times.
            side_effect=[httpx.Response(200, content=segment_content)] * 5
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
