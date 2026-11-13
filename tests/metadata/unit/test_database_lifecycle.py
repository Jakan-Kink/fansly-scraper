"""Tests for Database async lifecycle — create_entity_store, cleanup, close_sync, entity_store property.

These tests use real PostgreSQL via uuid_test_db_factory for isolation.
"""

import gc
import weakref
from collections.abc import Callable
from unittest.mock import patch

import pytest

from metadata.database import Database
from metadata.entity_store import PostgresEntityStore
from metadata.models import Account, FanslyObject, Wall
from metadata.tables import metadata as table_metadata
from tests.fixtures.stash import PerformerFactory, SceneFactory
from tests.fixtures.utils.test_isolation import snowflake_id


class TestCreateEntityStore:
    """Tests for Database.create_entity_store() — pool creation + preload."""

    @pytest.mark.asyncio
    async def test_store_lifecycle(self, config):
        """Walk the full create_entity_store lifecycle on a single DB:

        create → returns PostgresEntityStore with pool set, idempotent on
        re-call, exposed via the entity_store property, and functional for
        save/get round-trips.
        """
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        try:
            # create_entity_store returns a working store + sets the pool
            store = await db.create_entity_store()
            assert isinstance(store, PostgresEntityStore)
            assert db._asyncpg_pool is not None

            # Idempotent: a second call returns the same instance
            store2 = await db.create_entity_store()
            assert store2 is store

            # entity_store property returns that same store
            assert db.entity_store is store
            assert isinstance(db.entity_store, PostgresEntityStore)

            # Store is functional: save + get round-trips
            account = Account(id=snowflake_id(), username="lifecycle_test")
            await store.save(account)
            assert isinstance(account.id, int)
            found = await store.get(Account, account.id)
            assert found is not None
            assert found.username == "lifecycle_test"
        finally:
            FanslyObject._store = None
            if db._asyncpg_pool:
                await db._asyncpg_pool.close()
            db.close_sync()


class TestEntityStoreProperty:
    """Tests for Database.entity_store property."""

    def test_raises_before_init(self, mock_config):
        """Accessing entity_store before create_entity_store should raise."""
        db = Database(mock_config, skip_migrations=True)
        with pytest.raises(RuntimeError, match="EntityStore not initialized"):
            _ = db.entity_store


class TestCleanup:
    """Tests for Database.cleanup() — async teardown."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_pool_and_is_idempotent(self, config):
        """cleanup closes the pool/store and a second call is a safe no-op."""
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        await db.create_entity_store()

        await db.cleanup()
        assert db._cleanup_done.is_set()

        await db.cleanup()  # Second call should be a no-op
        assert db._cleanup_done.is_set()

        FanslyObject._store = None

    @pytest.mark.asyncio
    async def test_cleanup_without_entity_store(self, config):
        """cleanup should handle case where entity store was never created."""
        db = Database(config, skip_migrations=True)

        await db.cleanup()

        assert db._cleanup_done.is_set()


class TestCloseSync:
    """Tests for Database.close_sync() — synchronous teardown for atexit."""

    def test_close_sync_disposes_engine(self, mock_config):
        """close_sync should dispose the sync engine."""
        db = Database(mock_config, skip_migrations=True)
        db.close_sync()
        assert db._cleanup_done.is_set()

    def test_close_sync_is_idempotent(self, mock_config):
        """Calling close_sync twice should not error."""
        db = Database(mock_config, skip_migrations=True)
        db.close_sync()
        db.close_sync()
        assert db._cleanup_done.is_set()


class TestCleanupErrorPaths:
    """Tests for error handling during cleanup, close_sync, _run_migrations, __del__.

    Uses patch on upstream calls (alembic_upgrade, pool.close, engine.dispose,
    entity_store.close) to raise exceptions — verifying the except blocks
    catch them and cleanup still completes.
    """

    def test_run_migrations_catches_and_reraises(self, config):
        """Lines 203-205: _run_migrations exception → logs error, re-raises.
        Uses real config (real DB) so _sync_engine.begin() succeeds,
        then alembic_upgrade is patched to raise."""
        db = Database(config, skip_migrations=True)

        with (
            patch(
                "metadata.database.alembic_upgrade",
                side_effect=RuntimeError("migration boom"),
            ),
            pytest.raises(RuntimeError, match="migration boom"),
        ):
            db._run_migrations()

    @pytest.mark.asyncio
    async def test_cleanup_entity_store_close_error(self, config):
        """Lines 227-228: entity_store.close() raises → caught, cleanup continues."""
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        await db.create_entity_store()

        with patch.object(
            db._entity_store, "close", side_effect=OSError("store close boom")
        ):
            await db.cleanup()

        assert db._cleanup_done.is_set()
        FanslyObject._store = None

    @pytest.mark.asyncio
    async def test_cleanup_asyncpg_pool_close_error(self, config):
        """Lines 235-236: asyncpg_pool.close() raises → caught, cleanup continues.
        asyncpg.Pool.close is a read-only C attribute — can't patch it.
        Instead, replace _asyncpg_pool with an object whose close() raises."""
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        await db.create_entity_store()

        real_pool = db._asyncpg_pool

        class _FailingPool:
            async def close(self):
                raise OSError("pool close boom")

        db._asyncpg_pool = _FailingPool()  # type: ignore[assignment]  # failure-injection double, not a real Pool

        await db.cleanup()

        assert db._cleanup_done.is_set()
        FanslyObject._store = None
        # Clean up the real pool
        if real_pool and not real_pool.is_closing():
            await real_pool.close()

    @pytest.mark.asyncio
    async def test_cleanup_sync_engine_dispose_error(self, config):
        """Lines 239→249, 243-247: sync_engine.dispose() raises → caught, cleanup continues."""
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        await db.create_entity_store()

        with patch.object(
            db._sync_engine, "dispose", side_effect=OSError("dispose boom")
        ):
            await db.cleanup()

        assert db._cleanup_done.is_set()
        FanslyObject._store = None
        # Clean up the real pool since dispose was patched
        if db._asyncpg_pool and not db._asyncpg_pool.is_closing():
            await db._asyncpg_pool.close()

    @pytest.mark.asyncio
    async def test_cleanup_toctou_inner_guard(self, config):
        """Line 217: cleanup enters lock but flag already set (TOCTOU race).
        Spy on is_set: first call (line 212) → False, second call (line 217) → True,
        simulating another thread completing cleanup between the two checks."""
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        await db.create_entity_store()

        call_count = 0
        original_is_set = db._cleanup_done.is_set

        def spy_is_set():
            nonlocal call_count
            call_count += 1
            return (
                call_count != 1
            )  # Line 212: False passes outer check; Line 217: True triggers early return

        with patch.object(db._cleanup_done, "is_set", side_effect=spy_is_set):
            await db.cleanup()

        assert call_count == 2  # Both checks were hit
        FanslyObject._store = None
        # Clean up manually since cleanup bailed at 217
        if db._asyncpg_pool and not db._asyncpg_pool.is_closing():
            await db._asyncpg_pool.close()
        db._sync_engine.dispose()

    def test_close_sync_toctou_inner_guard(self, mock_config):
        """Line 259: close_sync enters lock but flag already set (TOCTOU race).
        Same spy pattern: first is_set → False, second → True."""
        db = Database(mock_config, skip_migrations=True)

        call_count = 0

        def spy_is_set():
            nonlocal call_count
            call_count += 1
            return (
                call_count != 1
            )  # Line 254: False passes outer check; Line 259: True triggers early return

        with patch.object(db._cleanup_done, "is_set", side_effect=spy_is_set):
            db.close_sync()

        assert call_count == 2

    def test_close_sync_dispose_error(self, mock_config):
        """Lines 262→267, 264-265: sync_engine.dispose() raises in close_sync."""
        db = Database(mock_config, skip_migrations=True)

        with patch.object(
            db._sync_engine, "dispose", side_effect=RuntimeError("dispose boom")
        ):
            db.close_sync()

        assert db._cleanup_done.is_set()

    def test_del_calls_close_sync(self, mock_config):
        """Lines 270-271: __del__ calls close_sync with suppress(Exception)."""
        db = Database(mock_config, skip_migrations=True)
        db.__del__()
        assert db._cleanup_done.is_set()

        # Safe to call again
        db.__del__()


class TestFreezeHeap:
    """Database.freeze_heap() and the unfreeze its cleanup performs."""

    @pytest.mark.asyncio
    async def test_freeze_exempts_preload_without_pinning_it(self, config):
        """Entities alive at the freeze skip collection but still die by refcount.

        FDNG entities frozen with the store are released as soon as their last
        reference goes, unless they sit in a reference cycle, which waits for
        the unfreeze. Entities built after the freeze, FDNG or SGC, stay with
        the collector.
        """
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        try:
            store = await db.create_entity_store()
            loner_id, owner_id, wall_id = snowflake_id(), snowflake_id(), snowflake_id()
            late_owner_id, late_wall_id = snowflake_id(), snowflake_id()
            loner = Account(id=loner_id, username="freeze_loner")
            owner = Account(id=owner_id, username="freeze_owner")
            await store.save(loner)
            await store.save(owner)
            wall = Wall(id=wall_id, accountId=owner_id)
            wall.account = owner
            owner.walls = [wall]
            await store.save(wall)
            loner_ref, owner_ref, wall_ref = (
                weakref.ref(loner),
                weakref.ref(owner),
                weakref.ref(wall),
            )

            db.freeze_heap()
            frozen_count = gc.get_freeze_count()

            # Frozen, in no cycle: gone with its last reference, collector off.
            store.invalidate(Account, loner_id)
            gc.disable()
            try:
                del loner
                loner_alive_without_collection = loner_ref() is not None
            finally:
                gc.enable()

            # Frozen and in a cycle: unreachable, but out of the collector's reach.
            store.invalidate(Account, owner_id)
            store.invalidate(Wall, wall_id)
            del owner, wall
            gc.collect()
            frozen_cycle_alive = owner_ref() is not None and wall_ref() is not None

            # Built after the freeze, FDNG store: an ordinary collectable cycle.
            late_owner = Account(id=late_owner_id, username="late_owner")
            late_wall = Wall(id=late_wall_id, accountId=late_owner_id)
            late_wall.account = late_owner
            late_owner.walls = [late_wall]
            late_refs: list[Callable[[], object]] = [
                weakref.ref(late_owner),
                weakref.ref(late_wall),
            ]
            store.invalidate(Account, late_owner_id)
            store.invalidate(Wall, late_wall_id)
            del late_owner, late_wall

            # Built after the freeze, SGC types: the same.
            performer = PerformerFactory.build()
            scene = SceneFactory.build()
            await scene.add_performer(performer)
            late_refs += [weakref.ref(performer), weakref.ref(scene)]
            del performer, scene

            late_alive_before_collect = [ref() is not None for ref in late_refs]
            gc.collect()
            late_alive_after_collect = [ref() is not None for ref in late_refs]
        finally:
            await db.cleanup()

        assert frozen_count > 0
        assert loner_alive_without_collection is False
        assert frozen_cycle_alive is True
        assert late_alive_before_collect == [True, True, True, True]
        assert late_alive_after_collect == [False, False, False, False]

        assert gc.get_freeze_count() == 0
        gc.collect()
        assert owner_ref() is None
        assert wall_ref() is None

    @pytest.mark.asyncio
    async def test_unload_frees_frozen_cycles_itself(self, config):
        """unload() unfreezes, empties the store and collects the cycles in it."""
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        try:
            store = await db.create_entity_store()
            owner_id, wall_id = snowflake_id(), snowflake_id()
            owner = Account(id=owner_id, username="unload_owner")
            await store.save(owner)
            wall = Wall(id=wall_id, accountId=owner_id)
            wall.account = owner
            owner.walls = [wall]
            await store.save(wall)
            owner_ref, wall_ref = weakref.ref(owner), weakref.ref(wall)
            del owner, wall

            db.freeze_heap()
            cached_before = store.cache_stats()["total"]

            # Collector off: only unload's own collection can free the cycle.
            gc.disable()
            try:
                db.unload()
                alive_after_unload = [owner_ref() is not None, wall_ref() is not None]
            finally:
                gc.enable()
            frozen_after = gc.get_freeze_count()
            cached_after = store.cache_stats()["total"]
        finally:
            await db.cleanup()

        assert cached_before == 2
        assert cached_after == 0
        assert frozen_after == 0
        assert alive_after_unload == [False, False]

    def test_unload_without_entity_store(self, mock_config):
        """unload() works on a database whose store was never created."""
        db = Database(mock_config, skip_migrations=True)
        try:
            db.unload()
        finally:
            db.close_sync()
        assert db._entity_store is None
