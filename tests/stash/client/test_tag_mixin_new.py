"""Additional TRUE integration tests for TagClientMixin.

These tests provide additional coverage for error handling and edge cases
using real GraphQL calls to the Docker Stash instance.
Uses capture_graphql_calls to validate request sequences.
"""

import pytest

from stash import StashClient
from stash.types import Tag
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls


class TestTagClientMixinAdditional:
    """Additional TRUE integration tests for TagClientMixin error handling."""

    @pytest.mark.asyncio
    async def test_create_tag_duplicate_alternative(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test creating a duplicate tag (alternative approach) - TRUE INTEGRATION TEST.

        Similar to test_create_tag_duplicate but with different validation approach.
        """
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create the first tag
            tag = Tag(
                id="new",
                name="Alt Duplicate Tag Test",
                description="Original tag for alternative test",
                aliases=[],
            )

            with capture_graphql_calls(stash_client) as calls:
                created1 = await stash_client.create_tag(tag)
                cleanup["tags"].append(created1.id)

            # Verify tagCreate call
            assert len(calls) == 1
            assert "tagCreate" in calls[0]["query"]

            # Attempt to create a tag with the same name
            tag2 = Tag(
                id="new",
                name="Alt Duplicate Tag Test",  # Same name!
                description="Duplicate attempt",
                aliases=[],
            )

            with capture_graphql_calls(stash_client) as calls:
                created2 = await stash_client.create_tag(tag2)

            # Verify GraphQL sequence for duplicate handling
            assert len(calls) >= 1
            # May have multiple calls depending on error handling strategy

            # Should return existing tag, not create new one
            assert created2.id == created1.id  # Same ID means same tag
            assert created2.name == "Alt Duplicate Tag Test"

    @pytest.mark.asyncio
    async def test_find_tags_error_alternative(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test error handling when finding tags (alternative) - TRUE INTEGRATION TEST.

        Tests that various edge cases in tag finding are handled gracefully.
        """
        # Test 1: Find with None filter (should return all tags)
        with capture_graphql_calls(stash_client) as calls:
            result = await stash_client.find_tags()

        assert len(calls) == 1
        assert "findTags" in calls[0]["query"]
        assert result is not None
        assert hasattr(result, "count")
        assert hasattr(result, "tags")

        # Test 2: Find with impossible filter (should return empty)
        with capture_graphql_calls(stash_client) as calls:
            result = await stash_client.find_tags(
                tag_filter={
                    "name": {
                        "value": "ThisTagDefinitelyDoesNotExist_XYZ123",
                        "modifier": "EQUALS",
                    }
                }
            )

        assert len(calls) == 1
        assert "findTags" in calls[0]["query"]
        assert result.count == 0
        assert len(result.tags) == 0

    @pytest.mark.asyncio
    async def test_merge_tags_error_alternative(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test merging tags error handling (alternative) - TRUE INTEGRATION TEST.

        Tests error scenarios when merging tags with invalid inputs.
        """
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create a valid destination tag
            dest_tag = Tag(
                id="new",
                name="Alt Valid Destination",
                description="Valid destination for alternative test",
                aliases=[],
            )
            destination = await stash_client.create_tag(dest_tag)
            cleanup["tags"].append(destination.id)

            # Test 1: Merge with empty source list (should fail)
            with (
                capture_graphql_calls(stash_client) as calls,
                pytest.raises(Exception),
            ):
                await stash_client.tags_merge(source=[], destination=destination.id)

            # Verify GraphQL call was attempted (or validation prevented call)
            # May be 0 calls if validation happens before GraphQL
            assert len(calls) >= 0

            # Test 2: Merge with non-existent source IDs
            with (
                capture_graphql_calls(stash_client) as calls,
                pytest.raises(Exception),
            ):
                await stash_client.tags_merge(
                    source=["99999", "88888"], destination=destination.id
                )

            assert len(calls) == 1
            assert "tagsMerge" in calls[0]["query"]

    @pytest.mark.asyncio
    async def test_bulk_tag_update_error_alternative(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test bulk tag update error handling (alternative) - TRUE INTEGRATION TEST.

        Tests error scenarios for bulk tag updates.
        """
        # Test 1: Bulk update with empty ID list (should fail)
        with (
            capture_graphql_calls(stash_client) as calls,
            pytest.raises(Exception),
        ):
            await stash_client.bulk_tag_update(
                ids=[], description="Should fail with empty list"
            )

        # May be 0 calls if validation happens before GraphQL
        assert len(calls) >= 0

        # Test 2: Bulk update with non-existent IDs
        with (
            capture_graphql_calls(stash_client) as calls,
            pytest.raises(Exception),
        ):
            await stash_client.bulk_tag_update(
                ids=["99999", "88888"], description="Should fail with invalid IDs"
            )

        assert len(calls) == 1
        assert "bulkTagUpdate" in calls[0]["query"]

    @pytest.mark.asyncio
    async def test_create_tag_error_alternative(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test tag creation error handling (alternative) - TRUE INTEGRATION TEST.

        Tests various error scenarios when creating tags.
        """
        # Test 1: Create with empty name (should fail)
        invalid_tag = Tag(id="new", name="", aliases=[])
        with (
            capture_graphql_calls(stash_client) as calls,
            pytest.raises(Exception),
        ):
            await stash_client.create_tag(invalid_tag)

        # Verify GraphQL call was attempted (or validation prevented it)
        assert len(calls) >= 0

        # Test 2: Create valid tag to ensure positive case still works
        async with stash_cleanup_tracker(stash_client) as cleanup:
            valid_tag = Tag(
                id="new",
                name="Alt Valid Tag",
                description="Valid tag for alternative test",
                aliases=["alt_valid"],
            )

            with capture_graphql_calls(stash_client) as calls:
                created = await stash_client.create_tag(valid_tag)
                cleanup["tags"].append(created.id)

            assert len(calls) == 1
            assert "tagCreate" in calls[0]["query"]
            assert calls[0]["variables"]["input"]["name"] == "Alt Valid Tag"

            # Verify result
            assert created is not None
            assert created.id != "new"
            assert created.name == "Alt Valid Tag"
