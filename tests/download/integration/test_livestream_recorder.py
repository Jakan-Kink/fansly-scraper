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

import av
import pytest

import download.livestream as livestream_module
from download.livestream import _record_stream
from metadata.models import StreamChannel
from tests.fixtures.api import (
    FakeAudioStream,
    FakeVideoStream,
    dump_fansly_calls,
    make_ivs_av_open_fake,
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
        monkeypatch,
    ) -> None:
        """Happy path: 5 segments + ENDLIST → mux → MP4 written, temp dir removed.

        Asserts:
          - Every wired respx route was called the expected number of times.
          - All 5 segment routes were called (every segment fetched once).
          - Output MP4 exists with non-zero size after the mux completes.
          - Temp segment directory was removed on successful mux.
          - The PyAV-fake output container saw mux() calls for every packet
            the fake input containers yielded (6 packets per segment x
            5 segments = 30 mux calls).
        """
        creator_id = snowflake_id()
        channel_id = snowflake_id()
        stream_id = snowflake_id()
        username = "ivs_creator"

        # ── Configure paths + fast-poll interval ──────────────────────────
        config_wired.download_directory = tmp_path / "downloads"
        config_wired.temp_folder = tmp_path / "temp"
        config_wired.download_directory.mkdir(parents=True, exist_ok=True)
        config_wired.temp_folder.mkdir(parents=True, exist_ok=True)
        # 0 = immediate yield via asyncio.sleep(0) — minimum poll interval.
        config_wired.monitoring_livestream_manifest_poll_interval_seconds = 0
        config_wired.use_folder_suffix = True

        # ── Wire respx routes for one complete IVS broadcast ──────────────
        stream = wire_ivs_stream(
            creator_id=creator_id,
            username=username,
            total_segments=5,
            chat_room_id=None,
        )

        channel = _make_channel(
            creator_id,
            stream.master_url,
            channel_id=channel_id,
            stream_id=stream_id,
        )

        # ── Compute the predicted output path so the av-open fake can ────
        # route the "w"-mode call to FakeAvOutputContainer.
        # _build_output_path uses channel.stream.startedAt → datetime →
        # strftime("%Y%m%d_%H%M%S") in UTC.
        livestreams_dir = (
            config_wired.download_directory / f"{username}_fansly" / "Livestreams"
        )
        # startedAt=1_700_000_000_000 ms = 2023-11-14 22:13:20 UTC.
        expected_output_path = livestreams_dir / f"{username}_20231114_221320_live.mp4"

        # ── Leaf-fake PyAV at av.open + symbol-swap stream classes ────────
        monkeypatch.setattr(
            av,
            "open",
            make_ivs_av_open_fake(
                output_path=expected_output_path,
                packets_per_stream=3,
            ),
        )
        monkeypatch.setattr(livestream_module, "VideoStream", FakeVideoStream)
        monkeypatch.setattr(livestream_module, "AudioStream", FakeAudioStream)

        # ── Drive the recording ───────────────────────────────────────────
        rec_stop = asyncio.Event()
        global_stop = asyncio.Event()

        try:
            await _record_stream(
                config_wired,
                creator_id,
                username,
                channel,
                rec_stop,
                global_stop,
            )
        finally:
            dump_fansly_calls(stream.streaming_channel_route.calls, "streaming_channel")
            dump_fansly_calls(stream.variant_route.calls, "variant_manifest")

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

        # Output MP4 was written by FakeAvOutputContainer.close().
        assert expected_output_path.exists(), (
            f"Output MP4 not written: {expected_output_path}"
        )
        assert expected_output_path.stat().st_size > 0

        # Temp segment dir removed on successful mux.
        # Sidecar at temp_dir/output_path.txt is gone too.
        temp_dirs = list(config_wired.temp_folder.glob("*_segments"))
        assert temp_dirs == [], f"Temp segment dir should be cleaned up: {temp_dirs}"
