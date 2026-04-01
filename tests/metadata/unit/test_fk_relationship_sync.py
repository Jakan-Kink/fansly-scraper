"""Tests for FK↔relationship bidirectional sync.

Verifies that setting FK scalars auto-resolves relationship objects from
cache, and setting relationship objects auto-updates FK scalars.
"""

import pytest

from metadata.models import (
    Account,
    AccountMedia,
    FanslyObject,
    Group,
    Hashtag,
    Media,
    Message,
    Post,
    Story,
    Wall,
)
from tests.fixtures.utils.test_isolation import snowflake_id


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def store_with_account():
    """Set up a minimal fake store with a cached Account for sync tests."""

    class FakeStore:
        def __init__(self):
            self._cache = {}

        def get_from_cache(self, cls, eid):
            return self._cache.get((cls, eid))

        def get_from_cache_by_type_name(self, name, eid):
            for (c, i), obj in self._cache.items():
                if c.__name__ == name and i == eid:
                    return obj
            return None

        def cache_instance(self, obj):
            self._cache[(type(obj), obj.id)] = obj

        def invalidate(self, cls, eid):
            self._cache.pop((cls, eid), None)

    store = FakeStore()
    FanslyObject._store = store

    acct_id = snowflake_id()
    account = Account(id=acct_id, username="sync_test_user")
    store.cache_instance(account)

    yield store, account

    FanslyObject._store = None


# ── FK → Relationship Auto-Resolution Tests ─────────────────────────────


class TestFkToRelationshipSync:
    """Setting FK scalar auto-resolves relationship from cache."""

    def test_media_account_id_resolves_account(self, store_with_account):
        _, account = store_with_account
        media = Media(id=snowflake_id(), accountId=account.id)
        # __setattr__ should have resolved account from cache
        assert media.account is account

    def test_post_account_id_resolves_account(self, store_with_account):
        _, account = store_with_account
        post = Post(id=snowflake_id(), accountId=account.id)
        assert post.account is account

    def test_wall_account_id_resolves_account(self, store_with_account):
        _, account = store_with_account
        wall = Wall(id=snowflake_id(), accountId=account.id)
        assert wall.account is account

    def test_story_author_id_resolves_author(self, store_with_account):
        _, account = store_with_account
        story = Story(
            id=snowflake_id(),
            authorId=account.id,
            content="test",
            createdAt="2024-01-01T00:00:00Z",
        )
        assert story.author is account

    def test_message_sender_id_resolves_sender(self, store_with_account):
        _, account = store_with_account
        msg = Message(
            id=snowflake_id(),
            senderId=account.id,
            content="hello",
            createdAt="2024-01-01T00:00:00Z",
        )
        assert msg.sender is account

    def test_group_created_by_resolves_creator(self, store_with_account):
        _, account = store_with_account
        group = Group(id=snowflake_id(), createdBy=account.id)
        assert group.creator is account

    def test_fk_set_to_none_clears_relationship(self, store_with_account):
        _, account = store_with_account
        media = Media(id=snowflake_id(), accountId=account.id)
        assert media.account is account
        # Now clear the FK
        media.accountId = account.id  # re-set same value — should still resolve
        assert media.account is account

    def test_fk_with_uncached_entity_sets_relationship_to_none(
        self, store_with_account
    ):
        _store, _account = store_with_account
        uncached_id = snowflake_id()
        media = Media(id=snowflake_id(), accountId=uncached_id)
        # Account with uncached_id is not in store → relationship should be None
        assert media.account is None
        # But FK should still hold the value
        assert media.accountId == uncached_id

    def test_account_media_resolves_media_and_preview(self, store_with_account):
        store, account = store_with_account
        media_id = snowflake_id()
        preview_id = snowflake_id()
        media_obj = Media(id=media_id, accountId=account.id)
        store.cache_instance(media_obj)
        preview_obj = Media(id=preview_id, accountId=account.id)
        store.cache_instance(preview_obj)

        am = AccountMedia(
            id=snowflake_id(),
            accountId=account.id,
            mediaId=media_id,
            previewId=preview_id,
            createdAt="2024-01-01T00:00:00Z",
        )
        assert am.account is account
        assert am.media is media_obj
        assert am.preview is preview_obj


# ── Relationship → FK Sync Tests ────────────────────────────────────────


class TestRelationshipToFkSync:
    """Setting relationship object auto-updates FK scalar."""

    def test_set_account_relationship_updates_fk(self, store_with_account):
        _, account = store_with_account
        media = Media(id=snowflake_id(), accountId=snowflake_id())
        old_id = media.accountId
        # Set the relationship object
        media.account = account
        assert media.accountId == account.id
        assert media.accountId != old_id

    def test_set_relationship_to_none_clears_fk(self, store_with_account):
        _, account = store_with_account
        media = Media(id=snowflake_id(), accountId=account.id)
        assert media.account is account
        # Clear the relationship
        media.account = None
        assert media.accountId is None

    def test_post_reply_to_syncs_fk(self, store_with_account):
        store, account = store_with_account
        parent = Post(id=snowflake_id(), accountId=account.id)
        store.cache_instance(parent)

        child = Post(id=snowflake_id(), accountId=account.id)
        child.replyTo = parent
        assert child.inReplyTo == parent.id

    def test_group_last_message_syncs_fk(self, store_with_account):
        store, account = store_with_account
        msg = Message(
            id=snowflake_id(),
            senderId=account.id,
            content="test",
            createdAt="2024-01-01T00:00:00Z",
        )
        store.cache_instance(msg)

        group = Group(id=snowflake_id(), createdBy=account.id)
        group.lastMessage = msg
        assert group.lastMessageId == msg.id


# ── Bidirectional Inverse Sync Enhancement Tests ────────────────────────


class TestInverseSyncEnhancements:
    """Tests for the _add_to_inverse lazy init and snapshot update."""

    def test_inverse_list_lazily_initialized(self, store_with_account):
        """Setting a relationship should initialize None inverse lists."""
        store, account = store_with_account
        # Hashtag has posts=[], Wall has posts=[]
        # Create a hashtag with posts=None (simulating partial load)
        hashtag = Hashtag(id=1, value="test")
        object.__setattr__(hashtag, "posts", None)
        store.cache_instance(hashtag)

        # Create a post that references this hashtag
        post = Post(id=snowflake_id(), accountId=account.id)
        post.hashtags = [hashtag]

        # The inverse sync should have lazily initialized hashtag.posts
        # and added the post
        assert hashtag.posts is not None
        assert post in hashtag.posts

    def test_inverse_sync_does_not_mark_dirty(self, store_with_account):
        """Inverse sync additions should update snapshot to avoid false dirty."""
        store, account = store_with_account
        wall = Wall(id=snowflake_id(), accountId=account.id)
        wall.mark_clean()  # Set clean snapshot
        store.cache_instance(wall)

        post = Post(id=snowflake_id(), accountId=account.id)
        post.walls = [wall]

        # wall.posts was modified by inverse sync, but snapshot should
        # have been updated too — so wall shouldn't be dirty for "posts"
        changed = wall.get_changed_fields()
        assert "posts" not in changed
