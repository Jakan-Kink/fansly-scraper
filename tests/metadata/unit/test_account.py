"""Unit tests for metadata.account module."""

import copy
import json
from datetime import UTC, datetime

import pytest

from download.core import DownloadState
from metadata import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Media,
    TimelineStats,
    Wall,
    process_account_data,
    process_media_bundles,
)
from metadata.account import _backfill_missing_account_media, process_media_bundles_data
from metadata.wall import process_account_walls
from tests.fixtures.utils.test_isolation import snowflake_id


# ── Test data builders (realistic API shapes) ─────────────────────────────


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


def _account_dict():
    aid = snowflake_id()
    return {
        "id": aid,
        "username": f"user_{aid}",
        "displayName": "Test Display",
        "about": "Bio with #hashtag and emoji \u2764\ufe0f",
        "location": "Test City",
        "flags": 18,
        "version": 19,
        "createdAt": 1673792358000,
        "following": True,
        "profileAccess": True,
        "avatar": _media_dict(aid),
        "banner": _media_dict(aid, mimetype="image/png"),
        "timelineStats": {
            "accountId": aid,
            "imageCount": 100,
            "videoCount": 50,
            "bundleCount": 10,
            "bundleImageCount": 20,
            "bundleVideoCount": 5,
            "fetchedAt": 1700000000,
        },
        "mediaStoryState": {"accountId": aid, "status": 0},
        "walls": [
            {
                "id": snowflake_id(),
                "accountId": aid,
                "name": "Wall",
                "pos": 0,
                "createdAt": 1700000000,
            }
        ],
    }


@pytest.mark.asyncio
async def test_account_media_bundle_creation(entity_store):
    """Test creating an AccountMediaBundle with ordered content.

    Saves entities in FK order, then sets the ordered accountMedia
    relationship on the bundle and verifies persistence.
    """
    store = entity_store

    account_id = snowflake_id()
    media_id1 = snowflake_id()
    media_id2 = snowflake_id()
    am_id1 = snowflake_id()
    am_id2 = snowflake_id()
    bundle_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    # Media records (required by AccountMedia FK)
    media1 = Media(id=media_id1, accountId=account_id)
    media2 = Media(id=media_id2, accountId=account_id)
    await store.save(media1)
    await store.save(media2)

    # AccountMedia linking to Media
    am1 = AccountMedia(
        id=am_id1, accountId=account_id, mediaId=media_id1, createdAt=datetime.now(UTC)
    )
    am2 = AccountMedia(
        id=am_id2, accountId=account_id, mediaId=media_id2, createdAt=datetime.now(UTC)
    )
    await store.save(am1)
    await store.save(am2)

    # Bundle with ordered media
    bundle = AccountMediaBundle(
        id=bundle_id, accountId=account_id, createdAt=datetime.now(UTC)
    )
    await store.save(bundle)

    # Assign the ordered relationship and re-save (triggers _sync_associations)
    bundle.accountMedia = [am2, am1]  # pos 0 = am2, pos 1 = am1
    await store.save(bundle)

    # Verify
    saved = await store.get(AccountMediaBundle, bundle_id)
    assert saved is not None
    media_ids = sorted([m.id for m in saved.accountMedia])
    assert media_ids == sorted([am_id1, am_id2])


@pytest.mark.asyncio
async def test_update_optimization(entity_store, config):
    """Test that process_account_data updates only changed values.

    process_account_data uses get_store() internally, wired via entity_store.
    """
    store = entity_store

    account_id = snowflake_id()

    # Create initial account
    account = Account(id=account_id, username="test_user", displayName="Test User")
    await store.save(account)

    # Process with same values — displayName should stay unchanged
    data = {
        "id": account_id,
        "username": "test_user",
        "displayName": "Test User",
        "timelineStats": {"imageCount": 0, "videoCount": 0},
    }
    await process_account_data(config, data)

    saved = await store.get(Account, account_id)
    assert saved.displayName == "Test User"

    # Process with different displayName
    data["displayName"] = "New Name"
    await process_account_data(config, data)

    saved = await store.get(Account, account_id)
    assert saved.displayName == "New Name"
    assert saved.username == "test_user"


@pytest.mark.asyncio
async def test_timeline_stats_optimization(entity_store, config):
    """Test that timeline stats are only updated when values change.

    process_account_data uses get_store() internally, wired via entity_store.
    """
    store = entity_store

    account_id = snowflake_id()

    # Create account + initial timeline stats
    account = Account(id=account_id, username="test_user")
    await store.save(account)

    stats = TimelineStats(accountId=account_id, imageCount=10, videoCount=5)
    await store.save(stats)

    # Process with same stats — should not change
    data = {
        "id": account_id,
        "username": "test_user",
        "timelineStats": {
            "imageCount": 10,
            "videoCount": 5,
            "fetchedAt": int(datetime(2023, 10, 10, tzinfo=UTC).timestamp()),
        },
    }
    await process_account_data(config, data)

    saved = await store.find_one(TimelineStats, accountId=account_id)
    assert saved.imageCount == 10

    # Process with different imageCount
    data["timelineStats"]["imageCount"] = 15
    await process_account_data(config, data)

    saved = await store.find_one(TimelineStats, accountId=account_id)
    assert saved.imageCount == 15
    assert saved.videoCount == 5


@pytest.mark.asyncio
async def test_process_media_bundles(entity_store, config):
    """Test processing media bundles from API response.

    process_media_bundles uses get_store() internally, wired via entity_store.
    """
    store = entity_store

    account_id = snowflake_id()
    media_id1 = snowflake_id()
    media_id2 = snowflake_id()
    am_id1 = snowflake_id()
    am_id2 = snowflake_id()
    bundle_id = snowflake_id()

    # Create prerequisite entities in FK order
    account = Account(id=account_id, username="test_user")
    await store.save(account)

    media1 = Media(id=media_id1, accountId=account_id)
    media2 = Media(id=media_id2, accountId=account_id)
    await store.save(media1)
    await store.save(media2)

    am1 = AccountMedia(
        id=am_id1, accountId=account_id, mediaId=media_id1, createdAt=datetime.now(UTC)
    )
    am2 = AccountMedia(
        id=am_id2, accountId=account_id, mediaId=media_id2, createdAt=datetime.now(UTC)
    )
    await store.save(am1)
    await store.save(am2)

    # Process bundles — use accountMediaIds (the Pydantic alias) directly.
    # NOTE: bundleContent → accountMediaIds conversion is a Phase 5.5
    # TODO in _process_single_bundle; for now the model expects IDs.
    bundles_data = [
        {
            "id": bundle_id,
            "accountId": account_id,
            "createdAt": int(datetime.now(UTC).timestamp()),
            "accountMediaIds": [am_id2, am_id1],  # IDs in desired order
        }
    ]
    await process_media_bundles(config, account_id, bundles_data)

    # Verify bundle was created with media
    bundle = await store.get(AccountMediaBundle, bundle_id)
    assert bundle is not None
    media_ids = sorted([m.id for m in bundle.accountMedia])
    assert media_ids == sorted([am_id1, am_id2])


class TestProcessMediaBundlesDataEdgeCases:
    """Cover account.py lines 39→43, 40→39, 70."""

    @pytest.mark.asyncio
    async def test_no_matching_id_field(self, entity_store, mock_config):
        """Lines 39→43, 40→39: id_fields loop finds no match → account_id stays None."""
        data = {
            "messages": [{"id": snowflake_id(), "content": "no sender"}],
            "accountMediaBundles": [
                {
                    "id": snowflake_id(),
                    "accountId": snowflake_id(),
                    "createdAt": 1700000000,
                    "deleted": False,
                },
            ],
        }
        await process_media_bundles_data(mock_config, data)

    @pytest.mark.asyncio
    async def test_bundle_with_preview(self, entity_store, mock_config, test_account):
        """Line 70: bundle_obj.preview is truthy → saves preview Media."""
        store = entity_store
        preview_id = snowflake_id()
        bundle_id = snowflake_id()

        preview = Media(id=preview_id, accountId=test_account.id, mimetype="image/jpeg")
        await store.save(preview)

        data = {
            "messages": [{"id": snowflake_id(), "senderId": test_account.id}],
            "accountMediaBundles": [
                {
                    "id": bundle_id,
                    "accountId": test_account.id,
                    "previewId": preview_id,
                    "createdAt": 1700000000,
                    "deleted": False,
                },
            ],
        }
        await process_media_bundles_data(mock_config, data)
        bundle = await store.get(AccountMediaBundle, bundle_id)
        assert bundle is not None


class TestFullAccountPipeline:
    """Process complete Account dict through process_account_data."""

    @pytest.mark.asyncio
    async def test_account_with_all_relationships(self, entity_store, mock_config):
        """Full account with avatar, banner, timelineStats, mediaStoryState, walls.

        Exercises: _coerce_api_types (ms timestamp → datetime), _process_nested_cache_lookups
        (avatar/banner as dict-with-id, timelineStats as dict-without-id, walls as list),
        entity_store.save with junction sync (account_avatar, account_banner), _add_to_inverse.
        """
        data = _account_dict()
        await process_account_data(mock_config, data=data)

        acct = await entity_store.get(Account, data["id"])
        assert acct is not None
        assert acct.displayName == "Test Display"
        assert isinstance(acct.createdAt, datetime)  # ms timestamp coerced

        # Avatar — scalar 1:1 junction
        assert acct.avatar is not None
        assert acct.avatar.mimetype == "image/jpeg"
        assert len(acct.avatar.variants) >= 1
        assert len(acct.avatar.locations) >= 1

        # Banner — same mechanism
        assert acct.banner is not None
        assert acct.banner.mimetype == "image/png"

        # TimelineStats — dict without "id" key, enriched with accountId
        assert acct.timelineStats is not None
        assert acct.timelineStats.imageCount == 100
        assert acct.timelineStats.id == acct.id  # PK derived from accountId

        # MediaStoryState
        assert acct.mediaStoryState is not None
        assert acct.mediaStoryState.id == acct.id

        # Walls
        walls = await entity_store.find(Wall, accountId=acct.id)
        assert len(walls) == 1

        # to_db_dict should serialize cleanly
        db_dict = acct.to_db_dict()
        assert "avatar" not in db_dict  # Relationships excluded
        assert db_dict["username"] == data["username"]

    @pytest.mark.asyncio
    async def test_account_revalidation_merges(self, entity_store, mock_config):
        """Processing same account twice should merge via identity map."""
        data = _account_dict()
        await process_account_data(mock_config, data=copy.deepcopy(data))
        acct1 = await entity_store.get(Account, data["id"])

        data2 = copy.deepcopy(data)
        data2["displayName"] = "Updated Name"
        await process_account_data(mock_config, data=data2)
        acct2 = await entity_store.get(Account, data["id"])

        assert acct1 is acct2  # Same identity map object
        assert acct2.displayName == "Updated Name"

    @pytest.mark.asyncio
    async def test_account_no_id_skipped(self, entity_store, mock_config):
        await process_account_data(mock_config, data={"username": "no_id"})

    @pytest.mark.asyncio
    async def test_wall_stale_deletion_and_missing_id(self, entity_store, mock_config):
        """Walls: stale deletion + missing-id skip."""
        data = _account_dict()
        await process_account_data(mock_config, data=copy.deepcopy(data))
        acct = await entity_store.get(Account, data["id"])

        stale = Wall(id=snowflake_id(), accountId=acct.id, name="stale", pos=1)
        await entity_store.save(stale)

        # Process with only original wall + one without id
        await process_account_walls(
            mock_config,
            acct,
            [data["walls"][0], {"name": "no_id_wall"}],
        )
        assert await entity_store.get(Wall, stale.id) is None  # Deleted

    @pytest.mark.asyncio
    async def test_wall_empty_after_id_filter(self, entity_store, mock_config):
        """All walls missing 'id' → current_wall_ids empty → no deletion (41→exit)."""
        data = _account_dict()
        await process_account_data(mock_config, data=copy.deepcopy(data))
        acct = await entity_store.get(Account, data["id"])
        # Only walls without id → no valid IDs → skip deletion
        await process_account_walls(
            mock_config, acct, [{"name": "no_id_1"}, {"name": "no_id_2"}]
        )

    @pytest.mark.asyncio
    async def test_wall_posts_with_missing_post(
        self, entity_store, mock_config, test_account
    ):
        """process_wall_posts where referenced post doesn't exist (wall.py:70→68)."""
        from metadata.wall import process_wall_posts

        # Create a wall first
        wall = Wall(id=snowflake_id(), accountId=test_account.id, name="wp_test", pos=0)
        await entity_store.save(wall)

        state = DownloadState()
        state.creator_id = test_account.id

        # Post data references a non-existent post
        posts_data = {
            "posts": [
                {
                    "id": snowflake_id(),
                    "accountId": test_account.id,
                    "fypFlags": 0,
                    "createdAt": 1700000000,
                }
            ],
            "accounts": [],
            "accountMedia": [],
            "accountMediaBundles": [],
        }
        await process_wall_posts(mock_config, state, str(wall.id), posts_data)

    @pytest.mark.asyncio
    async def test_bundles_with_account_id_extraction(
        self, entity_store, mock_config, test_account
    ):
        """process_media_bundles_data should extract accountId from messages."""
        bundle_id = snowflake_id()
        data = {
            "messages": [
                {
                    "id": snowflake_id(),
                    "senderId": test_account.id,
                    "createdAt": 1700000000,
                }
            ],
            "accountMediaBundles": [
                {
                    "id": bundle_id,
                    "accountId": test_account.id,
                    "createdAt": 1700000000,
                    "deleted": False,
                },
            ],
        }
        await process_media_bundles_data(mock_config, data)

    @pytest.mark.asyncio
    async def test_bundles_no_messages_no_crash(self, entity_store, mock_config):
        data = {
            "accountMediaBundles": [
                {
                    "id": snowflake_id(),
                    "accountId": snowflake_id(),
                    "createdAt": 1700000000,
                    "deleted": False,
                },
            ]
        }
        await process_media_bundles_data(mock_config, data)

    @pytest.mark.asyncio
    async def test_bundles_no_key_is_noop(self, entity_store, mock_config):
        await process_media_bundles_data(mock_config, {"messages": []})

    @pytest.mark.asyncio
    async def test_bundles_no_matching_id_field(self, entity_store, mock_config):
        """account.py 39→43, 40→39: id_fields loop where no field matches.
        All first_item.get(field) return None → account_id stays None → skip."""
        data = {
            "messages": [
                {"id": snowflake_id(), "content": "no sender or recipient fields"}
            ],
            "accountMediaBundles": [
                {
                    "id": snowflake_id(),
                    "accountId": snowflake_id(),
                    "createdAt": 1700000000,
                    "deleted": False,
                },
            ],
        }
        # id_fields defaults to ["senderId", "recipientId"] — neither present
        await process_media_bundles_data(mock_config, data)

    @pytest.mark.asyncio
    async def test_bundle_with_preview(self, entity_store, mock_config, test_account):
        """account.py line 70: bundle_obj.preview is truthy → saves preview Media first."""
        preview_id = snowflake_id()
        bundle_id = snowflake_id()

        preview = Media(id=preview_id, accountId=test_account.id, mimetype="image/jpeg")
        await entity_store.save(preview)

        data = {
            "messages": [{"id": snowflake_id(), "senderId": test_account.id}],
            "accountMediaBundles": [
                {
                    "id": bundle_id,
                    "accountId": test_account.id,
                    "previewId": preview_id,
                    "createdAt": 1700000000,
                    "deleted": False,
                },
            ],
        }
        await process_media_bundles_data(mock_config, data)
        bundle = await entity_store.get(AccountMediaBundle, bundle_id)
        assert bundle is not None

    @pytest.mark.asyncio
    async def test_backfill_still_missing(
        self, entity_store, mock_config, test_account
    ):
        """account.py line 146: backfill API returns data but ID still not in cache."""
        bundle = AccountMediaBundle(
            id=snowflake_id(),
            accountId=test_account.id,
            createdAt=datetime.now(UTC),
            deleted=False,
        )
        await entity_store.save(bundle)

        ghost_id = snowflake_id()
        # Mock the API to return empty (the ghost ID won't appear in cache)
        api_mock = type(
            "API",
            (),
            {
                "get_account_media": lambda _self, _ids: None,
                "get_json_response_contents": lambda _self, _resp: [],
            },
        )()
        mock_config.get_api = lambda: api_mock

        await _backfill_missing_account_media(
            mock_config, entity_store, bundle, [ghost_id]
        )
        # ghost_id was never resolved → else branch at line 146 fired
        assert entity_store.get_from_cache(AccountMedia, ghost_id) is None
