"""Unit tests for post metadata functionality."""

import copy
import json
import logging
from datetime import UTC, datetime

import pytest

from download.core import DownloadState
from helpers.common import JsonDict, expect_dict, expect_list
from metadata import Account, Attachment, ContentType, Post
from metadata.account import process_account_data
from metadata.models import PostMention
from metadata.post import (
    _process_timeline_post,
    process_pinned_posts,
    process_timeline_posts,
)
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


def _account_dict():
    aid = snowflake_id()
    return {
        "id": aid,
        "username": f"user_{aid}",
        "displayName": "Test Display",
        "about": "Bio with #hashtag and emoji ❤️",
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


def _post_dict(account_id):
    pid = snowflake_id()
    return {
        "id": pid,
        "accountId": account_id,
        "content": "Post with #tag1 and #tag2",
        "createdAt": 1700000000,
        "fypFlags": 0,
        "attachments": [
            {"contentId": snowflake_id(), "contentType": 1, "pos": 0},
            {"contentId": snowflake_id(), "contentType": 7, "pos": 1},  # TIP — filtered
        ],
        "accountMentions": [
            {
                "id": snowflake_id(),
                "postId": pid,
                "accountId": account_id,
                "handle": "mentioned",
                "pos": 0,
            },
        ],
    }


@pytest.mark.xdist_group("test_post_model")
class TestPostModel:
    """Walks the Post model facets against ONE shared store + Account.

    Facets, in order (cascade-delete LAST, on its own post id):
    1. basic fields  — id/content/accountId, createdAt int→datetime coercion, defaults
    2. attachments   — populated list saved via _sync_associations
    3. mentions      — reverse_fk Post.mentions → PostMention.postId
    4. reply fields  — inReplyTo / inReplyToRoot
    5. expiration    — expiresAt None then set
    6. cascade-delete — DELETE by PK, PostgreSQL ON DELETE CASCADE on FKs
    """

    @pytest.mark.asyncio
    async def test_post_model_facets(self, entity_store):
        store = entity_store

        account_id = snowflake_id()
        account = Account(id=account_id, username="test_user")
        await store.save(account)

        # 1. basic fields — int createdAt coerced to datetime, defaults applied.
        # model_validate is the real coercion path (str id, int timestamp).
        basic_id = snowflake_id()
        basic = Post.model_validate(
            {
                "id": basic_id,
                "accountId": account_id,
                "content": "Test post content",
                "createdAt": 1700000000,
                "fypFlags": 0,
            }
        )
        await store.save(basic)
        saved = await store.get(Post, basic_id)
        assert saved is not None
        assert saved.content == "Test post content"
        assert saved.accountId == account_id
        assert isinstance(saved.createdAt, datetime)
        assert saved.attachments == []
        assert saved.mentions == []
        assert saved.expiresAt is None

        # 2. attachments — populated list saved via _sync_associations
        attach_id = snowflake_id()
        attach_post = Post(
            id=attach_id, accountId=account_id, content="Post with attachments"
        )
        await store.save(attach_post)
        attach_post.attachments = [
            Attachment(
                postId=attach_id,
                contentId=snowflake_id(),
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=i,
            )
            for i in range(3)
        ]
        await store.save(attach_post)
        saved = await store.get(Post, attach_id)
        assert saved is not None
        assert len(saved.attachments) == 3
        assert sorted([a.pos for a in saved.attachments]) == [0, 1, 2]

        # 3. mentions — reverse_fk Post.mentions → PostMention.postId
        mention_id = snowflake_id()
        mention_post = Post(
            id=mention_id, accountId=account_id, content="Post with mentions"
        )
        await store.save(mention_post)
        mention_post.mentions = [
            PostMention(postId=mention_id, accountId=account_id, handle="test_handle")
        ]
        await store.save(mention_post)
        saved = await store.get(Post, mention_id)
        assert saved is not None
        assert len(saved.mentions) == 1
        assert saved.mentions[0].handle == "test_handle"

        # 4. reply fields — inReplyTo / inReplyToRoot
        parent_id = snowflake_id()
        reply_id = snowflake_id()
        await store.save(
            Post(id=parent_id, accountId=account_id, content="Parent post")
        )
        await store.save(
            Post(
                id=reply_id,
                accountId=account_id,
                content="Reply post",
                inReplyTo=parent_id,
                inReplyToRoot=parent_id,
            )
        )
        saved = await store.get(Post, reply_id)
        assert saved is not None
        assert saved.inReplyTo == parent_id
        assert saved.inReplyToRoot == parent_id

        # 5. expiration — None first, then set
        expire_id = snowflake_id()
        expire_post = Post(
            id=expire_id, accountId=account_id, content="Test post", expiresAt=None
        )
        await store.save(expire_post)
        saved = await store.get(Post, expire_id)
        assert saved is not None
        assert saved.expiresAt is None
        expire_post.expiresAt = datetime.now(UTC)
        await store.save(expire_post)
        saved = await store.get(Post, expire_id)
        assert saved is not None
        assert saved.expiresAt is not None

        # 6. cascade-delete — LAST, on its own post id so earlier posts stay intact
        cascade_id = snowflake_id()
        cascade_post = Post(id=cascade_id, accountId=account_id, content="Test post")
        await store.save(cascade_post)
        cascade_post.attachments = [
            Attachment(
                postId=cascade_id,
                contentId=snowflake_id(),
                contentType=ContentType.ACCOUNT_MEDIA,
                pos=0,
            )
        ]
        cascade_post.mentions = [
            PostMention(postId=cascade_id, accountId=account_id, handle="test_handle")
        ]
        await store.save(cascade_post)
        await store.delete(cascade_post)
        assert await store.get(Post, cascade_id) is None

        pool = await store._get_pool()
        att_row = await pool.fetchrow(
            'SELECT * FROM attachments WHERE "postId" = $1', cascade_id
        )
        assert att_row is None
        mention_row = await pool.fetchrow(
            'SELECT * FROM post_mentions WHERE "postId" = $1', cascade_id
        )
        assert mention_row is None

        # Earlier posts survive the cascade-delete facet
        assert await store.get(Post, basic_id) is not None
        assert await store.get(Post, reply_id) is not None


class TestFullPostPipeline:
    @pytest.mark.asyncio
    async def test_timeline_with_posts_media_accounts(self, entity_store, mock_config):
        """Full timeline processing — accounts, posts, aggregatedPosts, accountMedia."""
        # Pre-create the account so accountMedia FK is satisfied
        acct_data = _account_dict()
        acct_id = acct_data["id"]
        await process_account_data(mock_config, data=copy.deepcopy(acct_data))

        timeline: JsonDict = {
            "accounts": [acct_data],
            "posts": [_post_dict(acct_id)],
            "aggregatedPosts": [_post_dict(acct_id)],
            "accountMedia": [_account_media_dict(acct_id)],
            "accountMediaBundles": [],
        }

        state = DownloadState()
        state.creator_id = acct_id
        await process_timeline_posts(mock_config, state, timeline)

        # Account persisted
        assert await entity_store.get(Account, acct_id) is not None

        # Posts persisted, TIP attachments filtered
        all_posts = expect_list(timeline["posts"], "posts") + expect_list(
            timeline["aggregatedPosts"], "aggregatedPosts"
        )
        for pd in all_posts:
            post = await entity_store.get(Post, expect_dict(pd, "post")["id"])
            assert post is not None
            for att in post.attachments:
                assert att.contentType.value != 7

    @pytest.mark.asyncio
    async def test_timeline_no_creator_id(self, entity_store, mock_config):
        """process_timeline_posts with no state.creator_id → branch 73→78 (skips account get)."""
        acct_id = snowflake_id()
        # Pre-create the account so FK constraints are satisfied when posts are saved
        await entity_store.save(Account(id=acct_id, username=f"no_creator_{acct_id}"))
        timeline: JsonDict = {
            "accounts": [],
            "posts": [_post_dict(acct_id)],
            "aggregatedPosts": [],
            "accountMedia": [],
            "accountMediaBundles": [],
        }
        state = DownloadState()
        state.creator_id = None  # Triggers branch 73→78
        await process_timeline_posts(mock_config, state, timeline)

    @pytest.mark.asyncio
    async def test_timeline_creator_not_in_db(self, entity_store, mock_config):
        """process_timeline_posts where creator_id is set but account missing from DB.
        Falls back to processing the 'account' key in data (line 76)."""
        acct_id = snowflake_id()
        timeline: JsonDict = {
            "accounts": [],
            "posts": [],
            "aggregatedPosts": [],
            "accountMedia": [],
            "accountMediaBundles": [],
            "account": {"id": acct_id, "username": f"from_data_{acct_id}"},
        }
        state = DownloadState()
        state.creator_id = acct_id  # Account doesn't exist yet → line 75-76
        await process_timeline_posts(mock_config, state, timeline)
        acct = await entity_store.get(Account, acct_id)
        assert acct is not None

    @pytest.mark.asyncio
    async def test_single_post_missing_account_id(self, entity_store):
        await _process_timeline_post({"id": snowflake_id(), "content": "no acct"})

    @pytest.mark.asyncio
    async def test_pinned_posts_with_existing_and_missing(
        self, entity_store, mock_config, test_account, test_post, caplog
    ):
        """Pinned-posts walk on one Account+Post graph.

        Consolidates the former standalone test_process_pinned_posts /
        test_process_pinned_posts_nonexistent / test_process_pinned_posts_update
        (same code paths, one shared graph):
        - empty list → early return
        - missing post reference → skip, surfaced via a json_output INFO record
          (asserted through caplog — pytest-loguru bridges loguru to stdlib)
        - existing post → pinned_posts junction row inserted at pos 0
        - re-pin at pos 1 → sync_junction DELETE + re-INSERT updates the row
        """
        caplog.set_level(logging.INFO)

        # Empty list → early return, nothing logged or written.
        await process_pinned_posts(mock_config, test_account, [])

        # Missing post reference → skip with a skipping_missing_post log record.
        missing_post_id = snowflake_id()
        await process_pinned_posts(
            mock_config,
            test_account,
            [{"postId": missing_post_id, "pos": 0, "createdAt": 1700000000000}],
        )
        skip_messages = [
            r.getMessage()
            for r in caplog.records
            if r.levelname == "INFO" and "skipping_missing_post" in r.getMessage()
        ]
        assert len(skip_messages) == 1
        header, _, payload_text = skip_messages[0].partition("\n")
        assert header == "[meta/post - p_p_p - skipping_missing_post]"
        assert json.loads(payload_text) == {
            "postId": missing_post_id,
            "accountId": test_account.id,
        }

        # Existing post → junction row inserted at pos 0.
        await process_pinned_posts(
            mock_config,
            test_account,
            [{"postId": test_post.id, "pos": 0, "createdAt": 1700000000000}],
        )
        pool = await entity_store._get_pool()
        row = await pool.fetchrow(
            'SELECT * FROM pinned_posts WHERE "postId" = $1 AND "accountId" = $2',
            test_post.id,
            test_account.id,
        )
        assert row is not None
        assert row["pos"] == 0

        # Re-pin with a new position — sync_junction does DELETE + re-INSERT.
        await process_pinned_posts(
            mock_config,
            test_account,
            [{"postId": test_post.id, "pos": 1, "createdAt": 1700000000000}],
        )
        row = await pool.fetchrow(
            'SELECT * FROM pinned_posts WHERE "postId" = $1 AND "accountId" = $2',
            test_post.id,
            test_account.id,
        )
        assert row is not None
        assert row["pos"] == 1
