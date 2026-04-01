"""M3U8 Media Download Handling with Three-Tier Strategy.

This module provides HLS video downloading functionality with:
1. Direct PyAV download (fastest) - In-process HLS demux/remux via libav
2. Direct FFmpeg subprocess (fallback) - Let ffmpeg CLI handle the HLS stream
3. Manual segment download (robust fallback) - Download .ts files individually

Always tries PyAV first, then FFmpeg subprocess, then segments.
"""

import concurrent.futures
import os
from pathlib import Path
from typing import Any

import av
import ffmpeg
from m3u8 import M3U8
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Column

from config.fanslyconfig import FanslyConfig
from errors import M3U8Error
from helpers.web import get_file_name_from_url, get_qs_value, split_url
from textio import print_debug, print_error, print_info, print_warning


def get_m3u8_cookies(m3u8_url: str) -> dict[str, Any]:
    """Parses an M3U8 URL and returns CloudFront cookies."""
    # Parse URL query string for required cookie values
    policy = get_qs_value(m3u8_url, "Policy")
    key_pair_id = get_qs_value(m3u8_url, "Key-Pair-Id")
    signature = get_qs_value(m3u8_url, "Signature")

    cookies = {
        "CloudFront-Key-Pair-Id": key_pair_id,
        "CloudFront-Policy": policy,
        "CloudFront-Signature": signature,
    }

    return cookies


def _format_cookies_for_ffmpeg(cookies: dict[str, str]) -> str:
    """Format cookies for ffmpeg/PyAV cookie header.

    Args:
        cookies: Dictionary of cookie name/value pairs

    Returns:
        Formatted cookie string for HTTP Cookie header
    """
    return "; ".join([f"{k}={v}" for k, v in cookies.items()])


def get_m3u8_progress(disable_loading_bar: bool) -> Progress:
    """Returns a Rich progress bar customized for M3U8 Downloads."""
    text_column = TextColumn("", table_column=Column(ratio=1))
    bar_column = BarColumn(bar_width=60, table_column=Column(ratio=5))

    return Progress(
        text_column,
        bar_column,
        expand=True,
        transient=True,
        disable=disable_loading_bar,
    )


def _get_highest_quality_variant_url(
    config: FanslyConfig,
    m3u8_url: str,
    cookies: dict[str, str],
) -> str:
    """Fetch master playlist and return the highest quality variant URL.

    Selects the variant with the largest resolution (width * height).
    Falls back to guessing a 1080p variant URL if no playlists are found.

    Args:
        config: The downloader configuration
        m3u8_url: URL of the master HLS manifest
        cookies: CloudFront authentication cookies

    Returns:
        Absolute URL of the highest quality variant playlist
    """
    m3u8_base_url, m3u8_file_url = split_url(m3u8_url)

    stream_response = config.get_api().get_with_ngsw(
        url=m3u8_file_url,
        cookies=cookies,
        add_fansly_headers=False,
    )

    master_playlist = M3U8(content=stream_response.text, base_uri=m3u8_base_url)

    if len(master_playlist.playlists) > 0:
        variant_info = max(
            master_playlist.playlists,
            key=lambda p: p.stream_info.resolution[0] * p.stream_info.resolution[1],
        )
        return variant_info.absolute_uri

    # Fallback: guess 1080p variant URL
    print_warning(
        "No HLS variants found in master playlist. Guessing 1080p — this might fail!"
    )
    return f"{m3u8_url.split('.m3u8', maxsplit=1)[0]}_1080.m3u8"


def fetch_m3u8_segment_playlist(
    config: FanslyConfig,
    m3u8_url: str,
    cookies: dict[str, str] | None = None,
) -> M3U8:
    """Fetch the M3U8 endlist with all the MPEG-TS segments.

    Args:
        config: The downloader configuration.
        m3u8_url: The URL string of the M3U8 to download.
        cookies: Authentication cookies if they cannot be derived from m3u8_url.

    Returns:
        An M3U8 endlist with segments.
    """
    if cookies is None:
        cookies = get_m3u8_cookies(m3u8_url)

    m3u8_base_url, m3u8_file_url = split_url(m3u8_url)

    stream_response = config.get_api().get_with_ngsw(
        url=m3u8_file_url,
        cookies=cookies,
        add_fansly_headers=False,
    )

    if stream_response.status_code != 200:
        message = (
            f"Failed downloading M3U8 playlist info. "
            f"Response code: {stream_response.status_code}\n{stream_response.text}"
        )

        print_error(message, 12)

        raise M3U8Error(message)

    playlist_text = stream_response.text

    playlist = M3U8(
        content=playlist_text,
        base_uri=m3u8_base_url,
    )

    # pylint: disable-next=E1101
    if playlist.is_endlist is True and playlist.playlist_type == "vod":
        return playlist

    # Not an endlist — resolve variant and recurse
    segments_url = _get_highest_quality_variant_url(config, m3u8_url, cookies)
    return fetch_m3u8_segment_playlist(config, segments_url, cookies=cookies)


# ---------------------------------------------------------------------------
# Tier 1: PyAV direct HLS download (fastest — in-process libav)
# ---------------------------------------------------------------------------


def _try_direct_download_pyav(
    config: FanslyConfig,
    m3u8_url: str,
    output_path: Path,
    cookies: dict[str, str],
) -> bool:
    """Try downloading HLS video directly using PyAV (fastest path).

    PyAV opens the HLS URL in-process via libav, eliminating subprocess
    overhead.  Uses template-based stream mapping for true codec copy
    (remux without re-encoding).  The AAC ADTS→ASC conversion is handled
    implicitly by the MP4 muxer when codec extradata is copied via template.

    Args:
        config: The downloader configuration
        m3u8_url: URL of the master HLS manifest
        output_path: Path to save the final video
        cookies: CloudFront authentication cookies

    Returns:
        True if download succeeded, False otherwise
    """
    input_container = None
    output_container = None
    try:
        variant_url = _get_highest_quality_variant_url(config, m3u8_url, cookies)
        print_debug(f"PyAV direct: variant URL: {variant_url}")
        print_info("Trying PyAV fast path: in-process HLS download...")

        cookie_header = _format_cookies_for_ffmpeg(cookies)
        headers_str = f"Cookie: {cookie_header}\r\n"

        # Open HLS stream via PyAV
        input_container = av.open(
            variant_url,
            options={
                "protocol_whitelist": "file,http,https,tcp,tls,crypto",
                "headers": headers_str,
            },
            format="hls",
        )

        # Open output MP4
        output_container = av.open(str(output_path), "w", format="mp4")

        # Map input streams → output streams using template (codec copy)
        stream_mapping = {}
        for stream in input_container.streams:
            if not stream.codec_context:
                continue
            output_stream = output_container.add_stream_from_template(stream)
            stream_mapping[stream] = output_stream

        if not stream_mapping:
            print_warning("PyAV: no valid streams found in HLS manifest")
            return False

        # Remux all packets (codec copy — no re-encoding)
        packet_count = 0
        for packet in input_container.demux():
            if packet.dts is None:
                continue
            if packet.stream in stream_mapping:
                packet.stream = stream_mapping[packet.stream]
                output_container.mux(packet)
                packet_count += 1

        input_container.close()
        input_container = None
        output_container.close()
        output_container = None

        # Verify output
        if output_path.exists() and output_path.stat().st_size > 0:
            print_info(
                f"PyAV fast path succeeded! "
                f"({output_path.stat().st_size:,} bytes, {packet_count:,} packets)"
            )
            return True

        print_warning("PyAV download produced empty or missing file")

    except av.error.FFmpegError as e:
        print_debug(f"PyAV direct download failed: {e}")
        print_info("PyAV fast path failed, trying FFmpeg subprocess...")
    except Exception as e:
        print_debug(f"PyAV direct download failed: {e!s}")
        print_info("PyAV fast path failed, trying FFmpeg subprocess...")
    finally:
        if input_container is not None:
            input_container.close()
        if output_container is not None:
            output_container.close()

    return False


# ---------------------------------------------------------------------------
# Tier 2: FFmpeg subprocess (proven fallback)
# ---------------------------------------------------------------------------


def _try_direct_download_ffmpeg(
    config: FanslyConfig,
    m3u8_url: str,
    output_path: Path,
    cookies: dict[str, str],
) -> bool:
    """Try downloading HLS video using ffmpeg subprocess (fallback).

    This lets ffmpeg handle the entire HLS stream processing including:
    - Downloading the variant playlist
    - Fetching all .ts segments
    - Handling encryption keys
    - Muxing into final MP4

    Args:
        config: The downloader configuration
        m3u8_url: URL of the master HLS manifest
        output_path: Path to save the final video
        cookies: CloudFront authentication cookies

    Returns:
        True if direct download succeeded, False otherwise
    """
    try:
        variant_url = _get_highest_quality_variant_url(config, m3u8_url, cookies)
        print_debug(f"FFmpeg subprocess: variant URL: {variant_url}")
        print_info("Trying FFmpeg subprocess path...")

        cookie_header = _format_cookies_for_ffmpeg(cookies)
        headers_str = f"Cookie: {cookie_header}\r\n"

        # Build ffmpeg command using ffmpeg-python
        stream = (
            ffmpeg.input(
                variant_url,
                protocol_whitelist="file,crypto,data,http,https,tcp,tls",
                headers=headers_str,
                f="hls",
            )
            .output(
                str(output_path),
                vcodec="copy",
                acodec="copy",
                **{"bsf:a": "aac_adtstoasc"},  # AAC bitstream filter for MP4
            )
            .overwrite_output()
        )

        print_debug(f"FFmpeg command: {' '.join(stream.get_args())}")

        # Detect duration for progress tracking
        from helpers.rich_progress import ffmpeg_progress

        total_duration = 0.0
        try:
            probe = ffmpeg.probe(
                variant_url,
                headers=headers_str,
                f="hls",
                protocol_whitelist="file,crypto,data,http,https,tcp,tls",
            )
            total_duration = float(probe.get("format", {}).get("duration", 0))
            print_debug(f"HLS video duration: {total_duration:.2f}s")
        except ffmpeg.Error as probe_err:
            stderr_msg = (
                probe_err.stderr.decode(errors="replace").strip()
                if probe_err.stderr
                else "no stderr"
            )
            print_debug(f"Could not probe HLS duration: {stderr_msg}")
        except Exception as probe_err:
            print_debug(f"Could not probe HLS duration: {probe_err}")

        with ffmpeg_progress(
            total_duration if total_duration > 0 else 100.0,
            task_name="hls_ffmpeg_direct",
            description="Downloading HLS video (FFmpeg)",
        ) as progress_file:
            stream = stream.global_args("-progress", str(progress_file))
            stream.run(capture_stdout=True, capture_stderr=True)

        # Verify file exists and has content
        if not (output_path.exists() and output_path.stat().st_size > 0):
            print_warning("FFmpeg download produced invalid file")
            return False

        # Verify output has both audio and video streams
        try:
            probe = ffmpeg.probe(str(output_path))
            streams = probe.get("streams", [])
            has_video = any(s.get("codec_type") == "video" for s in streams)
            has_audio = any(s.get("codec_type") == "audio" for s in streams)

            if not has_video or not has_audio:
                print_warning(
                    f"FFmpeg download missing streams "
                    f"(video={has_video}, audio={has_audio}). "
                    f"Falling back to segment download."
                )
                output_path.unlink(missing_ok=True)
                return False

        except Exception as e:
            print_warning(
                f"Could not verify streams: {e}. "
                f"Assuming incomplete, trying segment download."
            )
            output_path.unlink(missing_ok=True)
            return False

        print_info(f"FFmpeg path succeeded! ({output_path.stat().st_size:,} bytes)")

    except ffmpeg.Error as e:
        stderr = e.stderr.decode() if e.stderr else str(e)
        print_debug(f"FFmpeg download failed: {stderr}")
        print_info("FFmpeg path failed, falling back to segment download...")
        return False
    except Exception as e:
        print_debug(f"FFmpeg download failed: {e!s}")
        print_info("FFmpeg path failed, falling back to segment download...")
        return False
    else:
        return True


# ---------------------------------------------------------------------------
# Tier 3: Manual segment download (most robust)
# ---------------------------------------------------------------------------


def _mux_segments_with_pyav(
    segment_files: list[Path],
    output_path: Path,
) -> bool:
    """Mux downloaded .ts segments into MP4 using PyAV.

    Opens each segment individually (to avoid fd accumulation), creates
    output streams from the first valid segment, then remuxes all packets.
    Skips corrupt packets and aborts if >25% of segments fail.

    Args:
        segment_files: Ordered list of .ts segment file paths
        output_path: Path for the output MP4 file

    Returns:
        True if muxing succeeded, False otherwise
    """
    output = None
    try:
        output = av.open(str(output_path), "w", options={"movflags": "faststart"})

        output_streams: dict[str, Any] = {}
        skipped_segments = 0
        total_skipped_packets = 0

        for segment_path in segment_files:
            input_options = {
                "err_detect": "ignore_err",
                "fflags": "+discardcorrupt+genpts",
            }

            try:
                input_container = av.open(str(segment_path), options=input_options)
            except Exception as e:
                print_debug(
                    f"Segment {segment_path.name} failed to open: {e} — skipping"
                )
                skipped_segments += 1
                continue

            try:
                # Create output streams from first valid segment
                if not output_streams:
                    for stream in input_container.streams:
                        if stream.type == "video" and "video" not in output_streams:
                            output_streams["video"] = output.add_stream_from_template(
                                stream
                            )
                        elif stream.type == "audio" and "audio" not in output_streams:
                            output_streams["audio"] = output.add_stream_from_template(
                                stream
                            )

                # Demux and remux packets, skip corrupt ones
                skipped_packets = 0
                for packet in input_container.demux():
                    if packet.dts is None or packet.is_corrupt:
                        skipped_packets += 1
                        continue
                    try:
                        if packet.stream.type == "video" and "video" in output_streams:
                            packet.stream = output_streams["video"]
                            output.mux(packet)
                        elif (
                            packet.stream.type == "audio" and "audio" in output_streams
                        ):
                            packet.stream = output_streams["audio"]
                            output.mux(packet)
                    except (OSError, av.error.FFmpegError):
                        skipped_packets += 1

                if skipped_packets > 0:
                    print_debug(
                        f"Segment {segment_path.name}: "
                        f"skipped {skipped_packets} bad packets"
                    )
                    total_skipped_packets += skipped_packets

            except Exception as e:
                print_debug(
                    f"Segment {segment_path.name} failed: {e} — skipping entirely"
                )
                skipped_segments += 1
            finally:
                input_container.close()

        # Check failure threshold
        if skipped_segments > 0:
            skip_pct = (skipped_segments / len(segment_files)) * 100
            print_debug(
                f"PyAV mux: {skipped_segments} segments skipped, "
                f"{total_skipped_packets} packets skipped"
            )
            if skip_pct > 25:
                print_warning(
                    f"Too many segments failed ({skip_pct:.1f}% > 25%) — aborting PyAV mux"
                )
                output.close()
                output = None
                return False

        output.close()
        output = None

        if output_path.exists() and output_path.stat().st_size > 0:
            return True

        print_debug("PyAV mux completed but output file is missing or empty")

    except Exception as e:
        print_debug(f"PyAV segment muxing error: {e!s}")
    finally:
        if output is not None:
            output.close()

    return False


def _mux_segments_with_ffmpeg(
    segment_files: list[Path],
    output_path: Path,
) -> bool:
    """Mux downloaded .ts segments into MP4 using ffmpeg concat (fallback).

    Args:
        segment_files: Ordered list of .ts segment file paths
        output_path: Path for the output MP4 file

    Returns:
        True if muxing succeeded, False otherwise
    """
    ffmpeg_list_file = output_path.parent / "_ffmpeg_concat_.ffc"
    try:
        with ffmpeg_list_file.open("w", encoding="utf-8") as list_file:
            list_file.write("ffconcat version 1.0\n")
            list_file.writelines([f"file '{f.name}'\n" for f in segment_files])

        stream = (
            ffmpeg.input(str(ffmpeg_list_file), f="concat", safe=0)
            .output(str(output_path), c="copy")
            .overwrite_output()
        )

        print_debug(f"FFmpeg concat command: {' '.join(stream.get_args())}")
        stream.run(capture_stdout=True, capture_stderr=True, quiet=True)

        return output_path.exists() and output_path.stat().st_size > 0

    except ffmpeg.Error as ex:
        stderr = ex.stderr.decode() if ex.stderr else str(ex)
        print_debug(f"FFmpeg concat failed: {stderr}")
        return False
    except Exception as e:
        print_debug(f"FFmpeg concat failed: {e!s}")
        return False
    finally:
        ffmpeg_list_file.unlink(missing_ok=True)


def _try_segment_download(
    config: FanslyConfig,
    m3u8_url: str,
    output_path: Path,
    cookies: dict[str, str],
    created_at: int | None = None,
) -> Path:
    """Download HLS video by fetching each segment then muxing with PyAV.

    Downloads each .ts segment individually via the API, then muxes them
    into a final MP4 using PyAV (with ffmpeg concat as internal fallback).

    Args:
        config: The downloader configuration
        m3u8_url: URL of the master HLS manifest
        output_path: Path to save the final video
        cookies: CloudFront authentication cookies
        created_at: Optional timestamp to set on final file

    Returns:
        Path to the downloaded video file

    Raises:
        M3U8Error: If download or conversion fails
    """
    chunk_size = 1_048_576

    print_info("Using segment download path (downloading .ts files individually)...")
    print_debug(f"Target output path: {output_path}")

    video_path = output_path.parent
    playlist = fetch_m3u8_segment_playlist(config, m3u8_url, cookies)

    def download_ts(segment_uri: str, segment_full_path: Path) -> None:
        """Download a single .ts segment."""
        segment_response = None
        try:
            segment_response = config.get_api().get_with_ngsw(
                url=segment_uri,
                cookies=cookies,
                stream=True,
                add_fansly_headers=False,
                bypass_rate_limit=True,
            )
            if segment_response.status_code != 200:
                print_debug(
                    f"Segment download failed with status "
                    f"{segment_response.status_code}: {segment_uri}"
                )
                return
            with segment_full_path.open("wb") as ts_file:
                for chunk in segment_response.iter_bytes(chunk_size):
                    if chunk:
                        ts_file.write(chunk)
        except Exception as e:
            print_debug(f"Error downloading segment {segment_uri}: {e!s}")
        finally:
            if segment_response is not None:
                segment_response.close()

    segments = playlist.segments

    segment_files: list[Path] = []
    segment_uris: list[str] = []

    for segment in segments:
        segment_uri = segment.absolute_uri
        segment_file_name = get_file_name_from_url(segment_uri)
        segment_full_path = video_path / segment_file_name
        segment_files.append(segment_full_path)
        segment_uris.append(segment_uri)

    # Display loading bar if there are many segments
    progress = get_m3u8_progress(disable_loading_bar=len(segment_files) < 5)

    try:
        print_debug(f"Downloading {len(segment_files)} segments")

        # Download segments with thread pool
        max_workers = min(16, max(4, len(segment_files) // 4))
        with (
            progress,
            concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor,
        ):
            list(
                progress.track(
                    executor.map(download_ts, segment_uris, segment_files),
                    total=len(segment_files),
                    description=f"Downloading segments ({max_workers} threads)",
                )
            )

        # Check for missing segments
        missing_segments = [f for f in segment_files if not f.exists()]
        if missing_segments:
            print_debug(f"Missing segments: {missing_segments}")
            raise M3U8Error(f"Stream segments failed to download: {missing_segments}")

        print_debug("All segments downloaded, muxing to MP4")

        # Try PyAV muxing first, fall back to ffmpeg concat
        if _mux_segments_with_pyav(segment_files, output_path):
            print_info(
                f"Segment download + PyAV mux succeeded "
                f"({output_path.stat().st_size:,} bytes)"
            )
        elif _mux_segments_with_ffmpeg(segment_files, output_path):
            print_info(
                f"Segment download + FFmpeg concat succeeded "
                f"({output_path.stat().st_size:,} bytes)"
            )
        else:
            raise M3U8Error("Both PyAV and FFmpeg muxing failed for segments")

        if created_at:
            os.utime(output_path, (created_at, created_at))

        return output_path

    finally:
        for file in segment_files:
            file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def download_m3u8(
    config: FanslyConfig,
    m3u8_url: str,
    save_path: Path,
    created_at: int | None = None,
) -> Path:
    """Download M3U8 content as MP4 using three-tier strategy.

    Strategy:
    1. **PyAV direct** (fastest): In-process HLS demux/remux via libav
    2. **FFmpeg subprocess** (fallback): Let ffmpeg CLI handle the HLS stream
    3. **Segment download** (robust fallback): Download .ts files individually,
       then mux with PyAV (or ffmpeg concat as last resort)

    Args:
        config: The downloader configuration.
        m3u8_url: The URL string of the M3U8 to download.
        save_path: The suggested file to save the video to (will use .mp4).
        created_at: Optional Unix timestamp to set as file modification time.

    Returns:
        The file path of the MPEG-4 download.
    """
    cookies = get_m3u8_cookies(m3u8_url)

    video_path = save_path.parent
    full_path = video_path / f"{save_path.stem}.mp4"

    try:
        # Tier 1: PyAV direct download (fastest — in-process)
        if _try_direct_download_pyav(config, m3u8_url, full_path, cookies):
            if created_at:
                os.utime(full_path, (created_at, created_at))
            return full_path

        # Tier 2: FFmpeg subprocess (proven, handles edge cases)
        if _try_direct_download_ffmpeg(config, m3u8_url, full_path, cookies):
            if created_at:
                os.utime(full_path, (created_at, created_at))
            return full_path

        # Tier 3: Manual segment download + mux
        return _try_segment_download(config, m3u8_url, full_path, cookies, created_at)

    except M3U8Error:
        raise
    except Exception as e:
        print_error(f"Failed to download HLS video from {m3u8_url}: {e}")
        raise M3U8Error(f"Failed to download HLS video: {e}") from e
