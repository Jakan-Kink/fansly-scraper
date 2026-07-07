"""Tests for PostgresEntityStore opt-in monotonic-clock TTL.

Verifies default_ttl, per-type set_ttl override, and that ``get_from_cache``
evicts (and returns None) once an entry's TTL has elapsed. Per-type TTL
matters because daemon poll cadences span 30s to 10min — see
project_monitoring_optimizations.md.

These are pure in-memory cache tests (no DB round-trip), so they only need the
store object. They share ONE class-scoped database via ``class_entity_store``
(saving 8 per-test UUID databases); requesting ``reset_class_store`` gives each
method the shared store with its cache + TTL config cleared, so state doesn't
leak across the class.
"""

from datetime import timedelta

import pytest

from metadata.models import Account, Media, Post
from tests.fixtures.utils.test_isolation import snowflake_id


@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.xdist_group("cache_ttl")
class TestCacheTtl:
    """PostgresEntityStore TTL/cache behavior over one shared class DB."""

    # ── Default behavior: TTL is opt-in ─────────────────────────────────

    async def test_no_ttl_no_expiry(self, reset_class_store, fake_monotonic_clock):
        store = reset_class_store
        a = Account(id=snowflake_id(), username="ttl_none")
        store.cache_instance(a)
        # Advance an absurd amount of fake time
        fake_monotonic_clock["now"] += 10**9
        assert store.get_from_cache(Account, a.id) is a

    async def test_default_ttl_applies_when_no_per_type(
        self, reset_class_store, fake_monotonic_clock
    ):
        store = reset_class_store
        store._default_ttl = timedelta(seconds=60)
        a = Account(id=snowflake_id(), username="ttl_default")
        store.cache_instance(a)
        fake_monotonic_clock["now"] += 30
        assert store.get_from_cache(Account, a.id) is a
        fake_monotonic_clock["now"] += 31
        assert store.get_from_cache(Account, a.id) is None
        # Eviction is real — _cache + _type_index + _cache_timestamps cleared
        assert (Account, a.id) not in store._cache
        assert a.id not in store._type_index.get(Account, set())
        assert (Account, a.id) not in store._cache_timestamps

    # ── set_ttl: per-type override ──────────────────────────────────────

    async def test_per_type_overrides_default(
        self, reset_class_store, fake_monotonic_clock
    ):
        store = reset_class_store
        store._default_ttl = timedelta(seconds=600)  # generous default
        store.set_ttl(Account, timedelta(seconds=10))
        a = Account(id=snowflake_id(), username="ttl_per_type")
        store.cache_instance(a)
        fake_monotonic_clock["now"] += 11
        # Account-specific TTL fires before default would
        assert store.get_from_cache(Account, a.id) is None

    async def test_per_type_int_seconds_form(
        self, reset_class_store, fake_monotonic_clock
    ):
        store = reset_class_store
        store.set_ttl(Account, 5)
        a = Account(id=snowflake_id(), username="ttl_int")
        store.cache_instance(a)
        fake_monotonic_clock["now"] += 6
        assert store.get_from_cache(Account, a.id) is None

    async def test_per_type_none_removes_override(
        self, reset_class_store, fake_monotonic_clock
    ):
        store = reset_class_store
        store._default_ttl = None
        store.set_ttl(Account, 5)
        # Setting per-type to None drops the override; with default=None,
        # cache should not expire.
        store.set_ttl(Account, None)
        a = Account(id=snowflake_id(), username="ttl_clear_override")
        store.cache_instance(a)
        fake_monotonic_clock["now"] += 60
        assert store.get_from_cache(Account, a.id) is a

    async def test_invalid_type_raises(self, reset_class_store):
        store = reset_class_store
        with pytest.raises(TypeError, match="ttl must be"):
            store.set_ttl(Account, "60s")  # type: ignore[arg-type]

    # ── Per-type isolation ──────────────────────────────────────────────

    async def test_account_ttl_does_not_evict_media(
        self, reset_class_store, fake_monotonic_clock
    ):
        store = reset_class_store
        store.set_ttl(Account, 5)
        # Media has no TTL set → should not expire
        a = Account(id=snowflake_id(), username="ttl_a")
        assert isinstance(a.id, int)
        m = Media(id=snowflake_id(), accountId=a.id)
        store.cache_instance(a)
        store.cache_instance(m)
        fake_monotonic_clock["now"] += 10
        assert store.get_from_cache(Account, a.id) is None
        assert store.get_from_cache(Media, m.id) is m

    # ── Re-cache resets the timestamp ───────────────────────────────────

    async def test_cache_instance_resets_timestamp(
        self, reset_class_store, fake_monotonic_clock
    ):
        store = reset_class_store
        store.set_ttl(Account, 5)
        a = Account(id=snowflake_id(), username="ttl_recache")
        store.cache_instance(a)
        fake_monotonic_clock["now"] += 4
        # Re-cache (e.g., new merge from API response) before expiry
        store.cache_instance(a)
        fake_monotonic_clock["now"] += 4  # 4s after re-cache, total 8s elapsed
        # Per-type TTL is 5s; if timestamp didn't reset we'd be expired.
        # But we re-cached at +4, so we're only 4s past the new timestamp.
        assert store.get_from_cache(Account, a.id) is a

    # ── Different model classes: Post for sanity ────────────────────────

    async def test_post_default_ttl(self, reset_class_store, fake_monotonic_clock):
        store = reset_class_store
        store._default_ttl = timedelta(seconds=10)
        a = Account(id=snowflake_id(), username="ttl_post_owner")
        assert isinstance(a.id, int)
        p = Post(id=snowflake_id(), accountId=a.id, content="x")
        store.cache_instance(a)
        store.cache_instance(p)
        fake_monotonic_clock["now"] += 11
        # Both expire under the default TTL, independently
        assert store.get_from_cache(Account, a.id) is None
        assert store.get_from_cache(Post, p.id) is None
