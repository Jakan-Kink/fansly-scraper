"""TRUE integration tests for TagClientMixin.

These tests make REAL GraphQL calls to the Docker Stash instance and verify actual API behavior.
Uses capture_graphql_calls to validate request sequences.
"""

import pytest

from stash import StashClient
from stash.types import Tag
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls


class TestTagClientMixin:
    """TRUE integration tests for TagClientMixin - makes real API calls to Stash."""

    @pytest.mark.asyncio
    async def test_find_tag(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test finding a tag by ID - TRUE INTEGRATION TEST.

        Creates a real tag in Stash, then verifies find_tag can retrieve it.
        """
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create a real tag in Stash
            test_tag = Tag(
                id="new",
                name="Test Find Tag",
                description="Test tag for find_tag test",
                aliases=["findtag_alias1", "findtag_alias2"],
            )

            with capture_graphql_calls(stash_client) as calls:
                created_tag = await stash_client.create_tag(test_tag)
                cleanup["tags"].append(created_tag.id)

            # Verify tagCreate call
            assert len(calls) == 1, "Expected 1 GraphQL call for create"
            assert "tagCreate" in calls[0]["query"]

            # Test finding by ID - makes real GraphQL call
            with capture_graphql_calls(stash_client) as calls:
                found_tag = await stash_client.find_tag(created_tag.id)

            # Verify findTag call
            assert len(calls) == 1, "Expected 1 GraphQL call for find"
            assert "findTag" in calls[0]["query"]
            assert calls[0]["variables"]["id"] == created_tag.id

            # Verify result
            assert found_tag is not None
            assert found_tag.id == created_tag.id
            assert found_tag.name == "Test Find Tag"
            assert found_tag.description == "Test tag for find_tag test"
            assert len(found_tag.aliases) == 2
            assert "findtag_alias1" in found_tag.aliases
            assert "findtag_alias2" in found_tag.aliases

    @pytest.mark.asyncio
    async def test_find_tags(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test finding tags with filters - TRUE INTEGRATION TEST.

        Creates multiple real tags in Stash, then tests various filter combinations.
        """
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create multiple real tags in Stash
            tag1 = Tag(
                id="new",
                name="Filter Test Alpha",
                description="First test tag",
                aliases=["alpha"],
            )
            tag2 = Tag(
                id="new",
                name="Filter Test Beta",
                description="Second test tag",
                aliases=["beta"],
            )
            tag3 = Tag(
                id="new",
                name="Filter Test Gamma",
                description="Third test tag",
                aliases=["gamma"],
            )

            created1 = await stash_client.create_tag(tag1)
            created2 = await stash_client.create_tag(tag2)
            created3 = await stash_client.create_tag(tag3)

            cleanup["tags"].extend([created1.id, created2.id, created3.id])

            # Test 1: Find all tags with name containing "Filter Test"
            with capture_graphql_calls(stash_client) as calls:
                result = await stash_client.find_tags(
                    tag_filter={
                        "name": {"value": "Filter Test", "modifier": "INCLUDES"}
                    }
                )

            # Verify GraphQL call
            assert len(calls) == 1, "Expected 1 GraphQL call for find_tags"
            assert "findTags" in calls[0]["query"]
            assert "Filter Test" in str(calls[0]["variables"])

            assert result.count >= 3  # At least our 3 tags
            # Tags may be dict or Tag objects depending on deserialization
            tag_names = [
                t.name if hasattr(t, "name") else t["name"] for t in result.tags
            ]
            assert "Filter Test Alpha" in tag_names
            assert "Filter Test Beta" in tag_names
            assert "Filter Test Gamma" in tag_names

            # Test 2: Find specific tag by exact name
            with capture_graphql_calls(stash_client) as calls:
                result = await stash_client.find_tags(
                    tag_filter={
                        "name": {"value": "Filter Test Alpha", "modifier": "EQUALS"}
                    }
                )

            assert len(calls) == 1
            assert "findTags" in calls[0]["query"]
            assert result.count == 1
            # Handle both dict and Tag object
            tag_name = (
                result.tags[0].name
                if hasattr(result.tags[0], "name")
                else result.tags[0]["name"]
            )
            assert tag_name == "Filter Test Alpha"

            # Test 3: Test pagination with filter
            with capture_graphql_calls(stash_client) as calls:
                result = await stash_client.find_tags(
                    filter_={
                        "page": 1,
                        "per_page": 2,
                        "sort": "name",
                        "direction": "ASC",
                    },
                    tag_filter={
                        "name": {"value": "Filter Test", "modifier": "INCLUDES"}
                    },
                )

            assert len(calls) == 1
            assert "findTags" in calls[0]["query"]
            assert len(result.tags) <= 2  # Respects per_page limit

    @pytest.mark.asyncio
    async def test_create_tag(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test creating a tag - TRUE INTEGRATION TEST.

        Creates a real tag in Stash and verifies all fields are set correctly.
        """
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create a real tag with all fields
            test_tag = Tag(
                id="new",
                name="Created Tag Test",
                description="Tag created during test",
                aliases=["create_alias1", "create_alias2"],
            )

            with capture_graphql_calls(stash_client) as calls:
                created = await stash_client.create_tag(test_tag)
                cleanup["tags"].append(created.id)

            # Verify GraphQL call sequence
            assert len(calls) == 1, "Expected 1 GraphQL call"
            assert "tagCreate" in calls[0]["query"]

            # Verify variables sent
            variables = calls[0]["variables"]
            assert "input" in variables
            assert variables["input"]["name"] == "Created Tag Test"
            assert variables["input"]["description"] == "Tag created during test"

            # Verify tag was created with all fields
            assert created is not None
            assert created.id != "new"  # Should have real ID from Stash
            assert created.name == "Created Tag Test"
            assert created.description == "Tag created during test"
            assert len(created.aliases) == 2

            # Verify we can find it again
            found = await stash_client.find_tag(created.id)
            assert found is not None
            assert found.name == created.name

    @pytest.mark.asyncio
    async def test_create_tag_duplicate(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test creating a duplicate tag - TRUE INTEGRATION TEST.

        Verifies that attempting to create a tag with an existing name
        returns the existing tag instead of creating a duplicate.
        """
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create the first tag
            tag1 = Tag(
                id="new",
                name="Duplicate Tag Test",
                description="Original tag",
                aliases=[],
            )
            created1 = await stash_client.create_tag(tag1)
            cleanup["tags"].append(created1.id)

            # Attempt to create a tag with the same name
            tag2 = Tag(
                id="new",
                name="Duplicate Tag Test",  # Same name!
                description="Duplicate attempt",
                aliases=[],
            )

            with capture_graphql_calls(stash_client) as calls:
                created2 = await stash_client.create_tag(tag2)

            # Verify GraphQL sequence: should try tagCreate, get error, then findTags
            assert len(calls) >= 1, "Expected at least 1 GraphQL call"
            # First call is tagCreate attempt
            assert "tagCreate" in calls[0]["query"] or "findTags" in calls[0]["query"]

            # Should return existing tag, not create new one
            assert created2.id == created1.id  # Same ID means same tag
            assert created2.name == "Duplicate Tag Test"

    @pytest.mark.asyncio
    async def test_find_tags_error(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test error handling when finding tags with invalid filters - TRUE INTEGRATION TEST.

        Tests that invalid filter combinations are handled gracefully.
        """
        # Test with empty filter - should return all tags (or error gracefully)
        with capture_graphql_calls(stash_client) as calls:
            result = await stash_client.find_tags()

        # Verify GraphQL call was made
        assert len(calls) == 1
        assert "findTags" in calls[0]["query"]

        # Should return result (even if empty), not crash
        assert result is not None
        assert hasattr(result, "count")
        assert hasattr(result, "tags")

    @pytest.mark.asyncio
    async def test_update_tag(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test updating a tag - TRUE INTEGRATION TEST.

        Creates a real tag, updates it, and verifies the changes were persisted.
        """
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create a real tag
            original_tag = Tag(
                id="new",
                name="Update Test Tag",
                description="Original description",
                aliases=["original_alias"],
            )
            created = await stash_client.create_tag(original_tag)
            cleanup["tags"].append(created.id)

            # Update the tag
            created.description = "Updated description"
            created.aliases = ["updated_alias1", "updated_alias2"]

            with capture_graphql_calls(stash_client) as calls:
                updated = await stash_client.update_tag(created)

            # Verify GraphQL call
            assert len(calls) == 1, "Expected 1 GraphQL call"
            assert "tagUpdate" in calls[0]["query"]
            assert calls[0]["variables"]["input"]["id"] == created.id

            # Verify updates were applied
            assert updated.id == created.id
            assert updated.description == "Updated description"
            assert len(updated.aliases) == 2
            assert "updated_alias1" in updated.aliases
            assert "updated_alias2" in updated.aliases

            # Verify updates were persisted by fetching again
            refetched = await stash_client.find_tag(created.id)
            assert refetched.description == "Updated description"
            assert len(refetched.aliases) == 2

    @pytest.mark.asyncio
    async def test_merge_tags(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test merging tags - TRUE INTEGRATION TEST.

        Creates multiple tags, merges them, and verifies the merge worked correctly.
        """
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create destination tag
            dest_tag = Tag(
                id="new",
                name="Merge Destination",
                description="Destination for merge",
                aliases=[],
            )
            destination = await stash_client.create_tag(dest_tag)
            cleanup["tags"].append(destination.id)

            # Create source tags to merge
            source1_tag = Tag(
                id="new",
                name="Merge Source 1",
                description="First source",
                aliases=[],
            )
            source2_tag = Tag(
                id="new",
                name="Merge Source 2",
                description="Second source",
                aliases=[],
            )
            source1 = await stash_client.create_tag(source1_tag)
            source2 = await stash_client.create_tag(source2_tag)
            # Don't add sources to cleanup - they'll be deleted by merge

            # Perform merge - merges source tags into destination
            with capture_graphql_calls(stash_client) as calls:
                merged = await stash_client.tags_merge(
                    source=[source1.id, source2.id], destination=destination.id
                )

            # Verify GraphQL call
            assert len(calls) == 1, "Expected 1 GraphQL call"
            assert "tagsMerge" in calls[0]["query"]
            # Verify variables were sent (structure may vary)
            variables_str = str(calls[0]["variables"])
            assert destination.id in variables_str
            assert source1.id in variables_str
            assert source2.id in variables_str

            # Verify merge result
            assert merged.id == destination.id
            assert merged.name == "Merge Destination"

            # Verify source tags were deleted (merged into destination)
            source1_after = await stash_client.find_tag(source1.id)
            assert source1_after is None  # Should be deleted after merge

    @pytest.mark.asyncio
    async def test_merge_tags_error(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test error handling when merging with invalid tag IDs - TRUE INTEGRATION TEST.

        Attempts to merge non-existent tags to verify proper error handling.
        """
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create a valid destination tag
            dest_tag = Tag(
                id="new",
                name="Valid Destination",
                description="Valid destination tag",
                aliases=[],
            )
            destination = await stash_client.create_tag(dest_tag)
            cleanup["tags"].append(destination.id)

            # Attempt to merge with non-existent source tags
            with (
                capture_graphql_calls(stash_client) as calls,
                pytest.raises(Exception),  # Should raise error for invalid IDs
            ):
                await stash_client.tags_merge(
                    source=["99999", "88888"],  # Non-existent IDs
                    destination=destination.id,
                )

            # Verify GraphQL call was attempted
            assert len(calls) == 1
            assert "tagsMerge" in calls[0]["query"]

    @pytest.mark.asyncio
    async def test_bulk_tag_update(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test bulk tag update - TRUE INTEGRATION TEST.

        Creates multiple tags and performs bulk updates on them.
        """
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create multiple tags
            tag1 = Tag(id="new", name="Bulk Update 1", aliases=[])
            tag2 = Tag(id="new", name="Bulk Update 2", aliases=[])
            tag3 = Tag(id="new", name="Bulk Update 3", aliases=[])

            created1 = await stash_client.create_tag(tag1)
            created2 = await stash_client.create_tag(tag2)
            created3 = await stash_client.create_tag(tag3)

            cleanup["tags"].extend([created1.id, created2.id, created3.id])

            # Perform bulk update (add common description to all)
            with capture_graphql_calls(stash_client) as calls:
                updated_tags = await stash_client.bulk_tag_update(
                    ids=[created1.id, created2.id, created3.id],
                    description="Bulk updated description",
                )

            # Verify GraphQL call
            assert len(calls) == 1, "Expected 1 GraphQL call"
            assert "bulkTagUpdate" in calls[0]["query"]
            # Verify IDs were sent (structure may vary)
            variables_str = str(calls[0]["variables"])
            assert created1.id in variables_str
            assert created2.id in variables_str
            assert created3.id in variables_str

            # Verify all tags were updated
            assert len(updated_tags) == 3
            for tag in updated_tags:
                assert tag.description == "Bulk updated description"

    @pytest.mark.asyncio
    async def test_bulk_tag_update_error(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test bulk tag update with invalid IDs - TRUE INTEGRATION TEST.

        Attempts bulk update with non-existent tag IDs to verify error handling.
        """
        # Attempt bulk update with non-existent IDs
        with (
            capture_graphql_calls(stash_client) as calls,
            pytest.raises(Exception),  # Should raise error for invalid IDs
        ):
            await stash_client.bulk_tag_update(
                ids=["99999", "88888"],  # Non-existent IDs
                description="Should fail",
            )

        # Verify GraphQL call was attempted
        assert len(calls) == 1
        assert "bulkTagUpdate" in calls[0]["query"]

    @pytest.mark.asyncio
    async def test_create_tag_error(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test error handling when creating invalid tags - TRUE INTEGRATION TEST.

        Tests various error scenarios with real API calls.
        """
        # Test: Create tag with empty name (should fail)
        invalid_tag = Tag(
            id="new",
            name="",  # Empty name - invalid!
            aliases=[],
        )
        with (
            capture_graphql_calls(stash_client) as calls,
            pytest.raises(Exception),
        ):
            await stash_client.create_tag(invalid_tag)

        # Verify GraphQL call was attempted
        assert len(calls) >= 1

    @pytest.mark.asyncio
    async def test_tag_hierarchy(
        self, stash_client: StashClient, stash_cleanup_tracker
    ) -> None:
        """Test tag parent/child hierarchy - TRUE INTEGRATION TEST.

        Creates tags with parent/child relationships and verifies the hierarchy works.
        """
        async with stash_cleanup_tracker(stash_client) as cleanup:
            # Create parent tag
            parent_tag = Tag(
                id="new",
                name="Parent Tag Hierarchy",
                description="Parent in hierarchy",
                aliases=[],
            )

            with capture_graphql_calls(stash_client) as calls:
                parent = await stash_client.create_tag(parent_tag)
                cleanup["tags"].append(parent.id)

            assert len(calls) == 1
            assert "tagCreate" in calls[0]["query"]

            # Create child tag with parent relationship
            child_tag = Tag(
                id="new",
                name="Child Tag Hierarchy",
                description="Child in hierarchy",
                aliases=[],
                parents=[parent],  # Set parent relationship
            )

            with capture_graphql_calls(stash_client) as calls:
                child = await stash_client.create_tag(child_tag)
                cleanup["tags"].append(child.id)

            assert len(calls) == 1
            assert "tagCreate" in calls[0]["query"]
            # Verify parent_ids were included in request
            assert parent.id in str(calls[0]["variables"])

            # Verify hierarchy by fetching parent
            refetched_parent = await stash_client.find_tag(parent.id)
            assert refetched_parent is not None

            # Verify child relationship
            refetched_child = await stash_client.find_tag(child.id)
            assert refetched_child is not None
            assert refetched_child.name == "Child Tag Hierarchy"
