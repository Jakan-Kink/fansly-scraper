"""Unit tests for MonitorState model — daemon per-creator persistence.

Tests cover:
- Factory produces a valid instance with defaults
- EntityStore save round-trips MonitorState to PostgreSQL
- FK constraint: saving MonitorState without Account fails
- Identity map dedup: loading by PK returns the same object
- Dirty tracking: mutating lastHasActiveStories marks field dirty
- lastCheckedAt auto-coerces from int timestamp
"""

from datetime import UTC, datetime

import pytest
from asyncpg import ForeignKeyViolationError

from metadata.models import MonitorState
from tests.fixtures.metadata.metadata_factories import (
    AccountFactory,
    MonitorStateFactory,
)


class TestMonitorStateFactory:
    """Test 1: Factory produces a valid instance with defaults."""

    def test_factory_produces_valid_instance(self):
        """MonitorStateFactory creates a MonitorState with sensible defaults."""
        state = MonitorStateFactory.build()

        assert isinstance(state, MonitorState)
        assert isinstance(state.creatorId, int)
        assert state.creatorId > 0
        # Optional fields default to None
        assert state.lastHasActiveStories is None
        assert state.lastCheckedAt is None
        assert state.lastRunAt is None
        # updatedAt has a value (set by factory)
        assert isinstance(state.updatedAt, datetime)
        # id mirrors creatorId (set by _set_id_from_pk validator)
        assert state.id == state.creatorId

    def test_factory_accepts_overrides(self):
        """Factory fields can be overridden at creation time."""
        creator_id = AccountFactory.build().id
        state = MonitorStateFactory.build(
            creatorId=creator_id,
            lastHasActiveStories=True,
        )

        assert state.creatorId == creator_id
        assert state.lastHasActiveStories is True


class TestMonitorStateEntityStore:
    """Test 2: EntityStore save round-trips MonitorState to PostgreSQL."""

    @pytest.mark.asyncio
    async def test_save_and_retrieve(self, entity_store):
        """Saving MonitorState with a valid Account FK persists and retrieves correctly."""
        account = AccountFactory.build()
        await entity_store.save(account)

        state = MonitorStateFactory.build(creatorId=account.id)
        state.lastHasActiveStories = False
        await entity_store.save(state)

        loaded = await entity_store.get(MonitorState, account.id)
        assert loaded is not None
        assert loaded.creatorId == account.id
        assert loaded.lastHasActiveStories is False
        assert isinstance(loaded.updatedAt, datetime)

    @pytest.mark.asyncio
    async def test_save_and_retrieve_nullable_fields(self, entity_store):
        """NULL fields persist and retrieve as None."""
        account = AccountFactory.build()
        await entity_store.save(account)

        state = MonitorStateFactory.build(creatorId=account.id)
        await entity_store.save(state)

        loaded = await entity_store.get(MonitorState, account.id)
        assert loaded is not None
        assert loaded.lastHasActiveStories is None
        assert loaded.lastCheckedAt is None
        assert loaded.lastRunAt is None


class TestMonitorStateForeignKey:
    """Test 3: FK constraint — saving without a parent Account raises an error."""

    @pytest.mark.asyncio
    async def test_save_without_account_raises_fk_error(self, entity_store):
        """Inserting MonitorState without a matching Account violates FK constraint."""
        # Use a creator_id that definitely has no Account row
        orphan_creator_id = 100_000_000_000_000_042

        state = MonitorState(
            creatorId=orphan_creator_id,
            updatedAt=datetime.now(UTC),
        )

        # Expect specifically asyncpg's ForeignKeyViolationError, not a
        # loose Exception fallback — a broader catch would let unrelated
        # errors (e.g. connection drops) silently satisfy the assertion.
        with pytest.raises(ForeignKeyViolationError):
            await entity_store.save(state)


class TestMonitorStateIdentityMap:
    """Test 4: Identity map dedup — loading by PK returns the same object."""

    @pytest.mark.asyncio
    async def test_identity_map_dedup(self, entity_store):
        """get() with same PK returns the cached instance, not a new one."""
        account = AccountFactory.build()
        await entity_store.save(account)

        state = MonitorStateFactory.build(creatorId=account.id)
        await entity_store.save(state)

        first = await entity_store.get(MonitorState, account.id)
        second = await entity_store.get(MonitorState, account.id)

        # Both should be the same Python object (identity map)
        assert first is second
        assert first.creatorId == account.id


class TestMonitorStateDirtyTracking:
    """Test 5: Dirty tracking — mutating lastHasActiveStories marks field dirty."""

    @pytest.mark.asyncio
    async def test_mutation_marks_dirty_and_updates(self, entity_store):
        """Changing lastHasActiveStories marks object dirty; next save does UPDATE."""
        account = AccountFactory.build()
        await entity_store.save(account)

        state = MonitorStateFactory.build(
            creatorId=account.id, lastHasActiveStories=False
        )
        await entity_store.save(state)

        # Object should be clean after save
        assert not state.is_dirty()

        # Mutate a tracked field
        state.lastHasActiveStories = True
        assert state.is_dirty()

        changed = state.get_changed_fields()
        assert "lastHasActiveStories" in changed
        assert changed["lastHasActiveStories"] is True

    @pytest.mark.asyncio
    async def test_update_persists(self, entity_store):
        """After marking dirty and saving, the updated value persists to DB."""
        account = AccountFactory.build()
        await entity_store.save(account)

        state = MonitorStateFactory.build(
            creatorId=account.id, lastHasActiveStories=False
        )
        await entity_store.save(state)

        state.lastHasActiveStories = True
        await entity_store.save(state)

        # Evict from cache and reload from DB
        entity_store.invalidate(MonitorState, account.id)
        reloaded = await entity_store.get(MonitorState, account.id)
        assert reloaded is not None
        assert reloaded.lastHasActiveStories is True


class TestMonitorStateTimestampCoercion:
    """Test 6: lastCheckedAt auto-coerces from int timestamp."""

    def test_int_milliseconds_coerced_to_datetime(self):
        """lastCheckedAt coerces ms int → datetime via BeforeValidator.

        Anchor is the exact year+month so the test distinguishes the ms
        branch (divide by 1000) from the seconds branch. A weak
        ``year > 2020`` assertion would pass for either interpretation
        as long as both branches land after 2020.
        """
        ms_timestamp = 1_776_270_684_000  # milliseconds since epoch → 2026-04-16 UTC

        state = MonitorState(
            creatorId=100_000_000_000_000_001,
            lastCheckedAt=ms_timestamp,
            updatedAt=datetime.now(UTC),
        )

        assert isinstance(state.lastCheckedAt, datetime)
        assert state.lastCheckedAt.year == 2026
        assert state.lastCheckedAt.month == 4

    def test_int_seconds_coerced_to_datetime(self):
        """lastCheckedAt coerces seconds int → datetime correctly.

        Tight year+month assertion — proves the seconds heuristic
        (``v < 1e10`` → treat as seconds) picks this path correctly
        rather than accidentally dividing by 1000.
        """
        sec_timestamp = 1_776_270_684  # seconds since epoch → 2026-04-16 UTC

        state = MonitorState(
            creatorId=100_000_000_000_000_002,
            lastCheckedAt=sec_timestamp,
            updatedAt=datetime.now(UTC),
        )

        assert isinstance(state.lastCheckedAt, datetime)
        assert state.lastCheckedAt.year == 2026
        assert state.lastCheckedAt.month == 4

    def test_none_passthrough(self):
        """None is accepted and not coerced."""
        state = MonitorState(
            creatorId=100_000_000_000_000_003,
            lastCheckedAt=None,
            updatedAt=datetime.now(UTC),
        )
        assert state.lastCheckedAt is None

    def test_datetime_passthrough(self):
        """datetime object is accepted as-is."""
        now = datetime.now(UTC)

        state = MonitorState(
            creatorId=100_000_000_000_000_004,
            lastCheckedAt=now,
            updatedAt=datetime.now(UTC),
        )
        assert state.lastCheckedAt == now

    def test_updated_at_also_coerces(self):
        """updatedAt field (ends in At) coerces via _coerce_api_types."""
        ms_timestamp = 1_776_270_684_000

        state = MonitorState(
            creatorId=100_000_000_000_000_005,
            updatedAt=ms_timestamp,
        )

        assert isinstance(state.updatedAt, datetime)
        assert state.updatedAt.year > 2020


class TestMonitorStateTableName:
    """Verify table name and PK column are correctly set."""

    def test_table_name(self):
        """MonitorState maps to the monitor_state table."""
        assert MonitorState.__table_name__ == "monitor_state"

    def test_pk_column(self):
        """PK column is creatorId, not id."""
        assert MonitorState.__pk_column__ == "creatorId"

    def test_write_excluded_has_id(self):
        """id is excluded from DB writes (synthetic PK copy)."""
        assert "id" in MonitorState._WRITE_EXCLUDED
