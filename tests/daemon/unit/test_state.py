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
