"""Tests for download/media.py — fetch_and_process_media, validation, stats."""

import pytest

from download.downloadstate import DownloadState
from download.media import (
    _update_media_type_stats,
    _validate_media,
    fetch_and_process_media,
)
from errors import MediaError
from metadata.models import Media
from tests.fixtures.utils.test_isolation import snowflake_id


class TestValidateMedia:
    """Lines 99-104: validate required download fields."""

    def test_missing_mimetype_raises(self):
        m = Media(
            id=snowflake_id(), accountId=snowflake_id(), download_url="http://x.com/a"
        )
        with pytest.raises(MediaError, match="MIME type"):
            _validate_media(m)

    def test_missing_download_url_raises(self):
        m = Media(id=snowflake_id(), accountId=snowflake_id(), mimetype="image/jpeg")
        with pytest.raises(MediaError, match="Download URL"):
            _validate_media(m)

    def test_valid_media_passes(self):
        m = Media(
            id=snowflake_id(),
            accountId=snowflake_id(),
            mimetype="image/jpeg",
            download_url="http://x.com/a",
        )
        _validate_media(m)


class TestUpdateMediaTypeStats:
    """Lines 107-118: update pic/vid/audio counters."""

    def test_image_counted(self):
        state = DownloadState()
        m = Media(id=snowflake_id(), accountId=snowflake_id(), mimetype="image/jpeg")
        _update_media_type_stats(state, m)
        assert str(m.id) in state.recent_photo_media_ids

    def test_video_counted(self):
        state = DownloadState()
        m = Media(id=snowflake_id(), accountId=snowflake_id(), mimetype="video/mp4")
        _update_media_type_stats(state, m)
        assert str(m.id) in state.recent_video_media_ids

    def test_audio_counted(self):
        state = DownloadState()
        m = Media(id=snowflake_id(), accountId=snowflake_id(), mimetype="audio/mpeg")
        _update_media_type_stats(state, m)
        assert str(m.id) in state.recent_audio_media_ids

    def test_preview_uses_preview_id(self):
        state = DownloadState()
        pid = snowflake_id()
        m = Media(
            id=snowflake_id(),
            accountId=snowflake_id(),
            mimetype="image/png",
            is_preview=True,
            preview_id=pid,
        )
        _update_media_type_stats(state, m)
        assert str(pid) in state.recent_photo_media_ids

    def test_uses_download_id_when_set(self):
        state = DownloadState()
        did = snowflake_id()
        m = Media(
            id=snowflake_id(),
            accountId=snowflake_id(),
            mimetype="video/mp4",
            download_id=did,
        )
        _update_media_type_stats(state, m)
        assert str(did) in state.recent_video_media_ids


class TestFetchAndProcessMedia:
    """Lines 53-96: batch fetch from API, process, select variants."""

    @pytest.mark.asyncio
    async def test_empty_ids_returns_empty(self, mock_config):
        """Line 53-54: empty media_ids → immediate return."""
        result = await fetch_and_process_media(mock_config, DownloadState(), [])
        assert result == []
