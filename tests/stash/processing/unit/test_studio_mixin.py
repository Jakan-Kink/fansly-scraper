"""Unit tests for StudioProcessingMixin.

These tests mock at the HTTP boundary using respx, allowing real code execution
through the entire processing pipeline. We verify that GraphQL calls are made
with correct data from our test objects.
"""

import asyncio
import json
import logging

import httpx
import pytest
import respx

from tests.fixtures import (
    create_find_studios_result,
    create_graphql_response,
    create_studio_dict,
)
from tests.fixtures.stash.stash_api_fixtures import assert_op, assert_op_with_vars


class TestStudioProcessingMixin:
    """Test the studio processing mixin functionality."""

    @pytest.mark.asyncio
    async def test_process_creator_studio_both_exist(
        self, respx_stash_processor, mock_account
    ):
        """Test process_creator_studio when both Fansly and Creator studios exist.

        Pattern: get_or_create() attempts creation, Stash returns existing if duplicate.
        Expected: findStudios (Fansly) → findStudios (creator not found) → studioCreate (creator returns existing)
        """
        # Create responses
        fansly_studio_dict = create_studio_dict(id="10400", name="Fansly (network)")
        fansly_studio_result = create_find_studios_result(
            count=1, studios=[fansly_studio_dict]
        )

        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        creator_studio_dict = create_studio_dict(id="10401", name="test_user (Fansly)")

        # Mock GraphQL responses (respx_stash_processor already has respx enabled)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: find Fansly studio
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", fansly_studio_result),
                ),
                # Second call: find creator studio (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                # Third call: studioCreate (returns existing studio if duplicate)
                httpx.Response(
                    200,
                    json=create_graphql_response("studioCreate", creator_studio_dict),
                ),
            ]
        )

        # Call process_creator_studio (respx will intercept HTTP calls)
        result = await respx_stash_processor.process_creator_studio(
            account=mock_account,
        )

        # === PERMANENT GraphQL call sequence assertions ===
        assert len(graphql_route.calls) == 3, (
            f"Expected exactly 3 GraphQL calls, got {len(graphql_route.calls)}"
        )

        # Call 1: Find Fansly studio
        # Note: Don't assert on filter structure - that's library implementation
        assert_op(graphql_route.calls[0], "findStudios")

        # Call 2: Find creator studio (not found)
        assert_op(graphql_route.calls[1], "findStudios")

        # Call 3: studioCreate (returns existing)
        assert_op_with_vars(
            graphql_route.calls[2],
            "studioCreate",
            input__name="test_user (Fansly)",
        )

        # Verify result
        assert result is not None
        assert result.id == "10401"
        assert result.name == "test_user (Fansly)"

    @pytest.mark.asyncio
    async def test_process_creator_studio_create_new(
        self, respx_stash_processor, mock_account, mock_studio, caplog
    ):
        """Test process_creator_studio when Creator studio doesn't exist and needs to be created.

        Pattern: get_or_create() creates immediately, doesn't search first.
        Expected: findStudios (Fansly) → findStudios (creator not found) → studioCreate (new)
        """
        caplog.set_level(logging.INFO)
        # Create responses
        fansly_studio_dict = create_studio_dict(id="10400", name="Fansly (network)")
        fansly_studio_result = create_find_studios_result(
            count=1, studios=[fansly_studio_dict]
        )

        creator_not_found_result = create_find_studios_result(count=0, studios=[])

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
                # Second call: find creator studio (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                # Third call: studioCreate (creates new)
                httpx.Response(
                    200,
                    json=create_graphql_response("studioCreate", new_studio_dict),
                ),
            ]
        )

        # Call process_creator_studio (respx will intercept HTTP calls)
        result = await respx_stash_processor.process_creator_studio(
            account=mock_account,
        )

        # === PERMANENT GraphQL call sequence assertions ===
        assert len(graphql_route.calls) == 3, (
            f"Expected exactly 3 GraphQL calls, got {len(graphql_route.calls)}"
        )

        # Call 1: Find Fansly studio
        # Note: Don't assert on filter structure - that's library implementation
        assert_op(graphql_route.calls[0], "findStudios")

        # Call 2: Find creator studio (not found)
        assert_op(graphql_route.calls[1], "findStudios")

        # Call 3: Create studio
        assert_op_with_vars(
            graphql_route.calls[2],
            "studioCreate",
            input__name="test_user (Fansly)",
        )
        call3_body = json.loads(graphql_route.calls[2].request.content)
        assert (
            "https://fansly.com/test_user" in call3_body["variables"]["input"]["urls"]
        )

        # Verify result
        assert result is not None
        # The result should be the created studio
        assert hasattr(result, "id")

        # Verify the "Studio created" info log was emitted via print_info → loguru.
        info_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "INFO"
        ]
        studio_created = [m for m in info_messages if "Studio created" in m]
        assert len(studio_created) == 1

    @pytest.mark.asyncio
    async def test_process_creator_studio_fansly_not_found(
        self, respx_stash_processor, mock_account
    ):
        """Test process_creator_studio when Fansly studio doesn't exist."""
        # Create empty response
        empty_result = create_find_studios_result(count=0, studios=[])

        # Mock GraphQL response (respx_stash_processor already has respx enabled)
        respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", empty_result),
                )
            ]
        )

        # Call process_creator_studio and expect error (respx will intercept HTTP calls)
        with pytest.raises(
            ValueError, match=r"Fansly Studio not found in Stash"
        ) as excinfo:
            await respx_stash_processor.process_creator_studio(
                account=mock_account,
            )

        # Verify error message
        assert "Fansly Studio not found in Stash" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_process_creator_studio_creation_fails_then_retry(
        self, respx_stash_processor, mock_account, caplog
    ):
        """Test process_creator_studio when creation fails (no automatic retry in current implementation).

        Pattern: find_one() first, then create if not found. On error, catch and return None.
        Expected: findStudios (Fansly) → findStudios (creator not found) → studioCreate (error)
        """
        # Create responses
        fansly_studio_dict = create_studio_dict(id="10400", name="Fansly (network)")
        fansly_studio_result = create_find_studios_result(
            count=1, studios=[fansly_studio_dict]
        )

        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        # Mock GraphQL responses (respx_stash_processor already has respx enabled)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: find Fansly studio (found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", fansly_studio_result),
                ),
                # Second call: find creator studio (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                # Third call: studioCreate returns error
                httpx.Response(
                    200,
                    json={
                        "errors": [{"message": "Test error"}],
                        "data": None,
                    },
                ),
            ]
        )

        # caplog.set_level(DEBUG) captures both print_error/logger.exception
        # (ERROR via loguru) and debug_print (DEBUG via stash_logger).
        caplog.set_level(logging.DEBUG)

        result = await respx_stash_processor.process_creator_studio(
            account=mock_account,
        )

        # === PERMANENT GraphQL call sequence assertions ===
        assert len(graphql_route.calls) == 3, (
            f"Expected exactly 3 GraphQL calls, got {len(graphql_route.calls)}"
        )

        # Call 1: Find Fansly studio
        # Note: Don't assert on filter structure - that's library implementation
        assert_op(graphql_route.calls[0], "findStudios")

        # Call 2: Find creator studio (not found)
        assert_op(graphql_route.calls[1], "findStudios")

        # Call 3: Create studio (will fail)
        assert_op_with_vars(
            graphql_route.calls[2],
            "studioCreate",
            input__name="test_user (Fansly)",
        )

        # Verify result is None (creation failed, no retry)
        assert result is None

        # Production emits 2 ERROR records on the failure path: one from
        # print_error and one from logger.exception (with exc_info attached).
        error_records = [r for r in caplog.records if r.levelname == "ERROR"]
        find_create_errors = [
            r for r in error_records if "Failed to find/create studio" in r.getMessage()
        ]
        assert len(find_create_errors) == 2
        assert any(r.exc_info is not None for r in find_create_errors), (
            "logger.exception should attach exc_info to its record"
        )

        # debug_print fires twice: initial method-entry pformat plus exception-branch pformat.
        debug_records = [
            r
            for r in caplog.records
            if r.levelname == "DEBUG"
            and "StashProcessing - process_creator_studio" in r.getMessage()
        ]
        assert len(debug_records) == 2
        assert any(
            "studio_find_or_create_failed" in r.getMessage() for r in debug_records
        )

    @pytest.mark.asyncio
    async def test_process_creator_studio_creation_fails_retry_also_fails(
        self, respx_stash_processor, mock_account, caplog
    ):
        """Test process_creator_studio when creation fails AND retry finds nothing (line 141).

        This covers the branch where studio creation fails, then the retry query
        also returns 0 studios, causing the method to return None.

        Pattern: get_or_create() creates immediately. On error, retry with find_one().
        Expected: findStudios (Fansly) → findStudios (creator not found) → studioCreate (error) → findStudios (retry, not found)
        """
        # Create responses
        fansly_studio_dict = create_studio_dict(id="10400", name="Fansly (network)")
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
                # Second call: find creator studio (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", empty_result),
                ),
                # Third call: studioCreate returns error
                httpx.Response(
                    200,
                    json={
                        "errors": [{"message": "Studio creation failed"}],
                        "data": None,
                    },
                ),
                # Fourth call: retry find Creator studio (STILL not found)
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", empty_result),
                ),
            ]
        )

        caplog.set_level(logging.DEBUG)

        result = await respx_stash_processor.process_creator_studio(
            account=mock_account,
        )

        # Verify result is None (line 141)
        assert result is None

        # 2 ERROR records: 1 from print_error + 1 from logger.exception (with exc_info).
        error_records = [r for r in caplog.records if r.levelname == "ERROR"]
        find_create_errors = [
            r for r in error_records if "Failed to find/create studio" in r.getMessage()
        ]
        assert len(find_create_errors) == 2
        assert any(r.exc_info is not None for r in find_create_errors), (
            "logger.exception should attach exc_info to its record"
        )

        # debug_print fires twice: method-entry + exception-branch.
        debug_records = [
            r
            for r in caplog.records
            if r.levelname == "DEBUG"
            and "StashProcessing - process_creator_studio" in r.getMessage()
        ]
        assert len(debug_records) == 2

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
