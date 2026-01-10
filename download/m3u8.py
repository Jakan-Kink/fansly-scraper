"""M3U8 Media Download Handling with Two-Tier Strategy.

This module provides HLS video downloading functionality with:
1. Direct ffmpeg download (fast path) - Let ffmpeg handle the HLS stream
2. Manual segment download (fallback) - Download .ts files individually

Always tries direct first for better performance.
"""

import concurrent.futures
from pathlib import Path
from typing import Any

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
    """Format cookies for ffmpeg's cookie header.

    Args:
        cookies: Dictionary of cookie name/value pairs

    Returns:
        Formatted cookie string for ffmpeg
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


def fetch_m3u8_segment_playlist(
    config: FanslyConfig,
    m3u8_url: str,
    cookies: dict[str, str] | None = None,
) -> M3U8:
    """Fetch the so-called M3U8 "endlist" with all the MPEG-TS segments.

    :param config: The downloader configuration.
    :type config: FanslyConfig

    :param m3u8_url: The URL string of the M3U8 to download.
    :type m3u8_url: str

    :param cookies: Authentication cookies if they cannot be derived
        from `m3u8_url`.
    :type cookies: Optional[dict[str, str]]

    :return: An M3U8 endlist with segments.
    :rtype: M3U8
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
        message = f"Failed downloading M3U8 playlist info. Response code: {stream_response.status_code}\n{stream_response.text}"

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

    if len(playlist.playlists) == 0:
        # Guess 1080p as a last resort
        print_warning(
            "Fansly returned an empty M3U8 playlist. I'll try fetch a 1080p version, this might fail!"
        )
        segments_url = f"{m3u8_url.split('.m3u8')[0]}_1080.m3u8"

    else:
        segments_playlist_info = max(
            playlist.playlists,
            key=lambda p: p.stream_info.resolution[0] * p.stream_info.resolution[1],
        )
        segments_url = segments_playlist_info.absolute_uri

    return fetch_m3u8_segment_playlist(config, segments_url, cookies=cookies)


def _try_direct_download(
    config: FanslyConfig,
    m3u8_url: str,
    output_path: Path,
    cookies: dict[str, str],
) -> bool:
    """Try downloading HLS video directly using ffmpeg (fast path).

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
        print_debug(f"Attempting direct HLS download from: {m3u8_url}")
        print_debug(f"Target output path: {output_path}")
        print_info("Trying fast path: letting FFmpeg handle HLS stream directly...")

        # Fetch the master playlist to get the highest quality variant URL
        m3u8_base_url, m3u8_file_url = split_url(m3u8_url)

        stream_response = config.get_api().get_with_ngsw(
            url=m3u8_file_url,
            cookies=cookies,
            add_fansly_headers=False,
        )

        master_playlist = M3U8(content=stream_response.text, base_uri=m3u8_base_url)

        # Get highest quality variant URL
        if len(master_playlist.playlists) > 0:
            variant_info = max(
                master_playlist.playlists,
                key=lambda p: p.stream_info.resolution[0] * p.stream_info.resolution[1],
            )
            variant_url = variant_info.absolute_uri
        else:
            # Fallback to guessing 1080p
            variant_url = f"{m3u8_url.split('.m3u8')[0]}_1080.m3u8"

        print_debug(f"Using variant URL: {variant_url}")

        # Format cookies for ffmpeg header
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

        print_debug(f"Direct HLS command: {' '.join(stream.get_args())}")

        # Run ffmpeg (synchronous - ffmpeg-python doesn't have async support)
        stream.run(capture_stdout=True, capture_stderr=True, quiet=True)

        # Verify file exists and has content
        if output_path.exists() and output_path.stat().st_size > 0:
            print_info(
                f"✓ Fast path succeeded! Downloaded via direct HLS "
                f"({output_path.stat().st_size:,} bytes)"
            )
            print_debug(f"Saved to: {output_path}")
            return True

        print_warning("Direct HLS download produced invalid file")

    except ffmpeg.Error as e:
        stderr = e.stderr.decode() if e.stderr else str(e)
        print_debug(f"Direct HLS download failed: {stderr}")
        print_info("Fast path failed, falling back to segment download...")
        return False
    except Exception as e:
        print_debug(f"Direct HLS download failed: {e!s}")
        print_info("Fast path failed, falling back to segment download...")
        return False
    else:
        return False


def _try_segment_download(
    config: FanslyConfig,
    m3u8_url: str,
    output_path: Path,
    cookies: dict[str, str],
    created_at: int | None = None,
) -> Path:
    """Download HLS video by manually fetching each segment (robust fallback).

    This approach downloads each .ts segment individually, creates a local
    concat file, then uses ffmpeg to merge. Provides:
    - Fine-grained control over authentication
    - Per-segment progress tracking
    - Retry logic for individual segments

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

    # region Nested function to download TS segments
    def download_ts(segment_uri: str, segment_full_path: Path) -> None:
        print_debug(f"Downloading segment: {segment_uri} -> {segment_full_path}")
        segment_response = None
        try:
            # Bypass rate limiting for TS segments to avoid slowing down video downloads
            segment_response = config.get_api().get_with_ngsw(
                url=segment_uri,
                cookies=cookies,
                stream=True,
                add_fansly_headers=False,
                bypass_rate_limit=True,
            )
            if segment_response.status_code != 200:
                print_debug(
                    f"Segment download failed with status {segment_response.status_code}: {segment_uri}"
                )
                return
            with segment_full_path.open("wb") as ts_file:
                for chunk in segment_response.iter_bytes(chunk_size):
                    if chunk:
                        ts_file.write(chunk)
            if segment_full_path.exists():
                print_debug(
                    f"Segment downloaded successfully: {segment_full_path} ({segment_full_path.stat().st_size} bytes)"
                )
            else:
                print_debug(f"Segment file missing after download: {segment_full_path}")
        except Exception as e:
            print_debug(f"Error downloading segment {segment_uri}: {e!s}")
        finally:
            # Important: Close streaming response to free resources
            if segment_response is not None:
                segment_response.close()

    # endregion

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

    ffmpeg_list_file = video_path / "_ffmpeg_concat_.ffc"

    try:
        print_debug(f"Starting segment download with {len(segment_files)} segments")
        print_debug(f"First segment: {segment_uris[0] if segment_uris else 'None'}")
        print_debug(f"Target path: {output_path}")

        # Use a limited thread pool to avoid too many semaphores
        max_workers = min(
            16, max(4, len(segment_files) // 4)
        )  # Between 4 and 16 workers
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

        # Check multi-threaded downloads
        missing_segments = [file for file in segment_files if not file.exists()]
        if missing_segments:
            print_debug(f"Missing segments: {missing_segments}")
            raise M3U8Error(f"Stream segments failed to download: {missing_segments}")

        print_debug("All segments downloaded, concatenating with ffmpeg")

        # Use ffmpeg-python for concatenation
        with ffmpeg_list_file.open("w", encoding="utf-8") as list_file:
            list_file.write("ffconcat version 1.0\n")
            list_file.writelines([f"file '{f.name}'\n" for f in segment_files])

        # Build and run ffmpeg concat command
        stream = (
            ffmpeg.input(str(ffmpeg_list_file), f="concat", safe=0)
            .output(str(output_path), c="copy")
            .overwrite_output()
        )

        print_debug(f"FFmpeg concat command: {' '.join(stream.get_args())}")

        try:
            stream.run(capture_stdout=True, capture_stderr=True, quiet=True)

            if output_path.exists():
                print_debug(
                    f"ffmpeg successful, output file exists: {output_path} ({output_path.stat().st_size} bytes)"
                )
            else:
                print_debug(f"ffmpeg completed but output file missing: {output_path}")
                raise M3U8Error("ffmpeg completed but output file is missing")

            # Set file timestamps if created_at is provided
            if created_at:
                import os

                os.utime(output_path, (created_at, created_at))

            print_info(
                f"✓ Segment download succeeded ({output_path.stat().st_size:,} bytes)"
            )
            print_debug(f"Saved to: {output_path}")

        except ffmpeg.Error as ex:
            stderr = ex.stderr.decode() if ex.stderr else str(ex)
            raise M3U8Error(f"Error running ffmpeg concat: {stderr}")
        else:
            return output_path

    finally:
        # region Clean up
        ffmpeg_list_file.unlink(missing_ok=True)

        for file in segment_files:
            file.unlink(missing_ok=True)
        # endregion


def download_m3u8(
    config: FanslyConfig,
    m3u8_url: str,
    save_path: Path,
    created_at: int | None = None,
) -> Path:
    """Download M3U8 content as MP4 using two-tier strategy.

    Strategy:
    1. **Direct ffmpeg download** (fast path):
       - Let ffmpeg handle the entire HLS stream directly
       - Works for most standard HLS streams
       - Minimal overhead, fastest approach (10-100x faster)

    2. **Manual segment download** (robust fallback):
       - Download each .ts segment individually
       - Handle complex authentication
       - Progress tracking per segment

    :param config: The downloader configuration.
    :type config: FanslyConfig

    :param m3u8_url: The URL string of the M3U8 to download.
    :type m3u8_url: str

    :param save_path: The suggested file to save the video to.
        This will be changed to MP4 (.mp4).
    :type save_path: Path

    :param created_at: Optional Unix timestamp to set as file modification time.
    :type created_at: Optional[int]

    :return: The file path of the MPEG-4 download/conversion.
    :rtype: Path
    """
    cookies = get_m3u8_cookies(m3u8_url)

    video_path = save_path.parent
    full_path = video_path / f"{save_path.stem}.mp4"

    try:
        # Step 1: Try direct ffmpeg download first (fast path)
        if _try_direct_download(config, m3u8_url, full_path, cookies):
            # Set file timestamps if created_at is provided
            if created_at:
                import os

                os.utime(full_path, (created_at, created_at))
            return full_path

        # Step 2: Direct failed, fall back to manual segment download
        return _try_segment_download(config, m3u8_url, full_path, cookies, created_at)

    except Exception as e:
        print_error(f"Failed to download HLS video from {m3u8_url}: {e}")
        raise M3U8Error(f"Failed to download HLS video: {e}") from e
