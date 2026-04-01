"""Tests for ActiveRecord-style relationship helpers and __init_subclass__.

Tests the helper functions (belongs_to, habtm, has_many, has_one,
has_one_through), string utilities (_singularize, _camel_to_snake),
and the __init_subclass__ deferred field resolution machinery.
"""

import pytest

from metadata.models import (
    _DEFERRED,
    Account,
    AccountMediaBundle,
    FanslyObject,
    Group,
    Hashtag,
    Media,
    Message,
    Post,
    RelationshipMetadata,
    Wall,
    _camel_to_snake,
    _singularize,
    belongs_to,
    habtm,
    has_many,
    has_one,
    has_one_through,
)


# ── String Utility Tests ────────────────────────────────────────────────


class TestSingularize:
    """Tests for _singularize() — naive English singularization."""

    @pytest.mark.parametrize(
        ("plural", "expected"),
        [
            ("posts", "post"),
            ("variants", "variant"),
            ("locations", "location"),
            ("hashtags", "hashtag"),
            ("walls", "wall"),
            ("mentions", "mention"),
            ("attachments", "attachment"),
            ("users", "user"),
            ("messages", "message"),
            ("stories", "story"),  # irregular
        ],
    )
    def test_singularize_fdng_fields(self, plural: str, expected: str):
        assert _singularize(plural) == expected

    def test_singularize_preserves_double_s(self):
        """Words ending in 'ss' should not be stripped."""
        assert _singularize("class") == "class"  # ends in 'ss' — preserved
        assert _singularize("boss") == "boss"  # -ss preserved


class TestCamelToSnake:
    """Tests for _camel_to_snake() — camelCase → snake_case conversion."""

    @pytest.mark.parametrize(
        ("camel", "expected"),
        [
            ("timelineStats", "timeline_stats"),
            ("mediaStoryState", "media_story_state"),
            ("accountMediaBundles", "account_media_bundles"),
            ("pinnedPosts", "pinned_posts"),
            ("accountMedia", "account_media"),
            ("lastMessage", "last_message"),
            ("createdBy", "created_by"),
            ("simple", "simple"),
        ],
    )
    def test_camel_to_snake(self, camel: str, expected: str):
        assert _camel_to_snake(camel) == expected


# ── Helper Function Tests ───────────────────────────────────────────────


class TestHelperFunctions:
    """Tests that helper functions produce correct RelationshipMetadata."""

    def test_belongs_to_produces_deferred(self):
        m = belongs_to("Media")
        assert isinstance(m, RelationshipMetadata)
        assert m.target_field is _DEFERRED
        assert m.fk_column is _DEFERRED
        assert m.query_field is _DEFERRED
        assert m.is_list is False
        assert m.query_strategy == "direct_field"

    def test_belongs_to_with_explicit_fk(self):
        m = belongs_to("Account", fk_column="accountId")
        assert m.fk_column == "accountId"
        assert m.target_field is _DEFERRED

    def test_has_one_through_produces_deferred(self):
        m = has_one_through("Media", assoc_table="account_avatar")
        assert m.target_field is _DEFERRED
        assert m.query_field is _DEFERRED
        assert m.is_list is False
        assert m.query_strategy == "assoc_table"
        assert m.assoc_table == "account_avatar"

    def test_habtm_produces_deferred(self):
        m = habtm("Post", assoc_table="post_hashtags", inverse_query_field="hashtags")
        assert m.target_field is _DEFERRED
        assert m.query_field is _DEFERRED
        assert m.is_list is True
        assert m.query_strategy == "assoc_table"
        assert m.assoc_table == "post_hashtags"
        assert m.inverse_query_field == "hashtags"

    def test_habtm_with_fk_column(self):
        m = habtm("MediaLocation", assoc_table="ml", fk_column="mediaId")
        assert m.fk_column == "mediaId"

    def test_habtm_with_ordered(self):
        m = habtm("AccountMedia", assoc_table="amb", ordered=True)
        assert m.ordered is True

    def test_has_many_produces_deferred(self):
        m = has_many("Attachment", fk_column="postId")
        assert m.target_field is _DEFERRED
        assert m.query_field is _DEFERRED
        assert m.is_list is True
        assert m.query_strategy == "reverse_fk"
        assert m.fk_column == "postId"

    def test_has_one_produces_deferred(self):
        m = has_one("TimelineStats", fk_column="accountId")
        assert m.target_field is _DEFERRED
        assert m.query_field is _DEFERRED
        assert m.is_list is False
        assert m.query_strategy == "reverse_fk"
        assert m.fk_column == "accountId"


# ── __init_subclass__ Resolution Tests ──────────────────────────────────


class TestInitSubclass:
    """Tests that __init_subclass__ resolves _DEFERRED fields correctly."""

    def test_belongs_to_resolution(self):
        """belongs_to: key → target_field = key + 'Id', fk_column = target_field."""

        class TestModel(FanslyObject):
            __table_name__ = "test"
            __relationships__ = {
                "preview": belongs_to("Media"),
            }

        meta = TestModel.__relationships__["preview"]
        assert meta.query_field == "preview"
        assert meta.target_field == "previewId"
        assert meta.fk_column == "previewId"

    def test_belongs_to_with_explicit_fk_resolution(self):
        """belongs_to with explicit fk_column: target_field still auto-derived."""

        class TestModel(FanslyObject):
            __table_name__ = "test"
            __relationships__ = {
                "account": belongs_to("Account", fk_column="accountId"),
            }

        meta = TestModel.__relationships__["account"]
        assert meta.query_field == "account"
        assert meta.target_field == "accountId"
        assert meta.fk_column == "accountId"

    def test_has_one_through_resolution(self):
        """has_one_through: key → target_field = key + '_media_id'."""

        class TestModel(FanslyObject):
            __table_name__ = "test"
            __relationships__ = {
                "avatar": has_one_through("Media", assoc_table="account_avatar"),
            }

        meta = TestModel.__relationships__["avatar"]
        assert meta.query_field == "avatar"
        assert meta.target_field == "avatar_media_id"

    def test_habtm_resolution(self):
        """habtm: key → target_field = singularize(key) + '_ids'."""

        class TestModel(FanslyObject):
            __table_name__ = "test"
            __relationships__ = {
                "tags": habtm("Tag", assoc_table="test_tags"),
                "stories": habtm("Story", assoc_table="test_stories"),
            }

        assert TestModel.__relationships__["tags"].target_field == "tag_ids"
        assert TestModel.__relationships__["stories"].target_field == "story_ids"

    def test_has_many_resolution(self):
        """has_many: key → target_field = singularize(key) + '_ids'."""

        class TestModel(FanslyObject):
            __table_name__ = "test"
            __relationships__ = {
                "items": has_many("Item", fk_column="testId"),
            }

        meta = TestModel.__relationships__["items"]
        assert meta.query_field == "items"
        assert meta.target_field == "item_ids"
        assert meta.fk_column == "testId"

    def test_has_one_resolution(self):
        """has_one: key → target_field = camel_to_snake(key) + '_id'."""

        class TestModel(FanslyObject):
            __table_name__ = "test"
            __relationships__ = {
                "timelineStats": has_one("TimelineStats", fk_column="accountId"),
            }

        meta = TestModel.__relationships__["timelineStats"]
        assert meta.query_field == "timelineStats"
        assert meta.target_field == "timeline_stats_id"
        assert meta.fk_column == "accountId"

    def test_explicit_target_field_not_overridden(self):
        """Explicit target_field should not be overridden by auto-derivation."""

        class TestModel(FanslyObject):
            __table_name__ = "test"
            __relationships__ = {
                "accountMedia": has_many(
                    "AccountMedia",
                    fk_column="accountId",
                    target_field="account_media_ids",
                ),
            }

        assert (
            TestModel.__relationships__["accountMedia"].target_field
            == "account_media_ids"
        )


# ── __fk_to_rel__ Reverse Map Tests ────────────────────────────────────


class TestFkToRel:
    """Tests that __fk_to_rel__ is correctly built by __init_subclass__."""

    def test_fk_to_rel_built_for_belongs_to(self):
        class TestModel(FanslyObject):
            __table_name__ = "test"
            __relationships__ = {
                "preview": belongs_to("Media"),
                "account": belongs_to("Account", fk_column="accountId"),
            }

        assert "previewId" in TestModel.__fk_to_rel__
        assert TestModel.__fk_to_rel__["previewId"][0] == "preview"
        assert "accountId" in TestModel.__fk_to_rel__
        assert TestModel.__fk_to_rel__["accountId"][0] == "account"

    def test_fk_to_rel_excludes_list_relationships(self):
        class TestModel(FanslyObject):
            __table_name__ = "test"
            __relationships__ = {
                "items": has_many("Item", fk_column="testId"),
            }

        # has_many is is_list=True, so testId should NOT be in __fk_to_rel__
        assert "testId" not in TestModel.__fk_to_rel__

    def test_fk_to_rel_includes_has_one(self):
        class TestModel(FanslyObject):
            __table_name__ = "test"
            __relationships__ = {
                "stats": has_one("Stats", fk_column="testId"),
            }

        # has_one is is_list=False with fk_column, so testId IS in __fk_to_rel__
        assert "testId" in TestModel.__fk_to_rel__


# ── Snapshot Assertion: Rewritten Relationships Match Originals ─────────


class TestRewrittenRelationshipsMatch:
    """Verify all rewritten __relationships__ resolve to the expected values.

    This is the safety net for Phase 2 — if any auto-derivation produces
    the wrong target_field or query_field, this test catches it.
    """

    EXPECTED = {
        (Hashtag, "posts"): {
            "target_field": "post_ids",
            "is_list": True,
            "query_field": "posts",
            "inverse_type": "Post",
            "inverse_query_field": "hashtags",
            "query_strategy": "assoc_table",
            "assoc_table": "post_hashtags",
        },
        (Media, "variants"): {
            "target_field": "variant_ids",
            "is_list": True,
            "query_field": "variants",
            "inverse_type": "Media",
            "query_strategy": "assoc_table",
            "assoc_table": "media_variants",
        },
        (Media, "locations"): {
            "target_field": "location_ids",
            "is_list": True,
            "query_field": "locations",
            "inverse_type": "MediaLocation",
            "query_strategy": "assoc_table",
            "assoc_table": "media_locations",
            "fk_column": "mediaId",
        },
        (AccountMediaBundle, "preview"): {
            "target_field": "previewId",
            "is_list": False,
            "query_field": "preview",
            "inverse_type": "Media",
            "query_strategy": "direct_field",
            "fk_column": "previewId",
        },
        (AccountMediaBundle, "accountMedia"): {
            "target_field": "account_media_ids",
            "is_list": True,
            "query_field": "accountMedia",
            "inverse_type": "AccountMedia",
            "query_strategy": "assoc_table",
            "assoc_table": "account_media_bundle_media",
            "ordered": True,
        },
        (Wall, "posts"): {
            "target_field": "post_ids",
            "is_list": True,
            "query_field": "posts",
            "inverse_type": "Post",
            "inverse_query_field": "walls",
            "query_strategy": "assoc_table",
            "assoc_table": "wall_posts",
        },
        (Post, "attachments"): {
            "target_field": "attachment_ids",
            "is_list": True,
            "query_field": "attachments",
            "inverse_type": "Attachment",
            "query_strategy": "reverse_fk",
            "fk_column": "postId",
        },
        (Post, "hashtags"): {
            "target_field": "hashtag_ids",
            "is_list": True,
            "query_field": "hashtags",
            "inverse_type": "Hashtag",
            "inverse_query_field": "posts",
            "query_strategy": "assoc_table",
            "assoc_table": "post_hashtags",
        },
        (Post, "walls"): {
            "target_field": "wall_ids",
            "is_list": True,
            "query_field": "walls",
            "inverse_type": "Wall",
            "inverse_query_field": "posts",
            "query_strategy": "assoc_table",
            "assoc_table": "wall_posts",
        },
        (Post, "mentions"): {
            "target_field": "mention_ids",
            "is_list": True,
            "query_field": "mentions",
            "inverse_type": "PostMention",
            "query_strategy": "reverse_fk",
            "fk_column": "postId",
        },
        (Message, "attachments"): {
            "target_field": "attachment_ids",
            "is_list": True,
            "query_field": "attachments",
            "inverse_type": "Attachment",
            "query_strategy": "reverse_fk",
            "fk_column": "messageId",
        },
        (Group, "users"): {
            "target_field": "user_ids",
            "is_list": True,
            "query_field": "users",
            "inverse_type": "Account",
            "query_strategy": "assoc_table",
            "assoc_table": "group_users",
        },
        (Group, "messages"): {
            "target_field": "message_ids",
            "is_list": True,
            "query_field": "messages",
            "inverse_type": "Message",
            "query_strategy": "reverse_fk",
            "fk_column": "groupId",
        },
        (Account, "avatar"): {
            "target_field": "avatar_media_id",
            "is_list": False,
            "query_field": "avatar",
            "inverse_type": "Media",
            "query_strategy": "assoc_table",
            "assoc_table": "account_avatar",
        },
        (Account, "banner"): {
            "target_field": "banner_media_id",
            "is_list": False,
            "query_field": "banner",
            "inverse_type": "Media",
            "query_strategy": "assoc_table",
            "assoc_table": "account_banner",
        },
        (Account, "pinnedPosts"): {
            "target_field": "pinned_post_ids",
            "is_list": True,
            "query_field": "pinnedPosts",
            "inverse_type": "PinnedPost",
            "query_strategy": "assoc_table",
            "assoc_table": "pinned_posts",
        },
        (Account, "walls"): {
            "target_field": "wall_ids",
            "is_list": True,
            "query_field": "walls",
            "inverse_type": "Wall",
            "query_strategy": "reverse_fk",
            "fk_column": "accountId",
        },
        (Account, "accountMedia"): {
            "target_field": "account_media_ids",
            "is_list": True,
            "query_field": "accountMedia",
            "inverse_type": "AccountMedia",
            "query_strategy": "reverse_fk",
            "fk_column": "accountId",
        },
        (Account, "accountMediaBundles"): {
            "target_field": "bundle_ids",
            "is_list": True,
            "query_field": "accountMediaBundles",
            "inverse_type": "AccountMediaBundle",
            "query_strategy": "reverse_fk",
            "fk_column": "accountId",
        },
        (Account, "stories"): {
            "target_field": "story_ids",
            "is_list": True,
            "query_field": "stories",
            "inverse_type": "Story",
            "query_strategy": "reverse_fk",
            "fk_column": "authorId",
        },
        (Account, "timelineStats"): {
            "target_field": "timeline_stats_id",
            "is_list": False,
            "query_field": "timelineStats",
            "inverse_type": "TimelineStats",
            "query_strategy": "reverse_fk",
            "fk_column": "accountId",
        },
        (Account, "mediaStoryState"): {
            "target_field": "media_story_state_id",
            "is_list": False,
            "query_field": "mediaStoryState",
            "inverse_type": "MediaStoryState",
            "query_strategy": "reverse_fk",
            "fk_column": "accountId",
        },
    }

    @pytest.mark.parametrize(
        "cls_and_field",
        list(EXPECTED.keys()),
        ids=[f"{c.__name__}.{f}" for c, f in EXPECTED],
    )
    def test_relationship_matches_expected(self, cls_and_field):
        cls, field_name = cls_and_field
        meta = cls.__relationships__[field_name]
        expected = self.EXPECTED[cls_and_field]

        for attr, exp_val in expected.items():
            actual = getattr(meta, attr)
            assert actual == exp_val, (
                f"{cls.__name__}.{field_name}.{attr}: "
                f"expected {exp_val!r}, got {actual!r}"
            )
