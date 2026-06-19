"""High-level IVS-broadcast fixtures.

Layered on top of ``respx_fansly_api`` (Fansly REST routing) and
``respx_ivs_cdn`` (IVS host routing). One call to :func:`wire_ivs_stream`
registers all respx routes a complete recording flow needs: the
authenticated-master fetch on Fansly, the IVS master playlist, the
sliding-window variant manifest sequence, and per-segment routes for
every segment the recorder will end up requesting.

**No chat WS support here by design.** When a test exercises the
livestream chat path (``download/livestream_chat.py``), reuse the
existing :class:`FakeFanslyWSServer` via the ``ws_server`` fixture —
it has a full push toolkit (``push_service_event``, ``push_batch``,
20+ domain-specific push_* helpers) and a real websockets server on
localhost. See the :func:`wire_ivs_stream` ``chat_room_id`` docstring
for the production-side wiring constraint.

Usage::

    async def test_record_full_broadcast(
        respx_fansly_api,
        respx_ivs_cdn,  # noqa: ARG001  (active respx context)
        config_wired,
        tmp_path,
    ):
        stream = wire_ivs_stream(creator_id=100_000, total_segments=5)
        ...
        try:
            await _record_stream(config_wired, stream.creator_id, ...)
        finally:
            dump_fansly_calls(stream.streaming_channel_route.calls, "stream-ch")
            dump_fansly_calls(stream.variant_route.calls, "variant")

        stream.assert_all_segments_fetched()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urljoin

import httpx
import respx

from tests.fixtures.api.livestream_factories import (
    DEFAULT_WINDOW_SIZE,
    IVS_MASTER_HOST,
    IVS_VARIANT_HOST,
    SlidingWindowScenario,
    build_master_playlist,
    build_streaming_channel_response,
    make_sliding_window_scenario,
)


# ── Default fake TS payload ────────────────────────────────────────────────

# Production's _download_segment writes bytes to disk; the bytes are then
# fed to PyAV via av.open. Tests that mock av at the leaf only need
# non-empty bytes to satisfy the write-to-disk path. Tests that exercise
# the real mux step need actual TS-shaped data — provide their own.
DEFAULT_FAKE_TS_BYTES = b"FAKE_TS_SEGMENT_PAYLOAD"


# ── IvsStreamFixture handle ────────────────────────────────────────────────


@dataclass(slots=True)
class IvsStreamFixture:
    """Bundle of respx routes + URLs for one IVS broadcast scenario.

    Returned by :func:`wire_ivs_stream`. Tests interact with the route
    handles to assert call counts / dump request details, and with the
    URL strings + creator_id when seeding production state.
    """

    creator_id: int
    username: str
    master_url: str
    variant_url: str
    streaming_channel_route: respx.Route
    master_route: respx.Route
    variant_route: respx.Route
    segment_routes: list[respx.Route]
    scenario: SlidingWindowScenario
    all_routes: list[respx.Route] = field(default_factory=list)

    def assert_all_segments_fetched(self) -> None:
        """Verify every per-segment route was called at least once.

        Production dedupes on MSN so each segment is fetched exactly once
        across the recording lifecycle. A segment route that's never
        called means the recorder stopped before consuming that
        manifest snapshot — usually a stop-event timing issue.
        """
        misses = [i for i, route in enumerate(self.segment_routes) if not route.called]
        assert not misses, (
            f"{len(misses)}/{len(self.segment_routes)} segment routes never "
            f"called (indices: {misses[:10]}{'...' if len(misses) > 10 else ''})"
        )

    @property
    def total_route_calls(self) -> int:
        """Sum of route.call_count across every wired route — useful for
        smoke-asserting that the broadcast ran end-to-end."""
        return sum(r.call_count for r in self.all_routes)


# ── Wiring entry point ─────────────────────────────────────────────────────


def wire_ivs_stream(
    *,
    creator_id: int = 100_000,
    username: str = "ivs_user",
    total_segments: int = 5,
    window_size: int = DEFAULT_WINDOW_SIZE,
    segment_bytes: bytes = DEFAULT_FAKE_TS_BYTES,
    chat_room_id: int | None = None,
    master_attempts: int = 1,
    extra_variant_manifests: list[str] | None = None,
) -> IvsStreamFixture:
    """Register every respx route needed for one full recording cycle.

    Must be called from inside a test that has both ``respx_fansly_api``
    and ``respx_ivs_cdn`` fixtures active (so the respx mock context is
    open). The routes register against the global respx router.

    Args:
        creator_id: Creator the broadcast belongs to. Used as the host
            prefix on the master URL and as the channel/stream id seed.
        username: Account username — surfaces in followingstreams/online
            payloads if the test also wires that endpoint.
        total_segments: Number of distinct segments the broadcast will
            expose across all manifest snapshots.
        window_size: Sliding-window size (max segments visible per
            manifest fetch).
        segment_bytes: Bytes returned by every segment route. The same
            payload covers every segment — production writes them to
            ``segment_NNNNNN.ts`` files which a downstream test step
            then either real-muxes (needs valid TS bytes) or
            leaf-mocks (any non-empty bytes work).
        chat_room_id: Optional chat-room id. When None (default), the
            recorder skips the chat WS branch. Tests that exercise the
            chat path should reuse the existing :class:`FakeFanslyWSServer`
            via the ``ws_server`` fixture — it already exposes
            ``push_service_event(serviceId=46, type=10, ...)``,
            ``push_batch``, and a real websockets server on localhost,
            so a separate chat-WS fixture is not needed. The wiring
            constraint to keep in mind: production ``_chat_ws_loop``
            (``download/livestream_chat.py:114``) constructs
            ``FanslyWebSocket()`` itself rather than accepting a
            ``ws_factory`` argument, so tests must either monkeypatch
            the constructor's default URL or refactor that loop to
            accept a factory (mirrors the daemon runner pattern).
        master_attempts: Number of streaming-channel + master-playlist
            response pairs to pre-register. Default 1 (happy path);
            bump for tests exercising the retry-on-failure loop.
        extra_variant_manifests: Additional variant manifests appended
            after the sliding-window scenario's own manifests. Useful
            for forcing extra poll iterations between specific events.

    Returns:
        :class:`IvsStreamFixture` with route handles wired and ready.
    """
    master_url = (
        f"https://abc{creator_id}.us-east-1.{IVS_MASTER_HOST}"
        f"/api/video/v1/us-east-1.fake.channel.{creator_id}.m3u8"
        f"?token=FAKE_JWT_{creator_id}"
    )
    variant_url = f"https://{IVS_VARIANT_HOST}/v1/v1/{creator_id}/master.m3u8"

    scenario = make_sliding_window_scenario(
        total_segments=total_segments,
        window_size=window_size,
    )
    if extra_variant_manifests:
        scenario = SlidingWindowScenario(
            manifests=[*scenario.manifests, *extra_variant_manifests],
            segment_uris_in_order=scenario.segment_uris_in_order,
        )

    # Segment URLs in the manifest are relative; production resolves them
    # against variant_url via urljoin (livestream.py:428).
    segment_absolute_urls = [
        urljoin(variant_url, uri) for uri in scenario.segment_uris_in_order
    ]

    # ── 1. Fansly streaming-channel REST endpoint ─────────────────────────
    streaming_payload = build_streaming_channel_response(
        creator_id=creator_id,
        master_url=master_url,
        chat_room_id=chat_room_id,
    )
    streaming_channel_route = respx.get(
        url__startswith=(
            f"https://apiv3.fansly.com/api/v1/streaming/channel/{creator_id}"
        )
    ).mock(
        side_effect=[
            httpx.Response(200, json=streaming_payload) for _ in range(master_attempts)
        ]
    )

    # ── 2. IVS master playlist ────────────────────────────────────────────
    # Match by URL prefix (ignoring the ?token= JWT) so per-attempt token
    # rotations don't require fresh routes.
    master_text = build_master_playlist(variant_url=variant_url)
    master_route = respx.get(url__startswith=master_url.split("?", 1)[0]).mock(
        side_effect=[
            httpx.Response(200, text=master_text) for _ in range(master_attempts)
        ]
    )

    # ── 3. IVS variant playlist (multi-response sliding window) ───────────
    variant_route = respx.get(variant_url).mock(
        side_effect=[
            httpx.Response(200, text=manifest_text)
            for manifest_text in scenario.manifests
        ]
    )

    # ── 4. Per-segment routes ─────────────────────────────────────────────
    segment_routes = [
        respx.get(seg_url).mock(
            side_effect=[httpx.Response(200, content=segment_bytes)]
        )
        for seg_url in segment_absolute_urls
    ]

    return IvsStreamFixture(
        creator_id=creator_id,
        username=username,
        master_url=master_url,
        variant_url=variant_url,
        streaming_channel_route=streaming_channel_route,
        master_route=master_route,
        variant_route=variant_route,
        segment_routes=segment_routes,
        scenario=scenario,
        all_routes=[
            streaming_channel_route,
            master_route,
            variant_route,
            *segment_routes,
        ],
    )


# ── PyAV fakes for the IVS mux step ────────────────────────────────────────
#
# Production ``_mux_ivs_segments`` uses ``isinstance(stream, VideoStream)`` /
# ``isinstance(stream, AudioStream)`` PID-discovery (livestream.py:674-679).
# To satisfy those checks, tests monkeypatch the imported ``VideoStream`` /
# ``AudioStream`` symbols in ``download.livestream`` to point at these
# fake classes — production's isinstance check then succeeds against the
# fake instances. Per the mocking-boundary rules these are leaf fakes for
# the ``av`` library (the lib's own stream classes are Cython-backed and
# not easily subclassable), not internal mocks.


class FakeVideoStream:
    """Fake ``av.VideoStream`` — id is the MPEG-TS PID."""

    def __init__(self, *, pid: int = 0x100, codec: str = "h264") -> None:
        self.id = pid
        self.type = "video"
        self.codec_context = SimpleNamespace(name=codec)


class FakeAudioStream:
    """Fake ``av.AudioStream`` — id is the MPEG-TS PID."""

    def __init__(self, *, pid: int = 0x101, codec: str = "aac") -> None:
        self.id = pid
        self.type = "audio"
        self.codec_context = SimpleNamespace(name=codec)


@dataclass(slots=True)
class FakeAvPacket:
    """Fake ``av.Packet`` — assignable pts/dts + stream pointer."""

    stream: Any
    pts: int
    dts: int
    duration: int = 1
    is_corrupt: bool = False


class FakeAvInputContainer:
    """Fake input container — exposes streams + demux for the mux loop."""

    def __init__(
        self,
        *,
        video_stream: FakeVideoStream,
        audio_stream: FakeAudioStream,
        packets_per_stream: int = 3,
    ) -> None:
        self.streams = [video_stream, audio_stream]
        self._packets_per_stream = packets_per_stream
        self._video_stream = video_stream
        self._audio_stream = audio_stream
        self.closed = False

    def demux(self, *_streams: Any) -> Any:
        """Yield interleaved video+audio packets with monotonic PTS.

        Production passes the input video + audio streams as args;
        the fake ignores them (the packet.stream field carries the
        routing target) and yields a fixed-shape packet sequence.
        """
        packets: list[FakeAvPacket] = []
        for i in range(self._packets_per_stream):
            packets.append(
                FakeAvPacket(stream=self._video_stream, pts=i, dts=i, duration=1)
            )
            packets.append(
                FakeAvPacket(stream=self._audio_stream, pts=i, dts=i, duration=1)
            )
        return iter(packets)

    def close(self) -> None:
        self.closed = True


class FakeAvOutputContainer:
    """Fake output container — records mux calls + writes a stub MP4 on close.

    The write-to-disk on close means production's
    ``output_path.stat().st_size > 0`` verify check (livestream.py:853)
    succeeds against a real file, not a mock — same pattern as the
    existing ``_FakeOutputContainer`` in tests/download/unit/test_m3u8.py.
    """

    _STUB_MP4_BYTES = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 1024

    def __init__(self, output_path: Path) -> None:
        self._output_path = Path(output_path)
        self.template_streams: list[Any] = []
        self.mux_calls: list[FakeAvPacket] = []
        self.closed = False

    def add_stream_from_template(self, src_stream: Any) -> Any:
        # Production assigns the returned stream as the routing target
        # via ``packet.stream = output_video_stream``; the fake just
        # echoes a placeholder so identity comparisons work.
        new_stream = SimpleNamespace(
            id=src_stream.id,
            codec_context=src_stream.codec_context,
        )
        self.template_streams.append(new_stream)
        return new_stream

    def mux(self, packet: FakeAvPacket) -> None:
        self.mux_calls.append(packet)

    def close(self) -> None:
        if not self.closed:
            self._output_path.write_bytes(self._STUB_MP4_BYTES)
            self.closed = True


class FakeAvVerifyContainer:
    """Fake verify-pass container — reports has_video + has_audio."""

    def __init__(self) -> None:
        self.streams = [
            SimpleNamespace(type="video"),
            SimpleNamespace(type="audio"),
        ]

    def close(self) -> None:
        pass


def make_ivs_av_open_fake(
    *,
    output_path: Path,
    video_pid: int = 0x100,
    audio_pid: int = 0x101,
    packets_per_stream: int = 3,
) -> Any:
    """Build an ``av.open`` replacement covering probe + mux + verify.

    Pattern-matches on the path argument:

    - ``av.open(<output_path>, "w", ...)`` → :class:`FakeAvOutputContainer`
      (the per-recording output sink).
    - ``av.open(<output_path>)`` (no mode) → :class:`FakeAvVerifyContainer`
      (the post-mux verify pass).
    - Any other path → :class:`FakeAvInputContainer` (a segment file —
      either during probe or during mux iteration).

    The returned callable is intended for ``monkeypatch.setattr(av, "open",
    make_ivs_av_open_fake(output_path=...))``.
    """
    output_path_str = str(output_path)

    def _fake_open(path: Any, mode: str = "r", **_kwargs: Any) -> Any:
        if str(path) == output_path_str and "w" in mode:
            return FakeAvOutputContainer(Path(path))
        if str(path) == output_path_str:
            return FakeAvVerifyContainer()
        # Segment file — fresh input container with the configured streams.
        # Each call gets its own instance so close() tracking is per-call.
        return FakeAvInputContainer(
            video_stream=FakeVideoStream(pid=video_pid),
            audio_stream=FakeAudioStream(pid=audio_pid),
            packets_per_stream=packets_per_stream,
        )

    return _fake_open


# ── Per-segment failure-injection toolkit (mux branch coverage) ─────────────
#
# make_ivs_av_open_fake is success-only. These let a single _mux_ivs_segments
# call traverse multiple skip branches by giving each segment FILE its own
# behavior (open error, missing PID, unusable packets), keyed on filename.


class _FakeDataStream:
    """A non-video/non-audio stream (the IVS data PID) — never matches a PID."""

    def __init__(self, *, pid: int = 0x102) -> None:
        self.id = pid
        self.type = "data"
        self.codec_context = SimpleNamespace(name="bin_data")


class FakeAvInputContainerCustom:
    """Input container with caller-supplied streams + packet sequence.

    ``demux_error``, when set, is raised from ``demux()`` to drive the
    segment-level except in _mux_ivs_segments.
    """

    def __init__(
        self,
        *,
        streams: list[Any],
        packets: list[Any],
        demux_error: BaseException | None = None,
    ) -> None:
        self.streams = streams
        self._packets = packets
        self._demux_error = demux_error
        self.closed = False

    def demux(self, *_streams: Any) -> Any:
        if self._demux_error is not None:
            raise self._demux_error
        return iter(self._packets)

    def close(self) -> None:
        self.closed = True


class FakeAvOutputContainerMuxError(FakeAvOutputContainer):
    """Output container whose mux() raises OSError — drives the per-packet
    (OSError, av.error.FFmpegError) skip branch in _mux_ivs_segments."""

    def mux(self, packet: Any) -> None:
        raise OSError("fake mux rejection")


class FakeAvVerifyContainerEmpty:
    """Verify-pass container reporting NO streams — drives the 'output missing
    streams' failure arm."""

    def __init__(self) -> None:
        self.streams: list[Any] = []

    def close(self) -> None:
        pass


@dataclass(slots=True)
class IvsMuxSegmentSpec:
    """How a faked segment behaves when av.open'd during mux.

    streams: the input container's streams. packets: what demux yields.
    open_error: when set, av.open(this segment) raises it.
    """

    streams: list[Any]
    packets: list[Any]
    open_error: BaseException | None = None
    demux_error: BaseException | None = None


def normal_av_segment(
    *, video_pid: int = 0x100, audio_pid: int = 0x101, n_packets: int = 3
) -> IvsMuxSegmentSpec:
    """A well-formed segment: matched video+audio streams + clean packets."""
    video = FakeVideoStream(pid=video_pid)
    audio = FakeAudioStream(pid=audio_pid)
    packets: list[Any] = []
    for i in range(n_packets):
        packets.append(FakeAvPacket(stream=video, pts=i, dts=i, duration=1))
        packets.append(FakeAvPacket(stream=audio, pts=i, dts=i, duration=1))
    return IvsMuxSegmentSpec(streams=[video, audio], packets=packets)


def data_only_av_segment(*, pid: int = 0x102) -> IvsMuxSegmentSpec:
    """A segment with only a data stream — no video/audio PID to discover."""
    return IvsMuxSegmentSpec(streams=[_FakeDataStream(pid=pid)], packets=[])


def missing_pid_av_segment(*, present_pid: int = 0x100) -> IvsMuxSegmentSpec:
    """A segment whose only matchable stream is video (audio PID absent) →
    the 'segment missing video/audio — skipping' branch."""
    return IvsMuxSegmentSpec(streams=[FakeVideoStream(pid=present_pid)], packets=[])


def bad_packet_av_segment(
    *, video_pid: int = 0x100, audio_pid: int = 0x101
) -> IvsMuxSegmentSpec:
    """Matched streams whose packets are all unusable: pts None, dts None, and
    corrupt — drives the per-packet skip cluster (and its sub-counters)."""
    video = FakeVideoStream(pid=video_pid)
    audio = FakeAudioStream(pid=audio_pid)
    packets = [
        FakeAvPacket(stream=video, pts=None, dts=0, duration=1),  # pts None
        FakeAvPacket(stream=audio, pts=0, dts=None, duration=1),  # dts None
        FakeAvPacket(stream=video, pts=0, dts=0, duration=1, is_corrupt=True),
    ]
    return IvsMuxSegmentSpec(streams=[video, audio], packets=packets)


def open_error_av_segment(exc: BaseException | None = None) -> IvsMuxSegmentSpec:
    """A segment whose av.open raises → the segment-open-fail skip branch."""
    return IvsMuxSegmentSpec(
        streams=[], packets=[], open_error=exc or OSError("fake segment open failure")
    )


def make_ivs_av_open_fake_seq(
    *,
    output_path: Path,
    segment_specs: dict[str, IvsMuxSegmentSpec],
    output_container_cls: type = FakeAvOutputContainer,
    verify_container_cls: type = FakeAvVerifyContainer,
) -> Any:
    """av.open replacement keyed on segment FILENAME → per-segment behavior.

    One _mux_ivs_segments call can hit several skip branches by mapping each
    segment file to a different IvsMuxSegmentSpec. The output/verify container
    classes are pluggable to drive the mux-exception and missing-streams arms.
    """
    output_path_str = str(output_path)

    def _fake_open(path: Any, mode: str = "r", **_kwargs: Any) -> Any:
        path_str = str(path)
        if path_str == output_path_str and "w" in mode:
            return output_container_cls(Path(path))
        if path_str == output_path_str:
            return verify_container_cls()
        spec = segment_specs[Path(path).name]
        if spec.open_error is not None:
            raise spec.open_error
        return FakeAvInputContainerCustom(
            streams=spec.streams, packets=spec.packets, demux_error=spec.demux_error
        )

    return _fake_open


# ── Output/verify variants for the mux finalize/verify branch arms ──────────


class FakeAvOutputContainerCloseError(FakeAvOutputContainer):
    """Writes the stub then raises on close() — drives the output.close()
    exception arm (the file is still non-empty so the size check passes)."""

    def close(self) -> None:
        super().close()
        raise OSError("fake output.close() failure")


class FakeAvOutputContainerNoWrite(FakeAvOutputContainer):
    """close() writes nothing → output stays missing/empty, driving the
    'output file missing or empty' arm."""

    def close(self) -> None:
        self.closed = True


class FakeAvVerifyContainerRaises:
    """av.open(output) for the verify pass raises — drives the verify
    try/except fallback (has_video = has_audio = False)."""

    def __init__(self) -> None:
        raise OSError("fake verify-open failure")


def codec_none_av_segment(
    *, video_pid: int = 0x100, audio_pid: int = 0x101
) -> IvsMuxSegmentSpec:
    """Matched video+audio plus a third stream whose codec_context is None —
    drives the 'skip streams without codec_context' guard."""
    video = FakeVideoStream(pid=video_pid)
    audio = FakeAudioStream(pid=audio_pid)
    no_codec = SimpleNamespace(id=0x102, type="data", codec_context=None)
    packets = [
        FakeAvPacket(stream=video, pts=0, dts=0, duration=1),
        FakeAvPacket(stream=audio, pts=0, dts=0, duration=1),
    ]
    return IvsMuxSegmentSpec(streams=[video, audio, no_codec], packets=packets)


def zero_duration_av_segment(
    *, video_pid: int = 0x100, audio_pid: int = 0x101
) -> IvsMuxSegmentSpec:
    """Matched streams whose packets carry duration=0 — drives the
    ``if packet.duration:`` falsy arm for both video and audio."""
    video = FakeVideoStream(pid=video_pid)
    audio = FakeAudioStream(pid=audio_pid)
    packets = [
        FakeAvPacket(stream=video, pts=0, dts=0, duration=0),
        FakeAvPacket(stream=audio, pts=0, dts=0, duration=0),
    ]
    return IvsMuxSegmentSpec(streams=[video, audio], packets=packets)


class FakeAvOutputContainerOpenError:
    """av.open(output, "w") raises on construction — drives the mux-level
    except + the 'output is None' finally arm."""

    def __init__(self, _output_path: Path) -> None:
        raise OSError("fake output-open failure")


def extra_stream_av_segment(
    *, video_pid: int = 0x100, audio_pid: int = 0x101, extra_pid: int = 0x200
) -> IvsMuxSegmentSpec:
    """video + audio + a third codec'd stream whose PID matches neither —
    drives the elif-False arms in PID matching (834->826) and packet routing
    (906->877)."""
    video = FakeVideoStream(pid=video_pid)
    audio = FakeAudioStream(pid=audio_pid)
    extra = SimpleNamespace(
        id=extra_pid, type="data", codec_context=SimpleNamespace(name="klv")
    )
    packets = [
        FakeAvPacket(stream=video, pts=0, dts=0, duration=1),
        FakeAvPacket(stream=audio, pts=0, dts=0, duration=1),
        FakeAvPacket(stream=extra, pts=0, dts=0, duration=1),  # routed to neither
    ]
    return IvsMuxSegmentSpec(streams=[video, audio, extra], packets=packets)


def many_bad_packets_av_segment(
    *, video_pid: int = 0x100, audio_pid: int = 0x101, n: int = 12
) -> IvsMuxSegmentSpec:
    """Matched streams plus >10 unusable (pts=None) packets — overruns the
    skip-examples cap so the cap-reached arm (887->892) is taken."""
    video = FakeVideoStream(pid=video_pid)
    audio = FakeAudioStream(pid=audio_pid)
    packets = [
        FakeAvPacket(stream=video, pts=None, dts=0, duration=1) for _ in range(n)
    ]
    return IvsMuxSegmentSpec(streams=[video, audio], packets=packets)


def demux_error_av_segment(
    *, video_pid: int = 0x100, audio_pid: int = 0x101
) -> IvsMuxSegmentSpec:
    """Matched streams but demux() raises — drives the segment-level except arm."""
    return IvsMuxSegmentSpec(
        streams=[FakeVideoStream(pid=video_pid), FakeAudioStream(pid=audio_pid)],
        packets=[],
        demux_error=RuntimeError("fake demux failure"),
    )
