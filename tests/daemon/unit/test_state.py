"""Unit tests for daemon.state — mark_creator_processed.

Uses a real EntityStore (PostgreSQL) and real Pydantic models.
No HTTP mocking needed — mark_creator_processed only touches the database.

Coverage targets:
  1. mark_creator_processed creates new MonitorState when none exists
  2. mark_creator_processed updates existing MonitorState.lastCheckedAt
  3. mark_creator_processed is a no-op when Account FK would be violated
     (row creation fails, but loop does not raise)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from daemon.state import mark_creator_processed
from metadata.models import MonitorState
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    MonitorStateFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config_wired(config, entity_store, fansly_api):
    """Config wired with a real FanslyApi and backed by the test entity_store.

    Both ``config`` and ``entity_store`` chain through ``uuid_test_db_factory``
    so they share the same underlying PostgreSQL database.
    entity_store must be listed before config_wired to ensure FanslyObject._store
    is set before the state functions call get_store().
    """
    config._api = fansly_api
    return config


# ---------------------------------------------------------------------------
# mark_creator_processed
# ---------------------------------------------------------------------------


class TestMarkCreatorProcessed:
    """Tests for mark_creator_processed()."""

    @pytest.mark.asyncio
    async def test_creates_new_monitor_state_when_none_exists(
        self, config_wired, entity_store
    ):
        """First call creates a MonitorState row with lastCheckedAt set to now."""
        account = AccountFactory.build()
        await entity_store.save(account)

        before = datetime.now(UTC)
        await mark_creator_processed(account.id)
        after = datetime.now(UTC)

        state = await entity_store.get(MonitorState, account.id)
        assert state is not None
        assert state.lastCheckedAt is not None
        assert before <= state.lastCheckedAt <= after
        assert isinstance(state.lastRunAt, datetime)
        assert isinstance(state.updatedAt, datetime)

    @pytest.mark.asyncio
    async def test_updates_existing_monitor_state_last_checked_at(
        self, config_wired, entity_store
    ):
        """Subsequent call updates lastCheckedAt to a more recent timestamp."""
        account = AccountFactory.build()
        await entity_store.save(account)

        old_time = datetime(2026, 1, 1, tzinfo=UTC)
        state = MonitorStateFactory.build(
            creatorId=account.id,
            lastCheckedAt=old_time,
        )
        await entity_store.save(state)

        before = datetime.now(UTC)
        await mark_creator_processed(account.id)
        after = datetime.now(UTC)

        entity_store.invalidate(MonitorState, account.id)
        reloaded = await entity_store.get(MonitorState, account.id)
        assert reloaded is not None
        assert reloaded.lastCheckedAt is not None
        assert before <= reloaded.lastCheckedAt <= after
        # Must be strictly newer than the old snapshot
        assert reloaded.lastCheckedAt > old_time

    @pytest.mark.asyncio
    async def test_noop_when_account_does_not_exist(self, config_wired, entity_store):
        """Unknown creator_id — FK constraint prevents row creation, no raise.

        The function must swallow the FK violation so the daemon loop stays
        running even if a creator_id has no corresponding Account row.
        """
        unknown_id = snowflake_id()
        # Should not raise — FK violation is caught internally
        await mark_creator_processed(unknown_id)
        # No MonitorState row should be present
        state = await entity_store.get(MonitorState, unknown_id)
        assert state is None

    @pytest.mark.asyncio
    async def test_account_load_exception_logs_warning_and_returns(
        self, config_wired, entity_store, monkeypatch, caplog
    ):
        """Lines 40-46: store.get(Account) raises → warning log + early return.

        Cache miss + DB exception path: the function must NOT propagate the
        exception (daemon loop would die), just log + skip this snapshot.
        """
        import logging as _logging

        caplog.set_level(_logging.WARNING)

        creator_id = snowflake_id()
        # Don't save the account — forces store.get_from_cache to miss and
        # trigger the await store.get(Account) path.

        # Patch the module-level get_store to return a wrapper that raises on get.
        from daemon.state import get_store as real_get_store

        real_store = real_get_store()

        class _RaisingStore:
            """Wrap real store; make Account.get raise."""

            def __init__(self, real):
                self._real = real

            def get_from_cache(self, model, _id):
                # Cache miss for Account so the await path fires.
                return None

            async def get(self, model, _id):
                if model.__name__ == "Account":
                    raise RuntimeError("simulated account load failure")
                return await self._real.get(model, _id)

            async def save(self, obj):
                return await self._real.save(obj)

            def __getattr__(self, name):
                return getattr(self._real, name)

        monkeypatch.setattr("daemon.state.get_store", lambda: _RaisingStore(real_store))

        # Must NOT raise.
        await mark_creator_processed(creator_id)

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "could not load Account" in m
            and str(creator_id) in m
            and "simulated account load failure" in m
            for m in warnings
        )

    @pytest.mark.asyncio
    async def test_save_exception_logs_warning_and_returns(
        self, config_wired, entity_store, monkeypatch, caplog
    ):
        """Lines 72-77: store.save(MonitorState) raises → warning + return.

        Account exists, but the MonitorState save fails. The function must
        catch the exception so the daemon loop stays running.
        """
        import logging as _logging

        caplog.set_level(_logging.WARNING)

        # Real account so account-load succeeds.
        account = AccountFactory.build()
        await entity_store.save(account)

        from daemon.state import get_store as real_get_store

        real_store = real_get_store()

        class _SaveRaisesStore:
            """Wrap real store; make MonitorState save raise."""

            def __init__(self, real):
                self._real = real

            def get_from_cache(self, model, key):
                return self._real.get_from_cache(model, key)

            async def get(self, model, key):
                return await self._real.get(model, key)

            async def save(self, obj):
                if obj.__class__.__name__ == "MonitorState":
                    raise RuntimeError("simulated save failure")
                return await self._real.save(obj)

            def __getattr__(self, name):
                return getattr(self._real, name)

        monkeypatch.setattr(
            "daemon.state.get_store", lambda: _SaveRaisesStore(real_store)
        )

        # Must NOT raise.
        await mark_creator_processed(account.id)

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "could not save MonitorState" in m and "simulated save failure" in m
            for m in warnings
        )
