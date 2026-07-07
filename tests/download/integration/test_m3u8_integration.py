"""Integration tests for the m3u8 module — real segment download + real mux.

These tests drive ``download_m3u8`` through its tier-3 segment-download path
end-to-end with NO behavior-replacing mocks:

- **Segments are real MPEG-TS bytes** (``make_synthetic_ivs_segment`` —
  genuine H.264-High + AAC, the shape of a real IVS/CloudFront segment),
  served via ``respx`` so they land on **real disk** under ``tmp_path``.
- **Muxing is real PyAV** (``_mux_segments_with_pyav`` runs unmocked); the
  happy-path test reopens the output with ``av.open`` and asserts a real MP4
  carrying video + audio — mirroring
  ``tests/download/integration/test_livestream_recorder.py``.
- **Missing-segment + both-mux-fail branches use the real filesystem**: a
  respx 404 leaves that ``.ts`` genuinely absent (real ``Path.exists`` drives
  the error path); deliberately-unmuxable bytes make both real mux tiers fail.

The only patches retained are **cascade-steering spies** on the tier-1/tier-2
direct-download wrappers (``_try_direct_download_pyav`` /
``_try_direct_download_ffmpeg`` → ``False``) so the cascade reaches tier 3.
They replace no observable shape — each has its own real-leaf unit suite in
``tests/download/unit/test_m3u8.py`` (``TestDirectDownloadPyAV`` /
``TestDirectDownloadFFmpeg``). They are NOT fake muxers.

Fixtures used:
- ``m3u8_mock_config`` (tests.fixtures.download.m3u8_fixtures) — MagicMock
  wrapping a real FanslyApi for respx HTTP interception at the edge.
- ``tmp_path`` (pytest built-in) — per-test temporary directory.
"""

from unittest.mock import patch

import av
import httpx
import pytest
import respx

from download.m3u8 import download_m3u8, fetch_m3u8_segment_playlist
from errors import M3U8Error
from tests.fixtures.api import (
    build_variant_playlist,
    dump_fansly_calls,
    make_synthetic_ivs_segment,
)


# VOD endlist playlist served directly at video.m3u8 (no variant re-fetch):
# fetch_m3u8_segment_playlist returns it as-is when is_endlist + type == vod.
_SEGMENT_PLAYLIST = (
    build_variant_playlist(
        media_sequence=0,
        segment_uris=["segment1.ts", "segment2.ts"],
        endlist=True,
    )
    + "#EXT-X-PLAYLIST-TYPE:VOD\n"
)

_M3U8_URL = "https://example.com/video.m3u8?Policy=abc&Key-Pair-Id=xyz&Signature=def"


class TestM3U8Integration:
    """Integration tests for the m3u8 module (tier-3 real-pipeline)."""

    @respx.mock
    @patch("download.m3u8._try_direct_download_ffmpeg")
    @patch("download.m3u8._try_direct_download_pyav")
    def test_full_segment_download_real_pyav_mux_workflow(
        self,
        mock_pyav_direct,
        mock_ffmpeg_direct,
        m3u8_mock_config,
        tmp_path,
    ):
        """End-to-end tier-3: real .ts segments → real PyAV mux → real MP4.

        Drives the full segment-download path with NO fake muxer. Two genuine
        H.264-High + AAC MPEG-TS segments are served via respx onto real disk;
        the real ``_try_segment_download`` downloads them and the real
        ``_mux_segments_with_pyav`` produces a real MP4 which we reopen with
        ``av.open`` (mirroring ``test_livestream_recorder.py``). Also asserts
        ``created_at`` is applied to the real output via ``os.utime`` — folding
        in the former ``test_m3u8_with_timestamp_setting`` (which previously
        patched ``Path.exists``/``Path.stat``/``open``/``os.utime`` wholesale).

        Cascade-steering spies force tiers 1+2 to fail so the cascade reaches
        tier 3; they replace no shape (see module docstring).
        """
        config = m3u8_mock_config
        mock_pyav_direct.return_value = False
        mock_ffmpeg_direct.return_value = False

        created_at = 1633046400  # 2021-10-01 — set as mtime on the real MP4.
        segment1 = make_synthetic_ivs_segment(seed=1)
        segment2 = make_synthetic_ivs_segment(seed=2)

        # `url__startswith` for CDN — get_with_ngsw appends ?ngsw-bypass=true.
        options_route = respx.options(url__startswith="https://example.com/").mock(
            side_effect=[httpx.Response(200)] * 8
        )
        playlist_route = respx.get(
            url__startswith="https://example.com/video.m3u8"
        ).mock(side_effect=[httpx.Response(200, text=_SEGMENT_PLAYLIST)])
        seg1_route = respx.get(url__startswith="https://example.com/segment1.ts").mock(
            side_effect=[httpx.Response(200, content=segment1)]
        )
        seg2_route = respx.get(url__startswith="https://example.com/segment2.ts").mock(
            side_effect=[httpx.Response(200, content=segment2)]
        )

        save_path = tmp_path / "video.ts"

        try:
            result = download_m3u8(
                config=config,
                m3u8_url=_M3U8_URL,
                save_path=save_path,
                created_at=created_at,
            )
        finally:
            dump_fansly_calls(
                playlist_route.calls + seg1_route.calls + seg2_route.calls,
                "test_full_segment_download_real_pyav_mux_workflow",
            )

        # Output is the real MP4 produced by real PyAV muxing.
        assert result == save_path.parent / "video.mp4"
        assert result.exists(), "Muxed output MP4 should exist on real disk"
        assert result.stat().st_size > 0

        # Reopen with real PyAV: must carry video + audio and preserve the
        # source's H.264 High profile (the add_stream_from_template extradata
        # copy — a dropped copy would yield Constrained Baseline / fail open).
        muxed = av.open(str(result))
        try:
            assert muxed.streams.video, "muxed MP4 has no video stream"
            assert muxed.streams.audio, "muxed MP4 has no audio stream"
            assert muxed.streams.video[0].profile == "High", (
                f"High profile not preserved: {muxed.streams.video[0].profile}"
            )
            # The muxed video decodes to real frames. ``_mux_segments_with_pyav``
            # does naive per-segment packet copy without DTS rebasing (unlike the
            # livestream ``_mux_ivs_segments`` PID-router), so a reopened demux
            # yields the first segment's GOP (~30 frames) — proof the real mux
            # produced a decodable stream, not an empty/corrupt container.
            video_frames = sum(
                1 for pkt in muxed.demux(muxed.streams.video[0]) if pkt.size
            )
            assert video_frames >= 30, (
                f"expected a decodable muxed stream, got {video_frames} frames"
            )
        finally:
            muxed.close()

        # created_at applied to the real file via os.utime (real mtime write).
        assert int(result.stat().st_mtime) == created_at

        # Exact call counts — playlist once (VOD endlist, no re-fetch),
        # each segment once.
        assert playlist_route.call_count == 1
        assert seg1_route.call_count == 1
        assert seg2_route.call_count == 1
        # m3u8 calls pass add_fansly_headers=False — CORS preflight is gated
        # off at api/fansly.py for CORS-simple requests.
        assert options_route.call_count == 0

        # The cascade ran tier1 → tier2 → tier3 as designed.
        mock_pyav_direct.assert_called_once()
        mock_ffmpeg_direct.assert_called_once()

    @respx.mock
    @patch("download.m3u8._try_direct_download_ffmpeg")
    @patch("download.m3u8._try_direct_download_pyav")
    def test_missing_segment_raises_via_real_filesystem(
        self,
        mock_pyav_direct,
        mock_ffmpeg_direct,
        m3u8_mock_config,
        tmp_path,
    ):
        """A respx 404 leaves that ``.ts`` absent → real ``Path.exists`` fires.

        No ``Path.exists``/``open`` patching: segment2 is served 404 so it is
        genuinely never written to disk, and production's real missing-segment
        check (``[f for f in segment_files if not f.exists()]``) raises.
        """
        config = m3u8_mock_config
        mock_pyav_direct.return_value = False
        mock_ffmpeg_direct.return_value = False

        segment1 = make_synthetic_ivs_segment(seed=1)

        respx.options(url__startswith="https://example.com/").mock(
            side_effect=[httpx.Response(200)] * 8
        )
        playlist_route = respx.get(
            url__startswith="https://example.com/video.m3u8"
        ).mock(side_effect=[httpx.Response(200, text=_SEGMENT_PLAYLIST)])
        seg1_route = respx.get(url__startswith="https://example.com/segment1.ts").mock(
            side_effect=[httpx.Response(200, content=segment1)]
        )
        seg2_route = respx.get(url__startswith="https://example.com/segment2.ts").mock(
            side_effect=[httpx.Response(404, text="Not Found")]
        )

        save_path = tmp_path / "video.ts"

        try:
            with pytest.raises(M3U8Error) as excinfo:
                download_m3u8(
                    config=config,
                    m3u8_url=_M3U8_URL,
                    save_path=save_path,
                )
        finally:
            dump_fansly_calls(
                playlist_route.calls + seg1_route.calls + seg2_route.calls,
                "test_missing_segment_raises_via_real_filesystem",
            )

        # M3U8Error propagates untouched (download_m3u8 re-raises M3U8Error).
        assert "Stream segments failed to download" in str(excinfo.value)
        assert "segment2.ts" in str(excinfo.value)
        mock_pyav_direct.assert_called_once()
        mock_ffmpeg_direct.assert_called_once()

    @respx.mock
    @patch("download.m3u8._try_direct_download_ffmpeg")
    @patch("download.m3u8._try_direct_download_pyav")
    def test_both_mux_tiers_fail_on_unmuxable_segments(
        self,
        mock_pyav_direct,
        mock_ffmpeg_direct,
        m3u8_mock_config,
        tmp_path,
    ):
        """Deliberately-unmuxable bytes → real PyAV AND real ffmpeg mux fail.

        No muxer patching: segments are non-TS garbage that libav cannot open,
        so real ``_mux_segments_with_pyav`` skips 100% of segments (> 25% abort
        threshold → False) and real ``_mux_segments_with_ffmpeg`` concat also
        fails. Production then raises the both-mux-failed M3U8Error.
        """
        config = m3u8_mock_config
        mock_pyav_direct.return_value = False
        mock_ffmpeg_direct.return_value = False

        garbage = b"NOT A REAL MPEG-TS SEGMENT" * 64

        respx.options(url__startswith="https://example.com/").mock(
            side_effect=[httpx.Response(200)] * 8
        )
        playlist_route = respx.get(
            url__startswith="https://example.com/video.m3u8"
        ).mock(side_effect=[httpx.Response(200, text=_SEGMENT_PLAYLIST)])
        seg1_route = respx.get(url__startswith="https://example.com/segment1.ts").mock(
            side_effect=[httpx.Response(200, content=garbage)]
        )
        seg2_route = respx.get(url__startswith="https://example.com/segment2.ts").mock(
            side_effect=[httpx.Response(200, content=garbage)]
        )

        save_path = tmp_path / "video.ts"

        try:
            with pytest.raises(M3U8Error) as excinfo:
                download_m3u8(
                    config=config,
                    m3u8_url=_M3U8_URL,
                    save_path=save_path,
                )
        finally:
            dump_fansly_calls(
                playlist_route.calls + seg1_route.calls + seg2_route.calls,
                "test_both_mux_tiers_fail_on_unmuxable_segments",
            )

        assert "Both PyAV and FFmpeg muxing failed" in str(excinfo.value)
        mock_pyav_direct.assert_called_once()
        mock_ffmpeg_direct.assert_called_once()

    @respx.mock
    def test_m3u8_error_propagation(self, m3u8_mock_config):
        """Proper error propagation when the playlist fetch returns non-200."""
        config = m3u8_mock_config

        respx.options(url__startswith="https://example.com/").mock(
            side_effect=[httpx.Response(200)] * 4
        )
        get_route = respx.get(url__startswith="https://example.com/video.m3u8").mock(
            side_effect=[httpx.Response(403, text="Forbidden")]
        )

        try:
            with pytest.raises(M3U8Error) as excinfo:
                fetch_m3u8_segment_playlist(
                    config=config,
                    m3u8_url=_M3U8_URL,
                )
        finally:
            dump_fansly_calls(get_route.calls, "test_m3u8_error_propagation")

        assert "Failed downloading M3U8 playlist" in str(excinfo.value)
        assert "403" in str(excinfo.value)
