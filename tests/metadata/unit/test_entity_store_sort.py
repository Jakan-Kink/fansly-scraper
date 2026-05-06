"""Tests for entity store sort/ordering functionality.

Tests SortDirection enum, _normalize_order_by, _sort_results (cache path),
and ORDER BY generation in _query_with_filters (SQL path — via integration).
"""

from datetime import UTC, datetime

import pytest

from metadata.entity_store import (
    PostgresEntityStore,
    SortDirection,
    _normalize_order_by,
)
from metadata.models import Account, Media
from tests.fixtures.utils.test_isolation import snowflake_id


# ── SortDirection Enum Tests ────────────────────────────────────────────


class TestSortDirection:
    def test_asc_value(self):
        assert SortDirection.ASC.value == "ASC"

    def test_desc_value(self):
        assert SortDirection.DESC.value == "DESC"


# ── _normalize_order_by Tests ───────────────────────────────────────────


class TestNormalizeOrderBy:
    def test_none_returns_empty(self):
        assert _normalize_order_by(None) == []

    def test_string_returns_asc_default(self):
        result = _normalize_order_by("createdAt")
        assert result == [("createdAt", SortDirection.ASC)]

    def test_tuple_returns_as_list(self):
        result = _normalize_order_by(("createdAt", SortDirection.DESC))
        assert result == [("createdAt", SortDirection.DESC)]

    def test_list_passes_through(self):
        spec = [("createdAt", SortDirection.DESC), ("id", SortDirection.ASC)]
        assert _normalize_order_by(spec) == spec

    def test_invalid_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid order_by"):
            _normalize_order_by(42)  # type: ignore[arg-type]


# ── _sort_results Tests (Cache Path) ───────────────────────────────────


class TestSortResults:
    """Tests for Python-side sorting used when model type is fully loaded."""

    @pytest.fixture
    def media_items(self):
        """Create 3 Media items with different IDs and timestamps."""
        base_id = 1000000000000000000
        m1 = Media(id=base_id + 1, accountId=base_id + 100)
        m1.createdAt = datetime(2024, 1, 15, tzinfo=UTC)
        m1.mimetype = "image/jpeg"

        m2 = Media(id=base_id + 2, accountId=base_id + 100)
        m2.createdAt = datetime(2024, 6, 1, tzinfo=UTC)
        m2.mimetype = "video/mp4"

        m3 = Media(id=base_id + 3, accountId=base_id + 100)
        m3.createdAt = datetime(2024, 3, 10, tzinfo=UTC)
        m3.mimetype = "image/jpeg"

        return m1, m2, m3

    def test_sort_by_single_column_asc(self, media_items):
        m1, m2, m3 = media_items
        result = PostgresEntityStore._sort_results(
            [m3, m1, m2], [("createdAt", SortDirection.ASC)]
        )
        assert result == [m1, m3, m2]  # Jan, Mar, Jun

    def test_sort_by_single_column_desc(self, media_items):
        m1, m2, m3 = media_items
        result = PostgresEntityStore._sort_results(
            [m3, m1, m2], [("createdAt", SortDirection.DESC)]
        )
        assert result == [m2, m3, m1]  # Jun, Mar, Jan

    def test_sort_by_id(self, media_items):
        m1, m2, m3 = media_items
        result = PostgresEntityStore._sort_results(
            [m2, m3, m1], [("id", SortDirection.ASC)]
        )
        assert result == [m1, m2, m3]

    def test_multi_column_sort(self, media_items):
        m1, m2, m3 = media_items
        # m1 and m3 have same mimetype (image/jpeg), m2 has video/mp4
        # Sort by mimetype ASC, then createdAt DESC
        result = PostgresEntityStore._sort_results(
            [m2, m3, m1],
            [("mimetype", SortDirection.ASC), ("createdAt", SortDirection.DESC)],
        )
        # image/jpeg comes first: m3 (Mar) before m1 (Jan) — DESC within group
        # then video/mp4: m2
        assert result == [m3, m1, m2]

    def test_sort_with_none_values_last_asc(self, media_items):
        m1, m2, m3 = media_items
        m2.createdAt = None  # Set one to None
        result = PostgresEntityStore._sort_results(
            [m2, m3, m1], [("createdAt", SortDirection.ASC)]
        )
        # None should sort last in ASC
        assert result[-1] is m2
        assert result[0] is m1  # Jan

    def test_sort_empty_list(self):
        result = PostgresEntityStore._sort_results([], [("id", SortDirection.ASC)])
        assert result == []

    def test_sort_no_spec(self, media_items):
        m1, m2, m3 = media_items
        original = [m3, m1, m2]
        result = PostgresEntityStore._sort_results(original, [])
        assert result == original  # Unchanged


# ── Integration: find() with order_by ───────────────────────────────────


class TestFindWithOrderBy:
    """Integration tests for find() with order_by using a real database."""

    @pytest.mark.asyncio
    async def test_find_with_order_by_asc(self, entity_store):
        store = entity_store
        acct_id = snowflake_id()
        account = Account(id=acct_id, username="sort_test")
        await store.save(account)

        ids = [snowflake_id() for _ in range(3)]
        for i, mid in enumerate(ids):
            m = Media(id=mid, accountId=acct_id)
            m.createdAt = datetime(2024, i + 1, 1, tzinfo=UTC)
            await store.save(m)

        results = await store.find(
            Media,
            order_by="createdAt",
            accountId=acct_id,
        )
        timestamps = [m.createdAt for m in results]
        assert timestamps == sorted(timestamps)

    @pytest.mark.asyncio
    async def test_find_with_order_by_desc(self, entity_store):
        store = entity_store
        acct_id = snowflake_id()
        account = Account(id=acct_id, username="sort_test_desc")
        await store.save(account)

        ids = [snowflake_id() for _ in range(3)]
        for i, mid in enumerate(ids):
            m = Media(id=mid, accountId=acct_id)
            m.createdAt = datetime(2024, i + 1, 1, tzinfo=UTC)
            await store.save(m)

        results = await store.find(
            Media,
            order_by=("createdAt", SortDirection.DESC),
            accountId=acct_id,
        )
        timestamps = [m.createdAt for m in results]
        assert timestamps == sorted(timestamps, reverse=True)

    @pytest.mark.asyncio
    async def test_find_one_with_order_by(self, entity_store):
        store = entity_store
        acct_id = snowflake_id()
        account = Account(id=acct_id, username="sort_test_one")
        await store.save(account)

        for i in range(3):
            m = Media(id=snowflake_id(), accountId=acct_id)
            m.createdAt = datetime(2024, i + 1, 1, tzinfo=UTC)
            await store.save(m)

        # Get the oldest
        oldest = await store.find_one(
            Media,
            order_by="createdAt",
            accountId=acct_id,
        )
        assert oldest is not None
        assert oldest.createdAt.month == 1

        # Get the newest
        newest = await store.find_one(
            Media,
            order_by=("createdAt", SortDirection.DESC),
            accountId=acct_id,
        )
        assert newest is not None
        assert newest.createdAt.month == 3

    @pytest.mark.asyncio
    async def test_invalid_column_raises_error(self, entity_store):
        store = entity_store
        with pytest.raises(ValueError, match="Invalid order_by column"):
            await store.find(
                Media,
                order_by="nonexistent_column",
                accountId=snowflake_id(),
            )
