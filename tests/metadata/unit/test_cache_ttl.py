"""Tests for PostgresEntityStore opt-in monotonic-clock TTL.

Verifies default_ttl, per-type set_ttl override, and that ``get_from_cache``
evicts (and returns None) once an entry's TTL has elapsed. Per-type TTL
matters because daemon poll cadences span 30s to 10min — see
project_monitoring_optimizations.md.
"""

from datetime import timedelta

import pytest

from metadata.models import Account, Media, Post
from tests.fixtures.utils.test_isolation import snowflake_id


pytestmark = pytest.mark.asyncio


# ── Default behavior: TTL is opt-in ─────────────────────────────────────


class TestTtlOptIn:
    async def test_no_ttl_no_expiry(self, entity_store, fake_monotonic_clock):
        a = Account(id=snowflake_id(), username="ttl_none")
        entity_store.cache_instance(a)
        # Advance an absurd amount of fake time
        fake_monotonic_clock["now"] += 10**9
        assert entity_store.get_from_cache(Account, a.id) is a

    async def test_default_ttl_applies_when_no_per_type(
        self, entity_store, fake_monotonic_clock
    ):
        entity_store._default_ttl = timedelta(seconds=60)
        a = Account(id=snowflake_id(), username="ttl_default")
        entity_store.cache_instance(a)
        fake_monotonic_clock["now"] += 30
        assert entity_store.get_from_cache(Account, a.id) is a
        fake_monotonic_clock["now"] += 31
        assert entity_store.get_from_cache(Account, a.id) is None
        # Eviction is real — _cache + _type_index + _cache_timestamps cleared
        assert (Account, a.id) not in entity_store._cache
        assert a.id not in entity_store._type_index.get(Account, set())
        assert (Account, a.id) not in entity_store._cache_timestamps


# ── set_ttl: per-type override ──────────────────────────────────────────


class TestSetTtl:
    async def test_per_type_overrides_default(self, entity_store, fake_monotonic_clock):
        entity_store._default_ttl = timedelta(seconds=600)  # generous default
        entity_store.set_ttl(Account, timedelta(seconds=10))
        a = Account(id=snowflake_id(), username="ttl_per_type")
        entity_store.cache_instance(a)
        fake_monotonic_clock["now"] += 11
        # Account-specific TTL fires before default would
        assert entity_store.get_from_cache(Account, a.id) is None

    async def test_per_type_int_seconds_form(self, entity_store, fake_monotonic_clock):
        entity_store.set_ttl(Account, 5)
        a = Account(id=snowflake_id(), username="ttl_int")
        entity_store.cache_instance(a)
        fake_monotonic_clock["now"] += 6
        assert entity_store.get_from_cache(Account, a.id) is None

    async def test_per_type_none_removes_override(
        self, entity_store, fake_monotonic_clock
    ):
        entity_store._default_ttl = None
        entity_store.set_ttl(Account, 5)
        # Setting per-type to None drops the override; with default=None,
        # cache should not expire.
        entity_store.set_ttl(Account, None)
        a = Account(id=snowflake_id(), username="ttl_clear_override")
        entity_store.cache_instance(a)
        fake_monotonic_clock["now"] += 60
        assert entity_store.get_from_cache(Account, a.id) is a

    async def test_invalid_type_raises(self, entity_store):
        with pytest.raises(TypeError, match="ttl must be"):
            entity_store.set_ttl(Account, "60s")  # type: ignore[arg-type]


# ── Per-type isolation ─────────────────────────────────────────────────


class TestPerTypeIsolation:
    async def test_account_ttl_does_not_evict_media(
        self, entity_store, fake_monotonic_clock
    ):
        entity_store.set_ttl(Account, 5)
        # Media has no TTL set → should not expire
        a = Account(id=snowflake_id(), username="ttl_a")
        m = Media(id=snowflake_id(), accountId=a.id)
        entity_store.cache_instance(a)
        entity_store.cache_instance(m)
        fake_monotonic_clock["now"] += 10
        assert entity_store.get_from_cache(Account, a.id) is None
        assert entity_store.get_from_cache(Media, m.id) is m


# ── Re-cache resets the timestamp ──────────────────────────────────────


class TestRecacheRefresh:
    async def test_cache_instance_resets_timestamp(
        self, entity_store, fake_monotonic_clock
    ):
        entity_store.set_ttl(Account, 5)
        a = Account(id=snowflake_id(), username="ttl_recache")
        entity_store.cache_instance(a)
        fake_monotonic_clock["now"] += 4
        # Re-cache (e.g., new merge from API response) before expiry
        entity_store.cache_instance(a)
        fake_monotonic_clock["now"] += 4  # 4s after re-cache, total 8s elapsed
        # Per-type TTL is 5s; if timestamp didn't reset we'd be expired.
        # But we re-cached at +4, so we're only 4s past the new timestamp.
        assert entity_store.get_from_cache(Account, a.id) is a


# ── Different model classes: Post for sanity ───────────────────────────


class TestSanityWithDifferentType:
    async def test_post_default_ttl(self, entity_store, fake_monotonic_clock):
        entity_store._default_ttl = timedelta(seconds=10)
        a = Account(id=snowflake_id(), username="ttl_post_owner")
        p = Post(id=snowflake_id(), accountId=a.id, content="x")
        entity_store.cache_instance(a)
        entity_store.cache_instance(p)
        fake_monotonic_clock["now"] += 11
        # Both expire under the default TTL, independently
        assert entity_store.get_from_cache(Account, a.id) is None
        assert entity_store.get_from_cache(Post, p.id) is None
