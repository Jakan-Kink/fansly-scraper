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
from tests.fixtures.utils.test_isolation import snowflake_id


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


@pytest.mark.xdist_group("monitor_state")
class TestMonitorStatePersistence:
    """Deep round-trip on ONE shared store + state object.

    Chains the facets the five shallow tests covered individually:
    round-trip, nullable-field NULL handling, identity-map dedup, dirty
    tracking on mutation, and persistence of the mutation after reload.
    Sharing a single ``entity_store`` (one UUID Postgres DB) replaces the
    five separate per-test databases the originals each spun up.
    """

    @pytest.mark.asyncio
    async def test_persistence_chain(self, entity_store):
        """Save Account + MonitorState once, then assert each facet in sequence."""
        account = AccountFactory.build()
        await entity_store.save(account)

        state = MonitorStateFactory.build(creatorId=account.id)
        state.lastHasActiveStories = False
        await entity_store.save(state)

        # Facet 1: round-trip — saved fields retrieve unchanged.
        loaded = await entity_store.get(MonitorState, account.id)
        assert loaded is not None
        assert loaded.creatorId == account.id
        assert loaded.lastHasActiveStories is False
        assert isinstance(loaded.updatedAt, datetime)

        # Facet 2: nullable fields — a state with no overrides round-trips
        # its optional columns as None. Distinct creator so the NULLs are
        # not shadowed by the primary state above.
        nullable_account = AccountFactory.build()
        await entity_store.save(nullable_account)
        nullable_state = MonitorStateFactory.build(creatorId=nullable_account.id)
        await entity_store.save(nullable_state)

        nullable_loaded = await entity_store.get(MonitorState, nullable_account.id)
        assert nullable_loaded is not None
        assert nullable_loaded.lastHasActiveStories is None
        assert nullable_loaded.lastCheckedAt is None
        assert nullable_loaded.lastRunAt is None

        # Facet 3: identity-map dedup — repeated get() by PK returns the
        # same Python object, not a fresh instance.
        first = await entity_store.get(MonitorState, account.id)
        second = await entity_store.get(MonitorState, account.id)
        assert first is second
        assert first.creatorId == account.id

        # Facet 4: dirty tracking — object is clean after save; mutating a
        # tracked field marks it dirty and surfaces the change.
        assert not first.is_dirty()
        first.lastHasActiveStories = True
        assert first.is_dirty()
        changed = first.get_changed_fields()
        assert "lastHasActiveStories" in changed
        assert changed["lastHasActiveStories"] is True

        # Facet 5: update persists — save the mutation, evict the cache,
        # reload from DB, and confirm the new value survived.
        await entity_store.save(first)
        entity_store.invalidate(MonitorState, account.id)
        reloaded = await entity_store.get(MonitorState, account.id)
        assert reloaded is not None
        assert reloaded.lastHasActiveStories is True


class TestMonitorStateForeignKey:
    """Test 3: FK constraint — saving without a parent Account raises an error."""

    @pytest.mark.asyncio
    async def test_save_without_account_raises_fk_error(self, entity_store):
        """Inserting MonitorState without a matching Account violates FK constraint."""
        # Unique per-run orphan id (never an Account row, never collides with a
        # MonitorState another test cached). A hardcoded id let a prior test's
        # same-id cached instance win the identity-map lookup below, returning it
        # with _is_new=False — then save() takes neither the INSERT nor the UPDATE
        # branch and silently no-ops, so no FK error fires. That was an
        # order-dependent isolation failure, not flakiness.
        orphan_creator_id = snowflake_id()

        state = MonitorState(
            creatorId=orphan_creator_id,
            updatedAt=datetime.now(UTC),
        )

        # Precondition: the orphan must take the INSERT path, or the FK check
        # never runs. Assert it explicitly so any residual cache pollution fails
        # loudly here instead of silently satisfying pytest.raises with a no-op.
        assert state._is_new is True

        # Expect specifically asyncpg's ForeignKeyViolationError, not a
        # loose Exception fallback — a broader catch would let unrelated
        # errors (e.g. connection drops) silently satisfy the assertion.
        with pytest.raises(ForeignKeyViolationError):
            await entity_store.save(state)


class TestMonitorStateTimestampCoercion:
    """Test 6: lastCheckedAt auto-coerces from int timestamp."""

    @pytest.mark.parametrize(
        ("timestamp", "creator_id"),
        [
            pytest.param(
                1_776_270_684_000,  # milliseconds since epoch → 2026-04-16 UTC
                100_000_000_000_000_001,
                id="int_milliseconds",
            ),
            pytest.param(
                1_776_270_684,  # seconds since epoch → 2026-04-16 UTC
                100_000_000_000_000_002,
                id="int_seconds",
            ),
        ],
    )
    def test_int_timestamp_coerced_to_datetime(
        self, timestamp: int, creator_id: int
    ) -> None:
        """lastCheckedAt coerces ms and seconds ints → datetime via BeforeValidator.

        Both rows encode the SAME instant (2026-04-16 UTC) in different units,
        and the anchor is the exact year+month, so each row distinguishes its
        branch of the heuristic (``v < 1e10`` → seconds, else divide by 1000).
        A weak ``year > 2020`` assertion would pass for either interpretation
        as long as both branches land after 2020.
        """
        state = MonitorState(
            creatorId=creator_id,
            lastCheckedAt=timestamp,  # type: ignore[arg-type]  # int→datetime coercion is the test
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
            updatedAt=ms_timestamp,  # type: ignore[arg-type]  # int→datetime coercion is the test
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
