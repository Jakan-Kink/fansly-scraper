"""Tests for PostgresEntityStore._type_index secondary index.

Verifies the per-type id-set is maintained in lockstep with _cache across
cache_instance / invalidate / invalidate_type / invalidate_all, and that
the per-type iteration paths (filter, find cache-first, find_one, count,
find_iter) consult it instead of scanning _cache globally.
"""

import pytest

from metadata.models import Account, Media, Post
from tests.fixtures.utils.test_isolation import snowflake_id


pytestmark = pytest.mark.asyncio


class TestTypeIndexMaintenance:
    """_type_index stays in sync with _cache through every mutation path."""

    async def test_cache_instance_populates_type_index(self, entity_store):
        a = Account(id=snowflake_id(), username="ti_alpha")
        entity_store.cache_instance(a)
        assert a.id in entity_store._type_index[Account]
        assert (Account, a.id) in entity_store._cache

    async def test_invalidate_drops_id_from_type_index(self, entity_store):
        a = Account(id=snowflake_id(), username="ti_invalidate")
        entity_store.cache_instance(a)
        entity_store.invalidate(Account, a.id)
        assert a.id not in entity_store._type_index.get(Account, set())
        assert (Account, a.id) not in entity_store._cache

    async def test_invalidate_type_clears_only_one_type(self, entity_store):
        a1 = Account(id=snowflake_id(), username="ti_a1")
        a2 = Account(id=snowflake_id(), username="ti_a2")
        m = Media(id=snowflake_id(), accountId=a1.id)
        entity_store.cache_instance(a1)
        entity_store.cache_instance(a2)
        entity_store.cache_instance(m)
        entity_store.invalidate_type(Account)
        assert Account not in entity_store._type_index
        # Media untouched
        assert m.id in entity_store._type_index[Media]
        assert (Media, m.id) in entity_store._cache

    async def test_invalidate_all_clears_index(self, entity_store):
        a = Account(id=snowflake_id(), username="ti_all")
        m = Media(id=snowflake_id(), accountId=a.id)
        entity_store.cache_instance(a)
        entity_store.cache_instance(m)
        entity_store.invalidate_all()
        assert entity_store._type_index == {}
        assert entity_store._cache == {}


class TestTypeIndexAffectsIteration:
    """Per-type iteration paths consult _type_index, not _cache."""

    async def test_filter_uses_type_index(self, entity_store):
        a = Account(id=snowflake_id(), username="ti_filter")
        entity_store.cache_instance(a)
        results = entity_store.filter(Account, lambda x: x.username == "ti_filter")
        assert len(results) == 1
        assert results[0] is a

    async def test_filter_skips_orphan_index_ids(self, entity_store):
        # Surgical invariant probe: poison the index with an id that has no
        # corresponding _cache entry — filter() must skip it via the
        # `obj is not None` guard rather than yield a phantom.
        a = Account(id=snowflake_id(), username="ti_orphan")
        entity_store.cache_instance(a)
        entity_store._type_index.setdefault(Account, set()).add(999_999_999)
        results = entity_store.filter(Account)
        assert all(r is not None for r in results)
        assert len(results) == 1

    async def test_find_cache_first_uses_type_index(self, entity_store):
        a = Account(id=snowflake_id(), username="ti_find")
        entity_store.cache_instance(a)
        entity_store._fully_loaded.add(Account)
        try:
            results = await entity_store.find(Account, username="ti_find")
            assert len(results) == 1
            assert results[0].id == a.id
        finally:
            entity_store._fully_loaded.discard(Account)


class TestCacheStatsUsesIndex:
    """cache_stats() reports per-type counts via _type_index (O(types))."""

    async def test_cache_stats_counts_by_type(self, entity_store):
        a1 = Account(id=snowflake_id(), username="cs_1")
        a2 = Account(id=snowflake_id(), username="cs_2")
        m = Media(id=snowflake_id(), accountId=a1.id)
        entity_store.cache_instance(a1)
        entity_store.cache_instance(a2)
        entity_store.cache_instance(m)
        stats = entity_store.cache_stats()
        assert stats["by_type"]["Account"] == 2
        assert stats["by_type"]["Media"] == 1
        assert stats["total"] >= 3
        assert "stats" in stats  # _stats counters exposed

    async def test_cache_stats_excludes_empty_buckets(self, entity_store):
        # invalidate empties a type's id-set; stats should omit it
        a = Account(id=snowflake_id(), username="cs_empty")
        entity_store.cache_instance(a)
        entity_store.invalidate(Account, a.id)
        stats = entity_store.cache_stats()
        assert "Account" not in stats["by_type"]


class TestPostFilterSurvives:
    """Smoke test: existing filter behavior unchanged for Post (a different
    model class) — guards against accidental coupling to Account.
    """

    async def test_post_filter_returns_only_posts(self, entity_store):
        a = Account(id=snowflake_id(), username="ti_post_test")
        entity_store.cache_instance(a)
        p = Post(id=snowflake_id(), accountId=a.id, content="hello")
        entity_store.cache_instance(p)
        result = entity_store.filter(Post)
        assert len(result) == 1
        assert result[0].id == p.id
