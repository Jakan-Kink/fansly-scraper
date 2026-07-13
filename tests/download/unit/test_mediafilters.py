"""Unit tests for download.mediafilters gates and recording."""

import json

import httpx
import pytest
import respx

from config.media_filters import MediaFilterOverride, MediaFilters
from download.downloadstate import DownloadState
from download.m3u8 import _get_highest_quality_variant_url
from download.media import _download_regular_file, download_media
from download.mediafilters import (
    check_media_filters,
    estimate_stream_size_gate,
    record_filter_observation,
    resolve_media_filters,
)
from download.types import DownloadType
from errors import MediaFilteredError
from metadata.models import Media
from tests.fixtures.api import build_master_playlist, dump_fansly_calls
from tests.fixtures.metadata import AccountFactory, MediaFactory
from tests.fixtures.utils.test_isolation import snowflake_id


def _state(download_type=DownloadType.TIMELINE):
    state = DownloadState(creator_name="creator1")
    state.creator_id = snowflake_id()
    state.download_type = download_type
    return state


class TestResolveMediaFilters:
    def test_inactive_returns_none(self, mock_config):
        mock_config.media_filters = MediaFilters()
        assert resolve_media_filters(mock_config, _state()) is None

    def test_single_mode_exempt(self, mock_config):
        mock_config.media_filters = MediaFilters(duration_max=10.0)
        assert resolve_media_filters(mock_config, _state(DownloadType.SINGLE)) is None

    def test_per_creator_resolution(self, mock_config):
        mock_config.media_filters = MediaFilters(
            duration_max=100.0,
            by_creator={"creator1": MediaFilterOverride(duration_max=50.0)},
        )
        resolved = resolve_media_filters(mock_config, _state())
        assert resolved is not None
        assert resolved.duration_max == 50.0


class TestMetadataGate:
    def test_inactive_filters_pass(self, mock_config):
        mock_config.media_filters = MediaFilters()
        media = MediaFactory.build(
            id=snowflake_id(), mimetype="video/mp4", duration=500.0
        )
        assert check_media_filters(mock_config, _state(), media) is None

    def test_duration_bounds_videos_only(self, mock_config):
        mock_config.media_filters = MediaFilters(duration_min=5.0, duration_max=100.0)
        state = _state()
        long_video = MediaFactory.build(
            id=snowflake_id(), mimetype="video/mp4", duration=500.0
        )
        image = MediaFactory.build(
            id=snowflake_id(), mimetype="image/jpeg", duration=None
        )
        unknown = MediaFactory.build(
            id=snowflake_id(), mimetype="video/mp4", duration=None
        )
        assert check_media_filters(mock_config, state, long_video) == "duration_max"
        assert check_media_filters(mock_config, state, image) is None
        assert check_media_filters(mock_config, state, unknown) is None

    def test_recorded_size_evidence_observed_beats_estimated(self, mock_config):
        mock_config.media_filters = MediaFilters(file_size_max=1000)
        state = _state()
        media = MediaFactory.build(
            id=snowflake_id(),
            mimetype="video/mp4",
            meta_info=json.dumps(
                {"estimatedContentLength": 5000, "observedContentLength": 500}
            ),
        )
        assert check_media_filters(mock_config, state, media) is None
        media.meta_info = json.dumps({"estimatedContentLength": 5000})
        assert check_media_filters(mock_config, state, media) == "file_size_max"

    def test_no_evidence_defers(self, mock_config):
        mock_config.media_filters = MediaFilters(file_size_max=1000)
        assert (
            check_media_filters(
                mock_config,
                _state(),
                MediaFactory.build(id=snowflake_id(), mimetype="video/mp4"),
            )
            is None
        )

    @pytest.mark.parametrize(
        "meta_info",
        [
            "not-json{",
            "[1, 2, 3]",
            json.dumps({"duration": 30}),
            json.dumps({"observedContentLength": True, "estimatedContentLength": "5"}),
        ],
        ids=["invalid-json", "non-dict-json", "no-evidence-keys", "non-numeric-values"],
    )
    def test_unusable_meta_info_is_no_evidence(self, mock_config, meta_info):
        mock_config.media_filters = MediaFilters(file_size_max=1000)
        media = MediaFactory.build(
            id=snowflake_id(), mimetype="image/jpeg", meta_info=meta_info
        )
        assert check_media_filters(mock_config, _state(), media) is None


@pytest.mark.asyncio
class TestRecording:
    async def test_merges_without_marking(self, entity_store):
        account_id = snowflake_id()
        account = AccountFactory(id=account_id, username="creator1")
        await entity_store.save(account)
        media = Media(
            id=snowflake_id(),
            accountId=account_id,
            mimetype="video/mp4",
            metadata=json.dumps({"duration": 30}),
        )
        await entity_store.save(media)
        await record_filter_observation(
            media, reason="file_size_max", observed=6_000_000_000
        )
        assert media.meta_info is not None
        payload = json.loads(media.meta_info)
        assert payload["lastFilteredReason"] == "file_size_max"
        assert payload["observedContentLength"] == 6_000_000_000
        assert payload["duration"] == 30  # existing keys preserved
        assert media.is_downloaded is False
        assert media.content_hash is None

    async def test_malformed_meta_info_skips_merge(self, entity_store):
        account_id = snowflake_id()
        account = AccountFactory(id=account_id, username="creator1")
        await entity_store.save(account)
        media = Media(
            id=snowflake_id(),
            accountId=account_id,
            mimetype="video/mp4",
            metadata="not-json{",
        )
        await entity_store.save(media)
        await record_filter_observation(media, reason="duration_max")
        assert media.meta_info == "not-json{"  # untouched, no crash

    async def test_non_dict_meta_info_skips_merge(self, entity_store):
        account_id = snowflake_id()
        account = AccountFactory(id=account_id, username="creator1")
        await entity_store.save(account)
        media = Media(
            id=snowflake_id(),
            accountId=account_id,
            mimetype="video/mp4",
            metadata="[1,2,3]",
        )
        await entity_store.save(media)
        await record_filter_observation(media, reason="duration_max")
        assert media.meta_info == "[1,2,3]"  # valid JSON, not an object; untouched

    async def test_estimated_only_records_without_observed(self, entity_store):
        account_id = snowflake_id()
        account = AccountFactory(id=account_id, username="creator1")
        await entity_store.save(account)
        media = Media(id=snowflake_id(), accountId=account_id, mimetype="video/mp4")
        await entity_store.save(media)
        await record_filter_observation(
            media, reason="file_size_max", estimated=1_000_000_000
        )
        assert media.meta_info is not None
        payload = json.loads(media.meta_info)
        assert payload["lastFilteredReason"] == "file_size_max"
        assert payload["estimatedContentLength"] == 1_000_000_000
        assert "observedContentLength" not in payload


class TestMediaFilteredError:
    def test_carries_attrs(self):
        e = MediaFilteredError("file_size_max", observed=123)
        assert e.reason == "file_size_max"
        assert e.observed == 123
        assert e.estimated is None


@pytest.mark.asyncio
class TestWireGate:
    async def _media(self, entity_store, url):
        account_id = snowflake_id()
        account = AccountFactory(id=account_id, username="creator1")
        await entity_store.save(account)
        media = Media(id=snowflake_id(), accountId=account_id, mimetype="video/mp4")
        media.download_url = url
        media.file_extension = "mp4"
        await entity_store.save(media)
        return media

    async def test_oversized_header_aborts_before_body(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        mock_config.media_filters = MediaFilters(file_size_max=1000)
        state = _state()
        url = "https://cdn.example.com/big.mp4?Key-Pair-Id=x"
        media = await self._media(entity_store, url)
        route = respx.get(url__startswith="https://cdn.example.com/big.mp4").mock(
            side_effect=[
                httpx.Response(
                    200, headers={"content-length": "5000"}, content=b"x" * 5000
                )
            ]
        )
        target = tmp_path / "big.mp4"
        try:
            with pytest.raises(MediaFilteredError) as exc_info:
                await _download_regular_file(mock_config, state, media, target)
        finally:
            dump_fansly_calls(route.calls, "test_oversized_header_aborts")
        assert route.called
        assert exc_info.value.reason == "file_size_max"
        assert exc_info.value.observed == 5000
        assert not target.exists()
        assert not list(tmp_path.glob(".*part*"))  # no temp file created

    async def test_missing_header_passes(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        mock_config.media_filters = MediaFilters(file_size_max=1000)
        state = _state()
        url = "https://cdn.example.com/nolen.mp4?Key-Pair-Id=x"
        media = await self._media(entity_store, url)
        # httpx.Response(content=...) auto-computes and sets content-length,
        # so a stream= response is required to leave the header genuinely
        # absent (as a real CDN response omitting it would).
        route = respx.get(url__startswith="https://cdn.example.com/nolen.mp4").mock(
            side_effect=[
                httpx.Response(200, stream=httpx.ByteStream(b"x" * 5000)),
            ]
        )
        target = tmp_path / "nolen.mp4"
        try:
            await _download_regular_file(mock_config, state, media, target)
        finally:
            dump_fansly_calls(route.calls, "test_missing_header_passes")
        assert route.called
        assert target.exists()
        assert target.stat().st_size == 5000

    async def test_loop_catches_filtered_error_and_records_observation(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        mock_config.media_filters = MediaFilters(file_size_max=1000)
        mock_config.download_directory = tmp_path
        mock_config.interactive = False
        state = _state()
        url = "https://cdn.example.com/loopbig.mp4?Key-Pair-Id=x"
        media = await self._media(entity_store, url)
        route = respx.get(url__startswith="https://cdn.example.com/loopbig.mp4").mock(
            side_effect=[
                httpx.Response(
                    200, headers={"content-length": "5000"}, content=b"x" * 5000
                )
            ]
        )
        try:
            await download_media(mock_config, state, [media])
        finally:
            dump_fansly_calls(route.calls, "test_loop_catches_filtered_error")
        assert route.called
        assert state.filtered_count == 1
        assert media.is_downloaded is False
        assert media.meta_info is not None
        payload = json.loads(media.meta_info)
        assert payload["observedContentLength"] == 5000
        assert payload["lastFilteredReason"] == "file_size_max"


@pytest.mark.asyncio
class TestEstimateGate:
    async def _m3u8_media(self, entity_store, url, duration):
        account_id = snowflake_id()
        account = AccountFactory(id=account_id, username="creator1")
        await entity_store.save(account)
        media = Media(
            id=snowflake_id(),
            accountId=account_id,
            mimetype="video/mp4",
            duration=duration,
        )
        media.download_url = url
        media.file_extension = "m3u8"
        await entity_store.save(media)
        return media

    async def test_estimate_violation_skips_before_segments(
        self, respx_fansly_api, mock_config, entity_store
    ):
        # 4,000,000 bps / 8 * 2000 s = 1_000_000_000 bytes > 500 MB max
        mock_config.media_filters = MediaFilters(file_size_max=500_000_000)
        state = _state()
        master_url = (
            "https://cdn.example.com/vod/master.m3u8?Policy=p&Key-Pair-Id=k&Signature=s"
        )
        media = await self._m3u8_media(entity_store, master_url, duration=2000.0)
        playlist_text = build_master_playlist(
            variant_url="https://cdn.example.com/vod/720.m3u8",
            bandwidth=4_000_000,
        )
        route = respx.get(url__startswith="https://cdn.example.com/vod/master").mock(
            side_effect=[httpx.Response(200, text=playlist_text)]
        )
        try:
            with pytest.raises(MediaFilteredError) as exc_info:
                await estimate_stream_size_gate(mock_config, state, media)
        finally:
            dump_fansly_calls(route.calls, "test_estimate_violation")
        assert exc_info.value.reason == "file_size_max"
        assert exc_info.value.estimated == 1_000_000_000

    async def test_unknown_duration_passes(
        self, respx_fansly_api, mock_config, entity_store
    ):
        mock_config.media_filters = MediaFilters(file_size_max=500)
        state = _state()
        media = await self._m3u8_media(
            entity_store,
            "https://cdn.example.com/vod2/master.m3u8?Policy=p&Key-Pair-Id=k&Signature=s",
            duration=None,
        )
        await estimate_stream_size_gate(mock_config, state, media)  # no raise

    async def test_inactive_passes_without_fetch(self, mock_config, entity_store):
        mock_config.media_filters = MediaFilters()
        state = _state()
        media = await self._m3u8_media(
            entity_store,
            "https://cdn.example.com/vod3/master.m3u8?Policy=p&Key-Pair-Id=k&Signature=s",
            duration=100.0,
        )
        await estimate_stream_size_gate(mock_config, state, media)  # no HTTP, no raise

    async def test_malformed_playlist_degrades_silently(
        self, respx_fansly_api, mock_config, entity_store
    ):
        mock_config.media_filters = MediaFilters(file_size_max=500_000_000)
        state = _state()
        master_url = "https://cdn.example.com/vod/malformed.m3u8?Policy=p&Key-Pair-Id=k&Signature=s"
        media = await self._m3u8_media(entity_store, master_url, duration=2000.0)
        route = respx.get(url__startswith="https://cdn.example.com/vod/malformed").mock(
            side_effect=[
                httpx.Response(
                    200,
                    text="#EXTM3U\n#EXT-X-TARGETDURATION:not_a_number\n#EXTINF:bogus,\nseg.ts",
                )
            ]
        )
        try:
            await estimate_stream_size_gate(mock_config, state, media)  # no raise
        finally:
            dump_fansly_calls(route.calls, "test_malformed_playlist_degrades_silently")

    async def test_estimate_under_limit_passes(
        self, respx_fansly_api, mock_config, entity_store
    ):
        # 4,000,000 bps / 8 * 100 s = 50_000_000 bytes < 500 MB max
        mock_config.media_filters = MediaFilters(file_size_max=500_000_000)
        state = _state()
        master_url = (
            "https://cdn.example.com/vod/small.m3u8?Policy=p&Key-Pair-Id=k&Signature=s"
        )
        media = await self._m3u8_media(entity_store, master_url, duration=100.0)
        playlist_text = build_master_playlist(
            variant_url="https://cdn.example.com/vod/720.m3u8",
            bandwidth=4_000_000,
        )
        route = respx.get(url__startswith="https://cdn.example.com/vod/small").mock(
            side_effect=[httpx.Response(200, text=playlist_text)]
        )
        try:
            await estimate_stream_size_gate(mock_config, state, media)  # no raise
        finally:
            dump_fansly_calls(route.calls, "test_estimate_under_limit_passes")
        assert route.called

    async def test_master_fetch_error_passes(
        self, respx_fansly_api, mock_config, entity_store
    ):
        mock_config.media_filters = MediaFilters(file_size_max=500)
        state = _state()
        master_url = (
            "https://cdn.example.com/vod/gone.m3u8?Policy=p&Key-Pair-Id=k&Signature=s"
        )
        media = await self._m3u8_media(entity_store, master_url, duration=2000.0)
        route = respx.get(url__startswith="https://cdn.example.com/vod/gone").mock(
            side_effect=[httpx.Response(403, text="Forbidden")]
        )
        try:
            await estimate_stream_size_gate(mock_config, state, media)  # no raise
        finally:
            dump_fansly_calls(route.calls, "test_master_fetch_error_passes")
        assert route.called

    async def test_media_playlist_without_variants_passes(
        self, respx_fansly_api, mock_config, entity_store
    ):
        mock_config.media_filters = MediaFilters(file_size_max=500)
        state = _state()
        master_url = (
            "https://cdn.example.com/vod/flat.m3u8?Policy=p&Key-Pair-Id=k&Signature=s"
        )
        media = await self._m3u8_media(entity_store, master_url, duration=2000.0)
        route = respx.get(url__startswith="https://cdn.example.com/vod/flat").mock(
            side_effect=[
                httpx.Response(
                    200,
                    text="#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\nseg0.ts\n#EXT-X-ENDLIST\n",
                )
            ]
        )
        try:
            await estimate_stream_size_gate(mock_config, state, media)  # no raise
        finally:
            dump_fansly_calls(route.calls, "test_media_playlist_without_variants")
        assert route.called

    async def test_zero_bandwidth_variant_passes(
        self, respx_fansly_api, mock_config, entity_store
    ):
        mock_config.media_filters = MediaFilters(file_size_max=500)
        state = _state()
        master_url = (
            "https://cdn.example.com/vod/nobw.m3u8?Policy=p&Key-Pair-Id=k&Signature=s"
        )
        media = await self._m3u8_media(entity_store, master_url, duration=2000.0)
        route = respx.get(url__startswith="https://cdn.example.com/vod/nobw").mock(
            side_effect=[
                httpx.Response(
                    200,
                    text="#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=0,RESOLUTION=1280x720\n720.m3u8\n",
                )
            ]
        )
        try:
            await estimate_stream_size_gate(mock_config, state, media)  # no raise
        finally:
            dump_fansly_calls(route.calls, "test_zero_bandwidth_variant_passes")
        assert route.called


class TestHlsResolutionCap:
    def test_picks_fitting_variant(self, respx_fansly_api, mock_config):
        master_url = (
            "https://cdn.example.com/vod/rescap.m3u8?Policy=p&Key-Pair-Id=k&Signature=s"
        )
        playlist_text = build_master_playlist(
            variants=[
                {
                    "url": "https://cdn.example.com/vod/720.m3u8",
                    "bandwidth": 1_000_000,
                    "resolution": (1280, 720),
                },
                {
                    "url": "https://cdn.example.com/vod/1080.m3u8",
                    "bandwidth": 3_000_000,
                    "resolution": (1920, 1080),
                },
                {
                    "url": "https://cdn.example.com/vod/4k.m3u8",
                    "bandwidth": 8_000_000,
                    "resolution": (3840, 2160),
                },
            ]
        )
        route = respx.get(url__startswith="https://cdn.example.com/vod/rescap").mock(
            side_effect=[httpx.Response(200, text=playlist_text)]
        )
        try:
            url = _get_highest_quality_variant_url(mock_config, master_url, {}, 1080)
        finally:
            dump_fansly_calls(route.calls, "test_picks_fitting_variant")
        assert route.called
        assert url == "https://cdn.example.com/vod/1080.m3u8"

    def test_raises_when_none_fit(self, respx_fansly_api, mock_config):
        master_url = "https://cdn.example.com/vod4k/only4k.m3u8?Policy=p&Key-Pair-Id=k&Signature=s"
        playlist_text = build_master_playlist(
            variants=[
                {
                    "url": "https://cdn.example.com/vod4k/4k.m3u8",
                    "bandwidth": 8_000_000,
                    "resolution": (3840, 2160),
                },
            ]
        )
        route = respx.get(url__startswith="https://cdn.example.com/vod4k/only4k").mock(
            side_effect=[httpx.Response(200, text=playlist_text)]
        )
        try:
            with pytest.raises(MediaFilteredError) as exc_info:
                _get_highest_quality_variant_url(mock_config, master_url, {}, 1080)
        finally:
            dump_fansly_calls(route.calls, "test_raises_when_none_fit")
        assert route.called
        assert exc_info.value.reason == "max_resolution"

    def test_unknown_resolution_is_not_sole_cause_of_skip(
        self, respx_fansly_api, mock_config
    ):
        master_url = "https://cdn.example.com/vodunk/unknown.m3u8?Policy=p&Key-Pair-Id=k&Signature=s"
        playlist_text = build_master_playlist(
            variants=[
                {
                    "url": "https://cdn.example.com/vodunk/stream.m3u8",
                    "bandwidth": 2_000_000,
                    "resolution": None,
                },
            ]
        )
        route = respx.get(
            url__startswith="https://cdn.example.com/vodunk/unknown"
        ).mock(side_effect=[httpx.Response(200, text=playlist_text)])
        try:
            url = _get_highest_quality_variant_url(mock_config, master_url, {}, 1080)
        finally:
            dump_fansly_calls(route.calls, "test_unknown_resolution_is_not_sole_cause")
        assert route.called
        assert url == "https://cdn.example.com/vodunk/stream.m3u8"
