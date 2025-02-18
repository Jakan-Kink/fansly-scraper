"""M3U8 Media Download Handling"""

import concurrent.futures
from pathlib import Path
from subprocess import CalledProcessError
from typing import Any

# from memory_profiler import profile
from m3u8 import M3U8
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Column

from config.fanslyconfig import FanslyConfig
from errors import M3U8Error
from helpers.ffmpeg import run_ffmpeg
from helpers.web import get_file_name_from_url, get_qs_value, split_url
from textio import print_error, print_warning


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

    with config.get_api().get_with_ngsw(
        url=m3u8_file_url,
        cookies=cookies,
        add_fansly_headers=False,
    ) as stream_response:

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


# @profile(precision=2, stream=open('memory_use.log', 'w', encoding='utf-8'))
def download_m3u8(
    config: FanslyConfig,
    m3u8_url: str,
    save_path: Path,
    created_at: int | None = None,
) -> Path:
    """Download M3U8 content as MP4.

    :param config: The downloader configuration.
    :type config: FanslyConfig

    :param m3u8_url: The URL string of the M3U8 to download.
    :type m3u8_url: str

    :param save_path: The suggested file to save the video to.
        This will be changed to MP4 (.mp4).
    :type save_path: Path

    :return: The file path of the MPEG-4 download/conversion.
    :rtype: Path
    """
    CHUNK_SIZE = 1_048_576

    cookies = get_m3u8_cookies(m3u8_url)

    video_path = save_path.parent
    full_path = video_path / f"{save_path.stem}.mp4"

    playlist = fetch_m3u8_segment_playlist(config, m3u8_url)

    # region Nested function to download TS segments
    def download_ts(segment_uri: str, segment_full_path: Path) -> None:
        print_debug(f"Downloading segment: {segment_uri} -> {segment_full_path}")
        try:
            with config.get_api().get_with_ngsw(
                url=segment_uri,
                cookies=cookies,
                stream=True,
                add_fansly_headers=False,
            ) as segment_response:
                if segment_response.status_code != 200:
                    print_debug(
                        f"Segment download failed with status {segment_response.status_code}: {segment_uri}"
                    )
                    return
                with open(segment_full_path, "wb") as ts_file:
                    for chunk in segment_response.iter_content(CHUNK_SIZE):
                        if chunk is not None:
                            ts_file.write(chunk)
            if segment_full_path.exists():
                print_debug(
                    f"Segment downloaded successfully: {segment_full_path} ({segment_full_path.stat().st_size} bytes)"
                )
            else:
                print_debug(f"Segment file missing after download: {segment_full_path}")
        except Exception as e:
            print_debug(f"Error downloading segment {segment_uri}: {str(e)}")

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
        from textio import print_debug

        print_debug(f"Starting m3u8 download with {len(segment_files)} segments")
        print_debug(f"First segment: {segment_uris[0] if segment_uris else 'None'}")
        print_debug(f"Target path: {full_path}")

        # Use a limited thread pool to avoid too many semaphores
        max_workers = min(
            16, max(4, len(segment_files) // 4)
        )  # Between 4 and 16 workers
        with progress:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max_workers
            ) as executor:
                list(
                    progress.track(
                        executor.map(download_ts, segment_uris, segment_files),
                        total=len(segment_files),
                        description=f"Downloading segments ({max_workers} threads)",
                    )
                )

        # Check multi-threaded downloads
        missing_segments = []
        for file in segment_files:
            if not file.exists():
                missing_segments.append(file)
        if missing_segments:
            print_debug(f"Missing segments: {missing_segments}")
            raise M3U8Error(f"Stream segments failed to download: {missing_segments}")

        print_debug("All segments downloaded, creating ffmpeg list file")

        with open(ffmpeg_list_file, "w", encoding="utf-8") as list_file:
            list_file.write("ffconcat version 1.0\n")
            list_file.writelines([f"file '{f.name}'\n" for f in segment_files])

        args = [
            "-f",
            "concat",
            "-i",
            str(ffmpeg_list_file),
            "-c",
            "copy",
            "-y",  # Always overwrite output file
            str(full_path),
        ]

        print_debug(f"Running ffmpeg with args: {args}")
        try:
            run_ffmpeg(args)
            if full_path.exists():
                print_debug(
                    f"ffmpeg successful, output file exists: {full_path} ({full_path.stat().st_size} bytes)"
                )
            else:
                print_debug(f"ffmpeg completed but output file missing: {full_path}")
                raise M3U8Error("ffmpeg completed but output file is missing")

            # Set file timestamps if created_at is provided
            if created_at:
                import os

                os.utime(full_path, (created_at, created_at))

            return full_path

        except CalledProcessError as ex:
            raise M3U8Error(
                f"Error running ffmpeg - exit code {ex.returncode}: {ex.stderr}"
            )

    finally:
        # region Clean up

        ffmpeg_list_file.unlink(missing_ok=True)

        for file in segment_files:
            file.unlink(missing_ok=True)

        # endregion
