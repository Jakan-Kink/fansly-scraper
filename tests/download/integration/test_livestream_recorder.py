"""End-to-end integration tests for ``download.livestream._record_stream``.

Exercises the full IVS recording lifecycle:

1. ``_get_authenticated_playback_url`` calls the Fansly streaming-channel
   endpoint to obtain a freshly-tokened master URL.
2. ``_resolve_variant_url`` fetches the IVS master playlist and picks
   the highest-bandwidth variant.
3. ``_poll_segments_loop`` polls the variant manifest, downloads
   per-segment ``.ts`` payloads via parallel ``httpx`` calls, and
   terminates on ``EXT-X-ENDLIST``.
4. ``_mux_ivs_segments`` PID-routes the collected packets into an MP4
   (PyAV leaf-faked — production isinstance ``VideoStream`` /
   ``AudioStream`` checks satisfied via monkeypatch).
5. Cleanup: temp segment dir removed, chat sidecar promoted if present.

Boundary policy:

- **Fansly REST** mocked at the HTTP boundary via ``respx_fansly_api``.
- **IVS hosts** (``*.playback.live-video.net``,
  ``use14.playlist.live-video.net``) mocked via ``respx_ivs_cdn``.
- **PyAV** leaf-faked via ``monkeypatch.setattr(av, "open", ...)`` and
  ``VideoStream`` / ``AudioStream`` symbol-swap in ``download.livestream``.
  The lib's Cython-backed stream classes are not easily subclassable; the
  fakes implement the minimum surface ``_mux_ivs_segments`` exercises.
- All other internals (poll loop, MSN dedup, semaphore-bounded segment
  downloads, PID discovery, mux orchestration, output verify) run real.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import av
import pytest

from download.livestream import _record_stream, _salvage_orphan_segments
from metadata.models import StreamChannel
from pathio.livestream import _get_segments_base
from tests.fixtures.api import (
    IvsStreamFixture,
    corrupt_ivs_segment,
    dump_fansly_calls,
    make_synthetic_ivs_segment,
    wire_ivs_stream,
)
from tests.fixtures.utils.test_isolation import snowflake_id


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_channel(
    creator_id: int,
    master_url: str,
    *,
    channel_id: int,
    stream_id: int,
) -> StreamChannel:
    """Build a StreamChannel matching the wired IVS fixture's master URL."""
    return StreamChannel.model_validate(
        {
            "id": channel_id,
            "accountId": creator_id,
            "status": 2,
            "playbackUrl": master_url,
            "chatRoomId": None,  # skip the chat WS branch — separate test
            "stream": {
                "id": stream_id,
                "channelId": channel_id,
                "accountId": creator_id,
                "status": 2,
                "title": "test broadcast",
                "viewerCount": 1,
                "startedAt": 1_700_000_000_000,
            },
        }
    )


async def _drive_ivs_recording(
    config_wired,
    tmp_path,
    *,
    segment_bytes: bytes,
    username: str = "ivs_creator",
    total_segments: int = 5,
) -> tuple[IvsStreamFixture, Path]:
    """Wire a full IVS broadcast serving ``segment_bytes`` for every segment,
    run ``_record_stream`` to completion, return (stream fixture, expected MP4
    path). Shared by the happy-path and bad-packet scenarios so the real-PyAV
    broadcast setup is written once.
    """
    creator_id = snowflake_id()
    channel_id = snowflake_id()
    stream_id = snowflake_id()

    config_wired.download_directory = tmp_path / "downloads"
    config_wired.temp_folder = tmp_path / "temp"
    config_wired.download_directory.mkdir(parents=True, exist_ok=True)
    config_wired.temp_folder.mkdir(parents=True, exist_ok=True)
    # 0 = immediate yield via asyncio.sleep(0) — minimum poll interval.
    config_wired.monitoring_livestream_manifest_poll_interval_seconds = 0
    config_wired.use_folder_suffix = True

    stream = wire_ivs_stream(
        creator_id=creator_id,
        username=username,
        total_segments=total_segments,
        chat_room_id=None,
        segment_bytes=segment_bytes,
    )
    channel = _make_channel(
        creator_id, stream.master_url, channel_id=channel_id, stream_id=stream_id
    )
    # startedAt=1_700_000_000_000 ms = 2023-11-14 22:13:20 UTC.
    expected_output_path = (
        config_wired.download_directory
        / f"{username}_fansly"
        / "Livestreams"
        / f"{username}_20231114_221320_live.mp4"
    )

    rec_stop = asyncio.Event()
    global_stop = asyncio.Event()
    try:
        await _record_stream(
            config_wired, creator_id, username, channel, rec_stop, global_stop
        )
    finally:
        dump_fansly_calls(stream.streaming_channel_route.calls, "streaming_channel")
        dump_fansly_calls(stream.variant_route.calls, "variant_manifest")
    return stream, expected_output_path


# ── Tests ──────────────────────────────────────────────────────────────────


class TestRecordStreamHappyPath:
    """Drive ``_record_stream`` end-to-end with a fully-wired IVS broadcast."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_full_record_cycle_writes_mp4_and_cleans_up(
        self,
        respx_fansly_api,
        respx_ivs_cdn,
        config_wired,
        tmp_path,
    ) -> None:
        """Happy path: 5 real .ts segments + ENDLIST → real PyAV mux → MP4.

        Drives the full lifecycle against REAL PyAV (no av.open fake): each
        segment is a genuine H.264-High + AAC MPEG-TS chunk, so the muxed
        output is a real MP4. Asserts:
          - Every wired respx route was called the expected number of times.
          - All 5 segment routes were called (every segment fetched once).
          - The output MP4 opens, carries video + audio, and preserves the
            source's H.264 High profile (the add_stream_from_template
            extradata copy — Constrained Baseline would mean the copy dropped).
          - Temp segment directory was removed on successful mux.
        """
        stream, expected_output_path = await _drive_ivs_recording(
            config_wired, tmp_path, segment_bytes=make_synthetic_ivs_segment()
        )

        # ── Assertions ────────────────────────────────────────────────────

        # Fansly streaming-channel endpoint called exactly once (one attempt).
        assert stream.streaming_channel_route.call_count == 1

        # Master playlist fetched exactly once.
        assert stream.master_route.call_count == 1

        # Variant manifest fetched at least once per scenario manifest
        # (5 sliding-window snapshots + 1 ENDLIST = 6 expected calls).
        assert stream.variant_route.call_count >= len(stream.scenario.manifests)

        # Every segment fetched.
        stream.assert_all_segments_fetched()

        # Output is a real MP4 muxed by real PyAV.
        assert expected_output_path.exists(), (
            f"Output MP4 not written: {expected_output_path}"
        )
        assert expected_output_path.stat().st_size > 0

        # Reopen with real PyAV: must carry video + audio and preserve the
        # source's H.264 High profile. A dropped extradata copy would yield
        # Constrained Baseline (or fail to open) — the bug this guards.
        muxed = av.open(str(expected_output_path))
        try:
            assert muxed.streams.video, "muxed MP4 has no video stream"
            assert muxed.streams.audio, "muxed MP4 has no audio stream"
            assert muxed.streams.video[0].profile == "High", (
                f"High profile not preserved in mux: {muxed.streams.video[0].profile}"
            )
            # Continuity: all 5 segments muxed sequentially into one stream,
            # not just the first. A single 30-frame segment yields ~30 frames;
            # a "only first segment muxed" regression would fail this.
            video_frames = sum(
                1 for pkt in muxed.demux(muxed.streams.video[0]) if pkt.size
            )
            assert video_frames > 2 * 30, (
                f"expected multi-segment continuity, got {video_frames} frames"
            )
        finally:
            muxed.close()

        # Temp segment dir removed on successful mux.
        temp_dirs = list(config_wired.temp_folder.glob("*_segments"))
        assert temp_dirs == [], f"Temp segment dir should be cleaned up: {temp_dirs}"


class TestRecordStreamBadPackets:
    """TEI-flagged (corrupt) packets are dropped by the recorder's libav
    ``+discardcorrupt`` path; the recording still produces a valid MP4."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_corrupt_packets_dropped_but_recording_survives(
        self,
        respx_fansly_api,
        respx_ivs_cdn,
        config_wired,
        tmp_path,
    ) -> None:
        """Each segment carries 2 TEI-flagged packets (AWS IVS drops ~2 per
        file). libav's +discardcorrupt drops them at demux — before the
        recorder's Python skip-counter can see them — so the discriminating
        proof is: the corrupt run muxes a valid MP4 with strictly fewer video
        frames than a clean baseline (dropped, not catastrophic, not hung).

        Relies on the >=30-frame segment default: below the GOP cliff the
        corruption would drop the whole segment and the recording would stall.
        """
        clean_seg = make_synthetic_ivs_segment()
        corrupt_seg = corrupt_ivs_segment(clean_seg, n_packets=2)

        _, clean_path = await _drive_ivs_recording(
            config_wired, tmp_path, segment_bytes=clean_seg, username="clean_ivs"
        )
        _, corrupt_path = await _drive_ivs_recording(
            config_wired, tmp_path, segment_bytes=corrupt_seg, username="corrupt_ivs"
        )

        def _video_frames(path: Path) -> int:
            container = av.open(str(path))
            try:
                return sum(
                    1 for pkt in container.demux(container.streams.video[0]) if pkt.size
                )
            finally:
                container.close()

        assert clean_path.exists()
        assert corrupt_path.exists()
        clean_frames = _video_frames(clean_path)
        corrupt_frames = _video_frames(corrupt_path)

        # Survived (valid MP4 with frames) AND strictly fewer than clean —
        # proving +discardcorrupt dropped the TEI packets without collapsing
        # the segment (the catastrophic <=24-frame behaviour) or hanging.
        assert 0 < corrupt_frames < clean_frames, (
            f"clean={clean_frames} corrupt={corrupt_frames} "
            "(expected 0 < corrupt < clean)"
        )

        # Corrupt output is still a well-formed MP4 with both streams.
        muxed = av.open(str(corrupt_path))
        try:
            assert muxed.streams.video, "corrupt-run MP4 has no video stream"
            assert muxed.streams.audio, "corrupt-run MP4 has no audio stream"
        finally:
            muxed.close()


class TestRecordStreamSalvage:
    """Orphan ``*_segments`` dirs left by a crashed recording are muxed to
    their sidecar MP4 path on startup, then removed."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_salvage_muxes_orphan_segment_dir(
        self,
        config_wired,
        tmp_path,
    ) -> None:
        """Fabricate an orphan segment dir (real synthetic .ts files + an
        ``output_path.txt`` sidecar) and run ``_salvage_orphan_segments``: it
        muxes the leftovers to the sidecar's MP4 (High profile preserved) and
        cleans the orphan dir. No network — salvage is a pure local mux.
        """
        config_wired.download_directory = tmp_path / "downloads"
        config_wired.temp_folder = tmp_path / "temp"
        config_wired.download_directory.mkdir(parents=True, exist_ok=True)
        config_wired.temp_folder.mkdir(parents=True, exist_ok=True)

        segments_base = _get_segments_base(config_wired)
        segments_base.mkdir(parents=True, exist_ok=True)

        output_mp4 = (
            config_wired.download_directory
            / "ivs_creator_fansly"
            / "Livestreams"
            / "ivs_creator_20231114_221320_live.mp4"
        )
        output_mp4.parent.mkdir(parents=True, exist_ok=True)

        # Orphan dir name = "<mp4 stem>_segments" (matches _record_stream).
        orphan_dir = segments_base / f"{output_mp4.stem}_segments"
        orphan_dir.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (orphan_dir / f"segment_{i:06d}.ts").write_bytes(
                make_synthetic_ivs_segment(seed=i)
            )
        (orphan_dir / "output_path.txt").write_text(str(output_mp4), encoding="utf-8")

        await _salvage_orphan_segments(config_wired)

        # Salvaged: orphan dir muxed to the sidecar MP4, High profile kept.
        assert output_mp4.exists(), f"salvage did not produce {output_mp4}"
        assert output_mp4.stat().st_size > 0
        muxed = av.open(str(output_mp4))
        try:
            assert muxed.streams.video, "salvaged MP4 has no video stream"
            assert muxed.streams.audio, "salvaged MP4 has no audio stream"
            assert muxed.streams.video[0].profile == "High", (
                "High profile not preserved in salvage mux: "
                f"{muxed.streams.video[0].profile}"
            )
        finally:
            muxed.close()

        # Orphan dir cleaned up after a successful salvage.
        assert not orphan_dir.exists(), "orphan segment dir not removed after salvage"
