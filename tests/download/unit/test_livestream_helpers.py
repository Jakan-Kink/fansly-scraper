"""Branch-coverage unit tests for download.livestream helper functions.

Targets the failure/fallback arms the integration recorder tests don't reach:

- ``_resolve_variant_url`` — master fetch failure (336-341) and a master with
  no variant streams (353-354).
- ``_download_segment`` — network failure → False (650-657).
- ``_get_authenticated_playback_url`` — both fallback paths to the
  unauthenticated ``channel.playbackUrl`` (301-310): the API-raises arm and the
  no-auth-url-in-response arm.

Boundary policy mirrors the recorder integration tests: IVS hosts and the
Fansly REST endpoint are mocked at the HTTP layer (``respx_ivs_cdn`` /
``respx_fansly_api``); the helper code paths run real.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
import respx

from api.fansly import FanslyApi
from config.fanslyconfig import FanslyConfig
from download.livestream import (
    _download_segment,
    _get_authenticated_playback_url,
    _mux_ivs_segments,
    _poll_segments_loop,
    _record_stream,
    _resolve_variant_url,
    _salvage_orphan_segments,
)
from fileio.livestream import _unique_output_path
from metadata.models import StreamChannel
from pathio.livestream import _get_livestreams_dir, _get_segments_base
from tests.fixtures.api import (
    FakeAudioStream,
    FakeAvOutputContainerCloseError,
    FakeAvOutputContainerMuxError,
    FakeAvOutputContainerNoWrite,
    FakeAvOutputContainerOpenError,
    FakeAvVerifyContainerEmpty,
    FakeAvVerifyContainerRaises,
    FakeVideoStream,
    bad_packet_av_segment,
    build_streaming_channel_response,
    build_variant_playlist,
    codec_none_av_segment,
    data_only_av_segment,
    demux_error_av_segment,
    dump_fansly_calls,
    extra_stream_av_segment,
    make_ivs_av_open_fake_seq,
    many_bad_packets_av_segment,
    missing_pid_av_segment,
    normal_av_segment,
    open_error_av_segment,
    zero_duration_av_segment,
)
from tests.fixtures.utils.test_isolation import snowflake_id


_MASTER_URL = "https://abc.us-east-1.playback.live-video.net/master.m3u8"


class TestResolveVariantUrl:
    """_resolve_variant_url failure arms (the happy path is covered by the
    recorder integration tests)."""

    @pytest.mark.asyncio
    async def test_master_fetch_failure_returns_none(
        self, respx_ivs_cdn: httpx.AsyncClient
    ) -> None:
        """A non-2xx master playlist response → raise_for_status → None (336-341)."""
        route = respx.get(_MASTER_URL).mock(side_effect=[httpx.Response(500)])
        try:
            result = await _resolve_variant_url(_MASTER_URL)
        finally:
            dump_fansly_calls(route.calls, "resolve_variant_master_500")

        assert result is None
        assert route.called

    @pytest.mark.asyncio
    async def test_master_without_variants_returns_none(
        self, respx_ivs_cdn: httpx.AsyncClient
    ) -> None:
        """A master playlist carrying no EXT-X-STREAM-INF → None (353-354)."""
        empty_master = "#EXTM3U\n#EXT-X-VERSION:3\n"
        route = respx.get(_MASTER_URL).mock(
            side_effect=[httpx.Response(200, text=empty_master)]
        )
        try:
            result = await _resolve_variant_url(_MASTER_URL)
        finally:
            dump_fansly_calls(route.calls, "resolve_variant_no_streams")

        assert result is None
        assert route.called

    @pytest.mark.asyncio
    async def test_master_parse_failure_returns_none(
        self, respx_ivs_cdn: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """m3u8 parsing the master content raising → None (345-350). The m3u8
        leaf is patched to raise (it parses too leniently to fail on its own)."""

        def _raise(*_a: object, **_k: object) -> None:
            raise ValueError("malformed m3u8")

        monkeypatch.setattr("download.livestream.m3u8.M3U8", _raise)
        route = respx.get(_MASTER_URL).mock(
            side_effect=[httpx.Response(200, text="#EXTM3U\n")]
        )
        try:
            result = await _resolve_variant_url(_MASTER_URL)
        finally:
            dump_fansly_calls(route.calls, "resolve_variant_parse_fail")

        assert result is None


class TestDownloadSegment:
    """_download_segment network-failure arm (650-657)."""

    @pytest.mark.asyncio
    async def test_network_failure_returns_false(
        self, respx_ivs_cdn: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        """A 404 on the segment URL → except → False; nothing written."""
        seg_url = "https://use14.playlist.live-video.net/seg-fail.ts"
        dest = tmp_path / "seg-fail.ts"
        route = respx.get(seg_url).mock(side_effect=[httpx.Response(404)])
        try:
            ok = await _download_segment(respx_ivs_cdn, seg_url, dest, "test")
        finally:
            dump_fansly_calls(route.calls, "download_segment_404")

        assert ok is False
        assert not dest.exists()


class TestGetAuthenticatedPlaybackUrl:
    """_get_authenticated_playback_url fallback arms (301-310). Both must fall
    back to the unauthenticated channel.playbackUrl."""

    def _channel(self, creator_id: int) -> StreamChannel:
        return StreamChannel.model_validate(
            {
                "id": snowflake_id(),
                "accountId": creator_id,
                "status": 2,
                "playbackUrl": "https://fallback.playback.live-video.net/master.m3u8",
            }
        )

    @pytest.mark.asyncio
    async def test_api_error_falls_back_to_channel_url(
        self, respx_fansly_api: FanslyApi, config_wired: FanslyConfig
    ) -> None:
        """Invalid JSON from the streaming-channel endpoint → except arm
        (301-307) → fallback to channel.playbackUrl (310)."""
        creator_id = snowflake_id()
        channel = self._channel(creator_id)
        route = respx.get(
            f"https://apiv3.fansly.com/api/v1/streaming/channel/{creator_id}"
        ).mock(side_effect=[httpx.Response(200, text="<<not json>>")])
        try:
            result = await _get_authenticated_playback_url(
                config_wired, creator_id, channel
            )
        finally:
            dump_fansly_calls(route.calls, "get_auth_url_bad_json")

        assert result == channel.playbackUrl

    @pytest.mark.asyncio
    async def test_no_auth_url_in_response_falls_back(
        self, respx_fansly_api: FanslyApi, config_wired: FanslyConfig
    ) -> None:
        """A well-formed response with no playbackUrl anywhere → the 299->310
        arm → fallback to channel.playbackUrl."""
        creator_id = snowflake_id()
        channel = self._channel(creator_id)
        # master_url="" → stream.playbackUrl is falsy and there is no top-level
        # playbackUrl, so auth_url stays None → the 299->310 fallback arm.
        payload = build_streaming_channel_response(creator_id=creator_id, master_url="")
        route = respx.get(
            f"https://apiv3.fansly.com/api/v1/streaming/channel/{creator_id}"
        ).mock(side_effect=[httpx.Response(200, json=payload)])
        try:
            result = await _get_authenticated_playback_url(
                config_wired, creator_id, channel
            )
        finally:
            dump_fansly_calls(route.calls, "get_auth_url_no_playback")

        assert result == channel.playbackUrl

    @pytest.mark.asyncio
    async def test_non_dict_response_falls_back(
        self, respx_fansly_api: FanslyApi, config_wired: FanslyConfig
    ) -> None:
        """Response contents that are not a dict (a list) → the isinstance
        guard's False arm → fallback to channel.playbackUrl."""
        creator_id = snowflake_id()
        channel = self._channel(creator_id)
        route = respx.get(
            f"https://apiv3.fansly.com/api/v1/streaming/channel/{creator_id}"
        ).mock(
            side_effect=[httpx.Response(200, json={"success": True, "response": []})]
        )
        try:
            result = await _get_authenticated_playback_url(
                config_wired, creator_id, channel
            )
        finally:
            dump_fansly_calls(route.calls, "get_auth_list_response")

        assert result == channel.playbackUrl


class TestMuxIvsSegments:
    """Branch coverage for _mux_ivs_segments failure/skip arms (PLR0911).

    PyAV is leaf-faked via make_ivs_av_open_fake_seq + the VideoStream/
    AudioStream symbol-swap; one mux call drives several skip branches by
    giving each segment file a distinct spec.
    """

    @staticmethod
    def _patch_av(
        monkeypatch: pytest.MonkeyPatch, fake_open: Callable[..., object]
    ) -> None:
        monkeypatch.setattr("download.livestream.av.open", fake_open)
        monkeypatch.setattr("download.livestream.VideoStream", FakeVideoStream)
        monkeypatch.setattr("download.livestream.AudioStream", FakeAudioStream)

    @staticmethod
    def _write_segments(tmp_path: Path, names: list[str]) -> list[Path]:
        segs = [tmp_path / n for n in names]
        for seg in segs:
            seg.write_bytes(b"fake-ts")
        return segs

    def test_empty_segments_returns_false(self, tmp_path: Path) -> None:
        """No segments → early False (no PyAV touched)."""
        assert _mux_ivs_segments([], [], tmp_path / "out.mp4", "test") is False

    def test_no_pids_discovered_aborts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Segments carrying only a data stream → PID discovery fails → False."""
        segs = self._write_segments(tmp_path, ["s0.ts", "s1.ts"])
        out = tmp_path / "out.mp4"
        specs = {s.name: data_only_av_segment() for s in segs}
        self._patch_av(
            monkeypatch, make_ivs_av_open_fake_seq(output_path=out, segment_specs=specs)
        )
        assert _mux_ivs_segments(segs, [1.0] * len(segs), out, "test") is False

    def test_mixed_segment_skip_branches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One mux call traverses several skip arms: a normal segment (PIDs
        found + output opened), an open-error segment, a missing-PID segment,
        and a bad-packets segment (pts/dts None + corrupt). Completes without
        raising — every skip branch executed."""
        names = ["s_ok.ts", "s_open_err.ts", "s_missing.ts", "s_badpkt.ts"]
        segs = self._write_segments(tmp_path, names)
        out = tmp_path / "out.mp4"
        specs = {
            "s_ok.ts": normal_av_segment(),
            "s_open_err.ts": open_error_av_segment(),
            "s_missing.ts": missing_pid_av_segment(),
            "s_badpkt.ts": bad_packet_av_segment(),
        }
        self._patch_av(
            monkeypatch, make_ivs_av_open_fake_seq(output_path=out, segment_specs=specs)
        )
        assert isinstance(_mux_ivs_segments(segs, [1.0] * len(segs), out, "test"), bool)

    def test_packet_mux_exception_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """output.mux() raising OSError per packet is caught + counted, not
        fatal (the (OSError, FFmpegError) skip arm)."""
        names = ["s0.ts", "s1.ts"]
        segs = self._write_segments(tmp_path, names)
        out = tmp_path / "out.mp4"
        specs = {n: normal_av_segment() for n in names}
        self._patch_av(
            monkeypatch,
            make_ivs_av_open_fake_seq(
                output_path=out,
                segment_specs=specs,
                output_container_cls=FakeAvOutputContainerMuxError,
            ),
        )
        assert isinstance(_mux_ivs_segments(segs, [1.0] * len(segs), out, "test"), bool)

    def test_output_missing_streams_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify pass reports no streams → 'output missing streams' → False."""
        names = ["s0.ts"]
        segs = self._write_segments(tmp_path, names)
        out = tmp_path / "out.mp4"
        specs = {n: normal_av_segment() for n in names}
        self._patch_av(
            monkeypatch,
            make_ivs_av_open_fake_seq(
                output_path=out,
                segment_specs=specs,
                verify_container_cls=FakeAvVerifyContainerEmpty,
            ),
        )
        assert _mux_ivs_segments(segs, [1.0] * len(segs), out, "test") is False

    def test_probe_open_failure_then_pids_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """First probed segment fails to open (probe except → continue); the
        next segment yields the PIDs so the mux still proceeds."""
        segs = self._write_segments(tmp_path, ["s_open_err.ts", "s_ok.ts"])
        out = tmp_path / "out.mp4"
        specs = {
            "s_open_err.ts": open_error_av_segment(),
            "s_ok.ts": normal_av_segment(),
        }
        self._patch_av(
            monkeypatch, make_ivs_av_open_fake_seq(output_path=out, segment_specs=specs)
        )
        assert isinstance(_mux_ivs_segments(segs, [1.0] * len(segs), out, "test"), bool)

    def test_stop_event_interrupts_mux(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A set stop_event interrupts the mux loop before the first segment."""
        segs = self._write_segments(tmp_path, ["s0.ts"])
        out = tmp_path / "out.mp4"
        self._patch_av(
            monkeypatch,
            make_ivs_av_open_fake_seq(
                output_path=out, segment_specs={"s0.ts": normal_av_segment()}
            ),
        )
        stop = threading.Event()
        stop.set()
        assert _mux_ivs_segments(segs, [1.0] * len(segs), out, "test", stop) is False

    def test_codec_none_and_zero_duration_clean_mux(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A codec_context-None stream is skipped, zero-duration packets take the
        duration-falsy arm, and a no-skip mux emits the clean summary + verifies."""
        segs = self._write_segments(tmp_path, ["s_codec.ts", "s_zero.ts"])
        out = tmp_path / "out.mp4"
        specs = {
            "s_codec.ts": codec_none_av_segment(),
            "s_zero.ts": zero_duration_av_segment(),
        }
        self._patch_av(
            monkeypatch, make_ivs_av_open_fake_seq(output_path=out, segment_specs=specs)
        )
        assert _mux_ivs_segments(segs, [1.0] * len(segs), out, "test") is True

    def test_output_close_error_handled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """output.close() raising is logged, not fatal (the file is written first)."""
        segs = self._write_segments(tmp_path, ["s0.ts"])
        out = tmp_path / "out.mp4"
        self._patch_av(
            monkeypatch,
            make_ivs_av_open_fake_seq(
                output_path=out,
                segment_specs={"s0.ts": normal_av_segment()},
                output_container_cls=FakeAvOutputContainerCloseError,
            ),
        )
        assert isinstance(_mux_ivs_segments(segs, [1.0] * len(segs), out, "test"), bool)

    def test_output_empty_after_close_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """close() writing nothing → output missing/empty → False."""
        segs = self._write_segments(tmp_path, ["s0.ts"])
        out = tmp_path / "out.mp4"
        self._patch_av(
            monkeypatch,
            make_ivs_av_open_fake_seq(
                output_path=out,
                segment_specs={"s0.ts": normal_av_segment()},
                output_container_cls=FakeAvOutputContainerNoWrite,
            ),
        )
        assert _mux_ivs_segments(segs, [1.0] * len(segs), out, "test") is False

    def test_verify_open_raises_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The verify-pass av.open raising → has_video/has_audio False → False."""
        segs = self._write_segments(tmp_path, ["s0.ts"])
        out = tmp_path / "out.mp4"
        self._patch_av(
            monkeypatch,
            make_ivs_av_open_fake_seq(
                output_path=out,
                segment_specs={"s0.ts": normal_av_segment()},
                verify_container_cls=FakeAvVerifyContainerRaises,
            ),
        )
        assert _mux_ivs_segments(segs, [1.0] * len(segs), out, "test") is False

    def test_extra_codec_stream_routed_to_neither(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A third codec'd stream whose PID matches neither is ignored in PID
        matching and packet routing (834->826, 906->877)."""
        segs = self._write_segments(tmp_path, ["s0.ts"])
        out = tmp_path / "out.mp4"
        self._patch_av(
            monkeypatch,
            make_ivs_av_open_fake_seq(
                output_path=out, segment_specs={"s0.ts": extra_stream_av_segment()}
            ),
        )
        assert _mux_ivs_segments(segs, [1.0] * len(segs), out, "test") is True

    def test_output_open_failure_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """av.open(output, 'w') raising → mux-level except + output-None finally."""
        segs = self._write_segments(tmp_path, ["s0.ts"])
        out = tmp_path / "out.mp4"
        self._patch_av(
            monkeypatch,
            make_ivs_av_open_fake_seq(
                output_path=out,
                segment_specs={"s0.ts": normal_av_segment()},
                output_container_cls=FakeAvOutputContainerOpenError,
            ),
        )
        assert _mux_ivs_segments(segs, [1.0] * len(segs), out, "test") is False

    def test_under_25pct_skip_does_not_abort(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """1 of 5 segments skipped (20% < 25%) → warning but no abort (1023->1036)."""
        names = [f"s{i}.ts" for i in range(5)]
        segs = self._write_segments(tmp_path, names)
        out = tmp_path / "out.mp4"
        specs = {n: normal_av_segment() for n in names}
        specs["s4.ts"] = missing_pid_av_segment()
        self._patch_av(
            monkeypatch, make_ivs_av_open_fake_seq(output_path=out, segment_specs=specs)
        )
        assert isinstance(_mux_ivs_segments(segs, [6.0] * len(segs), out, "test"), bool)

    def test_skip_examples_cap_reached(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """>10 unusable packets overruns the skip-examples cap (887->892)."""
        segs = self._write_segments(tmp_path, ["s0.ts"])
        out = tmp_path / "out.mp4"
        self._patch_av(
            monkeypatch,
            make_ivs_av_open_fake_seq(
                output_path=out, segment_specs={"s0.ts": many_bad_packets_av_segment()}
            ),
        )
        assert isinstance(_mux_ivs_segments(segs, [6.0], out, "test"), bool)

    def test_segment_level_except_after_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bad-packet segment fills the skip-examples cap, then a demux-raising
        segment hits the segment-level except with the cap already reached
        (979->983); a normal segment first supplies the PIDs."""
        names = ["s_cap.ts", "s_demux_err.ts"]
        segs = self._write_segments(tmp_path, names)
        out = tmp_path / "out.mp4"
        specs = {
            "s_cap.ts": many_bad_packets_av_segment(),
            "s_demux_err.ts": demux_error_av_segment(),
        }
        self._patch_av(
            monkeypatch, make_ivs_av_open_fake_seq(output_path=out, segment_specs=specs)
        )
        assert isinstance(_mux_ivs_segments(segs, [6.0] * len(segs), out, "test"), bool)


_VARIANT_URL = "https://use14.playlist.live-video.net/variant.m3u8"


class TestPollSegmentsLoop:
    """Branch coverage for _poll_segments_loop via a single sequenced variant
    route (manifest-fetch failure → a window with a prefetch hint and one
    failing segment → ENDLIST), plus the pre-set-stop early exit."""

    @pytest.mark.asyncio
    async def test_preset_stop_returns_empty(
        self, respx_ivs_cdn: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        """stop_event already set → loop body never runs → empty result."""
        stop = asyncio.Event()
        stop.set()
        segments, durations = await _poll_segments_loop(
            _VARIANT_URL, tmp_path, stop, "test", manifest_poll_interval=0.0
        )
        assert segments == []
        assert durations == []

    @pytest.mark.asyncio
    async def test_full_poll_traverses_branches(
        self, respx_ivs_cdn: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        """One drive over a sequenced variant route hits: the manifest-fetch
        failure arm (retry+continue), a window where one segment download fails
        (skipped) while a normal segment + a prefetch hint succeed, and the
        ENDLIST terminal break."""
        seg_ok = "https://use14.playlist.live-video.net/seg_ok.ts"
        seg_fail = "https://use14.playlist.live-video.net/seg_fail.ts"
        prefetch = "https://use14.playlist.live-video.net/prefetch.ts"
        window = build_variant_playlist(
            media_sequence=0,
            segment_uris=[seg_ok, seg_fail],
            prefetch_uris=["", prefetch],  # "" exercises the empty-prefetch skip
        )
        endlist = build_variant_playlist(
            media_sequence=2, segment_uris=[], endlist=True
        )
        variant_route = respx.get(_VARIANT_URL).mock(
            side_effect=[
                httpx.Response(500),  # manifest fetch failure → continue
                httpx.Response(200, text=window),
                httpx.Response(200, text=endlist),  # → break
            ]
        )
        respx.get(seg_ok).mock(side_effect=[httpx.Response(200, content=b"ts")])
        respx.get(seg_fail).mock(side_effect=[httpx.Response(404)])  # download fails
        respx.get(prefetch).mock(side_effect=[httpx.Response(200, content=b"ts")])

        stop = asyncio.Event()
        try:
            segments, durations = await _poll_segments_loop(
                _VARIANT_URL, tmp_path, stop, "test", manifest_poll_interval=0.0
            )
        finally:
            dump_fansly_calls(variant_route.calls, "poll_variant_sequence")

        # seg_ok + prefetch downloaded; seg_fail skipped (404).
        assert len(segments) == 2
        assert len(durations) == 2
        assert variant_route.call_count == 3

    @pytest.mark.asyncio
    async def test_prefetch_whitespace_and_already_seen(
        self, respx_ivs_cdn: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        """A whitespace EXT-X-PREFETCH line strips to empty and is skipped (458);
        a prefetch whose MSN is already seen is deduped (461)."""
        seg = "https://use14.playlist.live-video.net/seg.ts"
        pf1 = "https://use14.playlist.live-video.net/pf1.ts"
        pf_low = "https://use14.playlist.live-video.net/pf_low.ts"
        window1 = build_variant_playlist(
            media_sequence=5, segment_uris=[seg], prefetch_uris=[pf1]
        )
        window2 = build_variant_playlist(
            media_sequence=0, segment_uris=[], prefetch_uris=["   ", pf_low]
        )
        endlist = build_variant_playlist(
            media_sequence=7, segment_uris=[], endlist=True
        )
        variant_route = respx.get(_VARIANT_URL).mock(
            side_effect=[
                httpx.Response(200, text=window1),
                httpx.Response(200, text=window2),
                httpx.Response(200, text=endlist),
            ]
        )
        respx.get(seg).mock(side_effect=[httpx.Response(200, content=b"ts")])
        respx.get(pf1).mock(side_effect=[httpx.Response(200, content=b"ts")])

        stop = asyncio.Event()
        try:
            segments, _ = await _poll_segments_loop(
                _VARIANT_URL, tmp_path, stop, "test", manifest_poll_interval=0.0
            )
        finally:
            dump_fansly_calls(variant_route.calls, "poll_prefetch_edges")

        # window1: seg(msn5) + pf1(msn6) downloaded → last_msn=6. window2's
        # whitespace prefetch is empty (skipped) and pf_low(msn1) is already seen.
        assert len(segments) == 2
        assert variant_route.call_count == 3


class TestSalvageOrphanSegments:
    """Branch coverage for _salvage_orphan_segments skip/edge arms (the happy
    salvage→mux→cleanup path is covered by the recorder integration tests)."""

    @staticmethod
    def _base(config_wired: FanslyConfig, tmp_path: Path) -> Path:
        config_wired.download_directory = tmp_path / "downloads"
        config_wired.temp_folder = tmp_path / "temp"
        config_wired.download_directory.mkdir(parents=True, exist_ok=True)
        config_wired.temp_folder.mkdir(parents=True, exist_ok=True)
        return _get_segments_base(config_wired)

    @pytest.mark.asyncio
    async def test_segments_base_unavailable_returns(
        self, config_wired: FanslyConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_get_segments_base raising RuntimeError → early return."""

        def _raise(_cfg: FanslyConfig) -> Path:
            raise RuntimeError("no segments base")

        monkeypatch.setattr("download.livestream._get_segments_base", _raise)
        await _salvage_orphan_segments(config_wired)  # must not raise

    @pytest.mark.asyncio
    async def test_no_orphan_dirs_returns(
        self, config_wired: FanslyConfig, tmp_path: Path
    ) -> None:
        """An empty base (no *_segments dirs) → return."""
        self._base(config_wired, tmp_path)
        await _salvage_orphan_segments(config_wired)

    @pytest.mark.asyncio
    async def test_sidecarless_dir_skipped(
        self, config_wired: FanslyConfig, tmp_path: Path
    ) -> None:
        """A *_segments dir with no output_path.txt sidecar is skipped, not removed."""
        base = self._base(config_wired, tmp_path)
        orphan = base / "nosidecar_segments"
        orphan.mkdir(parents=True)
        await _salvage_orphan_segments(config_wired)
        assert orphan.exists()

    @pytest.mark.asyncio
    async def test_existing_output_removes_orphan(
        self, config_wired: FanslyConfig, tmp_path: Path
    ) -> None:
        """A prior run already produced the output MP4 → orphan dir removed."""
        base = self._base(config_wired, tmp_path)
        assert config_wired.download_directory is not None
        orphan = base / "done_segments"
        orphan.mkdir(parents=True)
        out = config_wired.download_directory / "done.mp4"
        out.write_bytes(b"already-muxed")
        (orphan / "output_path.txt").write_text(str(out), encoding="utf-8")
        await _salvage_orphan_segments(config_wired)
        assert not orphan.exists()

    @pytest.mark.asyncio
    async def test_empty_orphan_dir_removed(
        self, config_wired: FanslyConfig, tmp_path: Path
    ) -> None:
        """An orphan dir with a sidecar but no segment_*.ts files is removed."""
        base = self._base(config_wired, tmp_path)
        assert config_wired.download_directory is not None
        orphan = base / "empty_segments"
        orphan.mkdir(parents=True)
        out = config_wired.download_directory / "empty.mp4"
        (orphan / "output_path.txt").write_text(str(out), encoding="utf-8")
        await _salvage_orphan_segments(config_wired)
        assert not orphan.exists()

    @pytest.mark.asyncio
    async def test_preset_stop_preserves_orphans(
        self, config_wired: FanslyConfig, tmp_path: Path
    ) -> None:
        """stop_event set before the loop → interrupted, orphan preserved (also
        exercises the stop→threading.Event bridge task + its cancel)."""
        base = self._base(config_wired, tmp_path)
        assert config_wired.download_directory is not None
        orphan = base / "interrupt_segments"
        orphan.mkdir(parents=True)
        out = config_wired.download_directory / "interrupt.mp4"
        (orphan / "output_path.txt").write_text(str(out), encoding="utf-8")
        (orphan / "segment_000000.ts").write_bytes(b"ts")
        stop = asyncio.Event()
        stop.set()
        await _salvage_orphan_segments(config_wired, stop)
        assert orphan.exists()

    @pytest.mark.asyncio
    async def test_mux_failure_preserves_orphan(
        self,
        config_wired: FanslyConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A salvage mux that fails (verify reports no streams) preserves the
        orphan dir for a later retry."""
        base = self._base(config_wired, tmp_path)
        assert config_wired.download_directory is not None
        orphan = base / "fail_segments"
        orphan.mkdir(parents=True)
        out = config_wired.download_directory / "fail.mp4"
        (orphan / "output_path.txt").write_text(str(out), encoding="utf-8")
        seg = orphan / "segment_000000.ts"
        seg.write_bytes(b"ts")

        monkeypatch.setattr(
            "download.livestream.av.open",
            make_ivs_av_open_fake_seq(
                output_path=out,
                segment_specs={seg.name: normal_av_segment()},
                verify_container_cls=FakeAvVerifyContainerEmpty,
            ),
        )
        monkeypatch.setattr("download.livestream.VideoStream", FakeVideoStream)
        monkeypatch.setattr("download.livestream.AudioStream", FakeAudioStream)

        await _salvage_orphan_segments(config_wired)
        assert orphan.exists()

    @pytest.mark.asyncio
    async def test_bridge_sets_mux_stop(
        self,
        config_wired: FanslyConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The stop→threading.Event bridge fires when the asyncio stop is set
        during the (inlined) mux: _bridge's wait resumes and runs mux_stop.set()
        (551-552). to_thread is inlined so the asyncio Event is set on the loop
        thread, then a yield lets the bridge task run."""
        base = self._base(config_wired, tmp_path)
        assert config_wired.download_directory is not None
        orphan = base / "bridge_segments"
        orphan.mkdir(parents=True)
        out = config_wired.download_directory / "bridge.mp4"
        (orphan / "output_path.txt").write_text(str(out), encoding="utf-8")
        (orphan / "segment_000000.ts").write_bytes(b"ts")
        stop = asyncio.Event()

        async def _inline_to_thread(
            fn: Callable[..., object], *a: object, **k: object
        ) -> object:
            result = fn(*a, **k)
            await asyncio.sleep(0)  # let the bridge task resume now stop is set
            return result

        def _mux_sets_stop(*_a: object, **_k: object) -> bool:
            stop.set()
            return True

        monkeypatch.setattr("download.livestream.asyncio.to_thread", _inline_to_thread)
        monkeypatch.setattr("download.livestream._mux_ivs_segments", _mux_sets_stop)

        await _salvage_orphan_segments(config_wired, stop)
        assert stop.is_set()


async def _anoop(*_a: object, **_k: object) -> None:
    """An async no-op (stand-in for asyncio.sleep / _chat_ws_loop)."""


async def _await_forever(*_a: object, **_k: object) -> None:
    """Block until cancelled — a chat loop the recorder's finally must cancel."""
    await asyncio.Event().wait()


def _seq(values: list) -> Callable[..., Awaitable[object]]:
    """Build an async callable that pops the next value from *values* per call."""

    async def _call(*_a: object, **_k: object) -> object:
        return values.pop(0)

    return _call


def _always(value: object) -> Callable[..., Awaitable[object]]:
    """Build an async callable that returns *value* on every call."""

    async def _call(*_a: object, **_k: object) -> object:
        return value

    return _call


class TestRecordStream:
    """Control-flow branch coverage for the _record_stream orchestrator.

    The happy full-broadcast path is covered by the recorder integration tests.
    Here the internal collaborators (_get_authenticated_playback_url,
    _resolve_variant_url, _poll_segments_loop, _mux_ivs_segments) are sequenced
    at the download.livestream binding to drive the retry/break/chat arms, and
    asyncio.sleep is no-op'd so the retry loop runs instantly.
    """

    @staticmethod
    def _setup(config_wired: FanslyConfig, tmp_path: Path) -> None:
        config_wired.download_directory = tmp_path / "downloads"
        config_wired.temp_folder = tmp_path / "temp"
        config_wired.download_directory.mkdir(parents=True, exist_ok=True)
        config_wired.temp_folder.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _channel(creator_id: int, *, chat_room_id: int | None = None) -> StreamChannel:
        return StreamChannel.model_validate(
            {
                "id": creator_id + 1,
                "accountId": creator_id,
                "status": 2,
                "playbackUrl": "https://x.playback.live-video.net/master.m3u8",
                "chatRoomId": chat_room_id,
                "stream": {
                    "id": creator_id + 2,
                    "channelId": creator_id + 1,
                    "accountId": creator_id,
                    "status": 2,
                },
            }
        )

    @pytest.mark.asyncio
    async def test_early_break_on_stop(
        self, config_wired: FanslyConfig, tmp_path: Path
    ) -> None:
        """rec_stop set before the loop → break on the first attempt check."""
        self._setup(config_wired, tmp_path)
        cid = snowflake_id()
        rec_stop = asyncio.Event()
        rec_stop.set()
        await _record_stream(
            config_wired, cid, "u", self._channel(cid), rec_stop, asyncio.Event()
        )

    @pytest.mark.asyncio
    async def test_retry_chain_auth_variant_nosegments(
        self,
        config_wired: FanslyConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """3 attempts: auth-None retry → variant-None retry → no-segments."""
        self._setup(config_wired, tmp_path)
        cid = snowflake_id()
        monkeypatch.setattr("download.livestream.asyncio.sleep", _anoop)
        monkeypatch.setattr(
            "download.livestream._get_authenticated_playback_url",
            _seq([None, "auth", "auth"]),
        )
        monkeypatch.setattr(
            "download.livestream._resolve_variant_url", _seq([None, "variant"])
        )
        monkeypatch.setattr("download.livestream._poll_segments_loop", _seq([([], [])]))
        await _record_stream(
            config_wired, cid, "u", self._channel(cid), asyncio.Event(), asyncio.Event()
        )

    @pytest.mark.asyncio
    async def test_mux_fail_then_success(
        self,
        config_wired: FanslyConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """attempt 1 muxes to False (retry), attempt 2 succeeds → cleanup+break."""
        self._setup(config_wired, tmp_path)
        cid = snowflake_id()
        seg = tmp_path / "segment_000000.ts"
        seg.write_bytes(b"ts")
        monkeypatch.setattr("download.livestream.asyncio.sleep", _anoop)
        monkeypatch.setattr(
            "download.livestream._get_authenticated_playback_url", _seq(["a", "a"])
        )
        monkeypatch.setattr(
            "download.livestream._resolve_variant_url", _seq(["v", "v"])
        )
        monkeypatch.setattr(
            "download.livestream._poll_segments_loop",
            _seq([([seg], [6.0]), ([seg], [6.0])]),
        )
        mux_results = [False, True]
        monkeypatch.setattr(
            "download.livestream._mux_ivs_segments",
            lambda *_a, **_k: mux_results.pop(0),
        )
        await _record_stream(
            config_wired, cid, "u", self._channel(cid), asyncio.Event(), asyncio.Event()
        )

    @pytest.mark.asyncio
    async def test_chat_path_registers_promotes_unregisters(
        self,
        config_wired: FanslyConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """chatRoomId set → recorder registered, chat task spawned, chat.jsonl
        promoted next to the MP4 on success, recorder unregistered in finally."""
        self._setup(config_wired, tmp_path)
        cid = snowflake_id()
        seg = tmp_path / "segment_000000.ts"
        seg.write_bytes(b"ts")

        async def _poll_writes_chat(
            _url: str, temp_dir: Path, *_a: object, **_k: object
        ) -> tuple[list[Path], list[float]]:
            (temp_dir / "chat.jsonl").write_text('{"m":1}\n', encoding="utf-8")
            return ([seg], [6.0])

        monkeypatch.setattr("download.livestream.asyncio.sleep", _anoop)
        monkeypatch.setattr("download.livestream._chat_ws_loop", _await_forever)
        monkeypatch.setattr(
            "download.livestream._get_authenticated_playback_url", _seq(["a"])
        )
        monkeypatch.setattr("download.livestream._resolve_variant_url", _seq(["v"]))
        monkeypatch.setattr(
            "download.livestream._poll_segments_loop", _poll_writes_chat
        )
        monkeypatch.setattr(
            "download.livestream._mux_ivs_segments", lambda *_a, **_k: True
        )

        await _record_stream(
            config_wired,
            cid,
            "chatuser",
            self._channel(cid, chat_room_id=snowflake_id()),
            asyncio.Event(),
            asyncio.Event(),
        )

    @pytest.mark.asyncio
    async def test_segments_base_unavailable_falls_back(
        self,
        config_wired: FanslyConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_get_segments_base raising RuntimeError → output_path.parent fallback;
        rec_stop set so the loop then breaks immediately."""
        self._setup(config_wired, tmp_path)

        def _raise(_cfg: FanslyConfig) -> Path:
            raise RuntimeError("no segments base")

        monkeypatch.setattr("download.livestream._get_segments_base", _raise)
        rec_stop = asyncio.Event()
        rec_stop.set()
        cid = snowflake_id()
        await _record_stream(
            config_wired, cid, "u", self._channel(cid), rec_stop, asyncio.Event()
        )

    @pytest.mark.asyncio
    async def test_all_attempts_mux_fail(
        self,
        config_wired: FanslyConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Every attempt captures segments but muxes to False → retry warnings on
        the first attempts and the all-attempts-failed error on the last."""
        self._setup(config_wired, tmp_path)
        cid = snowflake_id()
        seg = tmp_path / "segment_000000.ts"
        seg.write_bytes(b"ts")
        monkeypatch.setattr("download.livestream.asyncio.sleep", _anoop)
        monkeypatch.setattr(
            "download.livestream._get_authenticated_playback_url", _always("a")
        )
        monkeypatch.setattr("download.livestream._resolve_variant_url", _always("v"))
        monkeypatch.setattr(
            "download.livestream._poll_segments_loop", _always(([seg], [6.0]))
        )
        monkeypatch.setattr(
            "download.livestream._mux_ivs_segments", lambda *_a, **_k: False
        )
        await _record_stream(
            config_wired, cid, "u", self._channel(cid), asyncio.Event(), asyncio.Event()
        )

    @pytest.mark.asyncio
    async def test_all_attempts_auth_fail(
        self,
        config_wired: FanslyConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """auth None on every attempt → the last-attempt no-sleep arm (142->144)."""
        self._setup(config_wired, tmp_path)
        cid = snowflake_id()
        monkeypatch.setattr("download.livestream.asyncio.sleep", _anoop)
        monkeypatch.setattr(
            "download.livestream._get_authenticated_playback_url", _always(None)
        )
        await _record_stream(
            config_wired, cid, "u", self._channel(cid), asyncio.Event(), asyncio.Event()
        )

    @pytest.mark.asyncio
    async def test_all_attempts_variant_fail(
        self,
        config_wired: FanslyConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """variant None on every attempt → the last-attempt no-sleep arm (156->158)."""
        self._setup(config_wired, tmp_path)
        cid = snowflake_id()
        monkeypatch.setattr("download.livestream.asyncio.sleep", _anoop)
        monkeypatch.setattr(
            "download.livestream._get_authenticated_playback_url", _always("a")
        )
        monkeypatch.setattr("download.livestream._resolve_variant_url", _always(None))
        await _record_stream(
            config_wired, cid, "u", self._channel(cid), asyncio.Event(), asyncio.Event()
        )

    @pytest.mark.asyncio
    async def test_nosegments_then_success(
        self,
        config_wired: FanslyConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """no-segments on attempt 1 takes the retry-sleep arm (229); attempt 2 wins."""
        self._setup(config_wired, tmp_path)
        cid = snowflake_id()
        seg = tmp_path / "segment_000000.ts"
        seg.write_bytes(b"ts")
        monkeypatch.setattr("download.livestream.asyncio.sleep", _anoop)
        monkeypatch.setattr(
            "download.livestream._get_authenticated_playback_url", _always("a")
        )
        monkeypatch.setattr("download.livestream._resolve_variant_url", _always("v"))
        monkeypatch.setattr(
            "download.livestream._poll_segments_loop", _seq([([], []), ([seg], [6.0])])
        )
        monkeypatch.setattr(
            "download.livestream._mux_ivs_segments", lambda *_a, **_k: True
        )
        await _record_stream(
            config_wired, cid, "u", self._channel(cid), asyncio.Event(), asyncio.Event()
        )

    @pytest.mark.asyncio
    async def test_forwarder_completes_and_chat_task_failure_logged(
        self,
        config_wired: FanslyConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_poll sets global_stop and yields so _forward_stops wakes and sets
        combined_stop (177); the (raising) chat task's done-callback logs the
        crash (186-189)."""
        self._setup(config_wired, tmp_path)
        cid = snowflake_id()
        seg = tmp_path / "segment_000000.ts"
        seg.write_bytes(b"ts")
        global_stop = asyncio.Event()

        async def _raise_chat(*_a: object, **_k: object) -> None:
            raise RuntimeError("chat ws crashed")

        async def _poll(
            _url: str, _temp_dir: Path, _combined: object, *_a: object, **_k: object
        ) -> tuple[list[Path], list[float]]:
            global_stop.set()  # wakes _forward_stops via global_stop.wait()
            # Real yield (asyncio.sleep is NOT patched here) so the forwarder's
            # asyncio.wait resolves and the chat task raises + fires its callback.
            # Returning segments → mux success → break before any retry sleep.
            await asyncio.sleep(0.05)
            return ([seg], [6.0])

        monkeypatch.setattr("download.livestream._chat_ws_loop", _raise_chat)
        monkeypatch.setattr(
            "download.livestream._get_authenticated_playback_url", _always("a")
        )
        monkeypatch.setattr("download.livestream._resolve_variant_url", _always("v"))
        monkeypatch.setattr("download.livestream._poll_segments_loop", _poll)
        monkeypatch.setattr(
            "download.livestream._mux_ivs_segments", lambda *_a, **_k: True
        )

        await _record_stream(
            config_wired,
            cid,
            "u",
            self._channel(cid, chat_room_id=snowflake_id()),
            asyncio.Event(),
            global_stop,
        )


class TestPathioLivestreamHelpers:
    """pathio/livestream.py path-building branches."""

    def test_get_livestreams_dir_requires_download_directory(
        self, config_wired: FanslyConfig
    ) -> None:
        """pathio:15 — download_directory unset raises RuntimeError."""
        config_wired.download_directory = None
        with pytest.raises(RuntimeError, match="download_directory is not set"):
            _get_livestreams_dir(config_wired, "creator")

    def test_get_segments_base_falls_back_to_download_temp(
        self, config_wired: FanslyConfig, tmp_path: Path
    ) -> None:
        """pathio:34 — temp_folder unset → ``<download_directory>/temp``."""
        config_wired.temp_folder = None
        config_wired.download_directory = tmp_path / "dl"
        assert _get_segments_base(config_wired) == tmp_path / "dl" / "temp"

    def test_get_segments_base_requires_download_directory_without_temp(
        self, config_wired: FanslyConfig
    ) -> None:
        """pathio:32-33 — neither temp_folder nor download_directory set raises."""
        config_wired.temp_folder = None
        config_wired.download_directory = None
        with pytest.raises(RuntimeError, match="download_directory is not set"):
            _get_segments_base(config_wired)


class TestFileioLivestreamHelpers:
    """fileio/livestream.py output-slot selection."""

    def test_unique_output_path_skips_taken_slots(self, tmp_path: Path) -> None:
        """fileio:25, 31-36 — a taken base and taken ``_part2`` → first free ``_part3``.

        A slot is taken when the MP4 exists with data (the ``exists() and
        st_size > 0`` arc, line 25); the loop advances past each taken variant
        (the ``n += 1`` at line 36) until it finds a free one.
        """
        segments_base = tmp_path / "temp"
        segments_base.mkdir()
        base = tmp_path / "rec.mp4"
        base.write_bytes(b"data")  # exists + size>0 → _taken True (line 25)
        (tmp_path / "rec_part2.mp4").write_bytes(
            b"data"
        )  # taken → loop again (line 36)

        result = _unique_output_path(base, segments_base)

        assert result == tmp_path / "rec_part3.mp4"
