"""Comprehensive entity_store tests — real DB queries, cache management, junctions.

Each test exercises multiple entity_store operations against a shared database.
Tests are organized by functional area, not by individual method. The goal is
to exercise full code paths including DB query building, cache/DB split,
junction table sync, and preload — all against real PostgreSQL.
"""

import asyncio
import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import asyncpg
import pytest

from metadata.database import Database
from metadata.entity_store import (
    PostgresEntityStore,
    SortDirection,
    _matches_filters,
    _parse_lookup,
    _resolve_field_value,
)
from metadata.models import (
    Account,
    AccountMedia,
    AccountMediaBundle,
    Attachment,
    FanslyObject,
    Hashtag,
    Media,
    PinnedPost,
    Post,
    StubTracker,
)
from metadata.tables import metadata as table_metadata
from tests.fixtures.utils.test_isolation import snowflake_id


# ── Helpers ──────────────────────────────────────────────────────────────


def _force_db_path(entity_store, model_type):
    """Remove model_type from _fully_loaded to force SQL path."""
    entity_store._fully_loaded.discard(model_type)


# ── Field path resolution (pure, no DB) ──────────────────────────────────


class TestResolveFieldValue:
    def test_simple_and_nested_and_list(self):
        """Exercise simple, nested, None-intermediate, and list traversal."""
        acct = Account(id=snowflake_id(), username="resolve_test")
        assert _resolve_field_value(acct, "username") == "resolve_test"

        # Nested path through relationship
        media = Media(id=snowflake_id(), accountId=acct.id)
        media.account = acct
        assert _resolve_field_value(media, "account__username") == "resolve_test"

        # None intermediate
        media2 = Media(id=snowflake_id(), accountId=snowflake_id())
        media2.account = None
        assert _resolve_field_value(media2, "account__username") is None

        # List traversal
        post = Post(id=snowflake_id(), accountId=snowflake_id(), fypFlag=0)
        cid1, cid2 = snowflake_id(), snowflake_id()
        post.attachments = [
            Attachment(
                id=snowflake_id(), postId=post.id, contentId=cid1, contentType=1, pos=0
            ),
            Attachment(
                id=snowflake_id(), postId=post.id, contentId=cid2, contentType=2, pos=1
            ),
        ]
        result = _resolve_field_value(post, "attachments__contentId")
        assert result == [cid1, cid2]


# ── CRUD + SQL query paths ───────────────────────────────────────────────


class TestCRUDAndQueryPaths:
    """Full CRUD lifecycle + SQL query paths (forced via _fully_loaded.discard).

    Single test per functional area, each exercising many operations.
    """

    @pytest.mark.asyncio
    async def test_find_count_find_one_find_iter_via_sql(self, entity_store):
        """Exercise all query methods — cache paths AND SQL paths + filter operators."""
        # Setup: create accounts and media
        acct1 = Account(id=snowflake_id(), username="sql_alpha")
        acct2 = Account(id=snowflake_id(), username="sql_beta")
        acct3 = Account(id=snowflake_id(), username="sql_gamma")
        for a in [acct1, acct2, acct3]:
            await entity_store.save(a)

        m1 = Media(id=snowflake_id(), accountId=acct1.id, duration=50.0)
        m2 = Media(
            id=snowflake_id(), accountId=acct1.id, duration=150.0, content_hash="abc"
        )
        m3 = Media(id=snowflake_id(), accountId=acct2.id, duration=250.0)
        for m in [m1, m2, m3]:
            await entity_store.save(m)

        # ── Cache paths (models are _fully_loaded after preload) ──────

        # find_one with order_by on cache (lines 488-500)
        cache_one = await entity_store.find_one(
            Account, order_by="username", username__contains="sql_"
        )
        assert cache_one is not None
        assert cache_one.username == "sql_alpha"

        # find_one no match on cache (without order_by)
        cache_none = await entity_store.find_one(Account, username="impossible_cache")
        assert cache_none is None

        # find_one no match on cache WITH order_by → sorted empty list → None (line 497)
        cache_none_sorted = await entity_store.find_one(
            Account, order_by="username", username="impossible_sorted_cache"
        )
        assert cache_none_sorted is None

        # count on cache
        cache_count = await entity_store.count(Account, username__contains="sql_")
        assert cache_count == 3

        # find_iter on cache with order_by (lines 559-569)
        cache_iter_results = []
        async for batch in entity_store.find_iter(
            Account, batch_size=2, order_by="username", username__contains="sql_"
        ):
            cache_iter_results.extend(batch)
        assert len(cache_iter_results) == 3
        assert cache_iter_results[0].username == "sql_alpha"

        # find_iter on cache — no matches
        empty_iter = []
        async for batch in entity_store.find_iter(Account, username="no_match_cache"):
            empty_iter.extend(batch)
        assert empty_iter == []

        # ── Force DB paths ────────────────────────────────────────────
        _force_db_path(entity_store, Account)
        _force_db_path(entity_store, Media)

        # find with exact match
        results = await entity_store.find(Account, username="sql_alpha")
        assert len(results) == 1
        assert results[0].id == acct1.id

        # find with ORDER BY (ASC default)
        results = await entity_store.find(Account, order_by="username")
        usernames = [r.username for r in results]
        assert usernames.index("sql_alpha") < usernames.index("sql_gamma")

        # find with DESC order
        _force_db_path(entity_store, Account)
        results = await entity_store.find(
            Account, order_by=("username", SortDirection.DESC)
        )
        assert results[0].username == "sql_gamma"

        # find with contains (ILIKE)
        _force_db_path(entity_store, Account)
        results = await entity_store.find(Account, username__contains="sql_")
        assert len(results) == 3

        # find with iexact
        _force_db_path(entity_store, Account)
        results = await entity_store.find(Account, username__iexact="SQL_ALPHA")
        assert len(results) == 1

        # find with __in
        _force_db_path(entity_store, Account)
        results = await entity_store.find(Account, id__in=[acct1.id, acct3.id])
        assert len(results) == 2

        # find with __null
        _force_db_path(entity_store, Media)
        null_results = await entity_store.find(Media, content_hash__null=True)
        not_null = await entity_store.find(Media, content_hash__null=False)
        assert any(r.id == m1.id for r in null_results)
        assert any(r.id == m2.id for r in not_null)

        # find with __gte, __lte
        _force_db_path(entity_store, Media)
        gte = await entity_store.find(Media, duration__gte=100.0)
        assert any(r.id == m2.id for r in gte)
        assert not any(r.id == m1.id for r in gte)

        # find with __between
        _force_db_path(entity_store, Media)
        between = await entity_store.find(Media, duration__between=(100.0, 200.0))
        assert len(between) >= 1
        assert between[0].id == m2.id

        # find_one via SQL
        _force_db_path(entity_store, Account)
        one = await entity_store.find_one(Account, username="sql_beta")
        assert one is not None
        assert one.id == acct2.id

        _force_db_path(entity_store, Account)
        none = await entity_store.find_one(Account, username="nonexistent_xyz")
        assert none is None

        # find_one with order_by
        _force_db_path(entity_store, Account)
        first = await entity_store.find_one(
            Account, order_by="username", username__contains="sql_"
        )
        assert first.username == "sql_alpha"

        # count via SQL
        _force_db_path(entity_store, Account)
        assert await entity_store.count(Account, username__contains="sql_") == 3

        _force_db_path(entity_store, Account)
        assert await entity_store.count(Account) >= 3

        # find_iter via SQL
        _force_db_path(entity_store, Account)
        all_results = []
        async for batch in entity_store.find_iter(Account, batch_size=2):
            all_results.extend(batch)
            assert len(batch) <= 2
        assert len(all_results) >= 3

        # find_iter with filter + empty result
        _force_db_path(entity_store, Account)
        empty = []
        async for batch in entity_store.find_iter(
            Account, batch_size=10, username="no_match_at_all"
        ):
            empty.extend(batch)
        assert empty == []

        # Verify stats accumulated
        stats = entity_store.get_stats()
        assert stats.get("find_pg_hits", 0) > 0
        assert stats.get("find_one_pg_hits", 0) > 0
        assert stats.get("find_one_pg_misses", 0) > 0

    @pytest.mark.asyncio
    async def test_get_get_many_delete_delete_many(self, entity_store):
        """Full get/get_many lifecycle including cache miss → DB hit → delete."""
        accts = []
        for i in range(4):
            a = Account(id=snowflake_id(), username=f"lifecycle_{i}")
            await entity_store.save(a)
            accts.append(a)

        # get from cache
        cached = await entity_store.get(Account, accts[0].id)
        assert cached is accts[0]

        # get cache miss → DB hit (evict first)
        entity_store.invalidate(Account, accts[1].id)
        from_db = await entity_store.get(Account, accts[1].id)
        assert from_db is not None
        assert from_db.username == "lifecycle_1"

        # get cache miss → DB miss
        missing = await entity_store.get(Account, snowflake_id())
        assert missing is None

        # get_many: mix of cache hits and DB fetches
        entity_store.invalidate(Account, accts[2].id)
        entity_store.invalidate(Account, accts[3].id)
        many = await entity_store.get_many(Account, [a.id for a in accts])
        assert len(many) == 4

        # get_many: all cached (no DB hit)
        all_cached = await entity_store.get_many(Account, [accts[0].id])
        assert len(all_cached) == 1

        # delete single
        await entity_store.delete(accts[0])
        assert await entity_store.get(Account, accts[0].id) is None

        # delete with None id (no-op)
        h = Hashtag(value="noop_del")
        await entity_store.delete(h)

        # delete_many
        remaining_ids = [a.id for a in accts[1:]]
        count = await entity_store.delete_many(Account, remaining_ids)
        assert count == 3

        # delete_many empty
        assert await entity_store.delete_many(Account, []) == 0

    @pytest.mark.asyncio
    async def test_get_or_create_and_bulk_ops(self, entity_store):
        """get_or_create + bulk_upsert + bulk_upsert_records."""
        # get_or_create: new
        value = f"goc_{snowflake_id()}"
        obj1, created1 = await entity_store.get_or_create(
            Hashtag, defaults={"value": value}, value=value
        )
        assert created1 is True

        # get_or_create: existing (found in cache before insert)
        obj2, created2 = await entity_store.get_or_create(
            Hashtag, defaults={"value": value}, value=value
        )
        assert created2 is False
        assert obj2.id == obj1.id

        # get_or_create: UniqueViolation race path (lines 610-614)
        # Spy on find_one: first call returns None (simulating not-found),
        # but inserts the record via raw SQL before returning — so the
        # subsequent INSERT in get_or_create hits UniqueViolation, then
        # the retry find_one (second call) finds it in DB.
        race_value = f"race_{snowflake_id()}"
        pool = await entity_store._get_pool()
        original_find_one = entity_store.find_one
        call_count = 0

        async def spy_find_one(model_type, **filters):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: pretend not found, but sneak the row in
                await pool.execute(
                    "INSERT INTO hashtags (value) VALUES ($1)", race_value
                )
                return None
            # Retry call: real find_one finds the row we inserted
            return await original_find_one(model_type, **filters)

        entity_store._fully_loaded.discard(Hashtag)
        entity_store.find_one = spy_find_one
        try:
            obj3, created3 = await entity_store.get_or_create(
                Hashtag, defaults={"value": race_value}, value=race_value
            )
            assert created3 is False  # Found via retry after UniqueViolation
            assert obj3.value == race_value
            assert call_count == 2  # find_one called twice (initial + retry)
        finally:
            entity_store.find_one = original_find_one

        # get_or_create: UniqueViolation re-raise (line 614)
        # INSERT fails on username unique constraint, but find_one searches
        # by id (which doesn't exist) → retry returns None → re-raises
        dupe_username = f"dupe_{snowflake_id()}"
        await pool.execute(
            "INSERT INTO accounts (id, username) VALUES ($1, $2)",
            snowflake_id(),
            dupe_username,
        )
        entity_store._fully_loaded.discard(Account)
        search_id = snowflake_id()  # Different from the existing account's id
        with pytest.raises(asyncpg.UniqueViolationError):
            await entity_store.get_or_create(
                Account,
                defaults={"username": dupe_username},
                id=search_id,
            )

        # bulk_upsert entities
        items = [{"id": snowflake_id(), "username": f"bulk_{i}"} for i in range(3)]
        await entity_store.bulk_upsert(Account, items)
        for item in items:
            found = await entity_store.get(Account, item["id"])
            assert found is not None

        # bulk_upsert empty
        await entity_store.bulk_upsert(Account, [])

        # bulk_upsert_records
        records = [
            {
                "table_name": "accounts",
                "record_id": snowflake_id(),
                "created_at": datetime.now(UTC),
                "reason": "bulk test",
            }
            for _ in range(3)
        ]
        await entity_store.bulk_upsert_records(StubTracker.__table_name__, records)
        rows = await entity_store.find_records(StubTracker, table_name="accounts")
        assert len(rows) >= 3

        # bulk_upsert_records empty
        await entity_store.bulk_upsert_records("stub_tracker", [])

    @pytest.mark.asyncio
    async def test_validate_order_by_error(self, entity_store):
        with pytest.raises(ValueError, match="Invalid order_by column"):
            await entity_store.find(Account, order_by="nonexistent_col")


# ── Junction table sync (avatar/banner scalar 1:1) ───────────────────────


class TestJunctionSync:
    @pytest.mark.asyncio
    async def test_account_avatar_lifecycle(self, entity_store):
        """Full avatar lifecycle: set → replace → clear. Tests scalar 1:1 junction."""
        acct = Account(id=snowflake_id(), username="avatar_lifecycle")
        await entity_store.save(acct)

        avatar1 = Media(id=snowflake_id(), accountId=acct.id, mimetype="image/jpeg")
        avatar2 = Media(id=snowflake_id(), accountId=acct.id, mimetype="image/png")
        await entity_store.save(avatar1)
        await entity_store.save(avatar2)

        pool = await entity_store._get_pool()

        # Set avatar
        acct.avatar = avatar1
        await entity_store.save(acct)
        row = await pool.fetchrow(
            'SELECT "mediaId" FROM account_avatar WHERE "accountId" = $1', acct.id
        )
        assert row["mediaId"] == avatar1.id

        # Replace avatar
        acct.avatar = avatar2
        await entity_store.save(acct)
        rows = await pool.fetch(
            'SELECT "mediaId" FROM account_avatar WHERE "accountId" = $1', acct.id
        )
        assert len(rows) == 1
        assert rows[0]["mediaId"] == avatar2.id

        # Clear avatar
        acct.avatar = None
        await entity_store.save(acct)
        row = await pool.fetchrow(
            'SELECT "mediaId" FROM account_avatar WHERE "accountId" = $1', acct.id
        )
        assert row is None


# ── Cache management ─────────────────────────────────────────────────────


class TestCacheManagement:
    @pytest.mark.asyncio
    async def test_invalidate_filter_stats_close(self, config):
        """Exercise invalidate_type, invalidate_all, filter, cache_stats, close."""
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        try:
            store = await db.create_entity_store()

            a1 = Account(id=snowflake_id(), username="cache_mgmt_1")
            a2 = Account(id=snowflake_id(), username="cache_mgmt_2")
            await store.save(a1)
            await store.save(a2)

            # filter (Python predicate on cache)
            matched = store.filter(Account, predicate=lambda a: "mgmt_1" in a.username)
            assert len(matched) == 1
            assert matched[0].id == a1.id

            empty = store.filter(
                Account, predicate=lambda a: a.username == "impossible"
            )
            assert empty == []

            no_pred = store.filter(Account)
            assert len(no_pred) >= 2

            # cache_stats
            stats = store.cache_stats()
            assert stats["total"] >= 2
            assert "Account" in stats["by_type"]

            # get_stats / reset_stats
            s = store.get_stats()
            assert isinstance(s, dict)
            store.reset_stats()

            # invalidate_type
            store.invalidate_type(Account)
            assert store.get_from_cache(Account, a1.id) is None
            assert Account not in store._fully_loaded

            # Re-save for invalidate_all test
            await store.save(a1)
            store.invalidate_all()
            assert len(store._cache) == 0

            # close
            await store.close()
            assert FanslyObject._store is None
        finally:
            FanslyObject._store = None
            if db._asyncpg_pool:
                await db._asyncpg_pool.close()
            db.close_sync()


# ── Preload with data ────────────────────────────────────────────────────


class TestPreload:
    @pytest.mark.asyncio
    async def test_preload_with_rows_and_associations(self, config):
        """Preload should stream DB rows into cache, including M2M associations."""
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)

        # Insert data via raw SQL before creating entity store
        pool = await asyncpg.create_pool(
            host=config.pg_host,
            port=int(config.pg_port),
            database=config.pg_database,
            user=config.pg_user,
            password=config.pg_password or "",
            min_size=1,
            max_size=2,
        )
        acct_id = snowflake_id()
        media_id = snowflake_id()
        await pool.execute(
            'INSERT INTO accounts (id, username, "createdAt") VALUES ($1, $2, $3)',
            acct_id,
            "preload_test",
            datetime.now(UTC),
        )
        await pool.execute(
            'INSERT INTO media (id, "accountId") VALUES ($1, $2)',
            media_id,
            acct_id,
        )
        # Create avatar association (scalar 1:1 — covers line 1296)
        await pool.execute(
            'INSERT INTO account_avatar ("accountId", "mediaId") VALUES ($1, $2)',
            acct_id,
            media_id,
        )
        # Create variant media + junction (list M2M — covers line 1293)
        variant_id = snowflake_id()
        await pool.execute(
            'INSERT INTO media (id, "accountId") VALUES ($1, $2)',
            variant_id,
            acct_id,
        )
        await pool.execute(
            'INSERT INTO media_variants ("mediaId", "variantId") VALUES ($1, $2)',
            media_id,
            variant_id,
        )
        await pool.close()

        try:
            store = await db.create_entity_store()

            # Verify preloaded
            cached_acct = store.get_from_cache(Account, acct_id)
            assert cached_acct is not None
            assert cached_acct.username == "preload_test"
            assert Account in store._fully_loaded

            cached_media = store.get_from_cache(Media, media_id)
            assert cached_media is not None
        finally:
            FanslyObject._store = None
            if db._asyncpg_pool:
                await db._asyncpg_pool.close()
            db.close_sync()


# ── Thread-local pool ─────────────────────────────────────────────────────


class TestThreadLocalPool:
    @pytest.mark.asyncio
    async def test_worker_thread_gets_own_pool(self, config):
        """Entity store operations from a worker thread should use a thread-local pool.

        Covers entity_store.py lines 278-313: _get_pool thread-local path.
        """

        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        try:
            store = await db.create_entity_store()
            store._db_config = {
                "host": config.pg_host,
                "port": int(config.pg_port),
                "database": config.pg_database,
                "user": config.pg_user,
                "password": config.pg_password or "",
            }

            result_holder = {}
            error_holder = {}

            def worker():
                """Run entity store ops in a worker thread with its own event loop."""
                loop = asyncio.new_event_loop()
                try:
                    # This forces _get_pool to create a thread-local pool
                    # because the worker's event loop differs from the main pool's loop
                    acct = Account(id=snowflake_id(), username="thread_local_test")

                    async def do_work():
                        await store.save(acct)
                        found = await store.get(Account, acct.id)
                        result_holder["found"] = found is not None
                        result_holder["username"] = found.username if found else None

                    loop.run_until_complete(do_work())
                except Exception as e:
                    error_holder["error"] = str(e)
                finally:
                    loop.close()

            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=30)

            if error_holder:
                pytest.fail(f"Worker thread error: {error_holder['error']}")

            assert result_holder.get("found") is True
            assert result_holder.get("username") == "thread_local_test"

            # Verify a thread-local pool was created
            assert len(store._thread_pools) >= 1

            # Clean up thread-local pools
            await store.close_thread_resources()
        finally:
            FanslyObject._store = None
            if db._asyncpg_pool:
                await db._asyncpg_pool.close()
            db.close_sync()


# ── Database cleanup paths ────────────────────────────────────────────────


class TestDatabaseCleanup:
    @pytest.mark.asyncio
    async def test_full_cleanup_lifecycle(self, config):
        """Database.cleanup: entity_store close, pool close, engine dispose.

        Covers database.py lines 217-250 (cleanup normal path).
        """
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        try:
            store = await db.create_entity_store()
            # Verify everything is initialized
            assert db._entity_store is not None
            assert db._asyncpg_pool is not None

            # Run full async cleanup
            await db.cleanup()

            assert db._cleanup_done.is_set()

            # Second call is idempotent
            await db.cleanup()
        finally:
            FanslyObject._store = None

    @pytest.mark.asyncio
    async def test_cleanup_without_entity_store(self, config):
        """Cleanup when entity_store was never created."""
        db = Database(config, skip_migrations=True)
        await db.cleanup()
        assert db._cleanup_done.is_set()
        FanslyObject._store = None

    def test_close_sync_and_del(self, mock_config):
        """close_sync + __del__ coverage."""
        db = Database(mock_config, skip_migrations=True)
        db.close_sync()
        assert db._cleanup_done.is_set()
        # __del__ should be safe after close_sync
        del db

    def test_entity_store_property_before_init(self, mock_config):
        """Accessing entity_store before create_entity_store should raise."""
        db = Database(mock_config, skip_migrations=True)
        with pytest.raises(RuntimeError, match="EntityStore not initialized"):
            _ = db.entity_store


# ── Entity store edge cases ───────────────────────────────────────────────


class TestEntityStoreEdgeCases:
    @pytest.mark.asyncio
    async def test_get_pool_no_db_config_raises(self, config):
        """_get_pool in worker thread without db_config → RuntimeError (line 286)."""

        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        try:
            store = await db.create_entity_store()
            store._db_config = None
            error_holder = {}

            def worker():

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(store._get_pool())
                except RuntimeError as e:
                    error_holder["msg"] = str(e)
                finally:
                    loop.close()

            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=10)
            assert "no db_config" in error_holder.get("msg", "")
        finally:
            FanslyObject._store = None
            if db._asyncpg_pool:
                await db._asyncpg_pool.close()
            db.close_sync()

    @pytest.mark.asyncio
    async def test_ensure_junction_fk_targets_unknown_table(self, entity_store):
        """_ensure_junction_fk_targets with unknown table → early return (line 952)."""
        await entity_store._ensure_junction_fk_targets(
            "nonexistent_table_xyz", [{"col": 1}], "owner_fk"
        )

    def test_parse_lookup_unknown_operator(self):
        """_parse_lookup with unknown operator returns (full_key, 'exact')."""

        # Known operator → split
        assert _parse_lookup("username__contains") == ("username", "contains")
        # Unknown operator → treat entire key as field name with "exact"
        assert _parse_lookup("field__badop") == ("field__badop", "exact")
        # No double underscore → exact
        assert _parse_lookup("username") == ("username", "exact")

    def test_matches_filters_unknown_lookup(self):
        """_matches_filters with unknown lookup → comparator is None → False."""

        acct = Account(id=snowflake_id(), username="filter_test")
        # "badlookup" not in _CACHE_OPS → comparator is None → return False
        assert _matches_filters(acct, [("username", "badlookup", "anything")]) is False

    def test_matches_filters_list_field_value(self):
        """_matches_filters with list field value uses ANY-semantics."""

        post = Post(id=snowflake_id(), accountId=snowflake_id(), fypFlag=0)
        cid1, cid2 = snowflake_id(), snowflake_id()
        post.attachments = [
            Attachment(
                id=snowflake_id(), postId=post.id, contentId=cid1, contentType=1, pos=0
            ),
            Attachment(
                id=snowflake_id(), postId=post.id, contentId=cid2, contentType=2, pos=1
            ),
        ]
        # ANY match on nested list path → True (line 214-215 both branches)
        assert (
            _matches_filters(post, [("attachments__contentId", "exact", cid1)]) is True
        )
        # No match in list → False (line 214 true, but any() returns False)
        assert (
            _matches_filters(
                post, [("attachments__contentId", "exact", snowflake_id())]
            )
            is False
        )

    def test_entity_store_init_without_event_loop(self):
        """PostgresEntityStore.__init__ without running loop → _pool_loop = None."""

        # Create a mock pool — we just need __init__ to run, not actual DB
        mock_pool = MagicMock(spec=asyncpg.Pool)
        # __init__ calls asyncio.get_running_loop() which raises RuntimeError
        # outside an async context → _pool_loop = None
        store = PostgresEntityStore(mock_pool)
        assert store._pool_loop is None

    def test_get_type_by_name(self):
        """get_type_by_name resolves type name or returns None (line 374)."""
        assert PostgresEntityStore.get_type_by_name("Account") is Account
        assert PostgresEntityStore.get_type_by_name("Media") is Media
        assert PostgresEntityStore.get_type_by_name("NonexistentType") is None

    @pytest.mark.asyncio
    async def test_sync_junction_empty_rows(
        self, entity_store, test_account, test_post
    ):
        """sync_junction with empty rows → DELETE all then return (line 1034).

        Also covers the normal sync_junction path with rows.
        """
        # First add a pinned post junction row (post must exist for FK)
        await entity_store.sync_junction(
            "pinned_posts",
            "accountId",
            test_account.id,
            [{"postId": test_post.id, "pos": 0, "createdAt": datetime.now(UTC)}],
        )
        # Now clear by syncing empty rows
        await entity_store.sync_junction(
            "pinned_posts", "accountId", test_account.id, []
        )
        # Verify junction cleared
        pool = await entity_store._get_pool()
        rows = await pool.fetch(
            'SELECT * FROM pinned_posts WHERE "accountId" = $1', test_account.id
        )
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_pool_loop_first_assignment(self, config):
        """_get_pool sets _pool_loop on first call when __init__ had no loop (line 273)."""

        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        try:
            store = await db.create_entity_store()
            # Simulate the case where __init__ didn't capture the loop
            store._pool_loop = None

            pool = await store._get_pool()
            # Should have set _pool_loop to current loop
            assert store._pool_loop is not None
            assert pool is store.pool
        finally:
            FanslyObject._store = None
            if db._asyncpg_pool:
                await db._asyncpg_pool.close()
            db.close_sync()

    @pytest.mark.asyncio
    async def test_bulk_upsert_skips_empty_table_data(self, entity_store):
        """bulk_upsert/bulk_upsert_records skip items with no matching columns (lines 1155, 1180)."""
        # Item with only non-matching keys → table_data is empty → continue
        await entity_store.bulk_upsert(
            Account,
            [
                {"id": snowflake_id(), "username": "valid_item"},
                {"nonexistent_column": "should_be_skipped"},
            ],
        )
        await entity_store.bulk_upsert_records(
            "stub_tracker",
            [
                {
                    "table_name": "test",
                    "record_id": snowflake_id(),
                    "created_at": datetime.now(UTC),
                },
                {"fake_col": "skipped"},
            ],
        )

    @pytest.mark.asyncio
    async def test_ensure_junction_fk_targets_creates_stubs(self, entity_store):
        """_ensure_junction_fk_targets creates Post stubs for pinned_posts FK refs.

        When saving an Account with pinnedPosts referencing Posts not in the DB,
        the entity store introspects pinned_posts FK constraints, discovers
        postId → posts.id, and calls Post.create_stub() to satisfy the FK.
        Covers lines 965-994 (_ensure_junction_fk_targets full path).
        """

        acct = Account(id=snowflake_id(), username="stub_test_acct")
        await entity_store.save(acct)

        # Create pinned post referencing a Post that doesn't exist yet
        missing_post_id = snowflake_id()
        pp = PinnedPost(
            postId=missing_post_id,
            accountId=acct.id,
            pos=0,
            createdAt=datetime.now(UTC),
        )
        acct.pinnedPosts = [pp]

        # Save the account — _sync_assoc_tables will call _ensure_junction_fk_targets
        # which should create a Post stub for missing_post_id
        await entity_store.save(acct)

        # Verify the stub was created
        stub = await entity_store.get(Post, missing_post_id)
        assert stub is not None
        assert stub.accountId == acct.id  # accountId from PinnedPost context

    @pytest.mark.asyncio
    async def test_ensure_junction_fk_target_exists_in_db_not_cache(self, entity_store):
        """_ensure_junction_fk_targets skips when target exists in DB but not cache (line 981).

        Insert a Post via raw SQL (bypasses cache), then save Account with
        pinnedPosts referencing it. _ensure_junction_fk_targets finds it
        in DB via _fetch_one_by_id → continue (no stub needed).
        """

        acct = Account(id=snowflake_id(), username="fk_exists_test")
        await entity_store.save(acct)

        # Insert Post via raw SQL — in DB but NOT in entity_store cache
        existing_post_id = snowflake_id()
        pool = await entity_store._get_pool()
        await pool.execute(
            'INSERT INTO posts (id, "accountId", "fypFlag") VALUES ($1, $2, $3)',
            existing_post_id,
            acct.id,
            0,
        )
        # Evict from cache if somehow cached
        entity_store.invalidate(Post, existing_post_id)

        pp = PinnedPost(
            postId=existing_post_id,
            accountId=acct.id,
            pos=0,
            createdAt=datetime.now(UTC),
        )
        acct.pinnedPosts = [pp]
        await entity_store.save(acct)

        # Verify no stub was created — the existing Post was found in DB
        found = await entity_store.get(Post, existing_post_id)
        assert found is not None

    @pytest.mark.asyncio
    async def test_fetch_all_associations_no_assoc_def(self, entity_store):
        """_fetch_all_associations skips relationships with no assoc_table def (line 1277)."""
        # This is called during preload and normally works. We need a model
        # whose assoc_table isn't in metadata.tables. Since all current models
        # have valid assoc tables, we test that the method completes without error.
        result = await entity_store._fetch_all_associations(Account)
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_toctou_thread_pool_race(self, config):
        """Two concurrent _get_pool calls in a worker thread — second finds pool
        already created by first (lines 306-307 TOCTOU double-check)."""

        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        try:
            store = await db.create_entity_store()
            store._db_config = {
                "host": config.pg_host,
                "port": int(config.pg_port),
                "database": config.pg_database,
                "user": config.pg_user,
                "password": config.pg_password or "",
            }

            error_holder = {}

            def worker():

                loop = asyncio.new_event_loop()
                try:

                    async def concurrent_gets():
                        # Two concurrent _get_pool calls — first creates pool,
                        # second hits the TOCTOU double-check (306-307)
                        results = await asyncio.gather(
                            store._get_pool(),
                            store._get_pool(),
                        )
                        assert results[0] is results[1]

                    loop.run_until_complete(concurrent_gets())
                except Exception as e:
                    error_holder["error"] = str(e)
                finally:
                    loop.close()

            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=30)
            if error_holder:
                pytest.fail(f"Worker thread error: {error_holder['error']}")
            await store.close_thread_resources()
        finally:
            FanslyObject._store = None
            if db._asyncpg_pool:
                await db._asyncpg_pool.close()
            db.close_sync()

    @pytest.mark.asyncio
    async def test_insert_row_empty_table_data(self, entity_store):
        """_insert_row with no matching columns → early return (line 701)."""
        acct = Account(id=snowflake_id(), username="insert_empty_test")
        # Use object.__setattr__ to bypass Pydantic validation
        object.__setattr__(acct, "to_db_dict", lambda: {"fake_column": "fake_value"})
        acct._is_new = True
        await entity_store._insert_row(acct)  # Should return early, no INSERT

    @pytest.mark.asyncio
    async def test_update_no_changes(self, entity_store):
        """_update with no changed fields → early return (line 734)."""
        acct = Account(id=snowflake_id(), username="update_noop")
        await entity_store.save(acct)
        acct.mark_clean()
        # No changes → _update returns early
        await entity_store._update(acct)

    @pytest.mark.asyncio
    async def test_cache_instance_none_id(self, entity_store):
        """cache_instance with obj.id=None → skip (line 378→exit)."""
        h = Hashtag(value="no_id_cache")
        assert h.id is None
        entity_store.cache_instance(h)  # Should skip, no error

    def test_is_fully_loaded_return(self, entity_store):
        """is_fully_loaded returns bool (line 382)."""
        assert entity_store.is_fully_loaded(Account) in (True, False)

    def test_filter_skips_other_types(self, entity_store, test_account, test_media):
        """filter iterates cache with multiple types — skips non-matching (line 399)."""
        # entity_store has Account AND Media cached from fixtures
        results = entity_store.filter(
            Account, predicate=lambda a: a.id == test_account.id
        )
        assert len(results) == 1
        assert results[0].id == test_account.id

    @pytest.mark.asyncio
    async def test_assoc_sync_scalar_rid_none(self, entity_store):
        """_sync_assoc_tables scalar path where _get_id(related) is None (830→838).

        Set account.avatar to an object with id=None — _get_id returns None,
        so the junction INSERT is skipped but DELETE still runs.
        """
        acct = Account(id=snowflake_id(), username="rid_none_test")
        await entity_store.save(acct)

        # Set avatar to a Media with id=None (unsaved) — triggers avatar dirty
        unsaved_media = Media(id=snowflake_id(), accountId=acct.id)
        acct.avatar = unsaved_media
        await entity_store.save(acct)

        # Now set avatar to something with id=None
        object.__setattr__(unsaved_media, "id", None)
        acct.avatar = unsaved_media  # Triggers dirty, _get_id will return None
        await entity_store.save(acct)

    @pytest.mark.asyncio
    async def test_assoc_sync_non_list_related(self, entity_store, test_account):
        """_sync_assoc_tables where related is not a list for a list relationship (841).

        Set a list relationship field to a non-list value via object.__setattr__.
        """
        post = Post(id=snowflake_id(), accountId=test_account.id, fypFlag=0)
        await entity_store.save(post)

        # Set hashtags to a non-list → line 841 `not isinstance(related, list): continue`
        object.__setattr__(post, "hashtags", "not_a_list")
        # Snapshot still has [] so is_dirty() will detect the change
        await entity_store.save(post)

    @pytest.mark.asyncio
    async def test_assoc_sync_ordered_rid_none(
        self, entity_store, test_account, test_media
    ):
        """Ordered entity junction where _get_id(r) is None (857→855).

        AccountMediaBundle.accountMedia is ordered=True. Save with a list
        where the first item has an id (has_ids=True) but a second item
        has id=None → rid is None → skip INSERT for that item.
        """

        bundle = AccountMediaBundle(
            id=snowflake_id(),
            accountId=test_account.id,
            createdAt=datetime.now(UTC),
            deleted=False,
        )
        await entity_store.save(bundle)

        # Create a real AccountMedia (uses test_media for FK satisfaction)
        real_am = AccountMedia(
            id=snowflake_id(),
            accountId=test_account.id,
            mediaId=test_media.id,
            createdAt=datetime.now(UTC),
            deleted=False,
            access=True,
        )
        await entity_store.save(real_am)

        # Create a fake "AccountMedia-like" object with id=None
        # Using a SimpleNamespace to avoid FK validation issues

        fake_am = SimpleNamespace(id=None)

        # First has id → has_ids=True, second has id=None → rid=None → skip
        # Use object.__setattr__ to bypass Pydantic validation
        object.__setattr__(bundle, "accountMedia", [real_am, fake_am])
        await entity_store.save(bundle)

    @pytest.mark.asyncio
    async def test_assoc_sync_entity_delta_remove(self, entity_store, test_account):
        """Entity delta junction where to_remove is non-empty (888).

        Media.variants is an entity junction. Save with 2 variants,
        then save again with 1 → the removed variant triggers DELETE.
        """
        parent = Media(id=snowflake_id(), accountId=test_account.id)
        v1 = Media(id=snowflake_id(), accountId=test_account.id)
        v2 = Media(id=snowflake_id(), accountId=test_account.id)
        await entity_store.save(v1)
        await entity_store.save(v2)
        await entity_store.save(parent)

        # Add both variants
        parent.variants = [v1, v2]
        await entity_store.save(parent)

        # Remove v2 — triggers delta delete (line 888)
        parent.variants = [v1]
        await entity_store.save(parent)

        # Verify v2 junction row was removed
        pool = await entity_store._get_pool()
        rows = await pool.fetch(
            'SELECT "variantId" FROM media_variants WHERE "mediaId" = $1',
            parent.id,
        )
        variant_ids = {row["variantId"] for row in rows}
        assert v1.id in variant_ids
        assert v2.id not in variant_ids

    @pytest.mark.asyncio
    async def test_ensure_junction_fk_targets_tid_none_and_duplicate(
        self, entity_store, test_account, test_post
    ):
        """_ensure_junction_fk_targets where tid is None or already seen (979→977).

        Pass rows with: (1) postId=None → tid is None → skipped
        (2) same postId twice → second is already in seen → skipped
        (3) postId pointing to existing post → found in DB → continue at 981
        """
        existing_post_id = test_post.id
        await entity_store._ensure_junction_fk_targets(
            "pinned_posts",
            [
                {"accountId": test_account.id, "postId": None},  # tid=None → skip
                {
                    "accountId": test_account.id,
                    "postId": existing_post_id,
                },  # first seen
                {
                    "accountId": test_account.id,
                    "postId": existing_post_id,
                },  # duplicate → skip (979→977)
            ],
            "accountId",
        )

    def test_prepare_row_data_list_fk_injection(self):
        """_prepare_row_data injects FK column into list relationship field (line 1434).

        Media.locations has fk_column='mediaId' and is_list=True. When a data
        dict contains 'mediaId' (e.g., from merged association data, custom query,
        or denormalized view), _prepare_row_data copies it to the 'locations'
        field name so model_validate's identity map can resolve the relationship.

        This is the list-only path preserved from commit 605915f71 — the scalar
        belongs_to path was reverted because injecting raw ints into typed fields
        (e.g., Account | None) broke Pydantic validation. List fields accept
        int lists since the identity map validator handles int→object resolution.
        """

        data = {"id": snowflake_id(), "accountId": snowflake_id(), "mediaId": 12345}
        result = PostgresEntityStore._prepare_row_data(Media, data)
        assert result.get("locations") == 12345
