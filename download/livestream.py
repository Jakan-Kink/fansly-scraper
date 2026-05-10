"""Livestream recording orchestration.

Owns the per-recording lifecycle: master/variant resolution, segment
download poll loop, PID-based PyAV mux to MP4, and orphan-segment
salvage.  The watcher loop in ``daemon.livestream_watcher`` calls
:func:`_record_stream` and :func:`_salvage_orphan_segments`.

PyAV mux notes
--------------
IVS segments share PIDs across the broadcast but stream **positions**
swap between segments — pinning by ``stream.id`` (PID) rather than
position is required for templating to produce a valid output.  Output
streams are templated via :py:meth:`av.OutputContainer.add_stream_from_template`
so codec extradata (H.264 SPS/PPS in ``avcC``) is preserved; without
this, decoders fall back to Baseline-profile defaults and break on
High-profile bitstreams.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import av
import httpx
import m3u8
from av import AudioStream, VideoStream
from loguru import logger

from config.fanslyconfig import FanslyConfig
from download.livestream_chat import (
    ChatRecorder,
    _chat_ws_loop,
    register_chat_recorder,
    unregister_chat_recorder,
)
from fileio.livestream import _unique_output_path
from metadata.models import StreamChannel
from pathio.livestream import _build_output_path, _get_segments_base


# ── Constants ─────────────────────────────────────────────────────────────

# Seconds to wait after the first EXT-X-ENDLIST before finalising.
# IVS emits ENDLIST for transient creator pauses, not just true end-of-stream.
_ENDLIST_GRACE_SECONDS = 60.0

# Maximum concurrent segment downloads per stream.
_PARALLEL_SEGMENT_LIMIT = 5

# Maximum number of PyAV probe segments when identifying audio/video PIDs.
_MAX_PROBE_SEGMENTS = 5

# Seconds to wait between retry attempts after a recoverable open error.
_RETRY_DELAY_SECONDS = 15.0

# Maximum number of times to retry opening the stream before giving up.
_MAX_OPEN_RETRIES = 3

# Regex for IVS EXT-X-PREFETCH hint lines.
_PREFETCH_RE = re.compile(r"^#EXT-X-PREFETCH:(.+)$", re.MULTILINE)

# Headers sent when fetching the IVS master playlist.  The master URL is on
# *.playback.live-video.net — entirely separate from Fansly.  No API key;
# just browser-compatible Accept + CORS origin headers.
_IVS_MASTER_HEADERS: dict[str, str] = {
    "Accept": "application/x-mpegURL, application/vnd.apple.mpegurl, application/json, text/plain",
    "Origin": "https://fansly.com",
    "Referer": "https://fansly.com/",
}


# ── Recording task ─────────────────────────────────────────────────────────


async def _record_stream(
    config: FanslyConfig,
    creator_id: int,
    username: str,
    channel: StreamChannel,
    rec_stop: asyncio.Event,
    global_stop: asyncio.Event,
) -> None:
    """Orchestrate a single creator's recording lifecycle.

    Flow per attempt:
    1. Fetch a fresh authenticated master URL (``?token=`` JWT, ~30 s TTL).
    2. Resolve highest-bandwidth variant URL via httpx + m3u8 master parse.
    3. Run the async segment poll loop until stop or end-of-broadcast.
    4. PyAV-mux collected ``.ts`` segments → MP4 (PID-based, in thread).
    5. Clean up the temp segment directory on success.
    """
    output_path = _build_output_path(config, username, channel)
    # Bump to _part2 / _part3 etc. if a prior reconnect already completed or
    # crashed mid-mux for the same broadcast (same startedAt → same base name).
    try:
        segments_base = _get_segments_base(config)
    except RuntimeError:
        segments_base = output_path.parent  # fallback: no collision check
    output_path = _unique_output_path(output_path, segments_base)
    # Segment dir lives in temp_folder (or <download_dir>/temp) so it does
    # not clutter the Livestreams output directory.
    temp_dir = segments_base / f"{output_path.stem}_segments"
    temp_dir.mkdir(parents=True, exist_ok=True)
    # Sidecar so the salvage pass knows where to write the final MP4.
    (temp_dir / "output_path.txt").write_text(str(output_path), encoding="utf-8")

    # Register a ChatRecorder now so WS events arriving before the poll
    # loop starts are also captured.
    recorder: ChatRecorder | None = None
    if channel.chatRoomId is not None:
        recorder = ChatRecorder(temp_dir / "chat.jsonl")
        register_chat_recorder(channel.chatRoomId, recorder)

    log_prefix = f"[{username}]"
    logger.info(
        "download.livestream: {} starting capture → {}",
        log_prefix,
        output_path,
    )

    try:
        for attempt in range(1, _MAX_OPEN_RETRIES + 1):
            if rec_stop.is_set() or global_stop.is_set():
                break

            # Fetch a fresh authenticated master URL each attempt —
            # the ?token= JWT on stream.playbackUrl expires in ~30 s.
            auth_url = await _get_authenticated_playback_url(
                config, creator_id, channel
            )
            if auth_url is None:
                logger.warning(
                    "download.livestream: {} no authenticated URL (attempt {}/{})",
                    log_prefix,
                    attempt,
                    _MAX_OPEN_RETRIES,
                )
                if attempt < _MAX_OPEN_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                continue

            # Resolve the highest-bandwidth variant URL from the master manifest.
            variant_url = await _resolve_variant_url(auth_url)
            if variant_url is None:
                logger.warning(
                    "download.livestream: {} could not resolve variant URL "
                    "(attempt {}/{})",
                    log_prefix,
                    attempt,
                    _MAX_OPEN_RETRIES,
                )
                if attempt < _MAX_OPEN_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                continue

            logger.debug(
                "download.livestream: {} variant URL: {}",
                log_prefix,
                variant_url,
            )

            # Unified stop: fires when either rec_stop or global_stop is set.
            combined_stop = asyncio.Event()

            async def _forward_stops(_cs: asyncio.Event = combined_stop) -> None:
                await asyncio.wait(
                    [
                        asyncio.ensure_future(rec_stop.wait()),
                        asyncio.ensure_future(global_stop.wait()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                _cs.set()

            monitor_task = asyncio.create_task(_forward_stops())
            chat_task: asyncio.Task | None = None
            if channel.chatRoomId is not None and recorder is not None:
                chat_task = asyncio.create_task(
                    _chat_ws_loop(
                        config,
                        channel.chatRoomId,
                        recorder,
                        combined_stop,
                        log_prefix,
                    )
                )
            try:
                segments, durations = await _poll_segments_loop(
                    variant_url,
                    temp_dir,
                    combined_stop,
                    log_prefix,
                    float(config.monitoring_livestream_manifest_poll_interval_seconds),
                )
            finally:
                combined_stop.set()
                monitor_task.cancel()
                if chat_task is not None:
                    chat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await chat_task

            if not segments:
                logger.warning(
                    "download.livestream: {} no segments captured (attempt {}/{})",
                    log_prefix,
                    attempt,
                    _MAX_OPEN_RETRIES,
                )
                if attempt < _MAX_OPEN_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                continue

            # PID-based PyAV mux — blocking, run in thread pool.
            success = await asyncio.to_thread(
                _mux_ivs_segments, segments, durations, output_path, log_prefix
            )

            if success:
                logger.info(
                    "download.livestream: {} recording complete → {}",
                    log_prefix,
                    output_path,
                )
                # Promote chat log next to the MP4 before removing the temp dir.
                chat_src = temp_dir / "chat.jsonl"
                if chat_src.exists() and chat_src.stat().st_size > 0:
                    chat_dest = output_path.with_name(f"{output_path.stem}_chat.jsonl")
                    shutil.move(str(chat_src), chat_dest)
                    logger.info(
                        "download.livestream: {} chat log → {}",
                        log_prefix,
                        chat_dest,
                    )
                shutil.rmtree(temp_dir, ignore_errors=True)
                break

            if attempt < _MAX_OPEN_RETRIES:
                logger.warning(
                    "download.livestream: {} mux failed "
                    "(attempt {}/{}) — retrying in {}s",
                    log_prefix,
                    attempt,
                    _MAX_OPEN_RETRIES,
                    _RETRY_DELAY_SECONDS,
                )
                await asyncio.sleep(_RETRY_DELAY_SECONDS)
            else:
                logger.error(
                    "download.livestream: {} all {} attempts failed; "
                    "segments preserved at {}",
                    log_prefix,
                    _MAX_OPEN_RETRIES,
                    temp_dir,
                )
    finally:
        if channel.chatRoomId is not None:
            unregister_chat_recorder(channel.chatRoomId)


async def _get_authenticated_playback_url(
    config: FanslyConfig,
    creator_id: int,
    channel: StreamChannel,
) -> str | None:
    """Return an authenticated HLS master URL for the given channel.

    Calls ``/api/v1/streaming/channel/<creator_id>`` which returns
    ``stream.playbackUrl`` with a fresh ``?token=`` JWT.  Falls back
    to the unauthenticated ``channel.playbackUrl`` if the API call
    fails (IVS may still serve the stream without the token for public
    channels).
    """
    try:
        api = config.get_api()
        response = await api.get_streaming_channel(creator_id)
        data = api.get_json_response_contents(response)
        if isinstance(data, dict):
            stream = data.get("stream") or {}
            auth_url = stream.get("playbackUrl") or data.get("playbackUrl")
            if auth_url:
                return auth_url
    except Exception as exc:
        logger.warning(
            "download.livestream: get_streaming_channel({}) failed — {}; "
            "falling back to unauthenticated URL",
            creator_id,
            exc,
        )

    # Fallback: unauthenticated master URL.
    return channel.playbackUrl


async def _resolve_variant_url(master_url: str) -> str | None:
    """Fetch the IVS master playlist and return the highest-bandwidth variant URL.

    IVS master URL format (from ``stream.playbackUrl``):
    ``https://<id>.us-east-1.playback.live-video.net/api/video/v1/
    us-east-1.<customer>.channel.<content>.m3u8?token=<JWT>``

    The master carries no ``?dna=`` — auth is the ``?token=`` JWT already
    embedded in ``master_url``.  Variant URLs have auth baked into the
    opaque path token on ``use14.playlist.live-video.net`` and are
    self-authenticating (no extra headers needed by PyAV).

    Returns the absolute variant URL string, or ``None`` on failure.
    """
    parsed = urlsplit(master_url)
    base_uri = f"{parsed.scheme}://{parsed.netloc}"

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            response = await client.get(master_url, headers=_IVS_MASTER_HEADERS)
            response.raise_for_status()
    except Exception as exc:
        logger.warning(
            "download.livestream: failed to fetch master playlist — {}",
            exc,
        )
        return None

    try:
        playlist = m3u8.M3U8(content=response.text, base_uri=base_uri)
    except Exception as exc:
        logger.warning(
            "download.livestream: failed to parse master M3U8 — {}",
            exc,
        )
        return None

    if not playlist.playlists:
        logger.warning("download.livestream: no variant streams in master playlist")
        return None

    best = max(playlist.playlists, key=lambda p: p.stream_info.bandwidth or 0)
    variant_url = best.absolute_uri
    logger.debug(
        "download.livestream: selected variant {}p @ {} kbps — {}",
        best.stream_info.resolution[1] if best.stream_info.resolution else "?",
        (best.stream_info.bandwidth or 0) // 1000,
        variant_url,
    )
    return variant_url


async def _poll_segments_loop(
    variant_url: str,
    temp_dir: Path,
    stop_event: asyncio.Event,
    log_prefix: str,
    manifest_poll_interval: float = 3.0,
) -> tuple[list[Path], list[float]]:
    """Poll the IVS variant manifest, download new segments to *temp_dir*.

    IVS publishes a sliding window of ~15 recent 6-second segments.  Each
    manifest fetch rotates the auth token in the segment URLs, so URL-based
    dedup is unreliable.  Dedup is done by ``EXT-X-MEDIA-SEQUENCE`` index
    instead (stable across fetches for the same segment).

    ``EXT-X-PREFETCH`` hint lines (leading-edge segments that are still being
    written) are also collected to minimise the gap at the head of the window.

    An ``EXT-X-ENDLIST`` tag is honoured only after a
    ``_ENDLIST_GRACE_SECONDS`` window, because IVS emits it for transient
    creator pauses as well as true end-of-broadcast.

    Args:
        variant_url: IVS variant manifest URL (self-authenticating opaque path).
        temp_dir: Directory to write numbered ``.ts`` files into.
        stop_event: Set to break out of the poll loop (graceful stop).
        log_prefix: Log line prefix, e.g. ``"[username]"``.
        manifest_poll_interval: Seconds between manifest re-fetches.
            IVS TARGETDURATION is 6 s; default 3 s (half), max 15 s.

    Returns:
        ``(segments, durations)`` — parallel lists of downloaded segment paths
        and their declared durations in seconds.  Both lists may be empty if
        the stream ended before any segments were downloaded.
    """
    segments_collected: list[Path] = []
    durations: list[float] = []
    last_msn = -1
    endlist_first_seen: float | None = None

    # Manifest requests go directly to AWS IVS/CloudFront — no Fansly auth.
    # Variant segment URLs are self-authenticating via their opaque path token.
    async with httpx.AsyncClient(
        http2=True,
        timeout=30.0,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:150.0) "
                "Gecko/20100101 Firefox/150.0"
            ),
            "Accept": (
                "application/x-mpegURL, application/vnd.apple.mpegurl, "
                "application/json, text/plain"
            ),
            "Origin": "https://fansly.com",
            "Referer": "https://fansly.com/",
        },
    ) as client:
        while not stop_event.is_set():
            try:
                response = await client.get(variant_url, timeout=10.0)
                response.raise_for_status()
            except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
                logger.warning(
                    "download.livestream: {} manifest fetch failed — {}",
                    log_prefix,
                    exc,
                )
                await asyncio.sleep(manifest_poll_interval)
                continue

            resp_text = response.text
            playlist = m3u8.loads(resp_text, uri=variant_url)

            # EXT-X-ENDLIST grace: IVS uses ENDLIST for transient pauses.
            if playlist.is_endlist:
                if endlist_first_seen is None:
                    endlist_first_seen = time.monotonic()
                    logger.info(
                        "download.livestream: {} EXT-X-ENDLIST — {:.0f}s grace period",
                        log_prefix,
                        _ENDLIST_GRACE_SECONDS,
                    )
                elif time.monotonic() - endlist_first_seen > _ENDLIST_GRACE_SECONDS:
                    logger.info(
                        "download.livestream: {} EXT-X-ENDLIST persisted "
                        ">{}s — finalising",
                        log_prefix,
                        _ENDLIST_GRACE_SECONDS,
                    )
                    break
            elif endlist_first_seen is not None:
                logger.info(
                    "download.livestream: {} EXT-X-ENDLIST cleared — stream resumed",
                    log_prefix,
                )
                endlist_first_seen = None

            playlist_msn: int = getattr(playlist, "media_sequence", 0) or 0
            jobs: list[tuple[int, str, Path, float]] = []

            for seg_pos, segment in enumerate(playlist.segments):
                seg_msn = playlist_msn + seg_pos
                if seg_msn <= last_msn:
                    continue
                abs_uri = urljoin(variant_url, segment.uri)
                seg_idx = len(segments_collected) + len(jobs)
                seg_path = temp_dir / f"segment_{seg_idx:06d}.ts"
                jobs.append((seg_msn, abs_uri, seg_path, segment.duration or 6.0))

            # EXT-X-PREFETCH: leading-edge hint lines for segments that are
            # still being written.  Same MSN-based dedup applies.
            prefetch_base = playlist_msn + len(playlist.segments)
            for pf_idx, raw_pf in enumerate(_PREFETCH_RE.findall(resp_text)):
                pf_uri = raw_pf.strip()
                if not pf_uri:
                    continue
                seg_msn = prefetch_base + pf_idx
                if seg_msn <= last_msn:
                    continue
                abs_uri = urljoin(variant_url, pf_uri)
                seg_idx = len(segments_collected) + len(jobs)
                seg_path = temp_dir / f"segment_{seg_idx:06d}.ts"
                jobs.append((seg_msn, abs_uri, seg_path, 6.0))

            if jobs:
                sem = asyncio.Semaphore(_PARALLEL_SEGMENT_LIMIT)

                async def _bounded(
                    seg_url: str,
                    seg_path: Path,
                    _c: httpx.AsyncClient = client,
                    _s: asyncio.Semaphore = sem,
                ) -> bool:
                    async with _s:
                        return await _download_segment(
                            _c, seg_url, seg_path, log_prefix
                        )

                results = await asyncio.gather(
                    *(_bounded(u, p) for _, u, p, _ in jobs),
                    return_exceptions=True,
                )

                for (seg_msn, _, seg_path, dur), ok in zip(jobs, results, strict=True):
                    if ok is True:
                        segments_collected.append(seg_path)
                        durations.append(dur)
                        last_msn = seg_msn

                logger.debug(
                    "download.livestream: {} +{} segments (total {}), msn={}",
                    log_prefix,
                    sum(1 for ok in results if ok is True),
                    len(segments_collected),
                    last_msn,
                )

            await asyncio.sleep(manifest_poll_interval)

    return segments_collected, durations


async def _salvage_orphan_segments(config: FanslyConfig) -> None:
    """Mux ``.ts`` segment dirs left behind by prior aborted recordings.

    At startup the watcher scans ``_get_segments_base(config)`` for any
    directories whose name ends with ``_segments``.  These are temp dirs
    created by ``_record_stream`` that were not cleaned up because the
    process was killed or crashed before the mux step completed.

    Each segment dir carries an ``output_path.txt`` sidecar written at
    recording start that records the intended final MP4 path.  Dirs without
    a sidecar are skipped (they belong to an older format or an unrelated
    tool).

    Segment durations default to 6 s (IVS TARGETDURATION) since the
    original manifest is no longer available.
    """
    try:
        segments_base = _get_segments_base(config)
    except RuntimeError:
        return

    orphan_dirs = await asyncio.to_thread(
        lambda: sorted(
            p
            for p in (segments_base.iterdir() if segments_base.exists() else [])
            if p.is_dir() and p.name.endswith("_segments")
        )
    )

    if not orphan_dirs:
        return

    logger.info(
        "download.livestream: found {} orphan segment dir(s) — salvaging",
        len(orphan_dirs),
    )

    for orphan_dir in orphan_dirs:
        stem = orphan_dir.name[: -len("_segments")]
        log_prefix = f"[salvage:{stem}]"

        sidecar = orphan_dir / "output_path.txt"
        if not sidecar.exists():
            logger.debug(
                "download.livestream: {} no output_path.txt sidecar — skipping",
                log_prefix,
            )
            continue
        output_path = Path(sidecar.read_text(encoding="utf-8").strip())

        # Already completed by a prior salvage run.
        _exists = await asyncio.to_thread(output_path.exists)
        _size = (await asyncio.to_thread(output_path.stat)).st_size if _exists else 0
        if _exists and _size > 0:
            logger.info(
                "download.livestream: {} output already exists — removing orphan dir",
                log_prefix,
            )
            shutil.rmtree(orphan_dir, ignore_errors=True)
            continue

        segments = sorted(orphan_dir.glob("segment_*.ts"))
        if not segments:
            logger.warning(
                "download.livestream: {} orphan dir is empty — removing",
                log_prefix,
            )
            shutil.rmtree(orphan_dir, ignore_errors=True)
            continue

        logger.info(
            "download.livestream: {} muxing {} orphan segment(s) → {}",
            log_prefix,
            len(segments),
            output_path.name,
        )

        # Durations are unknown; IVS TARGETDURATION is 6 s.
        durations = [6.0] * len(segments)
        success = await asyncio.to_thread(
            _mux_ivs_segments, segments, durations, output_path, log_prefix
        )

        if success:
            shutil.rmtree(orphan_dir, ignore_errors=True)
        else:
            logger.error(
                "download.livestream: {} salvage mux failed — segments preserved at {}",
                log_prefix,
                orphan_dir,
            )


async def _download_segment(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    log_prefix: str,
) -> bool:
    """Download one ``.ts`` segment to *dest*.

    IVS segment URLs are self-authenticating (auth in the opaque path token)
    so no extra headers are required.

    Returns:
        ``True`` on success, ``False`` on any network or I/O error.
    """
    try:
        response = await client.get(url, timeout=20.0)
        response.raise_for_status()
        await asyncio.to_thread(dest.write_bytes, response.content)
    except Exception as exc:
        logger.warning(
            "download.livestream: {} segment {} failed — {}",
            log_prefix,
            dest.name,
            exc,
        )
        return False
    else:
        return True


def _mux_ivs_segments(
    segments: list[Path],
    _durations: list[float],
    output_path: Path,
    log_prefix: str,
) -> bool:
    """PID-based PyAV mux of IVS ``.ts`` segments into a single MP4.

    IVS segments have consistent PIDs across the broadcast but **inconsistent
    stream positions** — the video stream may be at position 0 in one segment
    and position 1 in the next.  This function probes up to
    ``_MAX_PROBE_SEGMENTS`` segments to identify the audio and video PIDs,
    then routes every packet by PID rather than position.  Output streams
    are templated from the PID-resolved input streams so codec extradata
    (SPS/PPS) is preserved.

    PTS rebasing ensures a continuous timeline across segment boundaries:
    each segment's first PTS is zeroed and shifted by the running global
    offset (max PTS of the previous segment + one packet duration).

    Args:
        segments: Ordered list of downloaded ``.ts`` segment paths.
        durations: Matching list of declared segment durations (seconds).
        output_path: Final MP4 destination path.
        log_prefix: Log line prefix.

    Returns:
        ``True`` if the output file was written with both video and audio.
    """
    if not segments:
        logger.warning("download.livestream: {} no segments to mux", log_prefix)
        return False

    # ── PID discovery ────────────────────────────────────────────────────
    audio_pid: int | None = None
    video_pid: int | None = None
    audio_codec: str | None = None
    video_codec: str | None = None

    n_probe = min(_MAX_PROBE_SEGMENTS, len(segments))
    for probe_idx in range(n_probe):
        if audio_pid is not None and video_pid is not None:
            break
        try:
            probe_container = av.open(
                str(segments[probe_idx]),
                options={"err_detect": "ignore_err", "fflags": "+discardcorrupt"},
            )
        except Exception as exc:
            logger.debug(
                "download.livestream: {} probe[{}] open failed — {}",
                log_prefix,
                probe_idx,
                exc,
            )
            continue
        try:
            for stream in probe_container.streams:
                if isinstance(stream, VideoStream) and video_pid is None:
                    video_pid = stream.id
                    video_codec = (
                        stream.codec_context.name if stream.codec_context else None
                    )
                elif isinstance(stream, AudioStream) and audio_pid is None:
                    audio_pid = stream.id
                    audio_codec = (
                        stream.codec_context.name if stream.codec_context else None
                    )
        finally:
            probe_container.close()

    if not audio_pid or not video_pid or not audio_codec or not video_codec:
        logger.error(
            "download.livestream: {} could not identify audio+video PIDs "
            "after probing {} segments — aborting mux",
            log_prefix,
            n_probe,
        )
        return False

    logger.info(
        "download.livestream: {} PIDs — video={} ({}), audio={} ({})",
        log_prefix,
        hex(video_pid),
        video_codec,
        hex(audio_pid),
        audio_codec,
    )

    # ── Mux ──────────────────────────────────────────────────────────────
    output: av.container.OutputContainer | None = None
    skipped_segments = 0
    total_skipped_packets = 0
    output_video_stream = None
    output_audio_stream = None
    video_pts_offset = 0
    audio_pts_offset = 0

    try:
        output = av.open(str(output_path), "w")

        for seg_path in segments:
            try:
                input_container = av.open(
                    str(seg_path),
                    options={
                        "err_detect": "ignore_err",
                        "fflags": "+discardcorrupt+genpts",
                    },
                )
            except Exception as exc:
                logger.warning(
                    "download.livestream: {} segment {} open failed — {}",
                    log_prefix,
                    seg_path.name,
                    exc,
                )
                skipped_segments += 1
                continue

            try:
                input_video = None
                input_audio = None
                for stream in input_container.streams:
                    if stream.id == video_pid:
                        input_video = stream
                    elif stream.id == audio_pid:
                        input_audio = stream

                if input_video is None or input_audio is None:
                    logger.warning(
                        "download.livestream: {} segment {} missing "
                        "video={} audio={} — skipping",
                        log_prefix,
                        seg_path.name,
                        input_video is None,
                        input_audio is None,
                    )
                    skipped_segments += 1
                    continue

                if output_video_stream is None:
                    output_video_stream = output.add_stream_from_template(input_video)
                    output_audio_stream = output.add_stream_from_template(input_audio)

                seg_video_first_pts: int | None = None
                seg_audio_first_pts: int | None = None
                seg_video_max_pts = video_pts_offset
                seg_audio_max_pts = audio_pts_offset
                seg_video_last_dur = 0
                seg_audio_last_dur = 0
                skipped_packets = 0

                for packet in input_container.demux(input_video, input_audio):
                    pkt_pts = packet.pts
                    pkt_dts = packet.dts
                    if pkt_pts is None or pkt_dts is None or packet.is_corrupt:
                        skipped_packets += 1
                        continue
                    try:
                        if packet.stream is input_video:
                            if seg_video_first_pts is None:
                                seg_video_first_pts = pkt_pts
                            packet.pts = (
                                pkt_pts - seg_video_first_pts + video_pts_offset
                            )
                            packet.dts = (
                                pkt_dts - seg_video_first_pts + video_pts_offset
                            )
                            seg_video_max_pts = max(seg_video_max_pts, packet.pts)
                            if packet.duration:
                                seg_video_last_dur = packet.duration
                            packet.stream = output_video_stream
                            output.mux(packet)
                        elif packet.stream is input_audio:
                            if seg_audio_first_pts is None:
                                seg_audio_first_pts = pkt_pts
                            packet.pts = (
                                pkt_pts - seg_audio_first_pts + audio_pts_offset
                            )
                            packet.dts = (
                                pkt_dts - seg_audio_first_pts + audio_pts_offset
                            )
                            seg_audio_max_pts = max(seg_audio_max_pts, packet.pts)
                            if packet.duration:
                                seg_audio_last_dur = packet.duration
                            packet.stream = output_audio_stream
                            output.mux(packet)
                    except (OSError, av.error.FFmpegError):
                        skipped_packets += 1

                # Advance global PTS offset for next segment (continuous timeline).
                video_pts_offset = seg_video_max_pts + seg_video_last_dur
                audio_pts_offset = seg_audio_max_pts + seg_audio_last_dur

                if skipped_packets:
                    total_skipped_packets += skipped_packets

            except Exception as exc:
                logger.warning(
                    "download.livestream: {} segment {} mux failed — {}",
                    log_prefix,
                    seg_path.name,
                    exc,
                )
                skipped_segments += 1
            finally:
                input_container.close()

        if skipped_segments > 0:
            skip_pct = (skipped_segments / len(segments)) * 100
            logger.warning(
                "download.livestream: {} skipped {}/{} segments ({:.1f}%), {} packets",
                log_prefix,
                skipped_segments,
                len(segments),
                skip_pct,
                total_skipped_packets,
            )
            if skip_pct > 25:
                logger.error(
                    "download.livestream: {} >25%% segments skipped — "
                    "aborting; segments preserved at {}",
                    log_prefix,
                    output_path.parent / f".{output_path.stem}_segments",
                )
                return False

    except Exception as exc:
        logger.exception("download.livestream: {} mux error — {}", log_prefix, exc)
        return False
    finally:
        if output is not None:
            with contextlib.suppress(Exception):
                output.close()

    # Verify output has both streams.
    if not output_path.exists() or output_path.stat().st_size == 0:
        logger.error(
            "download.livestream: {} output file missing or empty",
            log_prefix,
        )
        return False

    try:
        verify = av.open(str(output_path))
        has_video = any(s.type == "video" for s in verify.streams)
        has_audio = any(s.type == "audio" for s in verify.streams)
        verify.close()
    except Exception:
        has_video = has_audio = False

    ok = has_video and has_audio
    if ok:
        logger.info(
            "download.livestream: {} mux OK — {} ({:,} bytes)",
            log_prefix,
            output_path.name,
            output_path.stat().st_size,
        )
    else:
        logger.error(
            "download.livestream: {} output missing streams: video={}, audio={}",
            log_prefix,
            has_video,
            has_audio,
        )
    return ok
