"""Media variant selection and download URL resolution.

Selects the best variant (highest resolution) from cached Media objects
and populates transient download fields.
"""

from __future__ import annotations

from config.logging import textio_logger
from download.downloadstate import DownloadState
from metadata.models import Media, get_store
from textio.prompts import wait_for_enter


def simplify_mimetype(mimetype: str) -> str:
    """Normalize MIME types from Fansly API quirks."""
    if mimetype == "application/vnd.apple.mpegurl":
        return "video/mp4"
    if mimetype == "audio/mp4":
        return "audio/mp3"
    return mimetype


def _get_best_location_url(media: Media) -> str | None:
    """Get the raw CDN URL (with auth params) from a Media's first location."""
    if media.locations:
        loc = media.locations[0]
        return loc.raw_url or loc.location
    return None


def _select_best_variant(media: Media) -> Media | None:
    """Select the highest-resolution variant matching the parent's mimetype."""
    if not media.variants:
        return None

    parent_mime = simplify_mimetype(media.mimetype or "")

    matching = [
        v
        for v in media.variants
        if v.locations and simplify_mimetype(v.mimetype or "") == parent_mime
    ]

    if not matching:
        return None

    return max(matching, key=lambda v: (v.width or 0) * (v.height or 0))


def _build_m3u8_auth_url(variant: Media) -> str | None:
    """Construct auth URL for m3u8 streams missing Key-Pair-Id."""
    url = _get_best_location_url(variant)
    if not url or "Key-Pair-Id" in url:
        return url

    # m3u8 auth tokens live in location metadata
    if not variant.locations or not hasattr(variant.locations[0], "metadata"):
        return url

    # Location metadata comes from the API as a dict on the location object
    # For m3u8, we need Policy, Key-Pair-Id, Signature from it
    loc = variant.locations[0]
    meta = getattr(loc, "metadata", None)
    if not isinstance(meta, dict):
        return url

    try:
        return (
            f"{url}?ngsw-bypass=true"
            f"&Policy={meta['Policy']}"
            f"&Key-Pair-Id={meta['Key-Pair-Id']}"
            f"&Signature={meta['Signature']}"
        )
    except KeyError:
        return url


def parse_media_info(
    state: DownloadState,
    media_info: dict,
    post_id: str | None = None,
    *,
    interactive: bool = False,
) -> Media:
    """Select best variant and populate download fields on a cached Media object.

    The Media and its variants are already in the identity map (persisted by
    process_media_info earlier). This function selects the best resolution
    variant and sets transient download fields.
    """
    store = get_store()

    # Determine preview vs regular
    is_preview = media_info["previewId"] is not None
    if is_preview and media_info.get("access"):
        is_preview = False

    # Get the primary and preview Media from identity map
    content_key = "preview" if is_preview else "media"
    content_dict = media_info[content_key]
    media_id = (
        int(content_dict["id"])
        if isinstance(content_dict["id"], str)
        else content_dict["id"]
    )

    # Retrieve from identity map — should already be cached from process_media_info
    media = store.get_from_cache(Media, media_id)
    if media is None:
        # Fallback: construct from dict (first time seeing this media)
        media = Media.model_validate(content_dict)

    media.mimetype = simplify_mimetype(media.mimetype or "")
    media.is_preview = is_preview

    # Also track the default (non-variant) media ID for variant linking
    default_media_dict = media_info["media"]
    media.default_normal_id = (
        int(default_media_dict["id"])
        if isinstance(default_media_dict["id"], str)
        else default_media_dict["id"]
    )

    # Select best variant
    best = _select_best_variant(media)

    if best:
        # Use variant's location as download URL
        download_url = _build_m3u8_auth_url(best)
        use_variant = True

        # Fall back to default if it has higher resolution
        default_url = _get_best_location_url(media)
        if default_url and (media.height or 0) > (best.height or 0):
            download_url = default_url
            use_variant = False

        media.download_url = download_url

        # Track variant ID for filename without mutating cached identity
        if use_variant:
            media.download_id = best.id

    else:
        # No suitable variant — use default media location
        media.download_url = _get_best_location_url(media)

    # If still no URL and there's a preview, try preview variants
    if not media.download_url and "preview" in media_info and not is_preview:
        preview_dict = media_info["preview"]
        preview_id = (
            int(preview_dict["id"])
            if isinstance(preview_dict["id"], str)
            else preview_dict["id"]
        )
        preview_media = store.get_from_cache(Media, preview_id)
        if preview_media is None:
            preview_media = Media.model_validate(preview_dict)

        preview_best = _select_best_variant(preview_media)
        if preview_best:
            media.download_url = _build_m3u8_auth_url(preview_best)
        elif preview_media.locations:
            media.download_url = _get_best_location_url(preview_media)

    # Set file extension from URL
    if media.download_url:
        ext = media.download_url.split("/")[-1].split(".")[-1].split("?")[0]
        if ext == "mp4" and media.mimetype == "audio/mp3":
            ext = "mp3"
        media.file_extension = ext

        # Track video media IDs (use variant ID if selected)
        effective_id = media.download_id or media.id
        if media.mimetype and media.mimetype.startswith("video/"):
            state.recent_video_media_ids.add(str(effective_id))

        # Warn about missing m3u8 metadata
        if "Key-Pair-Id" not in media.download_url:
            textio_logger.opt(depth=1).log(
                "ERROR",
                f"<red>[14]</red> Failed downloading a video! Please open a GitHub issue "
                f"ticket called 'Metadata missing' and copy paste this:\n"
                f"\n\tMetadata Missing\n\tpost_id: {post_id} & media_id: {effective_id} "
                f"& creator username: {state.creator_name}\n",
            )
            if interactive:
                wait_for_enter("Press Enter to attempt continue downloading ...")

    # Set preview fields
    if "preview" in media_info:
        preview_dict = media_info["preview"]
        media.preview_id = (
            int(preview_dict["id"])
            if isinstance(preview_dict["id"], str)
            else preview_dict["id"]
        )
        media.preview_mimetype = simplify_mimetype(preview_dict["mimetype"])
        if preview_dict.get("locations"):
            media.preview_url = preview_dict["locations"][0]["location"]

    return media
