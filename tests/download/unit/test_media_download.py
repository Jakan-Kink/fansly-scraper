"""Tests for download/media.py — fetch_and_process_media, validation, stats.

``fetch_and_process_media`` runs end-to-end against the real metadata
pipeline: real ``process_media_info`` (persists Media + AccountMedia through
the Pydantic identity map into a real ``entity_store``) and real
``parse_media_info`` (selects the download variant). AccountMedia JSON is
served through respx at the Fansly API boundary.

AccountMedia payload shape (from project memory / media/media.py:95-118):
- a top-level ``previewId`` key (read at media/media.py:95)
- a nested ``media`` dict (read at media/media.py:101,118)
- the media's CDN ``location`` must contain ``Key-Pair-Id`` so the resolved
  download_url passes the metadata-present check at media/media.py:180.
"""

import httpx
import pytest
import respx

from api.fansly import FanslyApi
from download.downloadstate import DownloadState
from download.media import (
    _update_media_type_stats,
    _validate_media,
    fetch_and_process_media,
)
from download.types import DownloadType
from errors import MediaError
from metadata.models import Account, Media
from tests.fixtures.api import dump_fansly_calls
from tests.fixtures.utils.test_isolation import snowflake_id


_CDN_IMAGE_URL = (
    "https://cdn3.fansly.com/{acct}/{mid}.jpeg"
    "?ngsw-bypass=true&Key-Pair-Id=K23PG5J1AWEZX5&Signature=sig"
)


def _account_media_payload(account_id: int, *, valid: bool = True) -> dict:
    """Build a raw AccountMedia API item with a persistable nested ``media`` dict.

    Both variants carry a real ``media`` dict so the FK-ordered
    ``process_media_info`` persistence (Media before AccountMedia) succeeds.

    valid=True  → also carries ``previewId`` so the real parse_media_info
                  resolves a Key-Pair-Id download_url and the item survives the
                  access/preview filter.
    valid=False → omits the ``previewId`` key so the real parse_media_info
                  raises (KeyError on media_info["previewId"], media/media.py:95),
                  exercising the except/print arm in fetch_and_process_media.
    """
    am_id = snowflake_id()
    media_id = snowflake_id()
    media_dict = {
        "id": media_id,
        "accountId": account_id,
        "mimetype": "image/jpeg",
        "createdAt": 1700000000,
        "locations": [
            {
                "locationId": "1",
                "location": _CDN_IMAGE_URL.format(acct=account_id, mid=media_id),
            },
        ],
    }
    info: dict = {
        "id": am_id,
        "accountId": account_id,
        "mediaId": media_id,
        "createdAt": 1700000000,
        "deleted": False,
        "access": True,
        "media": media_dict,
    }
    if valid:
        info["previewId"] = None
    return info


class TestValidateMedia:
    """Lines 99-104: validate required download fields."""

    @pytest.mark.parametrize(
        ("mimetype", "download_url", "error_match"),
        [
            pytest.param(
                None, "http://x.com/a", "MIME type", id="missing-mimetype-raises"
            ),
            pytest.param(
                "image/jpeg", None, "Download URL", id="missing-download-url-raises"
            ),
            pytest.param("image/jpeg", "http://x.com/a", None, id="valid-media-passes"),
        ],
    )
    def test_validate_media(self, mimetype, download_url, error_match):
        """error_match None → no raise; else pytest.raises(MediaError, match=...)."""
        kwargs: dict = {"id": snowflake_id(), "accountId": snowflake_id()}
        if mimetype is not None:
            kwargs["mimetype"] = mimetype
        if download_url is not None:
            kwargs["download_url"] = download_url
        m = Media(**kwargs)
        if error_match is None:
            _validate_media(m)
        else:
            with pytest.raises(MediaError, match=error_match):
                _validate_media(m)


class TestUpdateMediaTypeStats:
    """Lines 107-118: update pic/vid/audio counters."""

    @pytest.mark.parametrize(
        ("mimetype", "id_field", "expected_target"),
        [
            pytest.param(
                "image/jpeg", "id", "recent_photo_media_ids", id="image-own-id"
            ),
            pytest.param(
                "video/mp4", "id", "recent_video_media_ids", id="video-own-id"
            ),
            pytest.param(
                "audio/mpeg", "id", "recent_audio_media_ids", id="audio-own-id"
            ),
            pytest.param(
                "image/png",
                "preview_id",
                "recent_photo_media_ids",
                id="preview-counted-by-preview-id",
            ),
            pytest.param(
                "video/mp4",
                "download_id",
                "recent_video_media_ids",
                id="download-id-overrides-own-id",
            ),
        ],
    )
    def test_media_type_stats(self, mimetype, id_field, expected_target):
        """``id_field`` names the Media field whose value must land in
        ``state.<expected_target>`` (preview_id rows also set is_preview)."""
        state = DownloadState()
        kwargs: dict = {
            "id": snowflake_id(),
            "accountId": snowflake_id(),
            "mimetype": mimetype,
        }
        if id_field == "preview_id":
            expected = snowflake_id()
            kwargs["is_preview"] = True
            kwargs["preview_id"] = expected
        elif id_field == "download_id":
            expected = snowflake_id()
            kwargs["download_id"] = expected
        else:
            expected = kwargs["id"]
        m = Media(**kwargs)
        _update_media_type_stats(state, m)
        assert str(expected) in getattr(state, expected_target)


class TestFetchAndProcessMedia:
    """Lines 53-96: batch fetch from API, process, select variants."""

    @pytest.mark.asyncio
    async def test_empty_ids_returns_empty(self, mock_config):
        """Line 53-54: empty media_ids → immediate return."""
        result = await fetch_and_process_media(mock_config, DownloadState(), [])
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_process_filter_and_parse_error(
        self, respx_fansly_api, mock_config, entity_store
    ):
        """Lines 56-119: real fetch → process_media_info → parse_media_info → filter.

        Merges the former test_fetches_and_filters + test_parse_error_caught.
        A single batch carries two AccountMedia items:

        1. valid item → real parse_media_info resolves a Key-Pair-Id download
           URL, real process_media_info persists Media + AccountMedia, and the
           item survives the access/preview filter (lines 115-119).
        2. malformed item (no nested ``media`` dict) → real parse_media_info
           raises KeyError, caught by the except arm (lines 104-111).

        Net result list has exactly the one valid, accessible Media.
        """
        mock_config.download_media_previews = False
        mock_config.interactive = False
        mock_config.BATCH_SIZE = 50

        account_id = snowflake_id()
        await entity_store.save(Account(id=account_id, username=f"u_{account_id}"))

        good = _account_media_payload(account_id, valid=True)
        bad = _account_media_payload(account_id, valid=False)

        route = respx.get(
            url__startswith=FanslyApi.ACCOUNT_MEDIA_ENDPOINT.format("")
        ).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"success": True, "response": [good, bad]},
                ),
            ],
        )

        state = DownloadState()
        state.creator_id = account_id
        state.creator_name = f"u_{account_id}"
        state.download_type = DownloadType.TIMELINE

        try:
            result = await fetch_and_process_media(
                mock_config, state, [good["id"], bad["id"]]
            )
        finally:
            dump_fansly_calls(route.calls, "fetch_process_filter_and_parse_error")

        assert route.called
        # Only the valid item survives — bad item's parse error was swallowed.
        assert len(result) == 1
        resolved = result[0]
        assert isinstance(resolved, Media)
        assert resolved.download_url is not None
        assert "Key-Pair-Id" in resolved.download_url
        # process_media_info persisted the Media through the real identity map.
        assert await entity_store.get(Media, good["media"]["id"]) is not None
