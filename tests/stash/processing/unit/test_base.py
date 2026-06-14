"""Unit tests for StashProcessingBase class - pure logic methods."""

from datetime import UTC, datetime

import pytest
import respx

from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls


class TestStashProcessingBase:
    """Test pure logic methods of StashProcessingBase class."""

    def test_generate_title_from_content(self, respx_stash_processor, faker):
        """Test _generate_title_from_content method - pure logic test."""
        # Test case 1: Content with a short first line
        content = "This is the title\nThis is the rest of the content"
        username = faker.user_name()
        created_at = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)

        # Call method on real processor (inherits from StashProcessingBase)
        title = respx_stash_processor._generate_title_from_content(
            content, username, created_at
        )

        # Verify result
        assert title == "This is the title"

        # Test case 2: Content with a very long first line
        long_content = "X" * 200
        title = respx_stash_processor._generate_title_from_content(
            long_content, username, created_at
        )

        # Verify result is truncated with ellipsis
        assert title == ("X" * 125 + "...")
        assert len(title) == 128

        # Test case 3: No suitable content line, use fallback
        title = respx_stash_processor._generate_title_from_content(
            None, username, created_at
        )

        # Verify fallback format
        assert title == f"{username} - 2023/01/01"

        # Test case 4: With position indicators
        title = respx_stash_processor._generate_title_from_content(
            content, username, created_at, 2, 5
        )

        # Verify position is appended
        assert title == "This is the title - 2/5"


class TestPreloadEntities:
    """Test _preload_stash_entities."""

    @pytest.mark.asyncio
    async def test_preload_entities_makes_no_network_calls(self, respx_stash_processor):
        """_preload_stash_entities only configures TTLs — no GraphQL fetch.

        Mixin call sites use the ``store.filter() → store.find_one()``
        pattern, which lazy-populates the cache as entities are looked up.
        """
        route = respx.post("http://localhost:9999/graphql").mock(side_effect=[])

        try:
            await respx_stash_processor._preload_stash_entities()
        finally:
            dump_graphql_calls(
                route.calls, "test_preload_entities_makes_no_network_calls"
            )

        assert route.call_count == 0
