"""Tests for Database async lifecycle — create_entity_store, cleanup, close_sync, entity_store property.

These tests use real PostgreSQL via uuid_test_db_factory for isolation.
"""

from unittest.mock import patch

import pytest

from metadata.database import Database
from metadata.entity_store import PostgresEntityStore
from metadata.models import Account, FanslyObject
from metadata.tables import metadata as table_metadata
from tests.fixtures.utils.test_isolation import snowflake_id


class TestCreateEntityStore:
    """Tests for Database.create_entity_store() — pool creation + preload."""

    @pytest.mark.asyncio
    async def test_creates_store(self, config):
        """create_entity_store should return a working PostgresEntityStore."""
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        try:
            store = await db.create_entity_store()
            assert isinstance(store, PostgresEntityStore)
            assert db._asyncpg_pool is not None
        finally:
            FanslyObject._store = None
            if db._asyncpg_pool:
                await db._asyncpg_pool.close()
            db.close_sync()

    @pytest.mark.asyncio
    async def test_idempotent(self, config):
        """Calling create_entity_store twice should return the same instance."""
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        try:
            store1 = await db.create_entity_store()
            store2 = await db.create_entity_store()
            assert store1 is store2
        finally:
            FanslyObject._store = None
            if db._asyncpg_pool:
                await db._asyncpg_pool.close()
            db.close_sync()

    @pytest.mark.asyncio
    async def test_store_is_functional(self, config):
        """The created store should support save/get operations."""
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        try:
            store = await db.create_entity_store()

            account = Account(id=snowflake_id(), username="lifecycle_test")
            await store.save(account)
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

    @pytest.mark.asyncio
    async def test_returns_store_after_init(self, config):
        """After create_entity_store, property should return the store."""
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        try:
            await db.create_entity_store()
            store = db.entity_store
            assert isinstance(store, PostgresEntityStore)
        finally:
            FanslyObject._store = None
            if db._asyncpg_pool:
                await db._asyncpg_pool.close()
            db.close_sync()


class TestCleanup:
    """Tests for Database.cleanup() — async teardown."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_pool(self, config):
        """cleanup should close the asyncpg pool and entity store."""
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        await db.create_entity_store()

        await db.cleanup()

        assert db._cleanup_done.is_set()
        FanslyObject._store = None

    @pytest.mark.asyncio
    async def test_cleanup_is_idempotent(self, config):
        """Calling cleanup twice should not error."""
        db = Database(config, skip_migrations=True)
        table_metadata.create_all(db._sync_engine)
        await db.create_entity_store()

        await db.cleanup()
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

        db._asyncpg_pool = _FailingPool()

        await db.cleanup()

        assert db._cleanup_done.is_set()
        FanslyObject._store = None
        # Clean up the real pool
        if real_pool and not real_pool._closed:
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
        if db._asyncpg_pool and not db._asyncpg_pool._closed:
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
        if db._asyncpg_pool and not db._asyncpg_pool._closed:
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
