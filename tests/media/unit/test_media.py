"""Tests for media/media.py — variant selection, URL resolution, download field population."""

from unittest.mock import patch

import pytest

from download.downloadstate import DownloadState
from errors import MediaFilteredError
from media.media import (
    _build_m3u8_auth_url,
    _fits_cap,
    _get_best_location_url,
    _select_best_variant,
    parse_media_info,
    simplify_mimetype,
)
from metadata.models import Account, Media, MediaLocation
from tests.fixtures.utils.test_isolation import snowflake_id


# ── Helpers ──────────────────────────────────────────────────────────────


def _media_with_locations(
    account_id,
    *,
    mimetype="image/jpeg",
    url="https://cdn.fansly.com/img.jpeg?Policy=x&Key-Pair-Id=K&Signature=s",
    width=1920,
    height=1080,
):
    """Build a Media with one location via direct construction.

    Uses MediaLocation(location=url) which triggers _normalize_location
    validator to split raw_url (full URL with params) from location
    (normalized for DB dedup).
    """
    mid = snowflake_id()
    m = Media(
        id=mid, accountId=account_id, mimetype=mimetype, width=width, height=height
    )
    loc = MediaLocation(mediaId=mid, locationId=1, location=url)
    m.locations = [loc]
    return m


def _media_info_dict(
    account_id,
    media,
    *,
    preview=None,
    preview_id_val=None,
    access=True,
):
    """Build the dict that parse_media_info expects (accountMedia shape)."""
    media_sub = {
        "id": media.id,
        "accountId": account_id,
        "mimetype": media.mimetype,
    }
    d = {
        "id": snowflake_id(),
        "accountId": account_id,
        "mediaId": media.id,
        "media": media_sub,
        "previewId": preview_id_val,
        "access": access,
        "createdAt": 1700000000,
        "deleted": False,
    }
    if preview is not None:
        d["preview"] = {
            "id": preview.id,
            "accountId": account_id,
            "mimetype": preview.mimetype or "image/jpeg",
            "locations": [
                {
                    "locationId": 1,
                    "location": (
                        preview.locations[0].raw_url
                        if preview.locations
                        else "https://cdn.fansly.com/preview.jpg"
                    ),
                }
            ],
        }
        d["previewId"] = preview.id
    return d


# ── simplify_mimetype ────────────────────────────────────────────────────


class TestSimplifyMimetype:
    def test_mpegurl_to_mp4(self):
        assert simplify_mimetype("application/vnd.apple.mpegurl") == "video/mp4"

    def test_audio_mp4_to_mp3(self):
        assert simplify_mimetype("audio/mp4") == "audio/mp3"

    def test_passthrough(self):
        assert simplify_mimetype("image/jpeg") == "image/jpeg"
        assert simplify_mimetype("video/mp4") == "video/mp4"


# ── _get_best_location_url ──────────────────────────────────────────────


class TestGetBestLocationUrl:
    def test_returns_raw_url_when_present(self):
        acct = snowflake_id()
        m = _media_with_locations(acct, url="https://cdn.fansly.com/img.jpg?token=abc")
        result = _get_best_location_url(m)
        assert result == "https://cdn.fansly.com/img.jpg?token=abc"

    def test_falls_back_to_location_when_no_raw_url(self):
        m = Media(id=snowflake_id(), accountId=snowflake_id())
        assert m.id is not None
        loc = MediaLocation(mediaId=m.id, locationId=1, location="https://cdn/img.jpg")
        object.__setattr__(loc, "raw_url", None)
        m.locations = [loc]
        result = _get_best_location_url(m)
        assert result == "https://cdn/img.jpg"

    def test_no_locations_returns_none(self):
        m = Media(id=snowflake_id(), accountId=snowflake_id())
        assert _get_best_location_url(m) is None


# ── _select_best_variant ────────────────────────────────────────────────


class TestSelectBestVariant:
    def test_no_variants_returns_none(self):
        m = Media(id=snowflake_id(), accountId=snowflake_id(), mimetype="image/jpeg")
        assert _select_best_variant(m) is None

    def test_no_matching_mime_returns_none(self):
        acct = snowflake_id()
        parent = Media(id=snowflake_id(), accountId=acct, mimetype="image/jpeg")
        # Variant has video mime — doesn't match parent
        variant = _media_with_locations(
            acct, mimetype="video/mp4", width=1280, height=720
        )
        parent.variants = [variant]
        assert _select_best_variant(parent) is None

    def test_variant_without_locations_skipped(self):
        acct = snowflake_id()
        parent = Media(id=snowflake_id(), accountId=acct, mimetype="image/jpeg")
        # Variant has matching mime but no locations
        variant = Media(
            id=snowflake_id(),
            accountId=acct,
            mimetype="image/jpeg",
            width=800,
            height=600,
        )
        parent.variants = [variant]
        assert _select_best_variant(parent) is None

    def test_selects_highest_resolution(self):
        acct = snowflake_id()
        parent = Media(id=snowflake_id(), accountId=acct, mimetype="image/jpeg")
        small = _media_with_locations(
            acct, mimetype="image/jpeg", width=640, height=480
        )
        large = _media_with_locations(
            acct, mimetype="image/jpeg", width=1920, height=1080
        )
        parent.variants = [small, large]
        assert _select_best_variant(parent) is large

    def test_mpegurl_normalised_match(self):
        """Variant with application/vnd.apple.mpegurl matches parent video/mp4."""
        acct = snowflake_id()
        parent = Media(id=snowflake_id(), accountId=acct, mimetype="video/mp4")
        variant = _media_with_locations(
            acct,
            mimetype="application/vnd.apple.mpegurl",
            url="https://cdn.fansly.com/stream.m3u8?Key-Pair-Id=K",
            width=1920,
            height=1080,
        )
        parent.variants = [variant]
        assert _select_best_variant(parent) is variant


class TestSelectBestVariantCap:
    def test_downscales_to_fitting_variant(self):
        acct = snowflake_id()
        parent = Media(id=snowflake_id(), accountId=acct, mimetype="video/mp4")
        v720 = _media_with_locations(acct, mimetype="video/mp4", width=1280, height=720)
        v1080 = _media_with_locations(
            acct, mimetype="video/mp4", width=1920, height=1080
        )
        v4k = _media_with_locations(acct, mimetype="video/mp4", width=3840, height=2160)
        parent.variants = [v720, v1080, v4k]
        best = _select_best_variant(parent, 1080)
        assert best is v1080

    def test_none_when_no_variant_fits(self):
        acct = snowflake_id()
        parent = Media(id=snowflake_id(), accountId=acct, mimetype="video/mp4")
        v4k = _media_with_locations(acct, mimetype="video/mp4", width=3840, height=2160)
        parent.variants = [v4k]
        assert _select_best_variant(parent, 1080) is None

    def test_no_cap_picks_highest(self):
        acct = snowflake_id()
        parent = Media(id=snowflake_id(), accountId=acct, mimetype="video/mp4")
        v1080 = _media_with_locations(
            acct, mimetype="video/mp4", width=1920, height=1080
        )
        v4k = _media_with_locations(acct, mimetype="video/mp4", width=3840, height=2160)
        parent.variants = [v1080, v4k]
        best = _select_best_variant(parent, None)
        assert best is v4k

    def test_portrait_qualifies_by_shorter_edge(self):
        acct = snowflake_id()
        parent = Media(id=snowflake_id(), accountId=acct, mimetype="video/mp4")
        portrait = _media_with_locations(
            acct, mimetype="video/mp4", width=1080, height=1920
        )
        parent.variants = [portrait]
        assert _select_best_variant(parent, 1080) is portrait

    def test_unknown_resolution_variant_excluded_by_cap(self):
        """A variant missing width/height never qualifies once a cap is set."""
        acct = snowflake_id()
        parent = Media(id=snowflake_id(), accountId=acct, mimetype="video/mp4")
        unknown = _media_with_locations(acct, mimetype="video/mp4")
        unknown.width = None
        unknown.height = None
        parent.variants = [unknown]
        assert _select_best_variant(parent, 1080) is None


# ── _fits_cap ────────────────────────────────────────────────────────────


class TestFitsCap:
    def test_uncapped_fits_with_unknown_width(self):
        assert _fits_cap(None, 2000, None) is True

    def test_uncapped_fits_with_unknown_height(self):
        assert _fits_cap(1000, None, None) is True

    def test_uncapped_fits_with_both_unknown(self):
        assert _fits_cap(None, None, None) is True

    def test_capped_requires_both_dimensions_known(self):
        assert _fits_cap(None, 2000, 1080) is False
        assert _fits_cap(1000, None, 1080) is False

    def test_capped_shorter_edge_within_cap(self):
        assert _fits_cap(1920, 1080, 1080) is True

    def test_capped_shorter_edge_exceeds_cap(self):
        assert _fits_cap(3840, 2160, 1080) is False


# ── _build_m3u8_auth_url ────────────────────────────────────────────────


class TestBuildM3u8AuthUrl:
    def test_none_url_returns_none(self):
        m = Media(id=snowflake_id(), accountId=snowflake_id())
        assert _build_m3u8_auth_url(m) is None

    def test_already_has_keypair_returns_url_unchanged(self):
        acct = snowflake_id()
        m = _media_with_locations(
            acct,
            url="https://cdn.fansly.com/v.m3u8?Key-Pair-Id=KP123&Policy=p&Signature=s",
        )
        result = _build_m3u8_auth_url(m)
        assert result is not None
        assert "Key-Pair-Id=KP123" in result

    def test_no_locations_returns_url(self):
        """Has a location for URL extraction but then we clear locations — shouldn't happen
        in practice, but exercises the guard at line 57."""
        m = Media(id=snowflake_id(), accountId=snowflake_id())
        assert m.id is not None
        loc = MediaLocation(mediaId=m.id, locationId=1, location="https://cdn/v.m3u8")
        m.locations = [loc]
        # Remove metadata attr to trigger the hasattr guard
        object.__delattr__(loc, "metadata") if hasattr(loc, "metadata") else None
        result = _build_m3u8_auth_url(m)
        # Returns URL since no Key-Pair-Id and no metadata → falls through
        assert result is not None

    def test_metadata_not_dict_returns_url(self):
        acct = snowflake_id()
        m = _media_with_locations(acct, url="https://cdn.fansly.com/v.m3u8?ngsw=1")
        m.locations[0].metadata = "not_a_dict"
        result = _build_m3u8_auth_url(m)
        assert result is not None
        assert "Key-Pair-Id" not in result

    def test_metadata_missing_key_returns_url(self):
        acct = snowflake_id()
        m = _media_with_locations(acct, url="https://cdn.fansly.com/v.m3u8")
        m.locations[0].metadata = {"Policy": "p"}  # Missing Key-Pair-Id and Signature
        result = _build_m3u8_auth_url(m)
        assert result is not None
        assert "Key-Pair-Id" not in result  # KeyError caught, returns original

    def test_full_auth_construction(self):
        acct = snowflake_id()
        m = _media_with_locations(acct, url="https://cdn.fansly.com/v.m3u8")
        m.locations[0].metadata = {
            "Policy": "pol123",
            "Key-Pair-Id": "KP456",
            "Signature": "sig789",
        }
        result = _build_m3u8_auth_url(m)
        assert result is not None
        assert "ngsw-bypass=true" in result
        assert "Policy=pol123" in result
        assert "Key-Pair-Id=KP456" in result
        assert "Signature=sig789" in result


# ── parse_media_info ────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.xdist_group("media_parse_media_info")
class TestParseMediaInfo:
    """Integration tests — need entity_store for identity map lookups.

    These share ONE class-scoped database (``reset_class_store``): every method
    reads the store's identity-map cache by a unique snowflake id, so clearing
    the in-memory cache between methods gives each a clean lookup while writes
    stay namespaced by id. Saves 19 per-test UUID databases.
    """

    async def test_regular_media_from_cache(self, reset_class_store):
        """Lines 89-111: non-preview, media found in cache, mimetype simplified."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = _media_with_locations(
            acct_id,
            mimetype="application/vnd.apple.mpegurl",
            url="https://cdn.fansly.com/vid.m3u8?Key-Pair-Id=K&Policy=p&Signature=s",
            width=1920,
            height=1080,
        )
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.id == media.id
        assert result.mimetype == "video/mp4"  # Simplified
        assert result.is_preview is False

    async def test_preview_media_selected(self, reset_class_store):
        """Lines 92-94, 97-98: previewId set + access=False → is_preview, uses preview dict."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        preview = _media_with_locations(acct_id, mimetype="image/jpeg")
        main_media = _media_with_locations(acct_id, mimetype="video/mp4")
        await reset_class_store.save(preview)
        await reset_class_store.save(main_media)

        info = _media_info_dict(
            acct_id,
            main_media,
            preview=preview,
            preview_id_val=preview.id,
            access=False,
        )
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.is_preview is True
        assert result.id == preview.id

    async def test_preview_override_when_access_true(self, reset_class_store):
        """Lines 93-94: previewId set but access=True → is_preview overridden to False."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        preview = _media_with_locations(acct_id, mimetype="image/jpeg")
        main_media = _media_with_locations(acct_id, mimetype="video/mp4")
        await reset_class_store.save(preview)
        await reset_class_store.save(main_media)

        info = _media_info_dict(
            acct_id, main_media, preview=preview, preview_id_val=preview.id, access=True
        )
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.is_preview is False
        assert result.id == main_media.id  # Uses main, not preview

    async def test_cache_miss_fallback(self, reset_class_store):
        """Lines 107-109: media not in cache → Media.model_validate fallback."""
        acct_id = snowflake_id()
        mid = snowflake_id()

        info = {
            "id": snowflake_id(),
            "accountId": acct_id,
            "mediaId": mid,
            "media": {
                "id": mid,
                "accountId": acct_id,
                "mimetype": "image/jpeg",
                "locations": [
                    {
                        "locationId": 1,
                        "location": "https://cdn.fansly.com/img.jpg?Key-Pair-Id=K",
                    },
                ],
            },
            "previewId": None,
            "access": True,
            "createdAt": 1700000000,
            "deleted": False,
        }
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.id == mid
        assert result.download_url is not None

    async def test_string_id_coercion(self, reset_class_store):
        """Lines 100-103, 117-120: string IDs in dict are coerced to int."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = _media_with_locations(acct_id, mimetype="image/jpeg")
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        # Force string IDs
        info["media"]["id"] = str(media.id)
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.id == media.id
        assert result.default_normal_id == media.id

    async def test_variant_selected_with_download_url(self, reset_class_store):
        """Lines 123-140: best variant selected, variant URL used, download_id set."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/default.mp4?Key-Pair-Id=K",
            width=1280,
            height=720,
        )
        variant = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/variant.mp4?Key-Pair-Id=K&Policy=p&Signature=s",
            width=1920,
            height=1080,
        )
        media.variants = [variant]
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.download_url is not None
        assert "variant" in result.download_url
        assert result.download_id == variant.id

    async def test_default_higher_res_overrides_variant(self, reset_class_store):
        """Lines 131-134: default media has higher resolution than variant → use default URL."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/default_4k.mp4?Key-Pair-Id=K",
            width=3840,
            height=2160,
        )
        variant = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/variant_720p.mp4?Key-Pair-Id=K&Policy=p&Signature=s",
            width=1280,
            height=720,
        )
        media.variants = [variant]
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.download_url is not None
        assert "default_4k" in result.download_url
        assert result.download_id is None  # use_variant=False → no download_id

    async def test_default_higher_res_overrides_variant_with_unknown_width(
        self, reset_class_store
    ):
        """Uncapped path: a known height alone must win over a lower-res variant,
        even when the default's width is unknown."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/default_2160.mp4?Key-Pair-Id=K",
            width=None,
            height=2160,
        )
        variant = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/variant_720p.mp4?Key-Pair-Id=K&Policy=p&Signature=s",
            width=1280,
            height=720,
        )
        media.variants = [variant]
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.download_url is not None
        assert "default_2160" in result.download_url
        assert result.download_id is None

    async def test_no_variant_uses_default_location(self, reset_class_store):
        """Lines 142-144: no suitable variant → download_url from default media."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = _media_with_locations(
            acct_id,
            mimetype="image/jpeg",
            url="https://cdn.fansly.com/photo.jpeg?Key-Pair-Id=K",
        )
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.download_url is not None
        assert "photo.jpeg" in result.download_url

    async def test_preview_fallback_when_no_url(self, reset_class_store):
        """Lines 147-162: no download URL on main → falls back to preview variant/location."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        main_media = Media(id=snowflake_id(), accountId=acct_id, mimetype="video/mp4")
        preview = _media_with_locations(
            acct_id,
            mimetype="image/jpeg",
            url="https://cdn.fansly.com/preview.jpg?Key-Pair-Id=K",
        )
        await reset_class_store.save(main_media)
        await reset_class_store.save(preview)

        info = _media_info_dict(acct_id, main_media, preview=preview)
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.download_url is not None
        assert "preview" in result.download_url

    async def test_preview_fallback_uses_variant(self, reset_class_store):
        """Lines 158-160: preview has a variant → uses variant URL."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        main_media = Media(id=snowflake_id(), accountId=acct_id, mimetype="video/mp4")
        preview = _media_with_locations(
            acct_id,
            mimetype="image/jpeg",
            url="https://cdn.fansly.com/preview_base.jpg?Key-Pair-Id=K",
        )
        preview_variant = _media_with_locations(
            acct_id,
            mimetype="image/jpeg",
            url="https://cdn.fansly.com/preview_variant.jpg?Key-Pair-Id=K&Policy=p&Signature=s",
            width=1920,
            height=1080,
        )
        preview.variants = [preview_variant]
        await reset_class_store.save(main_media)
        await reset_class_store.save(preview)

        info = _media_info_dict(acct_id, main_media, preview=preview)
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.download_url is not None
        assert "preview_variant" in result.download_url

    async def test_preview_fallback_cache_miss(self, reset_class_store):
        """Lines 155-156: preview not in cache → model_validate fallback."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        main_media = Media(id=snowflake_id(), accountId=acct_id, mimetype="video/mp4")
        await reset_class_store.save(main_media)

        preview_id = snowflake_id()
        info = {
            "id": snowflake_id(),
            "accountId": acct_id,
            "mediaId": main_media.id,
            "media": {
                "id": main_media.id,
                "accountId": acct_id,
                "mimetype": "video/mp4",
            },
            "preview": {
                "id": preview_id,
                "accountId": acct_id,
                "mimetype": "image/jpeg",
                "locations": [
                    {
                        "locationId": 1,
                        "location": "https://cdn.fansly.com/pv.jpg?Key-Pair-Id=K",
                    },
                ],
            },
            "previewId": None,
            "access": True,
            "createdAt": 1700000000,
            "deleted": False,
        }
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.download_url is not None

    async def test_file_extension_extraction(self, reset_class_store):
        """Lines 165-169: extension parsed from URL, mp4→mp3 for audio."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        audio = _media_with_locations(
            acct_id,
            mimetype="audio/mp4",
            url="https://cdn.fansly.com/track.mp4?Key-Pair-Id=K",
        )
        await reset_class_store.save(audio)

        info = _media_info_dict(acct_id, audio)
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.file_extension == "mp3"  # audio/mp4 → audio/mp3, .mp4 → .mp3

    async def test_video_media_id_tracking(self, reset_class_store):
        """Lines 172-174: video media IDs added to state.recent_video_media_ids."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        vid = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/vid.mp4?Key-Pair-Id=K",
        )
        await reset_class_store.save(vid)

        info = _media_info_dict(acct_id, vid)
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert str(result.id) in state.recent_video_media_ids

    async def test_video_tracks_download_id_when_variant(self, reset_class_store):
        """Lines 172-174: effective_id uses download_id (variant) when set."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/default.mp4?Key-Pair-Id=K",
            width=720,
            height=480,
        )
        variant = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/variant_hd.mp4?Key-Pair-Id=K&Policy=p&Signature=s",
            width=1920,
            height=1080,
        )
        media.variants = [variant]
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert str(variant.id) in state.recent_video_media_ids

    async def test_missing_keypair_warns_and_prompts(self, reset_class_store):
        """Lines 177-185: URL without Key-Pair-Id → logs error + calls input()."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/vid.mp4?no_auth=1",
        )
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        state = DownloadState()
        state.creator_name = "test_creator"

        with patch("media.media.input", return_value=""):
            result = await parse_media_info(state, info, post_id="999")
        assert result.download_url is not None

    async def test_preview_fields_populated(self, reset_class_store):
        """Lines 188-197: preview dict → sets preview_id, preview_mimetype, preview_url."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/vid.mp4?Key-Pair-Id=K",
        )
        preview = _media_with_locations(
            acct_id,
            mimetype="image/png",
            url="https://cdn.fansly.com/thumb.png?token=t",
        )
        await reset_class_store.save(media)
        await reset_class_store.save(preview)

        info = _media_info_dict(acct_id, media, preview=preview)
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.preview_id == preview.id
        assert result.preview_mimetype == "image/png"
        assert result.preview_url is not None
        assert "thumb.png" in result.preview_url

    async def test_preview_without_locations(self, reset_class_store):
        """Lines 196-197: preview dict has no locations → preview_url not set."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = _media_with_locations(
            acct_id,
            mimetype="image/jpeg",
            url="https://cdn.fansly.com/img.jpg?Key-Pair-Id=K",
        )
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        # Add preview without locations
        info["preview"] = {
            "id": snowflake_id(),
            "accountId": acct_id,
            "mimetype": "image/jpeg",
        }
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.preview_mimetype == "image/jpeg"
        assert result.preview_url is None

    async def test_no_download_url_no_preview(self, reset_class_store):
        """Lines 142-147: no variant, no locations, no preview → download_url stays None."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = Media(id=snowflake_id(), accountId=acct_id, mimetype="image/jpeg")
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.download_url is None
        assert result.file_extension is None

    async def test_preview_fallback_no_variant_no_locations(self, reset_class_store):
        """Line 161→165: preview exists but has no variant AND no locations."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        main_media = Media(id=snowflake_id(), accountId=acct_id, mimetype="video/mp4")
        preview = Media(id=snowflake_id(), accountId=acct_id, mimetype="image/jpeg")
        await reset_class_store.save(main_media)
        await reset_class_store.save(preview)

        info = _media_info_dict(acct_id, main_media, preview=preview)
        # Override preview dict to have no locations
        info["preview"] = {
            "id": preview.id,
            "accountId": acct_id,
            "mimetype": "image/jpeg",
        }
        state = DownloadState()

        result = await parse_media_info(state, info)
        assert result.download_url is None


@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.xdist_group("media_parse_media_info_cap")
class TestParseMediaInfoMaxResolutionCap:
    """max_px-aware skip/downscale decision in parse_media_info."""

    async def test_downscales_to_fitting_variant(self, reset_class_store):
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/default_4k.mp4?Key-Pair-Id=K",
            width=3840,
            height=2160,
        )
        fitting = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/variant_1080.mp4?Key-Pair-Id=K&Policy=p&Signature=s",
            width=1920,
            height=1080,
        )
        too_big = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/variant_4k.mp4?Key-Pair-Id=K&Policy=p&Signature=s",
            width=3840,
            height=2160,
        )
        media.variants = [fitting, too_big]
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        state = DownloadState()

        result = await parse_media_info(state, info, max_px=1080)
        assert result.download_url is not None
        assert "variant_1080" in result.download_url
        assert result.download_id == fitting.id

    async def test_default_used_when_it_fits_and_no_variant_needed(
        self, reset_class_store
    ):
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/default_1080.mp4?Key-Pair-Id=K",
            width=1920,
            height=1080,
        )
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        state = DownloadState()

        result = await parse_media_info(state, info, max_px=1080)
        assert result.download_url is not None
        assert "default_1080" in result.download_url

    async def test_raises_when_no_rendition_fits_cap(self, reset_class_store):
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/default_4k.mp4?Key-Pair-Id=K",
            width=3840,
            height=2160,
        )
        too_big = _media_with_locations(
            acct_id,
            mimetype="video/mp4",
            url="https://cdn.fansly.com/variant_4k.mp4?Key-Pair-Id=K&Policy=p&Signature=s",
            width=3840,
            height=2160,
        )
        media.variants = [too_big]
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        state = DownloadState()

        with pytest.raises(MediaFilteredError) as exc_info:
            await parse_media_info(state, info, max_px=1080)
        assert exc_info.value.reason == "max_resolution"
        assert exc_info.value.media_id == media.id

    async def test_silent_pass_when_resolution_unknown(self, reset_class_store):
        """No known width/height anywhere -> never skip on missing metadata."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        mid = snowflake_id()
        media = Media(
            id=mid,
            accountId=acct_id,
            mimetype="video/mp4",
        )
        loc = MediaLocation(
            mediaId=mid,
            locationId=1,
            location="https://cdn.fansly.com/unknown_res.mp4?Key-Pair-Id=K",
        )
        media.locations = [loc]
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        state = DownloadState()

        result = await parse_media_info(state, info, max_px=1080)
        assert result.download_url is not None
        assert "unknown_res" in result.download_url

    async def test_silent_pass_when_only_known_variant_is_other_mimetype(
        self, reset_class_store
    ):
        """A known-resolution variant of a different mimetype must not count
        toward the known-rendition check; every video-matching candidate has
        unknown resolution, so this must silent-pass rather than skip."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        mid = snowflake_id()
        media = Media(id=mid, accountId=acct_id, mimetype="video/mp4")
        loc = MediaLocation(
            mediaId=mid,
            locationId=1,
            location="https://cdn.fansly.com/unknown_res.mp4?Key-Pair-Id=K",
        )
        media.locations = [loc]

        matching_unknown = _media_with_locations(acct_id, mimetype="video/mp4")
        matching_unknown.width = None
        matching_unknown.height = None

        other_mime_known = _media_with_locations(
            acct_id, mimetype="image/jpeg", width=1920, height=1080
        )
        media.variants = [matching_unknown, other_mime_known]
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        state = DownloadState()

        result = await parse_media_info(state, info, max_px=1080)
        assert result.download_url is not None
        assert "unknown_res" in result.download_url

    async def test_silent_pass_when_known_default_has_no_locations(
        self, reset_class_store
    ):
        """access:false (locked) media can carry known width/height with an
        empty locations list; such media has no downloadable candidate and
        must silent-pass rather than being treated as a cap violation."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = Media(
            id=snowflake_id(),
            accountId=acct_id,
            mimetype="video/mp4",
            width=1280,
            height=720,
        )
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        state = DownloadState()

        result = await parse_media_info(state, info, max_px=1080)
        assert result.download_url is None

    async def test_silent_pass_when_only_known_variant_has_no_locations(
        self, reset_class_store
    ):
        """A variant with known dimensions but no location is not a
        downloadable candidate either; it must not count toward the
        known-rendition check that triggers a cap-violation skip."""
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        media = Media(id=snowflake_id(), accountId=acct_id, mimetype="video/mp4")
        known_no_location = Media(
            id=snowflake_id(),
            accountId=acct_id,
            mimetype="video/mp4",
            width=1280,
            height=720,
        )
        media.variants = [known_no_location]
        await reset_class_store.save(media)

        info = _media_info_dict(acct_id, media)
        state = DownloadState()

        result = await parse_media_info(state, info, max_px=1080)
        assert result.download_url is None

    async def test_preview_fallback_re_selection_respects_cap(self, reset_class_store):
        acct_id = snowflake_id()
        await reset_class_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        main_media = Media(id=snowflake_id(), accountId=acct_id, mimetype="video/mp4")
        preview = Media(id=snowflake_id(), accountId=acct_id, mimetype="image/jpeg")
        preview_fit = _media_with_locations(
            acct_id,
            mimetype="image/jpeg",
            url="https://cdn.fansly.com/preview_1080.jpg?Key-Pair-Id=K&Policy=p&Signature=s",
            width=1920,
            height=1080,
        )
        preview_too_big = _media_with_locations(
            acct_id,
            mimetype="image/jpeg",
            url="https://cdn.fansly.com/preview_4k.jpg?Key-Pair-Id=K&Policy=p&Signature=s",
            width=3840,
            height=2160,
        )
        preview.variants = [preview_fit, preview_too_big]
        await reset_class_store.save(main_media)
        await reset_class_store.save(preview)

        info = _media_info_dict(acct_id, main_media, preview=preview)
        state = DownloadState()

        result = await parse_media_info(state, info, max_px=1080)
        assert result.download_url is not None
        assert "preview_1080" in result.download_url
