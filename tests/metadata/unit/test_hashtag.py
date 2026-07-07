"""Tests for metadata/hashtag.py — extract_hashtags and process_post_hashtags."""

import pytest

from metadata.entity_store import PostgresEntityStore
from metadata.hashtag import extract_hashtags, process_post_hashtags
from metadata.models import Account, Hashtag, Post
from tests.fixtures.utils.test_isolation import snowflake_id


async def _make_post(store: PostgresEntityStore) -> Post:
    """Create + persist a fresh Account and Post on the given store.

    Mirrors the ``test_post`` fixture but binds to the class-scoped store so the
    whole class can share ONE database. Unique snowflake ids keep each method's
    rows from colliding in the shared DB.
    """
    account = Account(id=snowflake_id(), username=f"ht_{snowflake_id()}")
    await store.save(account)
    assert isinstance(account.id, int)
    post = Post(id=snowflake_id(), accountId=account.id, content="", fypFlags=0)
    await store.save(post)
    return post


class TestExtractHashtags:
    """Pure function tests — no DB needed.

    Parametrized to keep failure granularity per-case while collapsing 14
    near-identical test bodies into one definition.
    """

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            # Empty / null inputs
            pytest.param("", [], id="empty_content"),
            pytest.param(None, [], id="none_content"),
            pytest.param("Just a regular post with no tags", [], id="no_hashtags"),
            # Basic extraction
            pytest.param("Hello #world", ["world"], id="single_hashtag"),
            pytest.param(
                "Check out #foo and #bar today",
                ["foo", "bar"],
                id="multiple_hashtags",
            ),
            # Normalization behaviors
            pytest.param("#FOO #Bar #baz", ["foo", "bar", "baz"], id="lowercases"),
            pytest.param("#foo #FOO #Foo", ["foo"], id="deduplicates"),
            pytest.param(
                "#beta #alpha #gamma",
                ["beta", "alpha", "gamma"],
                id="preserves_order",
            ),
            # Edge syntactic shapes
            pytest.param("##doublehash", ["doublehash"], id="double_hash"),
            pytest.param("#test123", ["test123"], id="hashtag_with_numbers"),
            pytest.param("#my_tag", ["my_tag"], id="hashtag_with_underscores"),
            pytest.param("#first word", ["first"], id="hashtag_at_start"),
            pytest.param("word #last", ["last"], id="hashtag_at_end"),
            pytest.param("#one#two", ["one", "two"], id="adjacent_hashtags"),
        ],
    )
    def test_extract(self, content, expected):
        assert extract_hashtags(content) == expected


@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.xdist_group("process_post_hashtags")
class TestProcessPostHashtags:
    """Integration tests using a real store + Postgres over ONE shared class DB.

    Each method requests ``reset_class_store`` (clean in-memory cache/identity
    map) and creates its own Account + Post via ``_make_post`` with unique
    snowflake ids. Every method uses a DISTINCT hashtag value, so persisted
    hashtag rows never collide across the shared database — no method depends on
    an empty DB.
    """

    async def test_no_content_is_noop(self, reset_class_store):
        """Empty content should not modify the post."""
        test_post = await _make_post(reset_class_store)
        await process_post_hashtags(test_post, "")
        assert test_post.hashtags == []

    async def test_creates_hashtag_and_links_to_post(self, reset_class_store):
        """Should create a new Hashtag and append it to the post."""
        test_post = await _make_post(reset_class_store)
        await process_post_hashtags(test_post, "Check out #newhashtag")

        assert len(test_post.hashtags) == 1
        assert test_post.hashtags[0].value == "newhashtag"
        assert test_post.hashtags[0].id is not None  # auto-increment assigned

        # Verify persisted in DB
        found = await reset_class_store.find_one(Hashtag, value__iexact="newhashtag")
        assert found is not None
        assert found.id == test_post.hashtags[0].id

    async def test_reuses_existing_hashtag(self, reset_class_store):
        """If the hashtag already exists, it should be reused, not duplicated."""
        test_post = await _make_post(reset_class_store)
        # Pre-create the hashtag
        existing, _ = await reset_class_store.get_or_create(
            Hashtag, defaults={"value": "existing"}, value="existing"
        )

        await process_post_hashtags(test_post, "Hello #existing")

        assert len(test_post.hashtags) == 1
        assert test_post.hashtags[0].id == existing.id

    async def test_case_insensitive_reuse(self, reset_class_store):
        """Hashtag lookup is case-insensitive; #FOO should reuse #foo."""
        test_post = await _make_post(reset_class_store)
        existing, _ = await reset_class_store.get_or_create(
            Hashtag, defaults={"value": "casematch"}, value="casematch"
        )

        await process_post_hashtags(test_post, "Check #CASEMATCH")

        assert len(test_post.hashtags) == 1
        assert test_post.hashtags[0].id == existing.id

    async def test_multiple_hashtags_in_one_call(self, reset_class_store):
        """Multiple hashtags in one content string."""
        test_post = await _make_post(reset_class_store)
        await process_post_hashtags(test_post, "Love #art and #music today")

        assert len(test_post.hashtags) == 2
        values = {h.value for h in test_post.hashtags}
        assert values == {"art", "music"}

    async def test_no_duplicates_appended(self, reset_class_store):
        """Calling with same hashtag twice should not duplicate in post.hashtags."""
        test_post = await _make_post(reset_class_store)
        await process_post_hashtags(test_post, "#repeat")
        await process_post_hashtags(test_post, "#repeat")

        assert len(test_post.hashtags) == 1

    async def test_content_with_no_hashtags_is_noop(self, reset_class_store):
        """Content without hashtags should not modify post."""
        test_post = await _make_post(reset_class_store)
        await process_post_hashtags(test_post, "Just plain text")
        assert test_post.hashtags == []
