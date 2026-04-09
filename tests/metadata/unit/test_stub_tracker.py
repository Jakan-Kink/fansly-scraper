"""Unit tests for metadata.stub_tracker module."""

import pytest

from metadata.stub_tracker import (
    count_stubs,
    get_all_stubs_by_table,
    get_stubs,
    is_stub,
    register_stub,
    remove_stub,
)
from tests.fixtures.utils.test_isolation import snowflake_id


class TestStubTrackerOperations:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self, entity_store):
        """register, is_stub, get_stubs, get_all_stubs_by_table, count_stubs, remove."""
        rid1, rid2, rid3 = snowflake_id(), snowflake_id(), snowflake_id()
        await register_stub("accounts", rid1, reason="FK")
        await register_stub("accounts", rid2)
        await register_stub("media", rid3, reason="preview")

        assert await is_stub("accounts", rid1) is True
        assert await is_stub("media", snowflake_id()) is False

        stubs = await get_stubs("accounts")
        assert rid1 in stubs
        assert rid2 in stubs

        limited = await get_stubs("accounts", limit=1)
        assert len(limited) == 1

        by_table = await get_all_stubs_by_table()
        assert "accounts" in by_table
        assert "media" in by_table
        assert by_table["accounts"][0]["reason"] == "FK"

        assert await count_stubs("accounts") == 2
        assert await count_stubs() == 3
        assert await count_stubs("nonexistent") == 0

        assert await remove_stub("accounts", rid1) is True
        assert await is_stub("accounts", rid1) is False
