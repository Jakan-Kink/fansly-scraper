"""Unit tests for StudioProcessingMixin.

These tests mock at the HTTP boundary using respx, allowing real code execution
through the entire processing pipeline. We verify that GraphQL calls are made
with correct data from our test objects.
"""

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest
import respx

from tests.fixtures import (
    create_find_studios_result,
    create_graphql_response,
    create_studio_dict,
)


class TestStudioProcessingMixin:
    """Test the studio processing mixin functionality."""

    @pytest.mark.asyncio
    async def test_process_creator_studio_both_exist(
        self, respx_stash_processor, mock_account, mock_performer
    ):
        """Test process_creator_studio when both Fansly and Creator studios exist.

        Pattern: get_or_create() attempts creation, Stash returns existing if duplicate.
        Expected: findStudios (Fansly) → studioCreate (creator returns existing)
        """
        # Create responses
        fansly_studio_dict = create_studio_dict(
            id="fansly_123", name="Fansly (network)"
        )
        fansly_studio_result = create_find_studios_result(
            count=1, studios=[fansly_studio_dict]
        )

        creator_studio_dict = create_studio_dict(
            id="studio_123", name="test_user (Fansly)"
        )

        # Mock GraphQL responses (respx_stash_processor already has respx enabled)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: find Fansly studio
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", fansly_studio_result),
                ),
                # Second call: studioCreate (returns existing studio if duplicate)
                httpx.Response(
                    200,
                    json=create_graphql_response("studioCreate", creator_studio_dict),
                ),
            ]
        )

        # Call process_creator_studio (respx will intercept HTTP calls)
        result = await respx_stash_processor.process_creator_studio(
            account=mock_account, performer=mock_performer
        )

        # === PERMANENT GraphQL call sequence assertions ===
        assert len(graphql_route.calls) == 2, (
            f"Expected exactly 2 GraphQL calls, got {len(graphql_route.calls)}"
        )

        # Call 1: Find Fansly studio
        call1_body = json.loads(graphql_route.calls[0].request.content)
        assert "findStudios" in call1_body.get("query", "")
        # Note: Don't assert on filter structure - that's library implementation

        # Call 2: studioCreate (returns existing)
        call2_body = json.loads(graphql_route.calls[1].request.content)
        assert "studioCreate" in call2_body.get("query", "")
        assert call2_body["variables"]["input"]["name"] == "test_user (Fansly)"

        # Verify result
        assert result is not None
        assert result.id == "studio_123"
        assert result.name == "test_user (Fansly)"

    @pytest.mark.asyncio
    async def test_process_creator_studio_create_new(
        self, respx_stash_processor, mock_account, mock_performer, mock_studio
    ):
        """Test process_creator_studio when Creator studio doesn't exist and needs to be created.

        Pattern: get_or_create() creates immediately, doesn't search first.
        Expected: findStudios (Fansly) → studioCreate (new)
        """
        # Create responses
        fansly_studio_dict = create_studio_dict(
            id="fansly_123", name="Fansly (network)"
        )
        fansly_studio_result = create_find_studios_result(
            count=1, studios=[fansly_studio_dict]
        )

        # Create studio for creation response
        new_studio_dict = create_studio_dict(
            id=mock_studio.id,
            name=mock_studio.name,
            urls=mock_studio.urls,
        )

        # Mock GraphQL responses (respx_stash_processor already has respx enabled)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: find Fansly studio (found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", fansly_studio_result),
                ),
                # Second call: studioCreate (creates new)
                httpx.Response(
                    200,
                    json=create_graphql_response("studioCreate", new_studio_dict),
                ),
            ]
        )

        # Call process_creator_studio (respx will intercept HTTP calls)
        with patch("stash.processing.mixins.studio.print_info") as mock_print_info:
            result = await respx_stash_processor.process_creator_studio(
                account=mock_account, performer=mock_performer
            )

            # === PERMANENT GraphQL call sequence assertions ===
            assert len(graphql_route.calls) == 2, (
                f"Expected exactly 2 GraphQL calls, got {len(graphql_route.calls)}"
            )

            # Call 1: Find Fansly studio
            call1_body = json.loads(graphql_route.calls[0].request.content)
            assert "findStudios" in call1_body.get("query", "")
            # Note: Don't assert on filter structure - that's library implementation

            # Call 2: Create studio
            call2_body = json.loads(graphql_route.calls[1].request.content)
            assert "studioCreate" in call2_body.get("query", "")
            assert call2_body["variables"]["input"]["name"] == "test_user (Fansly)"
            assert (
                "https://fansly.com/test_user"
                in call2_body["variables"]["input"]["urls"]
            )

            # Verify result
            assert result is not None
            # The result should be the created studio
            assert hasattr(result, "id")

            # Verify print_info called
            mock_print_info.assert_called_once()
            assert "Studio ready" in str(mock_print_info.call_args)

    @pytest.mark.asyncio
    async def test_process_creator_studio_fansly_not_found(
        self, respx_stash_processor, mock_account, mock_performer
    ):
        """Test process_creator_studio when Fansly studio doesn't exist."""
        # Create empty response
        empty_result = create_find_studios_result(count=0, studios=[])

        # Mock GraphQL response (respx_stash_processor already has respx enabled)
        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200,
                json=create_graphql_response("findStudios", empty_result),
            )
        )

        # Call process_creator_studio and expect error (respx will intercept HTTP calls)
        with pytest.raises(
            ValueError, match=r"Fansly Studio not found in Stash"
        ) as excinfo:
            await respx_stash_processor.process_creator_studio(
                account=mock_account, performer=mock_performer
            )

        # Verify error message
        assert "Fansly Studio not found in Stash" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_process_creator_studio_creation_fails_then_retry(
        self, respx_stash_processor, mock_account, mock_performer
    ):
        """Test process_creator_studio when creation fails, then succeeds on retry.

        Pattern: get_or_create() creates immediately. On error, retry with find_one().
        Expected: findStudios (Fansly) → studioCreate (error) → findStudios (retry, found)
        """
        # Create responses
        fansly_studio_dict = create_studio_dict(
            id="fansly_123", name="Fansly (network)"
        )
        fansly_studio_result = create_find_studios_result(
            count=1, studios=[fansly_studio_dict]
        )

        creator_studio_dict = create_studio_dict(
            id="studio_123",
            name="test_user (Fansly)",
            urls=["https://fansly.com/test_user"],
            aliases=[],
            tags=[],
            stash_ids=[],
        )
        creator_studio_result = create_find_studios_result(
            count=1, studios=[creator_studio_dict]
        )

        # Mock GraphQL responses (respx_stash_processor already has respx enabled)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: find Fansly studio (found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", fansly_studio_result),
                ),
                # Second call: studioCreate returns error
                httpx.Response(
                    200,
                    json={
                        "errors": [{"message": "Test error"}],
                        "data": None,
                    },
                ),
                # Third call: retry find Creator studio (found this time)
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", creator_studio_result),
                ),
            ]
        )

        # Call process_creator_studio with error mocks (respx will intercept HTTP calls)
        with (
            patch("stash.processing.mixins.studio.print_error") as mock_print_error,
            patch(
                "stash.processing.mixins.studio.logger.exception"
            ) as mock_logger_exception,
            patch("stash.processing.mixins.studio.debug_print") as mock_debug_print,
        ):
            result = await respx_stash_processor.process_creator_studio(
                account=mock_account, performer=mock_performer
            )

            # === PERMANENT GraphQL call sequence assertions ===
            assert len(graphql_route.calls) == 3, (
                f"Expected exactly 3 GraphQL calls, got {len(graphql_route.calls)}"
            )

            # Call 1: Find Fansly studio
            call1_body = json.loads(graphql_route.calls[0].request.content)
            assert "findStudios" in call1_body.get("query", "")
            # Note: Don't assert on filter structure - that's library implementation

            # Call 2: Create studio (will fail)
            call2_body = json.loads(graphql_route.calls[1].request.content)
            assert "studioCreate" in call2_body.get("query", "")
            assert call2_body["variables"]["input"]["name"] == "test_user (Fansly)"

            # Call 3: Retry find Creator studio
            call3_body = json.loads(graphql_route.calls[2].request.content)
            assert "findStudios" in call3_body.get("query", "")
            # Note: Don't assert on filter structure - that's library implementation

            # Verify result (should get the existing studio from retry)
            assert result is not None
            assert result.id == "studio_123"

            # Verify error handling
            mock_print_error.assert_called_once()
            assert "Failed to get/create studio" in str(mock_print_error.call_args)
            mock_logger_exception.assert_called_once()
            # debug_print is called twice: once at start, once in exception handler
            assert mock_debug_print.call_count == 2
            assert "studio_get_or_create_failed" in str(mock_debug_print.call_args)

    @pytest.mark.asyncio
    async def test_process_creator_studio_creation_fails_retry_also_fails(
        self, respx_stash_processor, mock_account, mock_performer
    ):
        """Test process_creator_studio when creation fails AND retry finds nothing (line 141).

        This covers the branch where studio creation fails, then the retry query
        also returns 0 studios, causing the method to return None.

        Pattern: get_or_create() creates immediately. On error, retry with find_one().
        Expected: findStudios (Fansly) → studioCreate (error) → findStudios (retry, not found)
        """
        # Create responses
        fansly_studio_dict = create_studio_dict(
            id="fansly_123", name="Fansly (network)"
        )
        fansly_studio_result = create_find_studios_result(
            count=1, studios=[fansly_studio_dict]
        )

        empty_result = create_find_studios_result(count=0, studios=[])

        # Mock GraphQL responses (respx_stash_processor already has respx enabled)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: find Fansly studio (found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", fansly_studio_result),
                ),
                # Second call: studioCreate returns error
                httpx.Response(
                    200,
                    json={
                        "errors": [{"message": "Studio creation failed"}],
                        "data": None,
                    },
                ),
                # Third call: retry find Creator studio (STILL not found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", empty_result),
                ),
            ]
        )

        # Call process_creator_studio with error mocks
        with (
            patch("stash.processing.mixins.studio.print_error") as mock_print_error,
            patch(
                "stash.processing.mixins.studio.logger.exception"
            ) as mock_logger_exception,
            patch("stash.processing.mixins.studio.debug_print") as mock_debug_print,
        ):
            result = await respx_stash_processor.process_creator_studio(
                account=mock_account, performer=mock_performer
            )

            # Verify result is None (line 141)
            assert result is None

            # Verify error handling was called
            mock_print_error.assert_called_once()
            assert "Failed to get/create studio" in str(mock_print_error.call_args)
            mock_logger_exception.assert_called_once()
            # debug_print is called twice: once at start, once in exception handler
            assert mock_debug_print.call_count == 2

    @pytest.mark.asyncio
    async def test_get_studio_lock_race_condition(self, respx_stash_processor, faker):
        """Test _get_studio_lock double-check race condition (line 41->43).

        This covers the branch where after acquiring the _studio_creation_locks_lock,
        we find that another task already created the lock for this username.

        Ultra-aggressive approach: Mock the dict to return False first time, True second time.
        """
        username = faker.user_name()

        # Clear any existing locks
        respx_stash_processor._studio_creation_locks.clear()

        # Create the lock that will be "created by another task" during the race
        pre_existing_lock = asyncio.Lock()

        # Track how many times we've checked the dict
        check_count = [0]

        # Save the original dict for reference
        original_dict = respx_stash_processor._studio_creation_locks

        # Create a wrapper that intercepts __contains__ checks
        class RaceConditionDict(dict):
            def __contains__(self, key):
                if key == username:
                    check_count[0] += 1
                    if check_count[0] == 1:
                        # First check (line 38): Return False - pass outer if
                        return False
                    if check_count[0] == 2:
                        # Second check (line 41): Inject lock and return True - fail inner if!
                        super().__setitem__(username, pre_existing_lock)
                        return True  # This triggers line 41->43!
                return super().__contains__(key)

        # Replace the dict with our race-injecting version
        race_dict = RaceConditionDict()
        respx_stash_processor._studio_creation_locks = race_dict

        try:
            # Call _get_studio_lock - it will:
            # 1. Line 38: Check username in dict -> our __contains__ returns False (check 1)
            # 2. Line 39: Enter lock
            # 3. Line 41: Check username in dict -> our __contains__ injects lock & returns True (check 2)
            # 4. Line 42: SKIPPED (double-check failed - username IS in dict!)
            # 5. Line 43: Return pre_existing_lock
            result_lock = await respx_stash_processor._get_studio_lock(username)

            # Verify we got the pre-existing lock (not a new one)
            assert result_lock is pre_existing_lock, (
                f"Should return the pre-existing lock. Got {id(result_lock)}, expected {id(pre_existing_lock)}"
            )

            # Verify the double-check was actually triggered
            assert check_count[0] == 2, (
                f"Should have checked dict twice, got {check_count[0]} checks"
            )

            # Verify only ONE lock exists
            assert len(race_dict) == 1

        finally:
            # Restore original dict
            respx_stash_processor._studio_creation_locks = original_dict
