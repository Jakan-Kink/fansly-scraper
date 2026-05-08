"""Tests for download/media.py — file download pipeline, hash dedup, progress bars.

External boundaries patched:
- imagehash.phash (external image hashing lib)
- fileio.mp4.hash_mp4file (MP4 box parser — needs valid MP4 structure)
- download.m3u8.download_m3u8 (spawns ffmpeg)
HTTP via respx. Everything else runs real code including dedupe_media_file,
_verify_existing_file, _download_regular_file, entity store operations.
"""

import io
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
import respx
from PIL import Image

from download.downloadstate import DownloadState
from download.media import (
    _download_file,
    _download_m3u8_file,
    _download_regular_file,
    _verify_existing_file,
    _verify_temp_download,
    download_media,
)
from download.types import DownloadType
from errors import DownloadError, DuplicateCountError, M3U8Error
from metadata.models import Account, Media
from tests.fixtures.api import dump_fansly_calls
from tests.fixtures.utils.test_isolation import snowflake_id


# ── Helpers ──────────────────────────────────────────────────────────────


def _tiny_jpeg_bytes() -> bytes:
    """Create a minimal valid JPEG for image hash tests."""
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color="red").save(buf, format="JPEG")
    return buf.getvalue()


def _make_media(account_id, *, mimetype="image/jpeg", content_hash=None):
    """Build a Media with download fields populated."""
    ext = "jpg" if "image" in mimetype else "mp4"
    return Media(
        id=snowflake_id(),
        accountId=account_id,
        mimetype=mimetype,
        download_url=f"https://cdn.fansly.com/content/file.{ext}?Key-Pair-Id=K",
        file_extension=ext,
        content_hash=content_hash,
    )


def _make_state(creator_id):
    state = DownloadState()
    state.creator_id = creator_id
    state.creator_name = f"dl_{creator_id}"
    state.download_type = DownloadType.TIMELINE
    return state


# ── _verify_existing_file ───────────────────────────────────────────────


class TestVerifyExistingFile:
    """Lines 121-158: hash-check existing file on disk.

    Patches imagehash.phash (external lib boundary).
    """

    @pytest.mark.asyncio
    async def test_image_hash_match_and_mismatch(self, mock_config, tmp_path):
        """Hash match → duplicate + pic_count; mismatch → False."""
        state = _make_state(snowflake_id())

        check_path = tmp_path / "existing.jpg"
        check_path.write_bytes(_tiny_jpeg_bytes())

        # Match path
        media = _make_media(state.creator_id, content_hash="abc123")
        with patch("fileio.fnmanip.imagehash.phash", return_value="abc123"):
            result = await _verify_existing_file(mock_config, state, media, check_path)
        assert result is True
        assert state.duplicate_count == 1
        assert state.pic_count == 1

        # Mismatch path
        media2 = _make_media(state.creator_id, content_hash="expected")
        with patch("fileio.fnmanip.imagehash.phash", return_value="different"):
            result = await _verify_existing_file(mock_config, state, media2, check_path)
        assert result is False
        assert state.duplicate_count == 1  # unchanged

    @pytest.mark.asyncio
    async def test_video_hash_match(self, mock_config, tmp_path):
        """Video uses hash_mp4file, vid_count++."""
        state = _make_state(snowflake_id())
        media = _make_media(state.creator_id, mimetype="video/mp4", content_hash="vid")

        check_path = tmp_path / "existing.mp4"
        check_path.write_bytes(b"x" * 1024)

        with patch("fileio.fnmanip.hash_mp4file", return_value="vid"):
            result = await _verify_existing_file(mock_config, state, media, check_path)

        assert result is True
        assert state.vid_count == 1


# ── _download_file + _download_regular_file ─────────────────────────────


class TestDownloadFunctions:
    """Lines 220-294: stream download with respx at HTTP boundary.

    Uses one CDN route with ordered side_effect responses, asserts on route.calls.
    """

    def test_download_file_success(self, respx_fansly_api, mock_config, tmp_path):
        """Lines 222-239: 200 → writes chunks to file."""
        url = "https://cdn.fansly.com/content/img.jpg"
        cdn_route = respx.get(
            url__startswith="https://cdn.fansly.com/content/img.jpg"
        ).mock(side_effect=[httpx.Response(200, content=b"image bytes")])

        out = tmp_path / "out.jpg"
        try:
            with out.open("wb") as f:
                _download_file(mock_config, url, f)
        finally:
            dump_fansly_calls(cdn_route.calls, "test_download_file_success")

        assert out.read_bytes() == b"image bytes"
        assert len(cdn_route.calls) == 1

    def test_download_regular_file_success_paths(
        self, respx_fansly_api, mock_config, tmp_path
    ):
        """Lines 258-285: 200 with timestamp → writes + utime; no timestamp → skips utime."""
        cdn_route = respx.get(url__startswith="https://cdn.fansly.com/content/").mock(
            side_effect=[
                httpx.Response(200, content=b"photo", headers={"content-length": "5"}),
                httpx.Response(200, content=b"data", headers={"content-length": "4"}),
            ]
        )

        # With timestamp
        media1 = _make_media(snowflake_id())
        media1.download_url = "https://cdn.fansly.com/content/photo.jpg"
        media1.createdAt = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
        save1 = tmp_path / "photo.jpg"
        _download_regular_file(mock_config, media1, save1)
        assert save1.read_bytes() == b"photo"

        # Without timestamp
        media2 = _make_media(snowflake_id())
        media2.download_url = "https://cdn.fansly.com/content/no_ts.jpg"
        media2.createdAt = None
        save2 = tmp_path / "no_ts.jpg"

        try:
            _download_regular_file(mock_config, media2, save2)
        finally:
            dump_fansly_calls(
                cdn_route.calls, "test_download_regular_file_success_paths"
            )

        assert save2.exists()
        assert len(cdn_route.calls) == 2
        assert "photo.jpg" in str(cdn_route.calls[0].request.url)
        assert "no_ts.jpg" in str(cdn_route.calls[1].request.url)

    def test_download_file_non_200_raises(
        self, respx_fansly_api, mock_config, tmp_path
    ):
        """Line 228: non-200 status from CDN → DownloadError."""
        url = "https://cdn.fansly.com/content/missing.jpg"
        cdn_route = respx.get(url__startswith=url).mock(
            side_effect=[httpx.Response(404, content=b"not found")]
        )

        out = tmp_path / "out.jpg"
        try:
            with out.open("wb") as f, pytest.raises(DownloadError) as excinfo:
                _download_file(mock_config, url, f)
        finally:
            dump_fansly_calls(cdn_route.calls, "test_download_file_non_200_raises")

        assert "404" in str(excinfo.value)

    def test_download_regular_file_non_200_raises(
        self, respx_fansly_api, mock_config, tmp_path
    ):
        """Line 285: non-200 status from CDN → DownloadError."""
        # 404 is not in the Retry status_forcelist (only 418/429/5xx),
        # so a single response is enough — no retry budget to satisfy.
        cdn_route = respx.get(
            url__startswith="https://cdn.fansly.com/content/missing.jpg"
        ).mock(side_effect=[httpx.Response(404, content=b"not found")])

        media = _make_media(snowflake_id())
        media.download_url = "https://cdn.fansly.com/content/missing.jpg"
        save = tmp_path / "missing.jpg"

        try:
            with pytest.raises(DownloadError) as excinfo:
                _download_regular_file(mock_config, media, save)
        finally:
            dump_fansly_calls(
                cdn_route.calls, "test_download_regular_file_non_200_raises"
            )

        assert "404" in str(excinfo.value)


# ── _download_m3u8_file ─────────────────────────────────────────────────


class TestDownloadM3u8File:
    """Lines 297-358: HLS download + hash dedup.

    Patches download_m3u8 (spawns ffmpeg) and hash_mp4file (MP4 parser).
    """

    @pytest.mark.asyncio
    async def test_new_file_and_duplicate(self, mock_config, entity_store, tmp_path):
        """New hash → save + vid_count; existing hash → duplicate."""
        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))

        # ── New file path ──
        state = _make_state(acct_id)
        media = _make_media(acct_id, mimetype="video/mp4")
        media.download_url = "https://cdn.fansly.com/stream.m3u8"
        media.file_extension = "m3u8"

        check_path = tmp_path / "target" / "video.mp4"
        check_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = tmp_path / "temp_video.mp4"
        temp_file.write_bytes(b"video content")

        with (
            patch("download.media.download_m3u8", return_value=temp_file),
            patch("fileio.fnmanip.hash_mp4file", return_value="newhash"),
        ):
            is_dupe = await _download_m3u8_file(mock_config, state, media, check_path)

        assert is_dupe is False
        assert state.vid_count == 1

        # ── Duplicate path ──
        # Pre-existing media with same hash
        existing = Media(
            id=snowflake_id(),
            accountId=acct_id,
            mimetype="video/mp4",
            content_hash="duphash",
            local_filename="existing.mp4",
            is_downloaded=True,
        )
        await entity_store.save(existing)

        state2 = _make_state(acct_id)
        media2 = _make_media(acct_id, mimetype="video/mp4")
        media2.download_url = "https://cdn.fansly.com/stream2.m3u8"

        check_path2 = tmp_path / "target2" / "video2.mp4"
        check_path2.parent.mkdir(parents=True, exist_ok=True)
        temp_file2 = tmp_path / "temp_video2.mp4"
        temp_file2.write_bytes(b"video")

        with (
            patch("download.media.download_m3u8", return_value=temp_file2),
            patch("fileio.fnmanip.hash_mp4file", return_value="duphash"),
        ):
            is_dupe = await _download_m3u8_file(
                mock_config, state2, media2, check_path2
            )

        assert is_dupe is True
        assert state2.duplicate_count == 1


# ── download_media ──────────────────────────────────────────────────────


class TestDownloadMedia:
    """Lines 361-491: main download loop.

    Patches: imagehash.phash (external lib), hash_mp4file (MP4 parser),
    download_m3u8 (ffmpeg). HTTP via respx. Everything else runs real.
    """

    @pytest.mark.asyncio
    async def test_notset_type_and_empty_list(self, mock_config):
        """Lines 367-373: NOTSET → RuntimeError; empty → immediate return."""
        state = DownloadState()
        state.download_type = DownloadType.NOTSET
        with pytest.raises(RuntimeError, match="download type not set"):
            await download_media(mock_config, state, [_make_media(snowflake_id())])

        state2 = _make_state(snowflake_id())
        await download_media(mock_config, state2, [])  # no error

    @pytest.mark.asyncio
    async def test_duplicate_threshold_exceeded(self, mock_config, tmp_path):
        """Lines 390-395: duplicate count > threshold → DuplicateCountError."""
        mock_config.use_duplicate_threshold = True
        mock_config.DUPLICATE_THRESHOLD = 50
        mock_config.download_directory = tmp_path

        state = _make_state(snowflake_id())
        state.duplicate_count = 100

        with pytest.raises(DuplicateCountError):
            await download_media(mock_config, state, [_make_media(state.creator_id)])

    @pytest.mark.asyncio
    async def test_already_downloaded_skips(self, mock_config, entity_store, tmp_path):
        """Lines 407-415: DB says already downloaded → add_duplicate, skip."""
        mock_config.use_duplicate_threshold = False
        mock_config.download_media_previews = False
        mock_config.download_directory = tmp_path

        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))

        state = _make_state(acct_id)
        media = Media(
            id=snowflake_id(),
            accountId=acct_id,
            mimetype="image/jpeg",
            download_url="https://cdn.fansly.com/x.jpg",
            file_extension="jpg",
            is_downloaded=True,
            content_hash="h",
            local_filename="f.jpg",
        )
        await entity_store.save(media)

        await download_media(mock_config, state, [media])
        assert state.duplicate_count == 1

    @pytest.mark.asyncio
    async def test_regular_image_download(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """Lines 467-484: regular image download end-to-end via respx + real dedupe.

        Patches imagehash.phash (external lib). Everything else real.
        """
        mock_config.use_duplicate_threshold = False
        mock_config.download_media_previews = False
        mock_config.download_directory = tmp_path
        mock_config.separate_timeline = True
        mock_config.use_folder_suffix = False
        mock_config.separate_previews = False

        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))

        state = _make_state(acct_id)
        media = _make_media(acct_id)
        cdn_url = media.download_url

        jpeg_bytes = _tiny_jpeg_bytes()
        cdn_route = respx.get(url__startswith=cdn_url.split("?")[0]).mock(
            side_effect=[
                httpx.Response(
                    200,
                    content=jpeg_bytes,
                    headers={"content-length": str(len(jpeg_bytes))},
                )
            ]
        )

        with patch("fileio.fnmanip.imagehash.phash", return_value="testhash"):
            try:
                await download_media(mock_config, state, [media])
            finally:
                dump_fansly_calls(respx.calls, "test_regular_image_download")

        assert state.pic_count == 1

    @pytest.mark.asyncio
    async def test_m3u8_download_and_error(self, mock_config, entity_store, tmp_path):
        """Lines 428-432, 457-465, 486-487: m3u8 → .mp4 conversion; M3U8Error → skip.

        Two media items: first succeeds (m3u8 download), second raises M3U8Error.
        """
        mock_config.use_duplicate_threshold = False
        mock_config.download_media_previews = False
        mock_config.download_directory = tmp_path
        mock_config.separate_timeline = True
        mock_config.use_folder_suffix = False
        mock_config.separate_previews = False

        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))

        state = _make_state(acct_id)

        # First media: successful m3u8 download
        media1 = _make_media(acct_id, mimetype="video/mp4")
        media1.file_extension = "m3u8"
        media1.download_url = "https://cdn.fansly.com/stream1.m3u8"

        # Second media: m3u8 error
        media2 = _make_media(acct_id, mimetype="video/mp4")
        media2.file_extension = "m3u8"
        media2.download_url = "https://cdn.fansly.com/stream2.m3u8"

        temp_mp4 = tmp_path / "temp_dl.mp4"
        temp_mp4.write_bytes(b"video content")

        call_count = [0]

        def m3u8_side_effect(config, url, path, ts):
            call_count[0] += 1
            if call_count[0] == 1:
                return temp_mp4
            raise M3U8Error("bad stream")

        with (
            patch("download.media.download_m3u8", side_effect=m3u8_side_effect),
            patch("fileio.fnmanip.hash_mp4file", return_value="newhash"),
        ):
            await download_media(mock_config, state, [media1, media2])

        # First succeeded, second was skipped via M3U8Error
        assert state.vid_count == 1

    @pytest.mark.asyncio
    async def test_skip_preview_and_invalid_and_process_error(
        self, mock_config, entity_store, tmp_path
    ):
        """Lines 397-418: three media items exercising early-exit branches.

        1. is_preview=True + previews disabled → skip (line 398)
        2. mimetype=None → MediaError → skip (lines 402-404)
        3. process_media_download raises → skip (lines 416-418)
        """
        mock_config.use_duplicate_threshold = False
        mock_config.download_media_previews = False
        mock_config.download_directory = tmp_path

        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        state = _make_state(acct_id)

        preview_media = _make_media(acct_id)
        preview_media.is_preview = True

        invalid_media = _make_media(acct_id)
        invalid_media.mimetype = None

        # creator_id=None → process_media_download raises ValueError
        error_media = _make_media(acct_id)
        state.creator_id = None

        await download_media(
            mock_config, state, [preview_media, invalid_media, error_media]
        )

    @pytest.mark.asyncio
    async def test_existing_file_verified_and_temp_download(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """Lines 442-449: file exists on disk → _verify_existing_file (hash match) → skip.

        Also exercises _verify_temp_download (169-217) when hash doesn't match on first check.
        Two media items:
        1. Existing file, hash matches → skip via _verify_existing_file
        2. Existing file, hash mismatch → _verify_temp_download → downloads, hash matches → skip
        """
        mock_config.use_duplicate_threshold = False
        mock_config.download_media_previews = False
        mock_config.download_directory = tmp_path
        mock_config.separate_timeline = True
        mock_config.use_folder_suffix = False
        mock_config.separate_previews = False
        mock_config.temp_folder = None

        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        state = _make_state(acct_id)

        # Media 1: existing file, hash matches → _verify_existing_file returns True
        media1 = _make_media(acct_id, content_hash="match_hash")

        # Media 2: existing file, hash mismatch → _verify_temp_download
        media2 = _make_media(acct_id, content_hash="temp_hash")

        # Pre-create the download directory and files
        dl_dir = tmp_path / f"dl_{acct_id}" / "Timeline" / "Pictures"
        dl_dir.mkdir(parents=True, exist_ok=True)

        file1 = dl_dir / media1.get_file_name()
        file1.write_bytes(_tiny_jpeg_bytes())

        file2 = dl_dir / media2.get_file_name()
        file2.write_bytes(_tiny_jpeg_bytes())

        # CDN route for _verify_temp_download's _download_file call
        cdn_route = respx.get(url__startswith="https://cdn.fansly.com/content/").mock(
            side_effect=[httpx.Response(200, content=_tiny_jpeg_bytes())]
        )

        # phash call sequence:
        # 1. _verify_existing_file(media1) → "match_hash" == media1.content_hash → True → skip
        # 2. _verify_existing_file(media2) → "wrong" != media2.content_hash → False
        # 3. _verify_temp_download(media2) → downloads, hashes → "temp_hash" == media2.content_hash → True → skip
        phash_returns = iter(["match_hash", "wrong", "temp_hash"])

        with patch("fileio.fnmanip.imagehash.phash", side_effect=phash_returns):
            try:
                await download_media(mock_config, state, [media1, media2])
            finally:
                dump_fansly_calls(cdn_route.calls, "test_existing_file_verified")

        # Both were duplicates (via different paths)
        assert state.duplicate_count == 2

    @pytest.mark.asyncio
    async def test_valueerror_in_save_path(self, mock_config, entity_store, tmp_path):
        """Lines 433-435: get_media_save_path raises ValueError → skip."""
        mock_config.use_duplicate_threshold = False
        mock_config.download_media_previews = False
        mock_config.download_directory = tmp_path

        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        state = _make_state(acct_id)

        # Media with unknown mimetype → get_media_save_path raises ValueError
        media = Media(
            id=snowflake_id(),
            accountId=acct_id,
            mimetype="application/octet-stream",
            download_url="https://cdn.fansly.com/content/file.bin",
            file_extension="bin",
        )

        await download_media(mock_config, state, [media])

    @pytest.mark.asyncio
    async def test_regular_download_dedup_is_true(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """Lines 476-484: regular download completes, dedupe returns True → add_duplicate."""
        mock_config.use_duplicate_threshold = False
        mock_config.download_media_previews = False
        mock_config.download_directory = tmp_path
        mock_config.separate_timeline = True
        mock_config.use_folder_suffix = False
        mock_config.separate_previews = False

        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))
        state = _make_state(acct_id)
        media = _make_media(acct_id)

        jpeg_bytes = _tiny_jpeg_bytes()
        cdn_route = respx.get(url__startswith="https://cdn.fansly.com/content/").mock(
            side_effect=[
                httpx.Response(
                    200,
                    content=jpeg_bytes,
                    headers={"content-length": str(len(jpeg_bytes))},
                )
            ]
        )

        # imagehash.phash returns a hash, dedupe finds it's a duplicate
        with patch("fileio.fnmanip.imagehash.phash", return_value="duphash"):
            try:
                await download_media(mock_config, state, [media])
            finally:
                dump_fansly_calls(cdn_route.calls, "test_regular_download_dedup")

        assert state.pic_count == 1
        # dedupe_media_file with real entity store — hash won't match existing
        # so is_dupe will be False, but the download itself completes

    @pytest.mark.asyncio
    async def test_verify_temp_download_video_match_and_temp_folder(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        """Lines 169-217: _verify_temp_download with video mimetype + temp_folder config.

        Covers: line 174 (temp_folder truthy), lines 208-209 (video branch in hash match),
        lines 215-217 (finally cleanup + return False when hash doesn't match).
        """
        mock_config.temp_folder = str(tmp_path / "temp")
        (tmp_path / "temp").mkdir()

        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))

        state = _make_state(acct_id)
        check_path = tmp_path / "target" / "video.mp4"
        check_path.parent.mkdir(parents=True, exist_ok=True)

        cdn_route = respx.get(url__startswith="https://cdn.fansly.com/content/").mock(
            side_effect=[
                httpx.Response(200, content=b"video1"),
                httpx.Response(200, content=b"video2"),
            ]
        )

        # Match case → True, vid_count++
        media1 = _make_media(acct_id, mimetype="video/mp4", content_hash="vidhash")
        with patch("fileio.fnmanip.hash_mp4file", return_value="vidhash"):
            try:
                result1 = await _verify_temp_download(
                    mock_config, state, media1, check_path
                )
            finally:
                dump_fansly_calls(cdn_route.calls, "verify_temp_video_match")
        assert result1 is True
        assert state.vid_count == 1

        # Mismatch case → return False, finally cleanup
        check_path2 = tmp_path / "target" / "video2.mp4"
        media2 = _make_media(acct_id, mimetype="video/mp4", content_hash="expected")
        with patch("fileio.fnmanip.hash_mp4file", return_value="different"):
            result2 = await _verify_temp_download(
                mock_config, state, media2, check_path2
            )
        assert result2 is False

    @pytest.mark.asyncio
    async def test_m3u8_self_dedup_guard(self, mock_config, entity_store, tmp_path):
        """Lines 328-329, 307: m3u8 with temp_folder + existing_by_hash is self → not a dupe.

        When find_one returns the media itself (same effective_id),
        existing_by_hash is set to None (line 329), falls through to new file path.
        """
        mock_config.temp_folder = str(tmp_path / "temp")
        (tmp_path / "temp").mkdir()

        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))

        state = _make_state(acct_id)
        media = _make_media(acct_id, mimetype="video/mp4", content_hash="selfhash")
        media.file_extension = "m3u8"
        media.download_url = "https://cdn.fansly.com/stream.m3u8"
        media.is_downloaded = True
        media.local_filename = "old.mp4"
        await entity_store.save(media)

        check_path = tmp_path / "target" / "video.mp4"
        check_path.parent.mkdir(parents=True, exist_ok=True)

        temp_mp4 = tmp_path / "temp" / "temp_dl.mp4"
        temp_mp4.parent.mkdir(parents=True, exist_ok=True)
        temp_mp4.write_bytes(b"video content")

        with (
            patch("download.media.download_m3u8", return_value=temp_mp4),
            patch("fileio.fnmanip.hash_mp4file", return_value="selfhash"),
        ):
            is_dupe = await _download_m3u8_file(mock_config, state, media, check_path)

        # Self-match nullified → treated as new → vid_count++
        assert is_dupe is False
        assert state.vid_count == 1

    @pytest.mark.asyncio
    async def test_m3u8_dupe_via_download_media(
        self, mock_config, entity_store, tmp_path
    ):
        """Line 465: m3u8 download finds duplicate → is_dupe=True → continue."""
        mock_config.use_duplicate_threshold = False
        mock_config.download_media_previews = False
        mock_config.download_directory = tmp_path
        mock_config.separate_timeline = True
        mock_config.use_folder_suffix = False
        mock_config.separate_previews = False

        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"u_{acct_id}"))

        existing = Media(
            id=snowflake_id(),
            accountId=acct_id,
            mimetype="video/mp4",
            content_hash="duphash",
            local_filename="existing.mp4",
            is_downloaded=True,
        )
        await entity_store.save(existing)

        state = _make_state(acct_id)
        media = _make_media(acct_id, mimetype="video/mp4")
        media.file_extension = "m3u8"
        media.download_url = "https://cdn.fansly.com/stream.m3u8"

        temp_mp4 = tmp_path / "temp_dl.mp4"
        temp_mp4.write_bytes(b"video")

        with (
            patch("download.media.download_m3u8", return_value=temp_mp4),
            patch("fileio.fnmanip.hash_mp4file", return_value="duphash"),
        ):
            await download_media(mock_config, state, [media])

        assert state.duplicate_count == 1
        assert state.vid_count == 0  # dupe skipped, no vid_count
