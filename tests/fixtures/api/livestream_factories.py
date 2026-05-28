"""Payload + manifest builders for AWS IVS livestream tests.

Pure-function builders — no fixtures, no respx, no httpx. Use these from
``livestream_fixtures.py`` (which wires the respx routes) or directly
from individual tests that need to construct one piece of the wire
shape.

Wire-shape references (verified against ``download/livestream.py`` and
``project_livestream_ivs.md`` 2026-05-20):

- **Fansly streaming endpoint**: ``/api/v1/streaming/channel/<id>``
  returns ``{success, response: {stream: {playbackUrl: <master_url>}}}``
  where ``master_url`` carries a short-lived ``?token=`` JWT (~30s TTL).
- **IVS master URL**: ``<id>.us-east-1.playback.live-video.net``.
  m3u8 master playlist with one or more ``EXT-X-STREAM-INF`` variants.
- **IVS variant URL**: ``use14.playlist.live-video.net`` (host is
  illustrative — production uses opaque path tokens, the host string
  itself isn't load-bearing). Sliding-window variant manifest with
  ``EXT-X-VERSION:3``, ``EXT-X-TARGETDURATION:6``, rolling
  ``EXT-X-MEDIA-SEQUENCE``, optional ``EXT-X-PREFETCH`` hints,
  ``EXT-X-ENDLIST`` only on broadcast end.
- **Segment URLs**: derived from the variant URL via relative paths in
  the manifest. Self-authenticating (no query string needed).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import av
import numpy as np


# ── Default constants matching production code ─────────────────────────────

# IVS TARGETDURATION as seen in the wild + assumed in download/livestream.py
# (`_PREFETCH_RE` default duration 6.0, manifest poll interval default 3.0
# = TARGETDURATION/2). Override per-test if simulating other deployments.
DEFAULT_TARGET_DURATION = 6
DEFAULT_SEGMENT_DURATION = 6.0

# Sliding-window size IVS publishes at any moment (livestream.py:357).
DEFAULT_WINDOW_SIZE = 15

# IVS HLS host patterns. Production master URL is on
# ``*.playback.live-video.net``; variant + segments live on
# ``use14.playlist.live-video.net`` (or a sibling region host). The fixture
# uses these as literal hostnames for respx routing — tests that exercise
# the host-substring sanity-checks in production code should use these
# exact strings.
IVS_MASTER_HOST = "playback.live-video.net"
IVS_VARIANT_HOST = "use14.playlist.live-video.net"


# ── Fansly streaming-channel response (REST API) ───────────────────────────


def build_streaming_channel_response(
    *,
    creator_id: int,
    master_url: str,
    channel_id: int | None = None,
    chat_room_id: int | None = None,
) -> dict:
    """Build the ``/api/v1/streaming/channel/<id>`` REST response shape.

    Production ``_get_authenticated_playback_url`` reads ``stream.playbackUrl``
    from this payload to obtain a freshly-tokened master URL each
    recording attempt.
    """
    return {
        "success": True,
        "response": {
            "id": channel_id or (creator_id + 10_000),
            "accountId": creator_id,
            "status": 2,
            "chatRoomId": chat_room_id,
            "stream": {
                "id": creator_id + 20_000,
                "accountId": creator_id,
                "status": 2,
                "playbackUrl": master_url,
                "startedAt": 1_700_000_000_000,
            },
        },
    }


def build_streaming_account(
    *,
    creator_id: int,
    username: str,
    master_url: str,
    chat_room_id: int | None = None,
) -> dict:
    """Build a single ``aggregationData.accounts[]`` entry for
    ``/api/v1/streaming/followingstreams/online``.

    Carries the same ``streaming.channel`` shape the watcher parses into
    ``StreamingInfo`` via ``model_validate`` (livestream_watcher.py:184).
    """
    channel_id = creator_id + 10_000
    return {
        "id": creator_id,
        "username": username,
        "streaming": {
            "accountId": creator_id,
            "enabled": True,
            "channel": {
                "id": channel_id,
                "accountId": creator_id,
                "playbackUrl": master_url,
                "chatRoomId": chat_room_id,
                "status": 2,
                "stream": {
                    "id": creator_id + 20_000,
                    "channelId": channel_id,
                    "accountId": creator_id,
                    "title": f"live broadcast by {username}",
                    "status": 2,
                    "viewerCount": 42,
                    "startedAt": 1_700_000_000_000,
                },
            },
        },
    }


# ── m3u8 manifest builders ─────────────────────────────────────────────────


def build_master_playlist(
    *,
    variant_url: str,
    bandwidth: int = 2_000_000,
    resolution: tuple[int, int] = (1280, 720),
    codecs: str = "avc1.64001f,mp4a.40.2",
) -> str:
    """Build an IVS-shape master playlist with a single variant.

    The single ``EXT-X-STREAM-INF`` entry points at ``variant_url`` — a
    fully-qualified URL on the variant host (use14.playlist.live-video.net
    or wherever the fixture wires it). Production ``_resolve_variant_url``
    picks ``max(bandwidth)`` so a single-variant master is enough to
    cover the highest-bandwidth-selection branch.
    """
    width, height = resolution
    return (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={width}x{height},"
        f'CODECS="{codecs}"\n'
        f"{variant_url}\n"
    )


def build_variant_playlist(
    *,
    media_sequence: int,
    segment_uris: list[str],
    target_duration: int = DEFAULT_TARGET_DURATION,
    segment_duration: float = DEFAULT_SEGMENT_DURATION,
    prefetch_uris: list[str] | None = None,
    endlist: bool = False,
) -> str:
    """Build an IVS-shape variant playlist (sliding-window LL-HLS).

    Args:
        media_sequence: First segment's MSN — production dedupes on
            ``playlist_msn + seg_pos``, so this rolls forward as the
            window slides (livestream.py:421-426).
        segment_uris: Ordered list of relative or absolute segment URIs.
            Relative URIs are resolved against the variant URL by
            ``urljoin`` in production (livestream.py:428).
        target_duration: TARGETDURATION tag value (6 for Fansly IVS).
        segment_duration: Per-segment EXTINF value.
        prefetch_uris: Optional EXT-X-PREFETCH hint URIs for leading-edge
            segments still being written. Same MSN-based dedup applies
            (livestream.py:436-446).
        endlist: When True, append ``EXT-X-ENDLIST`` — terminal signal,
            production finalizes immediately (livestream.py:481).

    Returns:
        m3u8 playlist text ready to feed ``m3u8.loads()``.
    """
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{target_duration}",
        f"#EXT-X-MEDIA-SEQUENCE:{media_sequence}",
    ]
    for uri in segment_uris:
        lines.append(f"#EXTINF:{segment_duration:.3f},")
        lines.append(uri)
    lines.extend(
        f"#EXT-X-PREFETCH:{prefetch_uri}" for prefetch_uri in prefetch_uris or []
    )
    if endlist:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


# ── Sliding-window scenario helper ─────────────────────────────────────────


@dataclass(slots=True)
class SlidingWindowScenario:
    """A scripted broadcast as a sequence of variant-manifest snapshots.

    Each ``manifests`` entry is one manifest text that the recorder will
    see on a successive poll. ``segment_uris_in_order`` is the
    deterministic ordered list of every segment URI the recorder will
    end up requesting across the whole scenario — fixtures use this to
    pre-register matching segment routes with the right call count.
    """

    manifests: list[str]
    segment_uris_in_order: list[str]


def make_sliding_window_scenario(
    *,
    total_segments: int,
    segment_url_template: str = "segment_{idx:06d}.ts",
    window_size: int = DEFAULT_WINDOW_SIZE,
    fetches: int | None = None,
    starting_media_sequence: int = 0,
    end_with_endlist: bool = True,
) -> SlidingWindowScenario:
    """Build a multi-snapshot scenario simulating ``fetches`` polls.

    The window slides forward across snapshots: snapshot ``k`` exposes
    segments ``[start_k, start_k + window_size)`` where ``start_k``
    advances enough between snapshots to surface at least one new
    segment, until all ``total_segments`` have been exposed.

    Default behavior: the recorder sees the broadcast progress with a
    fresh segment per fetch, then receives an ENDLIST manifest as the
    final snapshot to terminate the poll loop cleanly.

    Args:
        total_segments: Total number of distinct segments the broadcast
            will ever expose.
        segment_url_template: Format string with ``{idx}`` placeholder
            for the per-segment URI (relative — the variant URL anchors
            the join).
        window_size: How many segments are visible in any single
            manifest snapshot. Defaults to 15 (IVS norm).
        fetches: Number of variant-manifest snapshots to produce.
            Defaults to ``total_segments + 1`` (one new segment per
            fetch + a final ENDLIST snapshot).
        starting_media_sequence: MSN of the first segment in the first
            snapshot. Defaults to 0; non-zero simulates joining a
            broadcast in progress.
        end_with_endlist: Append a final ENDLIST manifest containing
            the trailing window. When False, the final snapshot is a
            normal sliding-window manifest (test must use an external
            stop signal to break the poll loop).

    Returns:
        ``SlidingWindowScenario`` carrying the manifest sequence and
        the deterministic segment-URI order.
    """
    all_uris = [segment_url_template.format(idx=i) for i in range(total_segments)]
    if fetches is None:
        fetches = total_segments + (1 if end_with_endlist else 0)

    manifests: list[str] = []
    for fetch_idx in range(fetches):
        # Snapshot k exposes segments [k, k + window_size), clipped.
        start = min(fetch_idx, max(0, total_segments - window_size))
        end = min(start + window_size, total_segments)
        if start >= total_segments:
            # Past the broadcast — repeat the trailing window so the
            # recorder keeps polling until ENDLIST or external stop.
            start = max(0, total_segments - window_size)
            end = total_segments
        snapshot_uris = all_uris[start:end]
        is_final = end_with_endlist and fetch_idx == fetches - 1
        manifests.append(
            build_variant_playlist(
                media_sequence=starting_media_sequence + start,
                segment_uris=snapshot_uris,
                endlist=is_final,
            )
        )

    return SlidingWindowScenario(
        manifests=manifests,
        segment_uris_in_order=all_uris,
    )


# ── Synthetic real .ts segment (PyAV-encoded) ──────────────────────────────

# 30 video frames: below ~24 a synthetic segment is effectively one GOP, so a
# TEI-corrupted post-keyframe packet cascades to end-of-GOP and libav's
# +discardcorrupt drops the WHOLE segment (catastrophic — nothing left to mux,
# the recorder stalls). By >=30 there are intermediate recovery points, so only
# the corrupted frames drop and the segment survives — keeping the "drop ~2
# packets, still muxable" promise honest (AWS IVS drops ~2 boundary packets per
# file). Deterministic cliff, verified on the MMsD IVS sibling: <=24 -> 8/8
# catastrophic, 30 -> 0/8. Do NOT lower below 30.
DEFAULT_SEGMENT_VIDEO_FRAMES = 30


def make_synthetic_ivs_segment(
    *,
    n_video_frames: int = DEFAULT_SEGMENT_VIDEO_FRAMES,
    width: int = 320,
    height: int = 240,
    fps: int = 25,
    sample_rate: int = 48000,
    seed: int = 0,
) -> bytes:
    """Encode a real H.264 **High**-profile + AAC MPEG-TS segment via PyAV.

    Returns genuine ``.ts`` bytes (real SPS/PPS extradata, PIDs 0x100 video /
    0x101 audio, one IDR-led GOP — the shape of a real IVS segment) for
    real-PyAV mux tests. No creator content, so it is safe to (re)generate
    in-process. High profile is explicit: ``preset=ultrafast`` silently drops
    to Constrained Baseline, and the ``add_stream_from_template`` extradata
    copy this suite guards is High-specific.

    Args:
        n_video_frames: Video frames (~= packets). Keep >=30: below the GOP
            cliff (see DEFAULT_SEGMENT_VIDEO_FRAMES) TEI corruption drops the
            whole segment instead of just the corrupted frames.
        seed: Varies frame content so sequential segments differ.
    """
    buf = io.BytesIO()
    out = av.open(buf, mode="w", format="mpegts")

    vstream = out.add_stream("libx264", rate=fps)
    vstream.width = width
    vstream.height = height
    vstream.pix_fmt = "yuv420p"
    vstream.codec_context.options = {"profile": "high", "preset": "veryfast"}

    astream = out.add_stream("aac", rate=sample_rate)

    for i in range(n_video_frames):
        shade = (seed * 16 + i * 8) % 256
        arr = np.full((height, width, 3), shade, dtype=np.uint8)
        arr[:, :, 1] = np.linspace(0, 255, width, dtype=np.uint8)[None, :]
        for pkt in vstream.encode(av.VideoFrame.from_ndarray(arr, format="rgb24")):
            out.mux(pkt)
    for pkt in vstream.encode():
        out.mux(pkt)

    n_audio_frames = int(n_video_frames / fps * sample_rate / 1024) + 1
    for _ in range(n_audio_frames):
        aframe = av.AudioFrame.from_ndarray(
            np.zeros((2, 1024), dtype=np.float32), format="fltp", layout="stereo"
        )
        aframe.sample_rate = sample_rate
        for pkt in astream.encode(aframe):
            out.mux(pkt)
    for pkt in astream.encode():
        out.mux(pkt)

    out.close()
    return buf.getvalue()


# MPEG-TS packet size + sync byte + video PID emitted by make_synthetic_ivs_segment.
_TS_PACKET_SIZE = 188
_TS_SYNC_BYTE = 0x47
_TS_VIDEO_PID = 0x100


def corrupt_ivs_segment(
    ts_bytes: bytes,
    *,
    n_packets: int = 2,
    skip_leading: int = 4,
) -> bytes:
    """Mark ``n_packets`` video TS packets corrupt via the Transport Error
    Indicator bit — models AWS IVS's ~2 dropped packets per file.

    Sets the TEI bit (``byte1 |= 0x80``) on video-PID packets: a single bit
    that libav's ``+discardcorrupt`` keys on, so the recorder drops those
    frames at demux. Leading video packets (SPS/PPS + the IDR keyframe) are
    skipped so the stream stays decodable apart from the dropped frames.

    Args:
        n_packets: How many video packets to flag corrupt.
        skip_leading: Leading video packets to leave intact (keyframe + params).
    """
    ba = bytearray(ts_bytes)
    seen = flipped = 0
    for off in range(0, len(ba) - _TS_PACKET_SIZE, _TS_PACKET_SIZE):
        if ba[off] != _TS_SYNC_BYTE:
            continue
        if ((ba[off + 1] & 0x1F) << 8) | ba[off + 2] != _TS_VIDEO_PID:
            continue
        seen += 1
        if seen <= skip_leading:
            continue
        ba[off + 1] |= 0x80  # Transport Error Indicator
        flipped += 1
        if flipped >= n_packets:
            break
    return bytes(ba)
