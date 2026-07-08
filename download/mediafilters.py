"""Media-filter gates (size/duration) and meta_info observation recording."""

import asyncio
import json

from m3u8 import M3U8

from config import FanslyConfig
from config.media_filters import MediaFilters
from download.downloadstate import DownloadState
from download.m3u8 import get_m3u8_cookies
from download.types import DownloadType
from errors import MediaFilteredError
from helpers.web import split_url
from metadata.models import Media, get_store
from textio import print_debug


def resolve_media_filters(
    config: FanslyConfig, state: DownloadState
) -> MediaFilters | None:
    """Per-creator resolved limits, or None when filtering is exempt/inactive."""
    if state.download_type == DownloadType.SINGLE:
        return None
    if not config.media_filters.is_active and not config.media_filters.by_creator:
        return None
    resolved = config.media_filters.for_creator(state.creator_name, state.creator_id)
    return resolved if resolved.is_active else None


def _size_evidence(media: Media) -> int | None:
    """Best size evidence from meta_info: observed wins over estimated."""
    if not media.meta_info:
        return None
    try:
        payload = json.loads(media.meta_info)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("observedContentLength", "estimatedContentLength"):
        value = payload.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return int(value)
    return None


def check_media_filters(
    config: FanslyConfig, state: DownloadState, media: Media
) -> str | None:
    """Metadata gate: violated-limit name, or None to proceed."""
    filters = resolve_media_filters(config, state)
    if filters is None:
        return None
    if (media.mimetype or "").startswith("video/"):
        verdict = filters.duration_verdict(media.duration)
        if verdict:
            return verdict
    return filters.size_verdict(_size_evidence(media))


async def record_filter_observation(
    media: Media,
    *,
    reason: str,
    observed: int | None = None,
    estimated: int | None = None,
) -> None:
    """Merge filter observations into meta_info; never touches download state."""
    raw = media.meta_info
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        print_debug(
            f"media_filters: meta_info for media {media.id} is not valid JSON; "
            "skipping observation merge."
        )
        return
    if not isinstance(payload, dict):
        print_debug(
            f"media_filters: meta_info for media {media.id} is not a JSON object; "
            "skipping observation merge."
        )
        return
    payload["lastFilteredReason"] = reason
    if observed is not None:
        payload["observedContentLength"] = observed
    if estimated is not None:
        payload["estimatedContentLength"] = estimated
    media.meta_info = json.dumps(payload)
    await get_store().save(media)


async def estimate_stream_size_gate(
    config: FanslyConfig, state: DownloadState, media: Media
) -> None:
    """Bitrate x duration estimate gate for HLS VODs; passes on unknowns.

    Raises:
        MediaFilteredError: When the estimated size violates a limit.
    """
    filters = resolve_media_filters(config, state)
    if filters is None or (
        filters.file_size_min is None and filters.file_size_max is None
    ):
        return
    duration = media.duration
    download_url = media.download_url
    if not duration or not download_url:
        return

    def _fetch_master_text() -> str | None:
        cookies = get_m3u8_cookies(download_url)
        _, file_url = split_url(download_url)
        response = config.get_api().get_with_ngsw_sync(
            url=file_url,
            cookies=cookies,
            add_fansly_headers=False,
            bypass_rate_limit=True,
        )
        if response.status_code != 200:
            return None
        return response.text

    try:
        master_text = await asyncio.to_thread(_fetch_master_text)
        if not master_text:
            return
        base_url, _ = split_url(download_url)
        playlist = M3U8(content=master_text, base_uri=base_url)
        if not playlist.playlists:
            return
        best = max(
            playlist.playlists,
            key=lambda p: (
                (p.stream_info.resolution or (0, 0))[0]
                * (p.stream_info.resolution or (0, 0))[1]
            ),
        )
        bandwidth = best.stream_info.average_bandwidth or best.stream_info.bandwidth
        if not bandwidth:
            return
    except Exception as e:
        print_debug(
            f"media_filters: could not estimate stream size for media {media.id}: {e}"
        )
        return
    estimated = int(bandwidth / 8 * duration)
    reason = filters.size_verdict(estimated)
    if reason:
        raise MediaFilteredError(reason, estimated=estimated)
