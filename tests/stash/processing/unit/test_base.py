"""Unit tests for StashProcessingBase class - pure logic methods."""

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx
from stash_graphql_client.types import Tag

from tests.fixtures.stash import (
    create_find_tags_result,
    create_graphql_response,
    create_tag_dict,
)
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

        # Test case 5: No content AND no created_at → username-only fallback.
        # created_at is Optional; a missing date must not crash the date format.
        title = respx_stash_processor._generate_title_from_content(None, username, None)
        assert title == username


class TestPreloadEntities:
    """Test _preload_stash_entities."""

    @pytest.mark.asyncio
    async def test_preload_entities_fetches_tags_once_and_indexes_them(
        self, respx_stash_processor
    ):
        """_preload_stash_entities loads every tag and indexes names and aliases.

        Lookups by name or alias, in any letter case, then need no request, and
        a second preload over the same store does not fetch again.
        """
        processor = respx_stash_processor
        processor.config.stash_tags_batch = 60
        tags = [
            create_tag_dict(id="1", name="Beach", aliases=["seaside"]),
            create_tag_dict(id="2", name="Harbour"),
        ]
        route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findTags", create_find_tags_result(count=2, tags=tags)
                    ),
                )
            ]
        )

        try:
            await processor._preload_stash_entities()
            by_name = processor._find_tag_in_cache("BEACH")
            by_alias = processor._find_tag_in_cache("Seaside")
            unknown = processor._find_tag_in_cache("mountain")
            # A tag the store only knows by id (nested in another payload) is
            # skipped by the index rather than breaking it.
            processor.store.add(Tag(id="3"))
            await processor._preload_stash_entities()
        finally:
            dump_graphql_calls(route.calls, "test_preload_entities_fetches_tags")

        assert route.call_count == 1
        request = json.loads(route.calls[0].request.content)
        assert "findTags" in request["query"]
        # Page size comes from stash_context.batches.tags.
        assert request["variables"]["filter"]["per_page"] == 60
        assert by_name is not None
        assert by_name.id == "1"
        assert by_alias is by_name
        assert unknown is None
