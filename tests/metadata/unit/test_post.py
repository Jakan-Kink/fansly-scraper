"""Unit tests for post metadata functionality."""

import copy
import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from download.core import DownloadState
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


@pytest.mark.asyncio
async def test_post_model_basic(entity_store):
    """Test basic Post model functionality."""
    store = entity_store

    account_id = snowflake_id()
    post_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    post = Post(
        id=post_id,
        accountId=account_id,
        content="Test post content",
        fypFlag=0,
    )
    await store.save(post)

    saved = await store.get(Post, post_id)
    assert saved is not None
    assert saved.content == "Test post content"
    assert saved.accountId == account_id


@pytest.mark.asyncio
async def test_post_with_attachments(entity_store):
    """Test Post with attachments relationship.

    Attachments are saved via _sync_associations when the Post
    has the attachments list populated before save.
    """
    store = entity_store

    account_id = snowflake_id()
    post_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    # Create post first (FK requirement for attachments)
    post = Post(id=post_id, accountId=account_id, content="Post with attachments")
    await store.save(post)

    # Add attachments and re-save (triggers _sync_associations)
    post.attachments = [
        Attachment(
            postId=post_id,
            contentId=snowflake_id(),
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=i,
        )
        for i in range(3)
    ]
    await store.save(post)

    saved = await store.get(Post, post_id)
    assert saved is not None
    assert len(saved.attachments) == 3
    assert sorted([a.pos for a in saved.attachments]) == [0, 1, 2]


@pytest.mark.asyncio
async def test_post_mentions(entity_store):
    """Test post mentions via PostMention entities.

    Mentions are a reverse_fk relationship: Post.mentions → PostMention.postId.
    """
    store = entity_store

    account_id = snowflake_id()
    post_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    post = Post(id=post_id, accountId=account_id, content="Post with mentions")
    await store.save(post)

    # Add mention and re-save
    mention = PostMention(postId=post_id, accountId=account_id, handle="test_handle")
    post.mentions = [mention]
    await store.save(post)

    saved = await store.get(Post, post_id)
    assert saved is not None
    assert len(saved.mentions) == 1
    assert saved.mentions[0].handle == "test_handle"


@pytest.mark.asyncio
async def test_process_pinned_posts(entity_store, config):
    """Test processing pinned posts.

    process_pinned_posts uses get_store() internally, wired via entity_store.
    """
    store = entity_store

    account_id = snowflake_id()
    post_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    post = Post(id=post_id, accountId=account_id, content="Test pinned post")
    await store.save(post)

    pinned_data = [
        {
            "postId": post_id,
            "pos": 0,
            "createdAt": int(datetime.now(UTC).timestamp() * 1000),
        }
    ]

    await process_pinned_posts(config, account, pinned_data)

    # Verify via raw query on pinned_posts junction
    pool = await store._get_pool()
    row = await pool.fetchrow(
        'SELECT * FROM pinned_posts WHERE "postId" = $1 AND "accountId" = $2',
        post_id,
        account_id,
    )
    assert row is not None
    assert row["pos"] == 0


@pytest.mark.asyncio
async def test_process_pinned_posts_nonexistent(entity_store, config):
    """Test processing pinned posts with nonexistent post.

    Should log a warning and skip the post.
    """
    store = entity_store

    account_id = snowflake_id()
    nonexistent_post_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    with patch("metadata.post.json_output") as mock_json_output:
        pinned_data = [
            {
                "postId": nonexistent_post_id,  # Nonexistent post
                "pos": 0,
                "createdAt": int(datetime.now(UTC).timestamp() * 1000),
            }
        ]

        await process_pinned_posts(config, account, pinned_data)

        mock_json_output.assert_any_call(
            1,
            "meta/post - p_p_p - skipping_missing_post",
            {"postId": nonexistent_post_id, "accountId": account_id},
        )


@pytest.mark.asyncio
async def test_process_pinned_posts_update(entity_store, config):
    """Test updating existing pinned post position.

    sync_junction does DELETE + re-INSERT, so updating pos should work.
    """
    store = entity_store

    account_id = snowflake_id()
    post_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    post = Post(id=post_id, accountId=account_id, content="Test pinned post")
    await store.save(post)

    # Initial pinned post
    initial_data = [
        {
            "postId": post_id,
            "pos": 0,
            "createdAt": int(datetime.now(UTC).timestamp() * 1000),
        }
    ]
    await process_pinned_posts(config, account, initial_data)

    # Update with new position
    updated_data = [
        {
            "postId": post_id,
            "pos": 1,
            "createdAt": int(datetime.now(UTC).timestamp() * 1000),
        }
    ]
    await process_pinned_posts(config, account, updated_data)

    # Verify update
    pool = await store._get_pool()
    row = await pool.fetchrow(
        'SELECT * FROM pinned_posts WHERE "postId" = $1 AND "accountId" = $2',
        post_id,
        account_id,
    )
    assert row is not None
    assert row["pos"] == 1


@pytest.mark.asyncio
async def test_post_reply_fields(entity_store):
    """Test post reply-related fields."""
    store = entity_store

    account_id = snowflake_id()
    parent_id = snowflake_id()
    reply_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    # Create parent post
    parent = Post(id=parent_id, accountId=account_id, content="Parent post")
    await store.save(parent)

    # Create reply post
    reply = Post(
        id=reply_id,
        accountId=account_id,
        content="Reply post",
        inReplyTo=parent_id,
        inReplyToRoot=parent_id,
    )
    await store.save(reply)

    saved = await store.get(Post, reply_id)
    assert saved is not None
    assert saved.inReplyTo == parent_id
    assert saved.inReplyToRoot == parent_id


@pytest.mark.parametrize(
    "expires_at",
    [
        datetime.now(UTC),
        None,
    ],
)
@pytest.mark.asyncio
async def test_post_expiration(entity_store, expires_at):
    """Test post expiration field."""
    store = entity_store

    account_id = snowflake_id()
    post_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    post = Post(
        id=post_id, accountId=account_id, content="Test post", expiresAt=expires_at
    )
    await store.save(post)

    saved = await store.get(Post, post_id)
    assert saved is not None
    if expires_at is not None:
        assert saved.expiresAt is not None
    else:
        assert saved.expiresAt is None


class TestFullPostPipeline:
    @pytest.mark.asyncio
    async def test_timeline_with_posts_media_accounts(self, entity_store, mock_config):
        """Full timeline processing — accounts, posts, aggregatedPosts, accountMedia."""
        # Pre-create the account so accountMedia FK is satisfied
        acct_data = _account_dict()
        acct_id = acct_data["id"]
        await process_account_data(mock_config, data=copy.deepcopy(acct_data))

        timeline = {
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
        for pd in timeline["posts"] + timeline["aggregatedPosts"]:
            post = await entity_store.get(Post, pd["id"])
            assert post is not None
            for att in post.attachments:
                assert att.contentType.value != 7

    @pytest.mark.asyncio
    async def test_timeline_no_creator_id(self, entity_store, mock_config):
        """process_timeline_posts with no state.creator_id → branch 73→78 (skips account get)."""
        acct_id = snowflake_id()
        # Pre-create the account so FK constraints are satisfied when posts are saved
        await entity_store.save(Account(id=acct_id, username=f"no_creator_{acct_id}"))
        timeline = {
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
        timeline = {
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
        self, entity_store, mock_config, test_account, test_post
    ):
        """Pinned posts: empty list (early return), missing post (skip), existing post (junction sync)."""
        await process_pinned_posts(mock_config, test_account, [])
        # Missing post reference → skip with log
        await process_pinned_posts(
            mock_config,
            test_account,
            [{"postId": snowflake_id(), "pos": 0, "createdAt": 1700000000000}],
        )
        # Existing post → junction sync
        await process_pinned_posts(
            mock_config,
            test_account,
            [{"postId": test_post.id, "pos": 0, "createdAt": 1700000000000}],
        )


@pytest.mark.asyncio
async def test_post_cascade_delete(entity_store):
    """Test deleting a post also removes associated data.

    EntityStore delete is a simple DELETE by PK. Cascade is handled
    by PostgreSQL ON DELETE CASCADE on the FK constraints.
    """
    store = entity_store

    account_id = snowflake_id()
    post_id = snowflake_id()
    content_id = snowflake_id()

    account = Account(id=account_id, username="test_user")
    await store.save(account)

    post = Post(id=post_id, accountId=account_id, content="Test post")
    await store.save(post)

    # Add attachment
    post.attachments = [
        Attachment(
            postId=post_id,
            contentId=content_id,
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
    ]
    await store.save(post)

    # Add mention
    mention = PostMention(postId=post_id, accountId=account_id, handle="test_handle")
    post.mentions = [mention]
    await store.save(post)

    # Delete the post
    await store.delete(post)

    # Verify post is gone
    assert await store.get(Post, post_id) is None

    # Verify cascade removed attachments (FK ON DELETE CASCADE)
    pool = await store._get_pool()
    att_row = await pool.fetchrow(
        'SELECT * FROM attachments WHERE "postId" = $1', post_id
    )
    assert att_row is None

    mention_row = await pool.fetchrow(
        'SELECT * FROM post_mentions WHERE "postId" = $1', post_id
    )
    assert mention_row is None
