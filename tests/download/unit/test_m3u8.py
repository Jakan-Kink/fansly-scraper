"""Unit tests for the m3u8 module.

Post-Wave-2.3 rewrite: previously every test used
``MagicMock(spec=FanslyConfig)`` + ``@patch("pathlib.Path.exists/stat/unlink")``
+ ``@patch("download.m3u8.ffmpeg")`` / ``@patch("download.m3u8.av")``, which
stubbed the entire external libraries and replaced every filesystem check.
Tests exercised mocks, not production code — download/m3u8.py sat at 60.88%
line coverage with the majority of ``_mux_segments_with_pyav`` (lines 418-516),
``_mux_segments_with_ffmpeg`` (lines 532-555), and ``_try_segment_download``'s
real segment-download inner function (lines 593-618) never exercised.

Replacement strategy:
- **Real ``tmp_path``** — stub files are written by fakes rather than
  ``@patch("pathlib.Path.exists/stat/unlink")`` stubs. The production
  ``exists()``/``stat()`` calls see real filesystem state.
- **Leaf-level library patches** — ``ffmpeg.input``/``ffmpeg.probe``/
  ``ffmpeg.Error`` and ``av.open``/``av.error.FFmpegError`` are the true
  external edges. Stubs replace those calls while every wrapper
  function (``_try_direct_download_ffmpeg``, ``_mux_segments_with_pyav``
  etc.) runs its real logic.
- **Real ``FanslyConfig``** — no ``MagicMock(spec=FanslyConfig)``.
- **Stateful fakes for muxer paths** — tests for ``_mux_segments_with_pyav``
  use small classes that track per-segment state (packet demux, codec
  context etc.) so the corrupt-packet-skip, per-segment error, and
  >25% abort-threshold branches all execute real code.

Orchestration tests for ``download_m3u8`` (the three-tier dispatcher)
still mock the three wrappers — that's the documented orchestration seam.
Each mocked wrapper has dedicated leaf-level tests below:
- ``TestDirectDownloadPyAV`` for ``_try_direct_download_pyav``
- ``TestDirectDownloadFFmpeg`` for ``_try_direct_download_ffmpeg``
- ``TestSegmentDownload`` for ``_try_segment_download``
- ``TestMuxSegmentsWithPyAV`` (NEW) for ``_mux_segments_with_pyav``
- ``TestMuxSegmentsWithFFmpeg`` (NEW) for ``_mux_segments_with_ffmpeg``

The integration test suite at
``tests/download/integration/test_m3u8_integration.py`` also patches the
three wrappers and its justification is the in-file comment plus this
dedicated-unit-coverage suite.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import av
import ffmpeg
import httpx
import pytest
import respx
from m3u8 import M3U8

from config.fanslyconfig import FanslyConfig
from download.m3u8 import (
    _mux_segments_with_ffmpeg,
    _mux_segments_with_pyav,
    _try_direct_download_ffmpeg,
    _try_direct_download_pyav,
    _try_segment_download,
    download_m3u8,
    fetch_m3u8_segment_playlist,
    get_m3u8_cookies,
    get_m3u8_progress,
)
from errors import M3U8Error
from tests.fixtures.api import dump_fansly_calls
from tests.fixtures.utils import SyncExecutor


# ---------------------------------------------------------------------------
# Shared helpers + fake classes
# ---------------------------------------------------------------------------


def _make_real_config() -> FanslyConfig:
    """Return a fresh real ``FanslyConfig`` with no API attached.

    Tests that need ``config.get_api().get_with_ngsw(...)`` attach a small
    namespace-object API via ``config._api = ...``.
    """
    return FanslyConfig(program_version="0.13.0-test")


class _FakeFFmpegStream:
    """Stub for ``ffmpeg.input(...).output(...).overwrite_output()``.

    Default behavior: ``run()`` creates a real file at ``output_path`` so the
    production code's ``output_path.exists()`` and ``output_path.stat().st_size > 0``
    checks exercise REAL filesystem behavior.

    Variants (via ctor kwargs):
      - ``output_path=None``: skip file creation (empty-file branch)
      - ``raises=ffmpeg.Error(...)``: raise on ``run()`` (error branch)
    """

    def __init__(
        self,
        output_path: Path | None = None,
        *,
        write_bytes: bytes = b"\x00" * 1024,
        raises: BaseException | None = None,
    ):
        self._output_path = output_path
        self._write_bytes = write_bytes
        self._raises = raises

    def get_args(self) -> list[str]:
        path = str(self._output_path) if self._output_path is not None else "output.mp4"
        return ["ffmpeg", "-i", "hls", path]

    def global_args(self, *_a, **_k) -> _FakeFFmpegStream:
        return self

    def run(self, *_a, **_k) -> None:
        if self._raises is not None:
            raise self._raises
        if self._output_path is not None:
            self._output_path.write_bytes(self._write_bytes)


def _install_fake_ffmpeg_input(monkeypatch, fake_stream):
    """Wire ``ffmpeg.input(...).output(...).overwrite_output()`` → fake_stream.

    Used by tests that need only the success/failure side-effect of ``run``;
    the intermediate fluent-API objects are also fakes.
    """

    def _fake_input(*_a, **_k):
        class _Input:
            def output(self, *_aa, **_kk):
                class _Output:
                    def overwrite_output(self_inner):  # noqa: N805
                        return fake_stream

                return _Output()

        return _Input()

    monkeypatch.setattr(ffmpeg, "input", _fake_input)


# ---------------------------------------------------------------------------
# TestM3U8Cookies — get_m3u8_cookies (pure function, no external deps)
# ---------------------------------------------------------------------------


class TestM3U8Cookies:
    """CloudFront cookie extraction from M3U8 URL query strings."""

    def test_all_values_present(self):
        url = (
            "https://media.example.com/hls/video.m3u8"
            "?Policy=abc123&Key-Pair-Id=xyz789&Signature=def456"
        )
        assert get_m3u8_cookies(url) == {
            "CloudFront-Key-Pair-Id": "xyz789",
            "CloudFront-Policy": "abc123",
            "CloudFront-Signature": "def456",
        }

    def test_missing_values_become_none(self):
        url = "https://media.example.com/hls/video.m3u8?Policy=abc123"
        assert get_m3u8_cookies(url) == {
            "CloudFront-Key-Pair-Id": None,
            "CloudFront-Policy": "abc123",
            "CloudFront-Signature": None,
        }

    def test_no_query_string_returns_all_none(self):
        url = "https://media.example.com/hls/video.m3u8"
        assert get_m3u8_cookies(url) == {
            "CloudFront-Key-Pair-Id": None,
            "CloudFront-Policy": None,
            "CloudFront-Signature": None,
        }


# ---------------------------------------------------------------------------
# TestM3U8Progress — get_m3u8_progress (Rich wrapper, no external deps)
# ---------------------------------------------------------------------------


class TestM3U8Progress:
    def test_enabled(self):
        progress = get_m3u8_progress(disable_loading_bar=False)
        assert progress.disable is False
        assert progress.expand is True

    def test_disabled(self):
        assert get_m3u8_progress(disable_loading_bar=True).disable is True


# ---------------------------------------------------------------------------
# TestFetchM3U8SegmentPlaylist — fetch_m3u8_segment_playlist via fake API
# ---------------------------------------------------------------------------


class TestFetchM3U8SegmentPlaylist:
    """Tests for ``fetch_m3u8_segment_playlist`` — real parser + fake API."""

    def test_endlist_vod_returned_directly(self, fansly_api_with_respx):
        """VOD endlist playlist → returned directly without recursion."""
        config = _make_real_config()
        config._api = fansly_api_with_respx

        playlist_text = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment1.ts
#EXTINF:8.0,
segment2.ts
#EXT-X-ENDLIST"""

        with respx.mock:
            respx.options(url__startswith="https://example.com/video.m3u8").mock(
                side_effect=[httpx.Response(200)]
            )
            get_route = respx.get(
                url__startswith="https://example.com/video.m3u8"
            ).mock(side_effect=[httpx.Response(200, text=playlist_text)])

            try:
                result = fetch_m3u8_segment_playlist(
                    config=config,
                    m3u8_url="https://example.com/video.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
                )
            finally:
                dump_fansly_calls(get_route.calls)

        assert isinstance(result, M3U8)
        assert result.is_endlist is True
        assert result.playlist_type == "vod"
        assert len(result.segments) == 2
        # Single HTTP call — no recursion.
        assert get_route.call_count == 1

    def test_master_playlist_selects_highest_resolution(self, fansly_api_with_respx):
        """Master playlist → recursive fetch of highest-resolution variant.

        The real code calls _get_highest_quality_variant_url which fetches
        the master playlist AGAIN (internally) and then recurses with the
        variant URL. Three total HTTP calls.
        """
        config = _make_real_config()
        config._api = fansly_api_with_respx

        master = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=640x360
video_360.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080
video_1080.m3u8"""

        segment_list = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-PLAYLIST-TYPE:VOD
#EXTINF:10.0,
segment1.ts
#EXT-X-ENDLIST"""

        with respx.mock:
            # CORS preflight (one per unique URL)
            respx.options(url__startswith="https://example.com/").mock(
                side_effect=[httpx.Response(200)] * 3
            )
            # Variant URL fetched once (segment list); declared first so it
            # matches before the broader "video.m3u8" prefix below.
            variant_route = respx.get(
                url__startswith="https://example.com/video_1080.m3u8"
            ).mock(side_effect=[httpx.Response(200, text=segment_list)])
            # Master URL fetched twice (initial + variant-selection re-fetch).
            master_route = respx.get(
                url__startswith="https://example.com/video.m3u8"
            ).mock(side_effect=[httpx.Response(200, text=master)] * 2)

            try:
                result = fetch_m3u8_segment_playlist(
                    config=config,
                    m3u8_url="https://example.com/video.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
                )
            finally:
                dump_fansly_calls(master_route.calls)
                dump_fansly_calls(variant_route.calls)

        assert isinstance(result, M3U8)
        assert result.playlist_type == "vod"
        # Master fetched twice, variant fetched once.
        assert master_route.call_count == 2
        assert variant_route.call_count == 1
        # Variant call targets the 1080p variant URL.
        assert "video_1080.m3u8" in str(variant_route.calls[0].request.url)

    def test_master_playlist_fallback_guesses_1080p_url(self, fansly_api_with_respx):
        """Master playlist with no variants → guesses ``_1080.m3u8`` fallback URL."""
        config = _make_real_config()
        config._api = fansly_api_with_respx

        empty_master = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=0,RESOLUTION=0x0"""

        segment_list = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-PLAYLIST-TYPE:VOD
#EXTINF:10.0,
segment1.ts
#EXT-X-ENDLIST"""

        with respx.mock:
            respx.options(url__startswith="https://example.com/").mock(
                side_effect=[httpx.Response(200)] * 3
            )
            # Guessed _1080 variant URL declared first (narrower prefix).
            variant_route = respx.get(
                url__startswith="https://example.com/video_1080.m3u8"
            ).mock(side_effect=[httpx.Response(200, text=segment_list)])
            master_route = respx.get(
                url__startswith="https://example.com/video.m3u8"
            ).mock(side_effect=[httpx.Response(200, text=empty_master)] * 2)

            try:
                fetch_m3u8_segment_playlist(
                    config=config,
                    m3u8_url="https://example.com/video.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
                )
            finally:
                dump_fansly_calls(master_route.calls)
                dump_fansly_calls(variant_route.calls)

        # Real code builds the guessed URL by splitting on ".m3u8" and
        # appending "_1080.m3u8" — verify the variant route was hit.
        assert variant_route.call_count == 1
        assert "_1080.m3u8" in str(variant_route.calls[0].request.url)

    def test_http_error_raises_m3u8error(self, fansly_api_with_respx):
        """Non-200 response → real ``raise M3U8Error`` fires."""
        config = _make_real_config()
        config._api = fansly_api_with_respx

        with respx.mock:
            respx.options(url__startswith="https://example.com/v.m3u8").mock(
                side_effect=[httpx.Response(200)]
            )
            get_route = respx.get(url__startswith="https://example.com/v.m3u8").mock(
                side_effect=[httpx.Response(404, text="Not Found")]
            )

            try:
                with pytest.raises(M3U8Error) as excinfo:
                    fetch_m3u8_segment_playlist(
                        config=config,
                        m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
                    )
            finally:
                dump_fansly_calls(get_route.calls)

        assert "Failed downloading M3U8 playlist" in str(excinfo.value)
        assert "404" in str(excinfo.value)


# ---------------------------------------------------------------------------
# TestDownloadM3U8ThreeTierStrategy — orchestration tests only
# ---------------------------------------------------------------------------
#
# The three wrapper functions patched here each have dedicated unit-level
# coverage below (TestDirectDownloadPyAV, TestDirectDownloadFFmpeg,
# TestSegmentDownload, TestMuxSegmentsWithPyAV, TestMuxSegmentsWithFFmpeg).
# Those dedicated suites exercise the real av/ffmpeg code paths via leaf-
# level library patches; this suite focuses strictly on download_m3u8's
# orchestration logic — which tier it tries, when it falls back, and how
# the created_at parameter flows through.


@patch("download.m3u8._try_segment_download")
@patch("download.m3u8._try_direct_download_ffmpeg")
@patch("download.m3u8._try_direct_download_pyav")
class TestDownloadM3U8ThreeTierStrategy:
    """Orchestration tests for ``download_m3u8`` (see module comment)."""

    def test_pyav_success_short_circuits(
        self, mock_pyav, mock_ffmpeg, mock_segment, tmp_path
    ):
        """PyAV succeeds → FFmpeg + segment paths are not tried."""
        config = _make_real_config()
        save_path = tmp_path / "video.mp4"
        mock_pyav.return_value = True

        result = download_m3u8(
            config=config,
            m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
            save_path=save_path,
        )

        assert result == save_path.parent / "video.mp4"
        mock_pyav.assert_called_once()
        mock_ffmpeg.assert_not_called()
        mock_segment.assert_not_called()

    def test_ffmpeg_fallback_when_pyav_fails(
        self, mock_pyav, mock_ffmpeg, mock_segment, tmp_path
    ):
        """PyAV fails → FFmpeg tried → succeeds → segment path skipped."""
        config = _make_real_config()
        save_path = tmp_path / "video.mp4"
        mock_pyav.return_value = False
        mock_ffmpeg.return_value = True

        result = download_m3u8(
            config=config,
            m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
            save_path=save_path,
        )

        assert result == save_path.parent / "video.mp4"
        mock_pyav.assert_called_once()
        mock_ffmpeg.assert_called_once()
        mock_segment.assert_not_called()

    def test_segment_fallback_when_both_direct_fail(
        self, mock_pyav, mock_ffmpeg, mock_segment, tmp_path
    ):
        """Both PyAV + FFmpeg fail → segment path is tried."""
        config = _make_real_config()
        save_path = tmp_path / "video.mp4"
        mock_pyav.return_value = False
        mock_ffmpeg.return_value = False
        mock_segment.return_value = save_path.parent / "video.mp4"

        result = download_m3u8(
            config=config,
            m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
            save_path=save_path,
        )

        assert result == save_path.parent / "video.mp4"
        mock_segment.assert_called_once()

    def test_created_at_set_on_pyav_success(
        self, mock_pyav, mock_ffmpeg, mock_segment, tmp_path, monkeypatch
    ):
        """created_at is applied via os.utime on the resulting path."""
        config = _make_real_config()
        save_path = tmp_path / "video.mp4"
        mock_pyav.return_value = True

        utime_calls: list[tuple] = []
        monkeypatch.setattr(
            "download.m3u8.os.utime", lambda p, t: utime_calls.append((p, t))
        )

        download_m3u8(
            config=config,
            m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
            save_path=save_path,
            created_at=1633046400,
        )

        assert utime_calls == [
            (save_path.parent / "video.mp4", (1633046400, 1633046400))
        ]

    def test_created_at_set_on_ffmpeg_fallback(
        self, mock_pyav, mock_ffmpeg, mock_segment, tmp_path, monkeypatch
    ):
        """FFmpeg fallback succeeds → created_at applied on that path too.

        Covers line 727 (os.utime in the FFmpeg-success branch of download_m3u8).
        """
        config = _make_real_config()
        save_path = tmp_path / "video.mp4"
        mock_pyav.return_value = False
        mock_ffmpeg.return_value = True

        utime_calls: list[tuple] = []
        monkeypatch.setattr(
            "download.m3u8.os.utime", lambda p, t: utime_calls.append((p, t))
        )

        download_m3u8(
            config=config,
            m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
            save_path=save_path,
            created_at=1700000000,
        )

        assert utime_calls == [
            (save_path.parent / "video.mp4", (1700000000, 1700000000))
        ]

    def test_created_at_passed_through_to_segment_fallback(
        self, mock_pyav, mock_ffmpeg, mock_segment, tmp_path, monkeypatch
    ):
        """Segment-download tier success → created_at applied on result path."""
        config = _make_real_config()
        save_path = tmp_path / "video.mp4"
        mock_pyav.return_value = False
        mock_ffmpeg.return_value = False
        mock_segment.return_value = save_path.parent / "video.mp4"

        utime_calls: list[tuple] = []
        monkeypatch.setattr(
            "download.m3u8.os.utime", lambda p, t: utime_calls.append((p, t))
        )

        download_m3u8(
            config=config,
            m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
            save_path=save_path,
            created_at=1633046400,
        )

        assert utime_calls == [
            (save_path.parent / "video.mp4", (1633046400, 1633046400))
        ]

    def test_non_m3u8error_exception_is_wrapped(
        self, mock_pyav, mock_ffmpeg, mock_segment, tmp_path
    ):
        """Generic exception from any tier → wrapped in M3U8Error."""
        config = _make_real_config()
        save_path = tmp_path / "video.mp4"
        mock_pyav.side_effect = RuntimeError("unexpected crash")

        with pytest.raises(M3U8Error, match="Failed to download HLS video"):
            download_m3u8(
                config=config,
                m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
                save_path=save_path,
            )

    def test_m3u8error_reraised_untouched(
        self, mock_pyav, mock_ffmpeg, mock_segment, tmp_path
    ):
        """M3U8Error from a tier propagates without being re-wrapped."""
        config = _make_real_config()
        save_path = tmp_path / "video.mp4"
        mock_pyav.side_effect = M3U8Error("specific segment failure")

        with pytest.raises(M3U8Error, match="specific segment failure"):
            download_m3u8(
                config=config,
                m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
                save_path=save_path,
            )


# ---------------------------------------------------------------------------
# TestDirectDownloadFFmpeg — leaf-level tests for _try_direct_download_ffmpeg
# ---------------------------------------------------------------------------


class TestDirectDownloadFFmpeg:
    """Real-code tests for ``_try_direct_download_ffmpeg``.

    Leaf-patches: ``ffmpeg.input`` (returns fake stream chain),
    ``ffmpeg.probe`` (returns plausible probe dict), and
    ``download.m3u8._get_highest_quality_variant_url`` (skips the HTTP
    master-playlist fetch — tested separately in
    ``TestFetchM3U8SegmentPlaylist``). Real ``tmp_path`` files mean the
    production ``exists()``/``stat()`` checks run against the filesystem.
    """

    def test_success_with_valid_streams(self, tmp_path, monkeypatch):
        """Full success path: probe OK → ffmpeg run → verify streams → return True."""
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"
        cookies = {"CloudFront-Policy": "abc"}

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/video_1080.m3u8",
        )

        _install_fake_ffmpeg_input(monkeypatch, _FakeFFmpegStream(output_path))

        probe_results = [
            {"format": {"duration": "120.5"}},
            {
                "format": {"duration": "120.5"},
                "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
            },
        ]
        monkeypatch.setattr(ffmpeg, "probe", lambda *_a, **_k: probe_results.pop(0))

        result = _try_direct_download_ffmpeg(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies=cookies,
        )

        assert result is True
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    def test_probe_ffmpeg_error_falls_through_with_zero_duration(
        self, tmp_path, monkeypatch
    ):
        """Duration probe raises ffmpeg.Error → caught, total_duration stays 0.

        Covers lines 334-342 (the ``except ffmpeg.Error`` around the
        initial duration probe).
        """
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/video_1080.m3u8",
        )
        _install_fake_ffmpeg_input(monkeypatch, _FakeFFmpegStream(output_path))

        probe_calls = {"n": 0}

        def _probe(*_a, **_k):
            probe_calls["n"] += 1
            if probe_calls["n"] == 1:
                # Duration probe — raise ffmpeg.Error with a stderr buffer
                # so the lines that decode it execute.
                raise ffmpeg.Error("ffmpeg", b"", b"probe failure")
            # Stream verification probe — return valid streams.
            return {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}

        monkeypatch.setattr(ffmpeg, "probe", _probe)

        result = _try_direct_download_ffmpeg(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies={"CloudFront-Policy": "a"},
        )

        assert result is True  # still succeeds — probe failure is non-fatal

    def test_probe_generic_exception_falls_through(self, tmp_path, monkeypatch):
        """Duration probe raises generic Exception → caught, continues to run."""
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/video_1080.m3u8",
        )
        _install_fake_ffmpeg_input(monkeypatch, _FakeFFmpegStream(output_path))

        probe_calls = {"n": 0}

        def _probe(*_a, **_k):
            probe_calls["n"] += 1
            if probe_calls["n"] == 1:
                raise RuntimeError("probe blew up")
            return {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}

        monkeypatch.setattr(ffmpeg, "probe", _probe)

        result = _try_direct_download_ffmpeg(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies={"CloudFront-Policy": "a"},
        )

        assert result is True

    def test_ffmpeg_run_raises_ffmpeg_error_returns_false(self, tmp_path, monkeypatch):
        """ffmpeg.run raises ffmpeg.Error → outer except catches, returns False.

        Covers lines 383-387 (ffmpeg.Error handler in main try block).
        """
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/video_1080.m3u8",
        )
        _install_fake_ffmpeg_input(
            monkeypatch,
            _FakeFFmpegStream(
                raises=ffmpeg.Error("ffmpeg", b"", b"simulated ffmpeg failure")
            ),
        )
        monkeypatch.setattr(
            ffmpeg, "probe", lambda *_a, **_k: {"format": {"duration": "60"}}
        )

        result = _try_direct_download_ffmpeg(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies={"CloudFront-Policy": "a"},
        )

        assert result is False

    def test_ffmpeg_run_generic_exception_returns_false(self, tmp_path, monkeypatch):
        """Generic Exception in main try block → returns False (lines 388-391)."""
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/video_1080.m3u8",
        )

        class _RaisingInput:
            def output(self, *_a, **_k):
                raise RuntimeError("unexpected ffmpeg setup failure")

        monkeypatch.setattr(ffmpeg, "input", lambda *_a, **_k: _RaisingInput())

        result = _try_direct_download_ffmpeg(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies={"CloudFront-Policy": "a"},
        )

        assert result is False

    def test_empty_output_returns_false(self, tmp_path, monkeypatch):
        """ffmpeg succeeds but output file is empty/missing → returns False.

        Covers lines 354-355 (the "FFmpeg download produced invalid file"
        warning after run succeeds but file validation fails).
        """
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/video_1080.m3u8",
        )
        _install_fake_ffmpeg_input(monkeypatch, _FakeFFmpegStream())
        monkeypatch.setattr(
            ffmpeg, "probe", lambda *_a, **_k: {"format": {"duration": "60"}}
        )

        result = _try_direct_download_ffmpeg(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies={"CloudFront-Policy": "a"},
        )

        assert result is False
        assert not output_path.exists()

    def test_missing_streams_cleanup_returns_false(self, tmp_path, monkeypatch):
        """Output has neither video nor audio → unlink + return False.

        Covers lines 364-371 (missing-streams branch with file cleanup).
        """
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/video_1080.m3u8",
        )
        _install_fake_ffmpeg_input(monkeypatch, _FakeFFmpegStream(output_path))

        probe_results = [
            {"format": {"duration": "60"}},  # duration probe
            {"streams": []},  # stream verification — no streams
        ]
        monkeypatch.setattr(ffmpeg, "probe", lambda *_a, **_k: probe_results.pop(0))

        result = _try_direct_download_ffmpeg(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies={"CloudFront-Policy": "a"},
        )

        assert result is False
        # Real cleanup happened — file was unlinked via production code.
        assert not output_path.exists()

    def test_stream_verification_probe_raises_cleanup_returns_false(
        self, tmp_path, monkeypatch
    ):
        """Second probe raises → cleanup + return False (lines 373-379)."""
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/video_1080.m3u8",
        )
        _install_fake_ffmpeg_input(monkeypatch, _FakeFFmpegStream(output_path))

        probe_calls = {"n": 0}

        def _probe(*_a, **_k):
            probe_calls["n"] += 1
            if probe_calls["n"] == 1:
                return {"format": {"duration": "60"}}
            raise RuntimeError("stream verification crashed")

        monkeypatch.setattr(ffmpeg, "probe", _probe)

        result = _try_direct_download_ffmpeg(
            config=config,
            m3u8_url="https://example.com/video.m3u8",
            output_path=output_path,
            cookies={"CloudFront-Policy": "a"},
        )

        assert result is False
        assert not output_path.exists()


# ---------------------------------------------------------------------------
# PyAV fakes for _try_direct_download_pyav + _mux_segments_with_pyav
# ---------------------------------------------------------------------------


class _FakeAVStream:
    """Stub for an av.Stream — ``codec_context`` is the check at line 217."""

    def __init__(self, stream_type: str = "video", has_codec_context: bool = True):
        self.type = stream_type
        self.codec_context = object() if has_codec_context else None


class _FakeAVPacket:
    """Stub for an av.Packet with configurable stream + dts + corrupt flag."""

    def __init__(
        self, stream: _FakeAVStream, dts: int | None = 0, is_corrupt: bool = False
    ):
        self.stream = stream
        self.dts = dts
        self.is_corrupt = is_corrupt


class _FakeInputContainer:
    """Stub for ``av.open(...)`` on the HLS input side."""

    def __init__(self, streams: list[_FakeAVStream], packets: list[_FakeAVPacket]):
        self.streams = streams
        self._packets = packets
        self.close_count = 0

    def demux(self, *_a, **_k):
        return iter(self._packets)

    def close(self) -> None:
        self.close_count += 1


class _FakeOutputContainer:
    """Stub for ``av.open(path, "w", ...)``.

    On ``close()`` writes a plausible MP4-shaped stub to the recorded
    output path, so production ``exists()``/``stat().st_size > 0`` checks
    succeed against REAL files.
    """

    def __init__(self, output_path: Path, *, write_bytes: bytes = b"\x00" * 1024):
        self._output_path = output_path
        self._write_bytes = write_bytes
        self.mux_calls = 0
        self.close_count = 0
        self.template_streams: list = []

    def add_stream_from_template(self, src_stream):
        self.template_streams.append(src_stream)
        return _FakeAVStream(src_stream.type)

    def add_stream(self, *_a, **_k):
        return _FakeAVStream()

    def mux(self, _packet):
        self.mux_calls += 1

    def close(self) -> None:
        if self.close_count == 0:
            self._output_path.write_bytes(self._write_bytes)
        self.close_count += 1


class TestDirectDownloadPyAV:
    """Real-code tests for ``_try_direct_download_pyav``.

    Leaf-patches: ``av.open`` (returns fake containers),
    ``download.m3u8._get_highest_quality_variant_url`` (skips HTTP master
    playlist fetch). Real ``tmp_path`` + fake's ``close()`` writes actual
    bytes → production exists/stat checks run against real filesystem.
    """

    def test_success_remuxes_packets(self, tmp_path, monkeypatch):
        """PyAV success: one video stream, three packets, remux succeeds."""
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/v_1080.m3u8",
        )

        video_stream = _FakeAVStream("video", has_codec_context=True)
        input_container = _FakeInputContainer(
            streams=[video_stream],
            packets=[
                _FakeAVPacket(video_stream, dts=0),
                _FakeAVPacket(video_stream, dts=1),
                _FakeAVPacket(video_stream, dts=2),
            ],
        )
        output_container = _FakeOutputContainer(output_path)

        containers = [input_container, output_container]
        monkeypatch.setattr(av, "open", lambda *_a, **_k: containers.pop(0))

        result = _try_direct_download_pyav(
            config=config,
            m3u8_url="https://example.com/v.m3u8",
            output_path=output_path,
            cookies={"CloudFront-Policy": "a"},
        )

        assert result is True
        assert output_container.mux_calls == 3
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    def test_stream_without_codec_context_is_skipped(self, tmp_path, monkeypatch):
        """Stream with codec_context=None → continue; remaining streams mapped.

        Covers line 217-218 (``if not stream.codec_context: continue``).
        """
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/v_1080.m3u8",
        )

        s_no_ctx = _FakeAVStream("data", has_codec_context=False)
        s_video = _FakeAVStream("video", has_codec_context=True)

        input_container = _FakeInputContainer(
            streams=[s_no_ctx, s_video],
            packets=[_FakeAVPacket(s_video, dts=0)],
        )
        output_container = _FakeOutputContainer(output_path)
        containers = [input_container, output_container]
        monkeypatch.setattr(av, "open", lambda *_a, **_k: containers.pop(0))

        result = _try_direct_download_pyav(
            config=config,
            m3u8_url="https://example.com/v.m3u8",
            output_path=output_path,
            cookies={"CloudFront-Policy": "a"},
        )

        assert result is True
        # Only one stream template added (video); data stream skipped.
        assert len(output_container.template_streams) == 1

    def test_no_valid_streams_returns_false(self, tmp_path, monkeypatch):
        """All streams lack codec_context → stream_mapping empty → False.

        Covers lines 222-224 (``if not stream_mapping: return False``).
        """
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/v_1080.m3u8",
        )

        s1 = _FakeAVStream("data", has_codec_context=False)
        s2 = _FakeAVStream("data", has_codec_context=False)
        input_container = _FakeInputContainer(streams=[s1, s2], packets=[])
        output_container = _FakeOutputContainer(output_path)

        containers = [input_container, output_container]
        monkeypatch.setattr(av, "open", lambda *_a, **_k: containers.pop(0))

        result = _try_direct_download_pyav(
            config=config,
            m3u8_url="https://example.com/v.m3u8",
            output_path=output_path,
            cookies={"CloudFront-Policy": "a"},
        )

        assert result is False

    def test_packet_without_dts_is_skipped(self, tmp_path, monkeypatch):
        """Packets with dts=None skipped; others remuxed (line 229-230)."""
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/v_1080.m3u8",
        )

        video = _FakeAVStream("video")
        input_container = _FakeInputContainer(
            streams=[video],
            packets=[
                _FakeAVPacket(video, dts=None),  # skipped
                _FakeAVPacket(video, dts=0),  # muxed
                _FakeAVPacket(video, dts=None),  # skipped
                _FakeAVPacket(video, dts=1),  # muxed
            ],
        )
        output_container = _FakeOutputContainer(output_path)
        containers = [input_container, output_container]
        monkeypatch.setattr(av, "open", lambda *_a, **_k: containers.pop(0))

        result = _try_direct_download_pyav(
            config=config,
            m3u8_url="https://example.com/v.m3u8",
            output_path=output_path,
            cookies={"CloudFront-Policy": "a"},
        )

        assert result is True
        assert output_container.mux_calls == 2

    def test_empty_output_file_returns_false(self, tmp_path, monkeypatch):
        """Demux succeeds but output is empty → "produced empty file" branch.

        Covers line 249 (the missing-or-empty-file warning).
        """
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/v_1080.m3u8",
        )

        video = _FakeAVStream("video")
        input_container = _FakeInputContainer(streams=[video], packets=[])
        output_container = _FakeOutputContainer(output_path, write_bytes=b"")
        containers = [input_container, output_container]
        monkeypatch.setattr(av, "open", lambda *_a, **_k: containers.pop(0))

        result = _try_direct_download_pyav(
            config=config,
            m3u8_url="https://example.com/v.m3u8",
            output_path=output_path,
            cookies={"CloudFront-Policy": "a"},
        )

        assert result is False

    def test_ffmpeg_error_from_av_returns_false(self, tmp_path, monkeypatch):
        """av.open raises av.error.FFmpegError → specific branch fires.

        Covers lines 251-253 (the dedicated ``except av.error.FFmpegError``
        handler separate from the generic Exception handler).
        """
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/v_1080.m3u8",
        )

        def _raise_ffmpeg_error(*_a, **_k):
            # av.error.FFmpegError requires (code, message, [filename, [log]]).
            raise av.error.FFmpegError(-1, "pyav decode failure")

        monkeypatch.setattr(av, "open", _raise_ffmpeg_error)

        result = _try_direct_download_pyav(
            config=config,
            m3u8_url="https://example.com/v.m3u8",
            output_path=output_path,
            cookies={"CloudFront-Policy": "a"},
        )

        assert result is False

    def test_generic_exception_returns_false(self, tmp_path, monkeypatch):
        """Generic Exception → ``except Exception`` handler fires (lines 254-256)."""
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/v_1080.m3u8",
        )
        monkeypatch.setattr(
            av,
            "open",
            lambda *_a, **_k: (_ for _ in ()).throw(
                RuntimeError("unexpected av failure")
            ),
        )

        result = _try_direct_download_pyav(
            config=config,
            m3u8_url="https://example.com/v.m3u8",
            output_path=output_path,
            cookies={"CloudFront-Policy": "a"},
        )

        assert result is False

    def test_packet_from_unknown_stream_is_skipped(self, tmp_path, monkeypatch):
        """Packet whose stream is NOT in stream_mapping → skipped silently.

        Covers partial branch 231->228: ``if packet.stream in stream_mapping``
        False → skip the packet, iterate next. Real production behavior
        when demux returns packets belonging to non-mapped streams
        (e.g., subtitle streams filtered out by codec_context=None check).
        """
        config = _make_real_config()
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8._get_highest_quality_variant_url",
            lambda *_a, **_k: "https://example.com/v_1080.m3u8",
        )

        mapped_video = _FakeAVStream("video", has_codec_context=True)
        orphan_stream = _FakeAVStream("subtitle", has_codec_context=True)

        # Note: only mapped_video will be put in stream_mapping because we
        # construct input_container with both, but orphan_stream packets
        # have a stream ref that won't appear in the mapping dict if the
        # mapping logic uses `is`/`==` identity comparison. Actually,
        # _try_direct_download_pyav iterates input.streams and adds EACH
        # one that has codec_context — so orphan_stream WOULD be added.
        # To force an orphan packet, inject a packet whose .stream points
        # to a DIFFERENT object than either container stream.
        rogue_stream = _FakeAVStream("data", has_codec_context=True)

        input_container = _FakeInputContainer(
            streams=[mapped_video],  # only ONE stream added to mapping
            packets=[
                _FakeAVPacket(mapped_video, dts=0),  # muxed
                _FakeAVPacket(rogue_stream, dts=1),  # NOT in mapping → skipped
                _FakeAVPacket(mapped_video, dts=2),  # muxed
            ],
        )
        output_container = _FakeOutputContainer(output_path)
        containers = [input_container, output_container]
        monkeypatch.setattr(av, "open", lambda *_a, **_k: containers.pop(0))

        result = _try_direct_download_pyav(
            config=config,
            m3u8_url="https://example.com/v.m3u8",
            output_path=output_path,
            cookies={"CloudFront-Policy": "a"},
        )

        assert result is True
        # Rogue packet skipped — only mapped_video packets muxed (2 of 3).
        assert output_container.mux_calls == 2


# ---------------------------------------------------------------------------
# TestMuxSegmentsWithPyAV — NEW dedicated tests (lines 418-516)
# ---------------------------------------------------------------------------


class _MuxInputContainer:
    """Input-container stub used by ``_mux_segments_with_pyav`` tests.

    Parameterized via ``streams`` and ``packets`` so individual tests
    choose which branches of the per-segment loop fire (corrupt packets,
    valid packets per stream type, demux exceptions).
    """

    def __init__(
        self,
        streams: list[_FakeAVStream],
        packets: list[_FakeAVPacket],
        *,
        demux_raises: Exception | None = None,
    ):
        self.streams = streams
        self._packets = packets
        self._demux_raises = demux_raises
        self.close_count = 0

    def demux(self, *_a, **_k):
        if self._demux_raises is not None:
            raise self._demux_raises
        return iter(self._packets)

    def close(self) -> None:
        self.close_count += 1


class TestMuxSegmentsWithPyAV:
    """Real-code tests for ``_mux_segments_with_pyav`` (NEW — was uncovered).

    Covers lines 418-516 including: output container open, per-segment
    template-stream creation, corrupt-packet skipping, per-stream
    muxing branches, per-segment exception handling, >25% skip abort,
    and the finally-block cleanup.
    """

    def test_success_muxes_packets_from_segments(self, tmp_path, monkeypatch):
        """All segments valid → output streams created, all packets muxed."""
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / f"seg{i}.ts" for i in range(3)]
        for f in segment_files:
            f.write_bytes(b"\x00" * 10)

        video = _FakeAVStream("video")
        audio = _FakeAVStream("audio")

        # Produce 2 packets per segment (1 video + 1 audio).
        inputs_iter = iter(
            [
                _MuxInputContainer(
                    streams=[video, audio],
                    packets=[
                        _FakeAVPacket(video, dts=0),
                        _FakeAVPacket(audio, dts=0),
                    ],
                ),
                _MuxInputContainer(
                    streams=[video, audio],
                    packets=[
                        _FakeAVPacket(video, dts=1),
                        _FakeAVPacket(audio, dts=1),
                    ],
                ),
                _MuxInputContainer(
                    streams=[video, audio],
                    packets=[
                        _FakeAVPacket(video, dts=2),
                        _FakeAVPacket(audio, dts=2),
                    ],
                ),
            ]
        )
        output_container = _FakeOutputContainer(output_path)

        def _open(path, mode="r", *_a, **_k):
            if mode == "w":
                return output_container
            return next(inputs_iter)

        monkeypatch.setattr(av, "open", _open)

        result = _mux_segments_with_pyav(segment_files, output_path)

        assert result is True
        assert output_container.mux_calls == 6
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    def test_corrupt_packets_are_skipped(self, tmp_path, monkeypatch):
        """Packets with is_corrupt=True are counted + skipped (line 457-459)."""
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / "seg.ts"]
        segment_files[0].write_bytes(b"\x00")

        video = _FakeAVStream("video")
        inputs_iter = iter(
            [
                _MuxInputContainer(
                    streams=[video],
                    packets=[
                        _FakeAVPacket(video, dts=0),
                        _FakeAVPacket(video, dts=1, is_corrupt=True),
                        _FakeAVPacket(video, dts=2),
                        _FakeAVPacket(video, dts=None),  # also skipped
                    ],
                ),
            ]
        )
        output_container = _FakeOutputContainer(output_path)

        def _open(_path, mode="r", *_a, **_k):
            if mode == "w":
                return output_container
            return next(inputs_iter)

        monkeypatch.setattr(av, "open", _open)

        result = _mux_segments_with_pyav(segment_files, output_path)

        assert result is True
        assert output_container.mux_calls == 2

    def test_segment_open_failure_is_skipped(self, tmp_path, monkeypatch):
        """av.open on a segment raises → skipped_segments++ and loop continues.

        Covers lines 434-439 (the per-segment open ``except`` + continue).
        """
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / f"seg{i}.ts" for i in range(4)]
        for f in segment_files:
            f.write_bytes(b"\x00")

        video = _FakeAVStream("video")
        segments_remaining = [
            "raise",
            _MuxInputContainer(streams=[video], packets=[_FakeAVPacket(video, dts=0)]),
            _MuxInputContainer(streams=[video], packets=[_FakeAVPacket(video, dts=1)]),
            _MuxInputContainer(streams=[video], packets=[_FakeAVPacket(video, dts=2)]),
        ]
        output_container = _FakeOutputContainer(output_path)

        def _open(_path, mode="r", *_a, **_k):
            if mode == "w":
                return output_container
            next_item = segments_remaining.pop(0)
            if next_item == "raise":
                raise RuntimeError("segment open failed")
            return next_item

        monkeypatch.setattr(av, "open", _open)

        result = _mux_segments_with_pyav(segment_files, output_path)

        # 1 out of 4 skipped = 25% — right at the threshold, NOT above, so
        # the "> 25%" abort does NOT fire. Success returned.
        assert result is True

    def test_abort_when_over_25_percent_skipped(self, tmp_path, monkeypatch):
        """>25% skip rate aborts PyAV mux (lines 494-500)."""
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / f"seg{i}.ts" for i in range(10)]
        for f in segment_files:
            f.write_bytes(b"\x00")

        video = _FakeAVStream("video")
        open_count = {"n": 0}

        def _open(_path, mode="r", *_a, **_k):
            if mode == "w":
                return _FakeOutputContainer(output_path)
            open_count["n"] += 1
            # Fail on segments 1, 3, 5, 7 → 4 of 10 = 40% > 25% → abort.
            if open_count["n"] in (1, 3, 5, 7):
                raise RuntimeError(f"segment {open_count['n']} failed")
            return _MuxInputContainer(
                streams=[video],
                packets=[_FakeAVPacket(video, dts=open_count["n"])],
            )

        monkeypatch.setattr(av, "open", _open)

        result = _mux_segments_with_pyav(segment_files, output_path)

        assert result is False

    def test_demux_error_skips_segment_entirely(self, tmp_path, monkeypatch):
        """Demux raises inside per-segment try → segment counted as skipped.

        Covers the outer ``except Exception`` at line 479 (after the
        output-streams-creation block).
        """
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / f"seg{i}.ts" for i in range(4)]
        for f in segment_files:
            f.write_bytes(b"\x00")

        video = _FakeAVStream("video")
        inputs = iter(
            [
                _MuxInputContainer(
                    streams=[video],
                    packets=[],
                    demux_raises=RuntimeError("demux blew up"),
                ),
                _MuxInputContainer(
                    streams=[video], packets=[_FakeAVPacket(video, dts=0)]
                ),
                _MuxInputContainer(
                    streams=[video], packets=[_FakeAVPacket(video, dts=1)]
                ),
                _MuxInputContainer(
                    streams=[video], packets=[_FakeAVPacket(video, dts=2)]
                ),
            ]
        )
        output_container = _FakeOutputContainer(output_path)

        def _open(_path, mode="r", *_a, **_k):
            if mode == "w":
                return output_container
            return next(inputs)

        monkeypatch.setattr(av, "open", _open)

        result = _mux_segments_with_pyav(segment_files, output_path)

        # 1 skipped of 4 = 25% — right at threshold, not above, success.
        assert result is True

    def test_mux_call_exception_increments_skipped_packets(self, tmp_path, monkeypatch):
        """Per-packet mux raises OSError → skipped_packets++ (lines 469-470)."""
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / "seg.ts"]
        segment_files[0].write_bytes(b"\x00")

        video = _FakeAVStream("video")
        inputs = iter(
            [
                _MuxInputContainer(
                    streams=[video],
                    packets=[
                        _FakeAVPacket(video, dts=0),
                        _FakeAVPacket(video, dts=1),
                        _FakeAVPacket(video, dts=2),
                    ],
                )
            ]
        )

        class _MuxRaisingOutput(_FakeOutputContainer):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.mux_attempts = 0

            def mux(self, packet):
                self.mux_attempts += 1
                if self.mux_attempts == 2:
                    raise OSError("mid-segment mux failure")
                super().mux(packet)

        output_container = _MuxRaisingOutput(output_path)

        def _open(_path, mode="r", *_a, **_k):
            if mode == "w":
                return output_container
            return next(inputs)

        monkeypatch.setattr(av, "open", _open)

        result = _mux_segments_with_pyav(segment_files, output_path)

        # Even with one packet skipped mid-segment, overall mux succeeds.
        assert result is True

    def test_outer_exception_returns_false(self, tmp_path, monkeypatch):
        """Outer ``except Exception`` fires → returns False (lines 510-511)."""
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / "seg.ts"]
        segment_files[0].write_bytes(b"\x00")

        # Make av.open raise on the FIRST call (output-container open) so
        # the outer try-block's except fires immediately.
        def _raise_on_output_open(*_a, **_k):
            raise RuntimeError("output container open failed")

        monkeypatch.setattr(av, "open", _raise_on_output_open)

        result = _mux_segments_with_pyav(segment_files, output_path)

        assert result is False

    def test_empty_output_returns_false(self, tmp_path, monkeypatch):
        """Mux completes but output file is empty → returns False (line 508)."""
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / "seg.ts"]
        segment_files[0].write_bytes(b"\x00")

        video = _FakeAVStream("video")
        inputs = iter(
            [_MuxInputContainer(streams=[video], packets=[_FakeAVPacket(video, dts=0)])]
        )
        # Empty write_bytes → output file will be 0 bytes → exists True but
        # st_size == 0 → fails the condition at line 505.
        output_container = _FakeOutputContainer(output_path, write_bytes=b"")

        def _open(_path, mode="r", *_a, **_k):
            if mode == "w":
                return output_container
            return next(inputs)

        monkeypatch.setattr(av, "open", _open)

        result = _mux_segments_with_pyav(segment_files, output_path)

        assert result is False

    def test_duplicate_video_stream_is_skipped(self, tmp_path, monkeypatch):
        """Second video stream is skipped (already in output_streams).

        Covers partial branch 449->444: the elif branch in the template-
        creation loop evaluates False for a video stream when "video" is
        already in output_streams. The previous iteration added the first
        video stream; this iteration's elif check fails (not an audio).
        """
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / "seg.ts"]
        segment_files[0].write_bytes(b"\x00")

        video1 = _FakeAVStream("video")
        video2 = _FakeAVStream("video")  # duplicate type

        inputs = iter(
            [
                _MuxInputContainer(
                    streams=[video1, video2],
                    packets=[_FakeAVPacket(video1, dts=0)],
                )
            ]
        )
        output_container = _FakeOutputContainer(output_path)

        def _open(_path, mode="r", *_a, **_k):
            if mode == "w":
                return output_container
            return next(inputs)

        monkeypatch.setattr(av, "open", _open)

        result = _mux_segments_with_pyav(segment_files, output_path)

        assert result is True
        # Only one template stream added — the second video stream hit the
        # elif (False because it's not audio) and was skipped.
        assert len(output_container.template_streams) == 1

    def test_packet_with_unmapped_stream_type_is_skipped(self, tmp_path, monkeypatch):
        """Packet of type "subtitle" (not video/audio) → both branches False.

        Covers partial branch 464->456: the audio elif inside the packet
        demux loop evaluates False when the packet is a subtitle (neither
        condition matches). Loop continues to next packet.
        """
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / "seg.ts"]
        segment_files[0].write_bytes(b"\x00")

        video = _FakeAVStream("video")
        subtitle = _FakeAVStream("subtitle")

        inputs = iter(
            [
                _MuxInputContainer(
                    streams=[video],  # only video → only "video" in output_streams
                    packets=[
                        _FakeAVPacket(video, dts=0),
                        _FakeAVPacket(subtitle, dts=1),  # doesn't match either branch
                        _FakeAVPacket(video, dts=2),
                    ],
                )
            ]
        )
        output_container = _FakeOutputContainer(output_path)

        def _open(_path, mode="r", *_a, **_k):
            if mode == "w":
                return output_container
            return next(inputs)

        monkeypatch.setattr(av, "open", _open)

        result = _mux_segments_with_pyav(segment_files, output_path)

        assert result is True
        # Only video packets muxed — subtitle skipped via branch 464->456.
        assert output_container.mux_calls == 2

    def test_input_close_raises_propagates_to_outer_finally(
        self, tmp_path, monkeypatch
    ):
        """input_container.close() raises → outer finally's output.close() fires.

        Covers line 514: when an exception escapes the outer try (because
        ``input_container.close()`` at line 485 raised), the finally block
        at 512-514 still needs to close the output container. Production
        code's output teardown is critical even on error paths.
        """
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / "seg.ts"]
        segment_files[0].write_bytes(b"\x00")

        video = _FakeAVStream("video")

        class _InputCloseRaises(_MuxInputContainer):
            def close(self):
                raise RuntimeError("input close failed in finally")

        inputs = iter(
            [
                _InputCloseRaises(
                    streams=[video],
                    packets=[_FakeAVPacket(video, dts=0)],
                )
            ]
        )
        output_container = _FakeOutputContainer(output_path)

        def _open(_path, mode="r", *_a, **_k):
            if mode == "w":
                return output_container
            return next(inputs)

        monkeypatch.setattr(av, "open", _open)

        result = _mux_segments_with_pyav(segment_files, output_path)

        assert result is False
        # Outer finally still ran output.close() — proof via close_count > 0.
        assert output_container.close_count >= 1

    def test_audio_stream_also_muxed(self, tmp_path, monkeypatch):
        """Audio + video segments both have template streams added + packets muxed.

        Covers partial branches 449->444 and 464->456: the elif audio branches
        in both the template-creation loop AND the packet-demux loop. The
        previous test_success covered video-only; this one adds the audio
        path separately to exercise the audio-specific elif.
        """
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / "seg.ts"]
        segment_files[0].write_bytes(b"\x00")

        video = _FakeAVStream("video")
        audio = _FakeAVStream("audio")
        inputs = iter(
            [
                _MuxInputContainer(
                    streams=[video, audio],
                    packets=[
                        _FakeAVPacket(video, dts=0),
                        _FakeAVPacket(audio, dts=0),
                    ],
                )
            ]
        )
        output_container = _FakeOutputContainer(output_path)

        def _open(_path, mode="r", *_a, **_k):
            if mode == "w":
                return output_container
            return next(inputs)

        monkeypatch.setattr(av, "open", _open)

        result = _mux_segments_with_pyav(segment_files, output_path)

        assert result is True
        # Both video + audio template streams created.
        assert len(output_container.template_streams) == 2
        # Both video + audio packets muxed.
        assert output_container.mux_calls == 2


# ---------------------------------------------------------------------------
# TestMuxSegmentsWithFFmpeg — NEW dedicated tests (lines 532-555)
# ---------------------------------------------------------------------------


class TestMuxSegmentsWithFFmpeg:
    """Real-code tests for ``_mux_segments_with_ffmpeg`` (NEW — was uncovered).

    Uses real ``tmp_path`` for both segment files + concat list file.
    Production code writes the ``_ffmpeg_concat_.ffc`` list file and
    cleans it up in the finally block — tests assert on REAL filesystem
    state to verify creation + cleanup. ``ffmpeg.input`` patched at leaf.
    """

    def test_success_creates_output_and_cleans_list_file(self, tmp_path, monkeypatch):
        """Success: output file exists + non-empty, concat list removed."""
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / f"seg{i}.ts" for i in range(2)]
        for f in segment_files:
            f.write_bytes(b"\x00" * 10)

        _install_fake_ffmpeg_input(monkeypatch, _FakeFFmpegStream(output_path))

        result = _mux_segments_with_ffmpeg(segment_files, output_path)

        assert result is True
        assert output_path.exists()
        assert output_path.stat().st_size > 0
        # Concat list file was created + cleaned up.
        assert not (tmp_path / "_ffmpeg_concat_.ffc").exists()

    def test_ffmpeg_error_returns_false(self, tmp_path, monkeypatch):
        """ffmpeg.run raises ffmpeg.Error → returns False (lines 549-552)."""
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / "seg.ts"]
        segment_files[0].write_bytes(b"\x00")

        _install_fake_ffmpeg_input(
            monkeypatch,
            _FakeFFmpegStream(
                raises=ffmpeg.Error("ffmpeg", b"", b"simulated ffmpeg failure")
            ),
        )

        result = _mux_segments_with_ffmpeg(segment_files, output_path)

        assert result is False
        # Concat list file still cleaned up despite the error.
        assert not (tmp_path / "_ffmpeg_concat_.ffc").exists()

    def test_generic_exception_returns_false(self, tmp_path, monkeypatch):
        """Generic Exception → returns False (lines 553-555)."""
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / "seg.ts"]
        segment_files[0].write_bytes(b"\x00")

        class _RaisingInput:
            def output(self, *_a, **_k):
                raise RuntimeError("ffmpeg setup failed")

        monkeypatch.setattr(ffmpeg, "input", lambda *_a, **_k: _RaisingInput())

        result = _mux_segments_with_ffmpeg(segment_files, output_path)

        assert result is False
        assert not (tmp_path / "_ffmpeg_concat_.ffc").exists()

    def test_empty_output_returns_false(self, tmp_path, monkeypatch):
        """ffmpeg run succeeds but produces no output → returns False (line 547)."""
        output_path = tmp_path / "muxed.mp4"
        segment_files = [tmp_path / "seg.ts"]
        segment_files[0].write_bytes(b"\x00")

        _install_fake_ffmpeg_input(monkeypatch, _FakeFFmpegStream())

        result = _mux_segments_with_ffmpeg(segment_files, output_path)

        assert result is False


# ---------------------------------------------------------------------------
# TestSegmentDownload — real orchestration + fake API + fake mux leaves
# ---------------------------------------------------------------------------


class TestSegmentDownload:
    """Real-code tests for ``_try_segment_download``.

    Exercises the real inner ``download_ts`` function (lines 593-618)
    against a fake ``get_with_ngsw`` that streams bytes. A
    ``SyncExecutor`` (from ``tests.fixtures.utils``) replaces
    ThreadPoolExecutor so coverage instrumentation sees the download_ts
    body — otherwise threaded code is invisible to coverage.py.
    ``_mux_segments_with_pyav`` / ``_mux_segments_with_ffmpeg`` patched
    at module level — their own tests (above) cover their real behavior.
    """

    _DEFAULT_PLAYLIST = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-PLAYLIST-TYPE:VOD\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXTINF:10.0,\n"
        "segment1.ts\n"
        "#EXTINF:8.0,\n"
        "segment2.ts\n"
        "#EXT-X-ENDLIST\n"
    )

    def _make_config_with_segments(self, fansly_api) -> FanslyConfig:
        """Build a real config attached to ``fansly_api``.

        Tests mount their own respx routes for the playlist + segment URLs
        via ``_mount_segment_routes`` — this helper just owns the config +
        api wiring so individual tests stay focused on the behavior under
        test.
        """
        config = _make_real_config()
        config._api = fansly_api
        return config

    @staticmethod
    def _mount_segment_routes(
        *,
        base_url: str = "https://example.com",
        playlist_text: str | None = None,
        segment_bytes: bytes = b"\x00" * 256,
        segment_status: int = 200,
        segment_count: int = 2,
        segment_raises: Exception | None = None,
    ) -> tuple[respx.MockRouter, respx.MockRouter]:
        """Mount respx routes for an m3u8 segment download test.

        Returns ``(playlist_route, segment_route)`` so callers can inspect
        ``.calls`` and ``.call_count``. Routes are declared narrow-first
        (segment then playlist) so the more-specific match wins.
        """
        if playlist_text is None:
            playlist_text = TestSegmentDownload._DEFAULT_PLAYLIST
        # CORS preflight blanket — pad for playlist + each segment.
        respx.options(url__startswith=f"{base_url}/").mock(
            side_effect=[httpx.Response(200)] * (segment_count + 2)
        )
        if segment_raises is not None:
            segment_route = respx.get(url__startswith=f"{base_url}/segment").mock(
                side_effect=[segment_raises] * segment_count
            )
        else:
            segment_route = respx.get(url__startswith=f"{base_url}/segment").mock(
                side_effect=[httpx.Response(segment_status, content=segment_bytes)]
                * segment_count
            )
        playlist_route = respx.get(url__startswith=f"{base_url}/v.m3u8").mock(
            side_effect=[httpx.Response(200, text=playlist_text)]
        )
        return playlist_route, segment_route

    def test_success_invokes_pyav_mux(
        self, tmp_path, monkeypatch, fansly_api_with_respx
    ):
        """All segments downloaded + PyAV mux succeeds → returns output_path."""
        config = self._make_config_with_segments(fansly_api_with_respx)
        output_path = tmp_path / "video.mp4"
        cookies = {"CloudFront-Policy": "abc"}

        # Synchronous executor — see SyncExecutor docstring for why.
        monkeypatch.setattr(
            "download.m3u8.concurrent.futures.ThreadPoolExecutor", SyncExecutor
        )
        monkeypatch.setattr(
            "download.m3u8._mux_segments_with_pyav",
            lambda _segs, out: out.write_bytes(b"\x00" * 1024) or True,
        )
        ffmpeg_called = {"n": 0}
        monkeypatch.setattr(
            "download.m3u8._mux_segments_with_ffmpeg",
            lambda *_a, **_k: ffmpeg_called.update(n=ffmpeg_called["n"] + 1) or True,
        )

        with respx.mock:
            playlist_route, segment_route = self._mount_segment_routes()

            try:
                result = _try_segment_download(
                    config=config,
                    m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
                    output_path=output_path,
                    cookies=cookies,
                )
            finally:
                dump_fansly_calls(playlist_route.calls)
                dump_fansly_calls(segment_route.calls)

        assert result == output_path
        # ffmpeg mux not called — PyAV succeeded first.
        assert ffmpeg_called["n"] == 0

    def test_ffmpeg_mux_fallback(self, tmp_path, monkeypatch, fansly_api_with_respx):
        """PyAV mux fails → FFmpeg concat fallback tried."""
        config = self._make_config_with_segments(fansly_api_with_respx)
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8.concurrent.futures.ThreadPoolExecutor", SyncExecutor
        )
        monkeypatch.setattr(
            "download.m3u8._mux_segments_with_pyav",
            lambda _segs, _out: False,
        )
        ffmpeg_called = {"n": 0}

        def _ffmpeg_mux(_segs, out):
            ffmpeg_called["n"] += 1
            out.write_bytes(b"\x00" * 1024)
            return True

        monkeypatch.setattr("download.m3u8._mux_segments_with_ffmpeg", _ffmpeg_mux)

        with respx.mock:
            playlist_route, segment_route = self._mount_segment_routes()

            try:
                result = _try_segment_download(
                    config=config,
                    m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
                    output_path=output_path,
                    cookies={"CloudFront-Policy": "a"},
                )
            finally:
                dump_fansly_calls(playlist_route.calls)
                dump_fansly_calls(segment_route.calls)

        assert result == output_path
        assert ffmpeg_called["n"] == 1

    def test_both_mux_paths_fail_raises(
        self, tmp_path, monkeypatch, fansly_api_with_respx
    ):
        """Both PyAV + FFmpeg mux fail → raises M3U8Error."""
        config = self._make_config_with_segments(fansly_api_with_respx)
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8.concurrent.futures.ThreadPoolExecutor", SyncExecutor
        )
        monkeypatch.setattr(
            "download.m3u8._mux_segments_with_pyav", lambda *_a, **_k: False
        )
        monkeypatch.setattr(
            "download.m3u8._mux_segments_with_ffmpeg", lambda *_a, **_k: False
        )

        with respx.mock:
            playlist_route, segment_route = self._mount_segment_routes()

            try:
                with pytest.raises(
                    M3U8Error, match="Both PyAV and FFmpeg muxing failed"
                ):
                    _try_segment_download(
                        config=config,
                        m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
                        output_path=output_path,
                        cookies={"CloudFront-Policy": "a"},
                    )
            finally:
                dump_fansly_calls(playlist_route.calls)
                dump_fansly_calls(segment_route.calls)

    def test_missing_segments_raises(
        self, tmp_path, monkeypatch, fansly_api_with_respx
    ):
        """Segment returns 404 → not-written on disk → raises with list."""
        config = self._make_config_with_segments(fansly_api_with_respx)
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8.concurrent.futures.ThreadPoolExecutor", SyncExecutor
        )
        monkeypatch.setattr(
            "download.m3u8._mux_segments_with_pyav", lambda *_a, **_k: True
        )

        with respx.mock:
            playlist_route, segment_route = self._mount_segment_routes(
                segment_status=404
            )

            try:
                with pytest.raises(
                    M3U8Error, match="Stream segments failed to download"
                ):
                    _try_segment_download(
                        config=config,
                        m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
                        output_path=output_path,
                        cookies={"CloudFront-Policy": "a"},
                    )
            finally:
                dump_fansly_calls(playlist_route.calls)
                dump_fansly_calls(segment_route.calls)

    def test_download_ts_skips_empty_chunks(
        self, tmp_path, monkeypatch, fansly_api_with_respx
    ):
        """iter_bytes yields empty chunks mixed with real ones → empties skipped.

        Covers partial branch 612->611: the ``if chunk:`` falsy branch
        (empty bytes) continues the loop without writing. Real httpx
        streaming responses can yield empty chunks when the network
        pauses; the production guard protects against writing empty data.
        """
        config = self._make_config_with_segments(fansly_api_with_respx)
        output_path = tmp_path / "video.mp4"

        playlist_text = (
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXT-X-PLAYLIST-TYPE:VOD\n"
            "#EXT-X-TARGETDURATION:10\n"
            "#EXTINF:10.0,\n"
            "segment1.ts\n"
            "#EXT-X-ENDLIST\n"
        )

        class _EmptyAndRealChunkStream(httpx.SyncByteStream):
            """Custom byte stream that yields empty chunks mixed with real bytes.

            Selective deviation from real httpx behavior: real responses
            rarely yield ``b""`` from ``iter_bytes``, but they CAN under
            network pauses. This stream injects that deviation to exercise
            production's ``if chunk:`` guard at download/m3u8.py line 612.
            """

            def __iter__(self):
                yield b""  # empty chunk — hits line 612 False
                yield b"\x00" * 128  # real chunk — hits line 612 True
                yield b""  # another empty

            def close(self) -> None:
                pass

        monkeypatch.setattr(
            "download.m3u8.concurrent.futures.ThreadPoolExecutor", SyncExecutor
        )
        monkeypatch.setattr(
            "download.m3u8._mux_segments_with_pyav",
            lambda _segs, out: out.write_bytes(b"\x00" * 1024) or True,
        )

        with respx.mock:
            respx.options(url__startswith="https://example.com/").mock(
                side_effect=[httpx.Response(200)] * 3
            )
            segment_route = respx.get(
                url__startswith="https://example.com/segment"
            ).mock(side_effect=[httpx.Response(200, stream=_EmptyAndRealChunkStream())])
            playlist_route = respx.get(
                url__startswith="https://example.com/v.m3u8"
            ).mock(side_effect=[httpx.Response(200, text=playlist_text)])

            try:
                result = _try_segment_download(
                    config=config,
                    m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
                    output_path=output_path,
                    cookies={"CloudFront-Policy": "a"},
                )
            finally:
                dump_fansly_calls(playlist_route.calls)
                dump_fansly_calls(segment_route.calls)

        assert result == output_path

    def test_download_ts_handles_non_200_status(
        self, tmp_path, monkeypatch, fansly_api_with_respx
    ):
        """Segment returns non-200 → debug log + returns without writing.

        Covers lines 604-609: the inner download_ts's status-check early
        return. Unlike the 404 test above, this lets the function return
        from ``download_ts`` without raising — the file is simply not
        created, which is then caught by the missing-segments check.
        """
        config = self._make_config_with_segments(fansly_api_with_respx)
        output_path = tmp_path / "video.mp4"

        monkeypatch.setattr(
            "download.m3u8.concurrent.futures.ThreadPoolExecutor", SyncExecutor
        )

        with respx.mock:
            playlist_route, segment_route = self._mount_segment_routes(
                segment_status=500
            )

            try:
                with pytest.raises(
                    M3U8Error, match="Stream segments failed to download"
                ):
                    _try_segment_download(
                        config=config,
                        m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
                        output_path=output_path,
                        cookies={"CloudFront-Policy": "a"},
                    )
            finally:
                dump_fansly_calls(playlist_route.calls)
                dump_fansly_calls(segment_route.calls)

    def test_download_ts_handles_http_exception(
        self, tmp_path, monkeypatch, fansly_api_with_respx
    ):
        """Segment get_with_ngsw raises → inner download_ts except fires.

        Covers lines 614-615: ``except Exception`` in the inner download_ts
        function. When ALL segments fail via exceptions, missing_segments
        detection raises M3U8Error.
        """
        config = self._make_config_with_segments(fansly_api_with_respx)
        output_path = tmp_path / "video.mp4"

        playlist_text = (
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXT-X-PLAYLIST-TYPE:VOD\n"
            "#EXT-X-TARGETDURATION:10\n"
            "#EXTINF:10.0,\n"
            "segment1.ts\n"
            "#EXT-X-ENDLIST\n"
        )

        monkeypatch.setattr(
            "download.m3u8.concurrent.futures.ThreadPoolExecutor", SyncExecutor
        )

        with respx.mock:
            playlist_route, segment_route = self._mount_segment_routes(
                playlist_text=playlist_text,
                segment_count=1,
                segment_raises=RuntimeError("segment download exception"),
            )

            try:
                with pytest.raises(
                    M3U8Error, match="Stream segments failed to download"
                ):
                    _try_segment_download(
                        config=config,
                        m3u8_url="https://example.com/v.m3u8?Policy=a&Key-Pair-Id=k&Signature=s",
                        output_path=output_path,
                        cookies={"CloudFront-Policy": "a"},
                    )
            finally:
                dump_fansly_calls(playlist_route.calls)
                dump_fansly_calls(segment_route.calls)
