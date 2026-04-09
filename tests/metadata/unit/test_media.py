"""Unit tests for metadata.media module."""

import json

import pytest

from download.core import DownloadState
from metadata import Media
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
from metadata.models import Account, AccountMedia, MediaLocation
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


@pytest.mark.asyncio
async def test_media_creation(entity_store):
    """Test creating a Media object with basic attributes via the EntityStore."""
    store = entity_store

    account_id = snowflake_id()
    media_id = snowflake_id()

    account = Account(id=account_id, username="test_user_123")
    await store.save(account)

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


@pytest.mark.asyncio
async def test_process_video_metadata(entity_store, config):
    """Test processing video metadata with duration and dimensions.

    process_media_item_dict uses get_store() internally, which is wired
    to the entity_store fixture via register_models().
    """
    store = entity_store

    account_id = snowflake_id()
    media_id = snowflake_id()

    account = Account(id=account_id, username="test_user_123")
    await store.save(account)

    media_item = {
        "id": media_id,
        "accountId": account_id,
        "mimetype": "video/mp4",
        "metadata": json.dumps(
            {"original": {"width": 1920, "height": 1080}, "duration": 30.5}
        ),
    }

    await process_media_item_dict(config, media_item)

    saved_media = await store.get(Media, media_id)
    assert saved_media is not None
    assert saved_media.width == 1920
    assert saved_media.height == 1080
    assert saved_media.duration == 30.5


@pytest.mark.asyncio
async def test_media_location(entity_store):
    """Test creating Media with associated MediaLocation objects.

    Locations are added after the initial save so the media row exists
    before _sync_associations inserts into media_locations (FK requirement).
    """
    store = entity_store

    account_id = snowflake_id()
    media_id = snowflake_id()

    account = Account(id=account_id, username="test_user_123")
    await store.save(account)

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
    assert "example.com/video.mp4" in media.locations[0].location


@pytest.mark.asyncio
async def test_invalid_metadata(entity_store, config):
    """Test handling invalid metadata JSON.

    When metadata is not valid JSON, the model stores it as meta_info
    and leaves dimension/duration fields as None.
    """
    store = entity_store

    account_id = snowflake_id()
    media_id = snowflake_id()

    account = Account(id=account_id, username="test_user_123")
    await store.save(account)

    media_item = {
        "id": media_id,
        "accountId": account_id,
        "mimetype": "video/mp4",
        "metadata": "invalid json",
    }

    await process_media_item_dict(config, media_item)

    saved_media = await store.get(Media, media_id)
    assert saved_media is not None
    assert saved_media.meta_info == "invalid json"
    assert saved_media.duration is None
    assert saved_media.width is None
    assert saved_media.height is None


class TestProcessMediaDownloadEdgeCases:
    """Cover metadata/media.py line 121."""

    @pytest.mark.asyncio
    async def test_no_account_id_uses_creator_id(
        self, entity_store, mock_config, test_account
    ):
        """Line 121: media.accountId is falsy → assign from state.creator_id."""
        state = DownloadState()
        state.creator_id = test_account.id

        media = Media(id=snowflake_id(), accountId=test_account.id)
        object.__setattr__(media, "accountId", 0)  # Force falsy

        result = await process_media_download(mock_config, state, media)
        assert result is not None
        assert result.accountId == test_account.id


class TestFullMediaPipeline:
    """Process AccountMedia through process_media_info + media_utils."""

    @pytest.mark.asyncio
    async def test_account_media_with_nested_media_and_variants(
        self, entity_store, mock_config
    ):
        """Full AccountMedia processing — nested media, variants, locations."""
        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"media_{acct_id}"))

        am = _account_media_dict(acct_id)
        await process_media_info(mock_config, am)

        media = await entity_store.get(Media, am["media"]["id"])
        assert media is not None
        assert media.mimetype == "video/mp4"
        assert len(media.variants) >= 1

        # Video metadata extraction (duration from JSON)
        assert media.width == 1920
        assert media.duration == 120.5

    @pytest.mark.asyncio
    async def test_batch_media_processing(self, entity_store, mock_config):
        acct_id = snowflake_id()
        await entity_store.save(Account(id=acct_id, username=f"batch_{acct_id}"))
        batch = {"batch": [_account_media_dict(acct_id) for _ in range(3)]}
        await process_media_info(mock_config, batch)
        for item in batch["batch"]:
            assert await entity_store.get(Media, item["media"]["id"]) is not None

    @pytest.mark.asyncio
    async def test_missing_account_id_skipped(self, entity_store, mock_config):
        mid = snowflake_id()
        await process_media_info(mock_config, {"id": mid, "media": {"id": mid}})
        assert await entity_store.get(Media, mid) is None

    @pytest.mark.asyncio
    async def test_process_media_item_dict_non_dict(self, entity_store, mock_config):
        await process_media_item_dict(mock_config, "not_a_dict")

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_process_media_download(
        self, entity_store, test_account, mock_config
    ):
        """process_media_download: skip downloaded, save new, raise without creator_id."""
        # Skip downloaded
        downloaded = Media(
            id=snowflake_id(),
            accountId=test_account.id,
            is_downloaded=True,
            content_hash="h",
            local_filename="f.mp4",
        )
        await entity_store.save(downloaded)
        state = DownloadState()
        state.creator_id = test_account.id
        assert await process_media_download(mock_config, state, downloaded) is None

        # Save new
        new = Media(id=snowflake_id(), accountId=test_account.id)
        result = await process_media_download(mock_config, state, new)
        assert result is not None
        assert result.id == new.id

        # Raise without creator_id
        state2 = DownloadState()
        state2.creator_id = None
        with pytest.raises(ValueError, match="creator_id"):
            await process_media_download(
                mock_config, state2, Media(id=snowflake_id(), accountId=test_account.id)
            )

    @pytest.mark.asyncio
    async def test_validate_media_id(self):
        assert validate_media_id(snowflake_id(), context_id=1) is not None
        assert validate_media_id(2**63, context_id=1) is None
        assert validate_media_id("not_numeric", context_id=1) is None
        assert validate_media_id(str(snowflake_id()), context_id=1) is not None
        assert validate_media_id({"id": 1}, context_id=1) is None
        assert validate_media_id([1], context_id=1) is None

    @pytest.mark.asyncio
    async def test_link_media_to_bundle_all_paths(
        self, entity_store, test_account, test_media, test_bundle
    ):
        """link_media_to_bundle — every branch in one test:
        1. New media (media_obj=None) + explicit account_id → creates Media + AccountMedia + junction
        2. Existing media + account_id=None → infers accountId from media (line 135)
        3. Existing media + existing AccountMedia → skips AM creation (line 152→165)
        4. No media + no account_id → early return (lines 137-143)
        """
        # Path 1: New media, explicit account_id → creates everything
        new_mid = snowflake_id()
        await link_media_to_bundle(
            test_bundle.id, new_mid, pos=0, account_id=test_account.id
        )
        created_media = await entity_store.get(Media, new_mid)
        assert created_media is not None
        assert created_media.accountId == test_account.id
        created_am = await entity_store.get(AccountMedia, new_mid)
        assert created_am is not None
        assert created_am.mediaId == new_mid

        # Path 2: Existing media (test_media), account_id=None → infers from media.accountId
        await link_media_to_bundle(
            test_bundle.id, test_media.id, pos=1, account_id=None
        )
        inferred_am = await entity_store.get(AccountMedia, test_media.id)
        assert inferred_am is not None
        assert inferred_am.accountId == test_account.id

        # Path 3: Call again on same media — Media + AccountMedia already exist → skips creation
        await link_media_to_bundle(
            test_bundle.id, test_media.id, pos=2, account_id=test_account.id
        )
        # No error, junction updated

        # Path 4: No media + no account_id → early return, nothing created
        orphan_id = snowflake_id()
        await link_media_to_bundle(test_bundle.id, orphan_id, pos=3, account_id=None)
        assert await entity_store.get(Media, orphan_id) is None

    @pytest.mark.asyncio
    async def test_process_preview_dict(self, entity_store, mock_config, test_account):
        parent = Media(id=snowflake_id(), accountId=test_account.id)
        await entity_store.save(parent)
        pid = snowflake_id()
        await process_preview(
            mock_config,
            parent,
            {"id": pid, "accountId": test_account.id, "mimetype": "image/jpeg"},
            account_id=test_account.id,
        )
        assert await entity_store.get(Media, pid) is not None

    @pytest.mark.asyncio
    async def test_process_preview_noop_cases(
        self, entity_store, mock_config, test_account
    ):
        parent = Media(id=snowflake_id(), accountId=test_account.id)
        await process_preview(mock_config, parent, None)
        await process_preview(mock_config, parent, "")
        await process_preview(mock_config, parent, "   ")
        await process_preview(mock_config, parent, 12345)
