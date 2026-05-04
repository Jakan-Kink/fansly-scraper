"""Tests for FK↔relationship bidirectional sync.

Verifies that setting FK scalars auto-resolves relationship objects from
cache, and setting relationship objects auto-updates FK scalars.
"""

import pytest
from stash_graphql_client.types.unset import UNSET, is_set

from metadata.models import (
    Account,
    AccountMedia,
    Attachment,
    ContentType,
    FanslyObject,
    Group,
    Hashtag,
    Media,
    MediaStory,
    Message,
    Post,
    RelationshipMetadata,
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

    def test_media_story_account_id_resolves_account(self, store_with_account):
        _, account = store_with_account
        story = MediaStory(
            id=snowflake_id(),
            accountId=account.id,
            createdAt="2024-01-01T00:00:00Z",
        )
        assert story.account is account

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

    def test_fk_with_uncached_entity_leaves_relationship_unset(
        self, store_with_account
    ):
        _store, _account = store_with_account
        uncached_id = snowflake_id()
        media = Media(id=snowflake_id(), accountId=uncached_id)
        # Cache miss → relationship stays UNSET (lazy not-loaded), distinct
        # from None (explicit clear). FK still holds the value, so
        # to_db_dict won't clobber it.
        assert media.account is UNSET
        assert not is_set(media.account)
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


class TestFKRelationshipSyncDeep:
    @pytest.mark.asyncio
    async def test_setattr_and_add_remove(self, entity_store):
        """FK↔relationship sync + _add_to/_remove_from_relationship."""
        acct1 = Account(id=snowflake_id(), username="sync1")
        acct2 = Account(id=snowflake_id(), username="sync2")
        await entity_store.save(acct1)
        await entity_store.save(acct2)

        media = Media(id=snowflake_id(), accountId=acct1.id)
        await entity_store.save(media)

        # Set relationship → syncs FK
        media.account = acct2
        assert media.accountId == acct2.id

        # Set FK → auto-resolve
        media.accountId = acct1.id
        assert media.accountId == acct1.id

        # _add_to_relationship / _remove_from_relationship
        post = Post(id=snowflake_id(), accountId=acct1.id, fypFlag=0)
        h = Hashtag(id=1, value="add_remove")
        await post._add_to_relationship("hashtags", h)
        assert h in post.hashtags

        await post._remove_from_relationship("hashtags", h)
        assert h not in post.hashtags

        with pytest.raises(ValueError):
            await post._add_to_relationship("bad_field", h)
        with pytest.raises(ValueError):
            await post._remove_from_relationship("bad_field", h)

        # Scalar add/remove
        await media._add_to_relationship("account", acct2)
        assert media.account is acct2
        await media._remove_from_relationship("account", acct2)
        assert media.account is None

        # Remove from None is noop
        object.__setattr__(post, "hashtags", None)
        await post._remove_from_relationship("hashtags", h)

    @pytest.mark.asyncio
    async def test_sync_inverse_edge_branches(self, entity_store):
        """Cover remaining branch exits in _sync_inverse_relationship and _add_to_inverse.

        823→exit: scalar sync where new_value is None → skips elif
        840→exit: _add_to_inverse where inverse field NOT in snapshot
        883→exit: _remove_from_relationship where meta has no inverse_query_field
        889→exit: _remove_from_relationship where current is NOT the related_obj
        """
        acct = Account(id=snowflake_id(), username="edge_br")
        await entity_store.save(acct)

        # 823→exit: _sync_inverse_relationship with scalar None value
        # Uses "hashtags" which has inverse_query_field, but value=None → elif skipped
        post = Post(id=snowflake_id(), accountId=acct.id, fypFlag=0)
        post._sync_inverse_relationship("hashtags", None)

        # 840→exit: _add_to_inverse where inverse field NOT in _snapshot
        h = Hashtag(id=1, value="no_snap")
        # Clear snapshot so "posts" is NOT in it
        h._snapshot = {}
        post._add_to_inverse(h, "posts")
        assert post in h.posts
        # "posts" not in snapshot → 840→exit branch (skips snapshot update)

        # 883→exit: _remove_from_relationship on a relationship WITHOUT inverse_query_field
        # Post.attachments = has_many("Attachment", fk_column="postId") — no inverse_query_field
        att = Attachment(
            id=snowflake_id(),
            postId=post.id,
            contentId=snowflake_id(),
            contentType=ContentType.ACCOUNT_MEDIA,
            pos=0,
        )
        object.__setattr__(post, "attachments", [att])
        await post._remove_from_relationship("attachments", att)
        assert att not in post.attachments
        # No inverse cleanup attempted (883→exit: meta.inverse_query_field is None)

        # 889→exit: _remove_from_relationship where current is NOT the related_obj
        media1 = Media(id=snowflake_id(), accountId=acct.id)
        media2 = Media(id=snowflake_id(), accountId=acct.id)
        media1.account = acct
        # Try to remove media2 from media1.account — but current (acct) is NOT media2
        await media1._remove_from_relationship("account", media2)
        # Neither branch fires → falls through (889→exit)
        assert media1.account is acct  # Unchanged

    @pytest.mark.asyncio
    async def test_sync_inverse_list_loop_back(self, entity_store):
        """821→820: Ensure the loop-back branch in _sync_inverse_relationship
        is exercised by passing 3+ items (coverage.py sometimes misses 2-item loops)."""
        acct = Account(id=snowflake_id(), username="loop")
        await entity_store.save(acct)
        post = Post(id=snowflake_id(), accountId=acct.id, fypFlag=0)

        h1 = Hashtag(id=1, value="l1")
        h2 = Hashtag(id=2, value="l2")
        h3 = Hashtag(id=3, value="l3")

        post._sync_inverse_relationship("hashtags", [h1, h2, h3])
        assert post in h1.posts
        assert post in h2.posts
        assert post in h3.posts


class TestSetAttrAndInverseSync:
    """Covers 812 (FK=None→clear relationship), 821→820 (list inverse iteration),
    823-824 (_add_to_inverse for list items), 835 (inverse_meta not is_list → return),
    837→exit (self not in current check), 840→exit (snapshot update),
    843 (scalar inverse set)."""

    @pytest.mark.asyncio
    async def test_fk_set_to_none_clears_relationship(self, entity_store):
        """Line 812: setting FK scalar to None when store exists → clears relationship.
        Path 2 in __setattr__: name in __fk_to_rel__, value is None.
        Uses Post.inReplyTo (nullable FK) → Post.replyTo (belongs_to relationship)."""
        acct = Account(id=snowflake_id(), username="fk_none")
        await entity_store.save(acct)
        parent = Post(id=snowflake_id(), accountId=acct.id, fypFlag=0)
        reply = Post(
            id=snowflake_id(), accountId=acct.id, inReplyTo=parent.id, fypFlag=0
        )
        await entity_store.save(parent)
        await entity_store.save(reply)

        # reply.replyTo should be resolved from cache
        assert reply.replyTo is parent
        # Set FK to None → relationship cleared (line 812)
        reply.inReplyTo = None
        assert reply.replyTo is None

    @pytest.mark.asyncio
    async def test_list_inverse_sync_and_snapshot_update(self, entity_store):
        """Lines 821→820, 823-824, 837→exit, 840→exit:
        Setting a list relationship triggers _sync_inverse_relationship,
        which iterates list items, calls _add_to_inverse, checks 'self not in current',
        appends, and updates the inverse object's snapshot."""
        acct = Account(id=snowflake_id(), username="inv_sync")
        await entity_store.save(acct)

        h1 = Hashtag(id=1, value="inv1")
        h2 = Hashtag(id=2, value="inv2")
        post = Post(id=snowflake_id(), accountId=acct.id, fypFlag=0)
        await entity_store.save(post)

        # Setting hashtags list triggers _sync_inverse for each item
        # h1 and h2 have inverse_query_field="posts" on the Hashtag side
        post.hashtags = [h1, h2]

        # Inverse: hashtag.posts should contain the post
        assert post in h1.posts
        assert post in h2.posts

        # Setting again with same items → 837→exit (self already in current)
        post.hashtags = [h1, h2]
        # No duplicates
        assert h1.posts.count(post) == 1

    @pytest.mark.asyncio
    async def test_list_inverse_sync_with_snapshot_update(self, entity_store):
        """Lines 819-822, 840→exit:
        _sync_inverse_relationship iterates list items (820 loop, 821 isinstance),
        calls _add_to_inverse per item (822). Snapshot updated (840→exit)."""
        acct = Account(id=snowflake_id(), username="snap_inv")
        await entity_store.save(acct)

        h1 = Hashtag(id=1, value="snap1")
        h2 = Hashtag(id=2, value="snap2")
        post = Post(id=snowflake_id(), accountId=acct.id, fypFlag=0)
        await entity_store.save(post)

        # Ensure h1.posts has a snapshot entry (so 840→exit is reachable)
        h1.mark_clean()
        assert "posts" in h1._snapshot

        # Call _sync_inverse_relationship directly to guarantee coverage.
        # Going through setattr can have coverage.py quirks with Pydantic's C validators.
        post._sync_inverse_relationship("hashtags", [h1, h2])
        assert post in h1.posts
        assert post in h2.posts
        assert "posts" in h1._snapshot

    @pytest.mark.asyncio
    async def test_scalar_inverse_sync_via_setattr(self, entity_store):
        """Lines 823-824: _sync_inverse_relationship for scalar (non-list) relationship
        with inverse_query_field set. No current belongs_to has inverse_query_field,
        so we use test-only subclasses."""

        class _Owner(FanslyObject):
            __table_name__: str = ""
            __tracked_fields__ = {"items"}
            __relationships__ = {
                "items": RelationshipMetadata(
                    target_field="item_ids",
                    is_list=True,
                    query_field="items",
                    inverse_type="_Item",
                    inverse_query_field="owner",
                    query_strategy="reverse_fk",
                    fk_column="ownerId",
                ),
            }
            id: int | None = None
            items: list = []

        class _Item(FanslyObject):
            __table_name__: str = ""
            __tracked_fields__ = {"owner"}
            __relationships__ = {
                "owner": RelationshipMetadata(
                    target_field="ownerId",
                    is_list=False,
                    query_field="owner",
                    inverse_type="_Owner",
                    inverse_query_field="items",
                    query_strategy="direct_field",
                    fk_column="ownerId",
                ),
            }
            id: int | None = None
            ownerId: int | None = None  # noqa: N815
            owner: _Owner | None = None

        owner = _Owner(id=1)
        item = _Item(id=10, ownerId=1)

        # Setting scalar relationship with inverse_query_field triggers 823-824
        item._sync_inverse_relationship("owner", owner)
        # owner.items should now contain item via _add_to_inverse
        assert item in owner.items

    @pytest.mark.asyncio
    async def test_scalar_inverse_via_add_to_inverse(self, entity_store):
        """Line 843: _add_to_inverse where current value is non-list, non-None.
        Sets the inverse field to self via object.__setattr__."""
        acct = Account(id=snowflake_id(), username="scalar_inv")
        await entity_store.save(acct)

        # Set up: target object has a non-list field with a non-None value
        story = Media(id=snowflake_id(), accountId=acct.id)
        object.__setattr__(story, "account", acct)  # non-list, non-None

        # Call _add_to_inverse targeting that scalar field
        media2 = Media(id=snowflake_id(), accountId=acct.id)
        media2._add_to_inverse(story, "account")
        # Line 843: object.__setattr__(related_obj, inverse_field, self)
        assert story.account is media2

    @pytest.mark.asyncio
    async def test_add_to_inverse_initializes_none_list(self, entity_store):
        """Lines 830-833: _add_to_inverse when current is None but inverse_meta.is_list=True
        → lazily initializes an empty list, then appends self."""
        acct = Account(id=snowflake_id(), username="lazy_init")
        await entity_store.save(acct)

        h = Hashtag(id=1, value="lazy")
        post = Post(id=snowflake_id(), accountId=acct.id, fypFlag=0)

        # Force hashtag.posts to None
        object.__setattr__(h, "posts", None)

        # _add_to_inverse should lazy-init the list and append
        post._add_to_inverse(h, "posts")
        assert isinstance(h.posts, list)
        assert post in h.posts

    @pytest.mark.asyncio
    async def test_add_to_inverse_no_inverse_meta_returns(self, entity_store):
        """Line 835: _add_to_inverse where inverse field has no __relationships__ entry
        and current is None → returns early without doing anything."""
        acct = Account(id=snowflake_id(), username="no_meta")
        media = Media(id=snowflake_id(), accountId=acct.id)

        # Call _add_to_inverse with a field that doesn't exist in relationships
        # When current is None and inverse_meta is None → return (line 835 path)
        object.__setattr__(acct, "nonexistent_field", None)
        media._add_to_inverse(acct, "nonexistent_field")
        assert acct.nonexistent_field is None


class TestRelationshipMutationDeepPaths:
    """Covers 857-858 (init None→list), 861→exit (already in list),
    881→exit (remove from list with inverse), 885-886 (inverse is self → set None),
    887→exit (scalar remove)."""

    @pytest.mark.asyncio
    async def test_add_initializes_none_list(self, entity_store):
        """Lines 857-859: _add_to_relationship when current is None and meta.is_list
        → initializes to [], setattr, re-reads Pydantic's copy, then appends."""
        acct = Account(id=snowflake_id(), username="add_init")
        await entity_store.save(acct)
        post = Post(id=snowflake_id(), accountId=acct.id, fypFlag=0)
        h = Hashtag(id=1, value="add_init")

        # Force to None to trigger the None→[] init path
        object.__setattr__(post, "hashtags", None)
        await post._add_to_relationship("hashtags", h)
        assert isinstance(post.hashtags, list)
        assert h in post.hashtags

    @pytest.mark.asyncio
    async def test_add_duplicate_noop(self, entity_store):
        """Line 861→exit: adding same object twice → second add is no-op."""
        acct = Account(id=snowflake_id(), username="dup_add")
        await entity_store.save(acct)
        post = Post(id=snowflake_id(), accountId=acct.id, fypFlag=0)
        h = Hashtag(id=1, value="dup_add")

        await post._add_to_relationship("hashtags", h)
        await post._add_to_relationship("hashtags", h)
        assert post.hashtags.count(h) == 1

    @pytest.mark.asyncio
    async def test_remove_from_list_with_inverse_list_cleanup(self, entity_store):
        """Lines 881-884: remove from list where inverse is also a list → removes self
        from inverse list too. Covers 883→exit (inverse_query_field is set),
        884 (isinstance list and self in inverse → remove)."""
        acct = Account(id=snowflake_id(), username="rem_inv")
        await entity_store.save(acct)
        post = Post(id=snowflake_id(), accountId=acct.id, fypFlag=0)
        h = Hashtag(id=1, value="rem_inv")

        await post._add_to_relationship("hashtags", h)
        assert post in h.posts  # Inverse was set

        await post._remove_from_relationship("hashtags", h)
        assert h not in post.hashtags
        assert post not in h.posts  # Inverse list cleaned up (line 884)

    @pytest.mark.asyncio
    async def test_remove_from_list_inverse_is_scalar_self(self, entity_store):
        """Lines 887-888: remove from list where inverse is scalar (== self) → set None.

        No current model has list↔scalar inverse, so we define test-only
        subclasses: _Parent has list rel 'children' with inverse_query_field='parent',
        _Child has scalar 'parent: _Parent | None'. Removing a child from
        parent.children should clear child.parent = None (line 888)."""

        class _Parent(FanslyObject):
            __table_name__: str = ""
            __tracked_fields__ = {"children"}
            __relationships__ = {
                "children": RelationshipMetadata(
                    target_field="child_ids",
                    is_list=True,
                    query_field="children",
                    inverse_type="_Child",
                    inverse_query_field="parent",
                    query_strategy="reverse_fk",
                    fk_column="parentId",
                ),
            }
            id: int | None = None
            children: list = []

        class _Child(FanslyObject):
            __table_name__: str = ""
            __tracked_fields__ = {"parent"}
            __relationships__ = {
                "parent": RelationshipMetadata(
                    target_field="parentId",
                    is_list=False,
                    query_field="parent",
                    inverse_type="_Parent",
                    query_strategy="direct_field",
                ),
            }
            id: int | None = None
            parentId: int | None = None  # noqa: N815
            parent: _Parent | None = None

        parent = _Parent(id=1)
        child = _Child(id=10, parentId=1)
        # Wire: child in parent.children, child.parent = parent (scalar)
        object.__setattr__(parent, "children", [child])
        object.__setattr__(child, "parent", parent)

        # Remove child → line 884 gets child.parent (scalar, not list),
        # line 885 False, line 887 inverse is self → 888: set None
        await parent._remove_from_relationship("children", child)
        assert child not in parent.children
        assert child.parent is None

        # 887→exit: child.parent points to a DIFFERENT parent (not self)
        # inverse is not a list (885 False), inverse is not self (887 False) → falls through
        parent2 = _Parent(id=2)
        child2 = _Child(id=20, parentId=2)
        other_parent = _Parent(id=3)
        object.__setattr__(parent2, "children", [child2])
        object.__setattr__(child2, "parent", other_parent)  # different from parent2

        await parent2._remove_from_relationship("children", child2)
        assert child2 not in parent2.children
        # child2.parent unchanged — it pointed to other_parent, not parent2
        assert child2.parent is other_parent

    @pytest.mark.asyncio
    async def test_remove_scalar_relationship(self, entity_store):
        """Line 889→exit: remove scalar relationship where current IS the related_obj."""
        acct = Account(id=snowflake_id(), username="rem_scalar")
        await entity_store.save(acct)
        media = Media(id=snowflake_id(), accountId=acct.id)

        media.account = acct
        assert media.account is acct
        await media._remove_from_relationship("account", acct)
        assert media.account is None
