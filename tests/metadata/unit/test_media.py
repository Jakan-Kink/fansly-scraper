"""Unit tests for metadata.media module.

Both classes share ONE class-scoped database each via ``class_entity_store``
(requested through ``reset_class_store``, which clears the in-memory
cache/identity map between methods). Rows are namespaced by unique snowflake
ids — accounts embed their id in the username because ``accounts.username``
is UNIQUE in the shared database.
"""

import json
from datetime import UTC, datetime

import pytest

from download.core import DownloadState
from helpers.common import JsonDict, expect_dict, expect_list
from metadata import Media
from metadata.entity_store import PostgresEntityStore
from metadata.media import (
    _should_skip_media,
    process_media_download,
    process_media_info,
    process_media_item_dict,
)
from metadata.media_utils import (
    link_media_to_bundle,
    process_preview,
    validate_media_id,
)
from metadata.models import Account, AccountMedia, AccountMediaBundle, MediaLocation
from tests.fixtures.utils.test_isolation import snowflake_id


# ── Test data builders ────────────────────────────────────────────────────


def _media_dict(account_id, *, mimetype="image/jpeg", with_variants=True):
    media_id = snowflake_id()
    d = {
        "id": media_id,
        "type": 1 if "image" in mimetype else 2,
        "status": 1,
        "accountId": account_id,
        "mimetype": mimetype,
        "flags": 426,
        "location": f"/{account_id}/{media_id}.jpeg",
        "width": 1920,
        "height": 1080,
        "updatedAt": 1730187497,
        "createdAt": 1730187495,
        "locations": [
            {
                "locationId": 1,
                "location": f"https://cdn3.fansly.com/{account_id}/{media_id}.jpeg?token=x",
            }
        ],
    }
    if with_variants:
        vid = snowflake_id()
        d["variants"] = [
            {
                "id": vid,
                "type": d["type"],
                "status": 1,
                "mimetype": mimetype,
                "flags": 0,
                "width": 720,
                "height": 480,
                "updatedAt": 1730187496,
                "locations": [
                    {
                        "locationId": 1,
                        "location": f"https://cdn3.fansly.com/{account_id}/{vid}.jpeg?token=y",
                    }
                ],
            }
        ]
    if mimetype.startswith("video/"):
        d["metadata"] = json.dumps(
            {"original": {"width": 1920, "height": 1080}, "duration": 120.5}
        )
    return d


def _account_media_dict(account_id, *, mimetype="video/mp4"):
    media = _media_dict(account_id, mimetype=mimetype)
    return {
        "id": snowflake_id(),
        "accountId": account_id,
        "mediaId": media["id"],
        "media": media,
        "createdAt": 1700000000,
        "deleted": False,
        "access": True,
    }


async def _make_account(store: PostgresEntityStore) -> tuple[Account, int]:
    """Create + persist a fresh Account, returning it with its (int) id.

    Usernames embed the id because ``accounts.username`` is UNIQUE and the
    whole class shares one database.
    """
    account_id = snowflake_id()
    account = Account(id=account_id, username=f"media_user_{account_id}")
    await store.save(account)
    return account, account_id


@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.xdist_group("metadata_media_store")
class TestMediaStore:
    """Media model round-trips + item-dict processing over ONE shared class DB."""

    async def test_media_creation(self, reset_class_store):
        """Test creating a Media object with basic attributes via the EntityStore."""
        store = reset_class_store

        _account, account_id = await _make_account(store)
        media_id = snowflake_id()

        media = Media(
            id=media_id,
            accountId=account_id,
            mimetype="video/mp4",
            width=1920,
            height=1080,
            duration=30.5,
        )
        await store.save(media)

        saved_media = await store.get(Media, media_id)
        assert saved_media is not None
        assert saved_media.id == media_id
        assert saved_media.accountId == account_id
        assert saved_media.mimetype == "video/mp4"
        assert saved_media.width == 1920
        assert saved_media.height == 1080
        assert saved_media.duration == 30.5

    async def test_local_path_is_transient(self, reset_class_store):
        """local_path is a download-time transient: excluded from writes, never dirties.

        It carries the full on-disk save path on the live identity-map object so the
        incremental Stash pass can scan exactly those files, while local_filename
        stays the basename. Being outside __tracked_fields__ and in _WRITE_EXCLUDED,
        setting it must not mark a clean Media dirty (so it is never UPDATE-persisted).
        """
        assert "local_path" in Media._WRITE_EXCLUDED
        assert "local_path" not in Media.__tracked_fields__

        store = reset_class_store
        _account, account_id = await _make_account(store)
        media_id = snowflake_id()

        media = Media(id=media_id, accountId=account_id, mimetype="video/mp4")
        await store.save(media)
        assert media.is_dirty() is False  # clean immediately after save

        media.local_path = "/dl/lp_user/Videos/clip_id_42.mp4"
        assert media.is_dirty() is False  # transient field does not dirty the row
        assert media.local_path == "/dl/lp_user/Videos/clip_id_42.mp4"

    async def test_process_video_metadata(self, reset_class_store, mock_config):
        """Test processing video metadata with duration and dimensions.

        process_media_item_dict uses get_store() internally, which is wired
        to the class-scoped store.
        """
        store = reset_class_store

        _account, account_id = await _make_account(store)
        media_id = snowflake_id()

        media_item: JsonDict = {
            "id": media_id,
            "accountId": account_id,
            "mimetype": "video/mp4",
            "metadata": json.dumps(
                {"original": {"width": 1920, "height": 1080}, "duration": 30.5}
            ),
        }

        await process_media_item_dict(mock_config, media_item)

        saved_media = await store.get(Media, media_id)
        assert saved_media is not None
        assert saved_media.width == 1920
        assert saved_media.height == 1080
        assert saved_media.duration == 30.5

    async def test_media_location(self, reset_class_store):
        """Test creating Media with associated MediaLocation objects.

        Locations are added after the initial save so the media row exists
        before _sync_associations inserts into media_locations (FK requirement).
        """
        store = reset_class_store

        _account, account_id = await _make_account(store)
        media_id = snowflake_id()

        # Save media first (creates the row)
        media = Media(id=media_id, accountId=account_id, mimetype="video/mp4")
        await store.save(media)

        # Then add locations and save again (triggers _sync_associations)
        media.locations = [
            MediaLocation(
                mediaId=media_id,
                locationId=102,
                location="https://example.com/video.mp4",
            )
        ]
        await store.save(media)

        assert len(media.locations) == 1
        assert media.locations[0].locationId == 102
        # normalize_cdn_url strips query params; plain path stays unchanged
        location = media.locations[0].location
        assert location is not None
        assert "example.com/video.mp4" in location

    async def test_media_validate_accepts_null_location_in_locations_entry(
        self, reset_class_store
    ):
        """Regression for issue #78 using the reporter's exact payload.

        Fansly returns ``locations=[{"location": None, "locationId": 1}]`` for
        some media (Direct slots with no CDN path yet). Before the fix
        ``Media.model_validate`` raised ``ValidationError: 8 validation errors``
        because ``MediaLocation.location`` was typed as required ``str``.

        The store is required so that ``_process_nested_cache_lookups``
        injects ``mediaId`` from the parent Media's ``id`` into each locations
        entry — that mirrors the real ``process_media_info`` call site.
        """
        account_id = 358160426321584128
        account = Account(id=account_id, username="worthlessholes")
        await reset_class_store.save(account)

        payload = {
            "accountId": account_id,
            "flags": 0,
            "height": 1440,
            "id": 388733720573517825,
            "location": "",
            "locations": [{"location": None, "locationId": 1}],
            "metadata": '{"duration":14.266667}',
            "mimetype": "video/mp4",
            "status": 1,
            "type": 2,
            "updatedAt": 1654177272,
            "width": 2560,
        }

        media = Media.model_validate(payload)

        assert media.id == 388733720573517825
        assert len(media.locations) == 1
        assert media.locations[0].locationId == 1
        assert media.locations[0].location is None
        assert media.locations[0].mediaId == 388733720573517825

    async def test_media_location_null_roundtrips_through_store(
        self, reset_class_store
    ):
        """A MediaLocation with ``location=None`` saves and reloads cleanly.

        Exercises the ``media_locations.location`` NOT NULL → NULL migration.
        """
        store = reset_class_store

        _account, account_id = await _make_account(store)
        media_id = snowflake_id()

        media = Media(id=media_id, accountId=account_id, mimetype="video/mp4")
        await store.save(media)

        media.locations = [
            MediaLocation(mediaId=media_id, locationId=1, location=None),
        ]
        await store.save(media)

        reloaded = await store.get(Media, media_id)
        assert reloaded is not None
        assert len(reloaded.locations) == 1
        assert reloaded.locations[0].locationId == 1
        assert reloaded.locations[0].location is None

    async def test_invalid_metadata(self, reset_class_store, mock_config):
        """Test handling invalid metadata JSON.

        When metadata is not valid JSON, the model stores it as meta_info
        and leaves dimension/duration fields as None.
        """
        store = reset_class_store

        _account, account_id = await _make_account(store)
        media_id = snowflake_id()

        media_item: JsonDict = {
            "id": media_id,
            "accountId": account_id,
            "mimetype": "video/mp4",
            "metadata": "invalid json",
        }

        await process_media_item_dict(mock_config, media_item)

        saved_media = await store.get(Media, media_id)
        assert saved_media is not None
        assert saved_media.meta_info == "invalid json"
        assert saved_media.duration is None
        assert saved_media.width is None
        assert saved_media.height is None


@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.xdist_group("metadata_media_pipeline")
class TestFullMediaPipeline:
    """Process AccountMedia through process_media_info + media_utils."""

    async def test_account_media_with_nested_media_and_variants(
        self, reset_class_store, mock_config
    ):
        """Full AccountMedia processing — nested media, variants, locations."""
        _account, acct_id = await _make_account(reset_class_store)

        am = _account_media_dict(acct_id)
        await process_media_info(mock_config, am)

        media = await reset_class_store.get(Media, am["media"]["id"])
        assert media is not None
        assert media.mimetype == "video/mp4"
        assert len(media.variants) >= 1

        # Video metadata extraction (duration from JSON)
        assert media.width == 1920
        assert media.duration == 120.5

    async def test_batch_media_processing(self, reset_class_store, mock_config):
        _account, acct_id = await _make_account(reset_class_store)
        batch: JsonDict = {"batch": [_account_media_dict(acct_id) for _ in range(3)]}
        await process_media_info(mock_config, batch)
        for item in expect_list(batch["batch"], "batch"):
            media_dict = expect_dict(expect_dict(item, "item")["media"], "media")
            assert await reset_class_store.get(Media, media_dict["id"]) is not None

    async def test_missing_account_id_skipped(self, reset_class_store, mock_config):
        mid = snowflake_id()
        await process_media_info(mock_config, {"id": mid, "media": {"id": mid}})
        assert await reset_class_store.get(Media, mid) is None

    async def test_process_media_item_dict_non_dict(
        self, reset_class_store, mock_config
    ):
        # process_media_item_dict now takes JsonDict; the defensive non-dict
        # guard was removed (its sole production caller isinstance-narrows
        # first). A non-dict therefore raises rather than silently returning.
        with pytest.raises(AttributeError):
            await process_media_item_dict(mock_config, "not_a_dict")  # type: ignore[arg-type]

    async def test_should_skip_media(self):
        assert _should_skip_media(None) is False
        m = Media(
            id=snowflake_id(),
            accountId=snowflake_id(),
            is_downloaded=True,
            content_hash="abc",
            local_filename="f.mp4",
        )
        assert _should_skip_media(m) is True
        m2 = Media(id=snowflake_id(), accountId=snowflake_id(), is_downloaded=True)
        assert _should_skip_media(m2) is False

    async def test_process_media_download(self, reset_class_store, mock_config):
        """process_media_download: skip downloaded, save new, raise without
        creator_id, and backfill a falsy accountId from state.creator_id
        (metadata/media.py line 122)."""
        _account, account_id = await _make_account(reset_class_store)

        # Skip downloaded
        downloaded = Media(
            id=snowflake_id(),
            accountId=account_id,
            is_downloaded=True,
            content_hash="h",
            local_filename="f.mp4",
        )
        await reset_class_store.save(downloaded)
        state = DownloadState()
        state.creator_id = account_id
        assert await process_media_download(mock_config, state, downloaded) is None

        # Save new
        new = Media(id=snowflake_id(), accountId=account_id)
        result = await process_media_download(mock_config, state, new)
        assert result is not None
        assert result.id == new.id

        # Raise without creator_id
        state2 = DownloadState()
        state2.creator_id = None
        with pytest.raises(ValueError, match="creator_id"):
            await process_media_download(
                mock_config, state2, Media(id=snowflake_id(), accountId=account_id)
            )

        # Falsy accountId → assigned from state.creator_id
        unowned = Media(id=snowflake_id(), accountId=account_id)
        object.__setattr__(unowned, "accountId", 0)  # Force falsy
        backfilled = await process_media_download(mock_config, state, unowned)
        assert backfilled is not None
        assert backfilled.accountId == account_id

    async def test_validate_media_id(self):
        assert validate_media_id(snowflake_id(), context_id=1) is not None
        assert validate_media_id(2**63, context_id=1) is None
        assert validate_media_id("not_numeric", context_id=1) is None
        assert validate_media_id(str(snowflake_id()), context_id=1) is not None
        assert validate_media_id({"id": 1}, context_id=1) is None
        assert validate_media_id([1], context_id=1) is None  # type: ignore[arg-type]  # list tests unsupported-type rejection

    async def test_link_media_to_bundle_all_paths(self, reset_class_store, mock_config):
        """link_media_to_bundle — every branch in one test:
        1. New media (media_obj=None) + explicit account_id → creates Media + AccountMedia + junction
        2. Existing media + account_id=None → infers accountId from media (line 135)
        3. Existing media + existing AccountMedia → skips AM creation (line 152→165)
        4. No media + no account_id → early return (lines 137-143)
        """
        store = reset_class_store
        _account, account_id = await _make_account(store)

        existing_mid = snowflake_id()
        existing_media = Media(
            id=existing_mid, accountId=account_id, mimetype="video/mp4"
        )
        await store.save(existing_media)

        bundle_id = snowflake_id()
        bundle = AccountMediaBundle(
            id=bundle_id,
            accountId=account_id,
            createdAt=datetime.now(UTC),
            deleted=False,
        )
        await store.save(bundle)

        # Path 1: New media, explicit account_id → creates everything
        new_mid = snowflake_id()
        await link_media_to_bundle(bundle_id, new_mid, pos=0, account_id=account_id)
        created_media = await store.get(Media, new_mid)
        assert created_media is not None
        assert created_media.accountId == account_id
        created_am = await store.get(AccountMedia, new_mid)
        assert created_am is not None
        assert created_am.mediaId == new_mid

        # Path 2: Existing media, account_id=None → infers from media.accountId
        await link_media_to_bundle(bundle_id, existing_mid, pos=1, account_id=None)
        inferred_am = await store.get(AccountMedia, existing_mid)
        assert inferred_am is not None
        assert inferred_am.accountId == account_id

        # Path 3: Call again on same media — Media + AccountMedia already exist → skips creation
        await link_media_to_bundle(
            bundle_id, existing_mid, pos=2, account_id=account_id
        )
        # No error, junction updated

        # Path 4: No media + no account_id → early return, nothing created
        orphan_id = snowflake_id()
        await link_media_to_bundle(bundle_id, orphan_id, pos=3, account_id=None)
        assert await store.get(Media, orphan_id) is None

    async def test_process_preview_dict(self, reset_class_store, mock_config):
        store = reset_class_store
        _account, account_id = await _make_account(store)
        parent = Media(id=snowflake_id(), accountId=account_id)
        await store.save(parent)
        pid = snowflake_id()
        await process_preview(
            mock_config,
            parent,  # type: ignore[arg-type]  # Media.id is int|None; HasPreview wants int, real Media is intended
            {"id": pid, "accountId": account_id, "mimetype": "image/jpeg"},
            account_id=account_id,
        )
        assert await store.get(Media, pid) is not None

    async def test_process_preview_noop_cases(self, reset_class_store, mock_config):
        _account, account_id = await _make_account(reset_class_store)
        parent = Media(id=snowflake_id(), accountId=account_id)
        # Media.id is int|None; HasPreview wants int — real Media is the intended arg.
        await process_preview(mock_config, parent, None)  # type: ignore[arg-type]
        await process_preview(mock_config, parent, "")  # type: ignore[arg-type]
        await process_preview(mock_config, parent, "   ")  # type: ignore[arg-type]
        await process_preview(mock_config, parent, 12345)  # type: ignore[arg-type]  # non-dict preview tests no-op path
