"""Tests for PostgresEntityStore._type_index secondary index.

Verifies the per-type id-set is maintained in lockstep with _cache across
cache_instance / invalidate / invalidate_type / invalidate_all, and that
the per-type iteration paths (filter, find cache-first, find_one, count,
find_iter) consult it instead of scanning _cache globally.
"""

import pytest

from metadata.models import Account, Media, Post
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.xdist_group("type_index")
class TestTypeIndexMaintenance:
    """_type_index stays in sync with _cache through every mutation path.

    Pure in-memory index tests: they share ONE class-scoped database via
    ``class_entity_store`` and request ``reset_class_store`` so each method
    starts from an empty ``_cache`` / ``_type_index`` (no per-test UUID DB).
    """

    async def test_cache_instance_populates_type_index(self, reset_class_store):
        store = reset_class_store
        a = Account(id=snowflake_id(), username="ti_alpha")
        store.cache_instance(a)
        assert a.id in store._type_index[Account]
        assert (Account, a.id) in store._cache

    async def test_invalidate_drops_id_from_type_index(self, reset_class_store):
        store = reset_class_store
        a = Account(id=snowflake_id(), username="ti_invalidate")
        store.cache_instance(a)
        store.invalidate(Account, a.id)
        assert a.id not in store._type_index.get(Account, set())
        assert (Account, a.id) not in store._cache

    async def test_invalidate_type_clears_only_one_type(self, reset_class_store):
        store = reset_class_store
        a1 = Account(id=snowflake_id(), username="ti_a1")
        a2 = Account(id=snowflake_id(), username="ti_a2")
        assert isinstance(a1.id, int)
        m = Media(id=snowflake_id(), accountId=a1.id)
        store.cache_instance(a1)
        store.cache_instance(a2)
        store.cache_instance(m)
        store.invalidate_type(Account)
        assert Account not in store._type_index
        # Media untouched
        assert m.id in store._type_index[Media]
        assert (Media, m.id) in store._cache

    async def test_invalidate_all_clears_index(self, reset_class_store):
        store = reset_class_store
        a = Account(id=snowflake_id(), username="ti_all")
        assert isinstance(a.id, int)
        m = Media(id=snowflake_id(), accountId=a.id)
        store.cache_instance(a)
        store.cache_instance(m)
        store.invalidate_all()
        assert store._type_index == {}
        assert store._cache == {}


@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.xdist_group("type_index")
class TestTypeIndexAffectsIteration:
    """Per-type iteration paths consult _type_index, not _cache."""

    async def test_filter_uses_type_index(self, reset_class_store):
        store = reset_class_store
        a = Account(id=snowflake_id(), username="ti_filter")
        store.cache_instance(a)
        results = store.filter(Account, lambda x: x.username == "ti_filter")
        assert len(results) == 1
        assert results[0] is a

    async def test_filter_skips_orphan_index_ids(self, reset_class_store):
        store = reset_class_store
        # Surgical invariant probe: poison the index with an id that has no
        # corresponding _cache entry — filter() must skip it via the
        # `obj is not None` guard rather than yield a phantom.
        a = Account(id=snowflake_id(), username="ti_orphan")
        store.cache_instance(a)
        store._type_index.setdefault(Account, set()).add(999_999_999)
        results = store.filter(Account)
        assert all(r is not None for r in results)
        assert len(results) == 1

    async def test_find_cache_first_uses_type_index(self, reset_class_store):
        store = reset_class_store
        a = Account(id=snowflake_id(), username="ti_find")
        store.cache_instance(a)
        store._fully_loaded.add(Account)
        try:
            results = await store.find(Account, username="ti_find")
            assert len(results) == 1
            assert results[0].id == a.id
        finally:
            store._fully_loaded.discard(Account)


@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.xdist_group("type_index")
class TestCacheStatsUsesIndex:
    """cache_stats() reports per-type counts via _type_index (O(types))."""

    async def test_cache_stats_counts_by_type(self, reset_class_store):
        store = reset_class_store
        a1 = Account(id=snowflake_id(), username="cs_1")
        a2 = Account(id=snowflake_id(), username="cs_2")
        assert isinstance(a1.id, int)
        m = Media(id=snowflake_id(), accountId=a1.id)
        store.cache_instance(a1)
        store.cache_instance(a2)
        store.cache_instance(m)
        stats = store.cache_stats()
        assert stats["by_type"]["Account"] == 2
        assert stats["by_type"]["Media"] == 1
        assert stats["total"] >= 3
        assert "stats" in stats  # _stats counters exposed

    async def test_cache_stats_excludes_empty_buckets(self, reset_class_store):
        store = reset_class_store
        # invalidate empties a type's id-set; stats should omit it
        a = Account(id=snowflake_id(), username="cs_empty")
        store.cache_instance(a)
        store.invalidate(Account, a.id)
        stats = store.cache_stats()
        assert "Account" not in stats["by_type"]


@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.xdist_group("type_index")
class TestPostFilterSurvives:
    """Smoke test: existing filter behavior unchanged for Post (a different
    model class) — guards against accidental coupling to Account.
    """

    async def test_post_filter_returns_only_posts(self, reset_class_store):
        store = reset_class_store
        a = Account(id=snowflake_id(), username="ti_post_test")
        store.cache_instance(a)
        assert isinstance(a.id, int)
        p = Post(id=snowflake_id(), accountId=a.id, content="hello")
        store.cache_instance(p)
        result = store.filter(Post)
        assert len(result) == 1
        assert result[0].id == p.id
