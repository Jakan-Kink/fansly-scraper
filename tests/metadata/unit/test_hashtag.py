"""Tests for metadata/hashtag.py — extract_hashtags and process_post_hashtags."""

import pytest

from metadata.hashtag import extract_hashtags, process_post_hashtags
from metadata.models import Hashtag


class TestExtractHashtags:
    """Pure function tests — no DB needed."""

    def test_empty_content(self):
        assert extract_hashtags("") == []

    def test_none_content(self):
        assert extract_hashtags(None) == []

    def test_no_hashtags(self):
        assert extract_hashtags("Just a regular post with no tags") == []

    def test_single_hashtag(self):
        assert extract_hashtags("Hello #world") == ["world"]

    def test_multiple_hashtags(self):
        result = extract_hashtags("Check out #foo and #bar today")
        assert result == ["foo", "bar"]

    def test_lowercases(self):
        assert extract_hashtags("#FOO #Bar #baz") == ["foo", "bar", "baz"]

    def test_deduplicates(self):
        assert extract_hashtags("#foo #FOO #Foo") == ["foo"]

    def test_preserves_order(self):
        assert extract_hashtags("#beta #alpha #gamma") == ["beta", "alpha", "gamma"]

    def test_double_hash(self):
        """Double ## should still extract the tag."""
        assert extract_hashtags("##doublehash") == ["doublehash"]

    def test_hashtag_with_numbers(self):
        assert extract_hashtags("#test123") == ["test123"]

    def test_hashtag_with_underscores(self):
        assert extract_hashtags("#my_tag") == ["my_tag"]

    def test_hashtag_at_start(self):
        assert extract_hashtags("#first word") == ["first"]

    def test_hashtag_at_end(self):
        assert extract_hashtags("word #last") == ["last"]

    def test_adjacent_hashtags(self):
        assert extract_hashtags("#one#two") == ["one", "two"]


class TestProcessPostHashtags:
    """Integration tests using real entity_store + Postgres."""

    @pytest.mark.asyncio
    async def test_no_content_is_noop(self, entity_store, test_post):
        """Empty content should not modify the post."""
        await process_post_hashtags(test_post, "")
        assert test_post.hashtags == []

    @pytest.mark.asyncio
    async def test_creates_hashtag_and_links_to_post(self, entity_store, test_post):
        """Should create a new Hashtag and append it to the post."""
        await process_post_hashtags(test_post, "Check out #newhashtag")

        assert len(test_post.hashtags) == 1
        assert test_post.hashtags[0].value == "newhashtag"
        assert test_post.hashtags[0].id is not None  # auto-increment assigned

        # Verify persisted in DB
        found = await entity_store.find_one(Hashtag, value__iexact="newhashtag")
        assert found is not None
        assert found.id == test_post.hashtags[0].id

    @pytest.mark.asyncio
    async def test_reuses_existing_hashtag(self, entity_store, test_post):
        """If the hashtag already exists, it should be reused, not duplicated."""
        # Pre-create the hashtag
        existing, _ = await entity_store.get_or_create(
            Hashtag, defaults={"value": "existing"}, value="existing"
        )

        await process_post_hashtags(test_post, "Hello #existing")

        assert len(test_post.hashtags) == 1
        assert test_post.hashtags[0].id == existing.id

    @pytest.mark.asyncio
    async def test_case_insensitive_reuse(self, entity_store, test_post):
        """Hashtag lookup is case-insensitive; #FOO should reuse #foo."""
        existing, _ = await entity_store.get_or_create(
            Hashtag, defaults={"value": "casematch"}, value="casematch"
        )

        await process_post_hashtags(test_post, "Check #CASEMATCH")

        assert len(test_post.hashtags) == 1
        assert test_post.hashtags[0].id == existing.id

    @pytest.mark.asyncio
    async def test_multiple_hashtags_in_one_call(self, entity_store, test_post):
        """Multiple hashtags in one content string."""
        await process_post_hashtags(test_post, "Love #art and #music today")

        assert len(test_post.hashtags) == 2
        values = {h.value for h in test_post.hashtags}
        assert values == {"art", "music"}

    @pytest.mark.asyncio
    async def test_no_duplicates_appended(self, entity_store, test_post):
        """Calling with same hashtag twice should not duplicate in post.hashtags."""
        await process_post_hashtags(test_post, "#repeat")
        await process_post_hashtags(test_post, "#repeat")

        assert len(test_post.hashtags) == 1

    @pytest.mark.asyncio
    async def test_content_with_no_hashtags_is_noop(self, entity_store, test_post):
        """Content without hashtags should not modify post."""
        await process_post_hashtags(test_post, "Just plain text")
        assert test_post.hashtags == []
