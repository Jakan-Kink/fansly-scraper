"""Unit tests for creator processing methods.

This module tests StashProcessing creator-related methods using real fixtures
and factories instead of mock objects, following the fixture refactoring patterns.

Key improvements:
- Uses real Account instances from AccountFactory
- Uses respx to mock GraphQL HTTP responses at the edge (not internal methods!)
- Uses real database fixtures instead of mocked database
- Maintains test isolation with proper cleanup
"""

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
import respx
from stash_graphql_client.types.job import JobStatus

from tests.fixtures import (
    create_find_performers_result,
    create_find_studios_result,
    create_graphql_response,
    create_performer_dict,
    create_studio_dict,
)
from tests.fixtures.metadata.metadata_factories import AccountFactory
from tests.fixtures.stash.stash_type_factories import JobFactory


# ============================================================================
# Test Class - Uses respx_stash_processor for unit testing with mocked HTTP
# ============================================================================


class TestCreatorProcessing:
    """Test the creator processing methods of StashProcessing.

    Uses real fixtures and factories instead of mocks where possible.
    Maintains unit test isolation by mocking external dependencies (Stash API).
    """

    @pytest.mark.asyncio
    async def test_process_creator(self, factory_session, respx_stash_processor):
        """Test process_creator method with real Account and respx HTTP mocking."""
        processor = respx_stash_processor

        # Create real Account in test database (metadata)
        # Note: id is auto-generated UUID, don't set it manually
        real_account = AccountFactory(
            username="test_user",
            displayName="Test User",
            stash_id=123,  # stash_id is integer field
        )
        factory_session.add(real_account)
        factory_session.commit()

        # Configure processor state to find the account
        processor.state.creator_id = str(real_account.id)

        # Create performer dict for GraphQL response
        performer_dict = create_performer_dict(
            id="performer_123",
            name="Test User",
        )

        # Mock GraphQL HTTP response
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers",
                        create_find_performers_result(
                            count=1, performers=[performer_dict]
                        ),
                    ),
                ),
            ]
        )

        # Initialize client
        await processor.context.get_client()

        # Call process_creator - uses real database and respx-mocked HTTP
        async with processor.database.async_session_scope() as session:
            account, performer = await processor.process_creator(session=session)

        # Verify Account came from real database
        assert account.id == real_account.id
        assert account.username == "test_user"

        # Verify Performer came from GraphQL HTTP (respx mock)
        assert performer is not None
        assert performer.name == "Test User"

        # Verify respx was hit - check the HTTP call was made
        assert graphql_route.called
        assert graphql_route.call_count >= 1

        # Inspect the GraphQL request to verify correct query and variables
        request = graphql_route.calls[0].request
        assert request.method == "POST"
        assert "graphql" in str(request.url)

        # Parse the GraphQL request body
        request_body = json.loads(request.content)

        # Verify it's a FindPerformers query
        assert "findPerformers" in request_body.get("query", "")

        # Verify the variables match what we expect
        variables = request_body.get("variables", {})

        # New library: find_performer uses performer_filter with name/alias searches
        # Note: Library uses per_page=1 for find_one (not -1), don't assert on implementation details

        # First call should be name search with EQUALS modifier
        performer_filter = variables.get("performer_filter", {})
        name_filter = performer_filter.get("name", {})
        assert name_filter.get("value") == "Test User"  # displayName from Account
        assert name_filter.get("modifier") == "EQUALS"

    @pytest.mark.asyncio
    async def test_process_creator_no_account_raises_error(
        self, factory_session, respx_stash_processor
    ):
        """Test process_creator raises ValueError when no account found.

        Uses real database behavior - no Account created means _find_account returns None,
        which raises ValueError with proper error message.
        """
        processor = respx_stash_processor

        # Configure processor state with non-existent creator_id
        processor.state.creator_id = "99999"  # No account with this ID exists
        processor.state.creator_name = "test_user"  # For error message

        # Expect ValueError when account not found
        with pytest.raises(
            ValueError, match=r"No account found for creator"
        ) as excinfo:
            async with processor.database.async_session_scope() as session:
                await processor.process_creator(session=session)

        # Verify error message includes creator details
        assert "No account found" in str(excinfo.value)
        assert "99999" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_process_creator_creates_new_performer(
        self, factory_session, respx_stash_processor
    ):
        """Test process_creator creates new performer when not found in Stash."""
        processor = respx_stash_processor

        # Create real Account in database (no stash_id yet)
        # Note: id is auto-generated UUID
        real_account = AccountFactory(
            username="new_user",
            displayName="New User",
            stash_id=None,
        )
        factory_session.add(real_account)
        factory_session.commit()

        # Configure processor state
        processor.state.creator_id = str(real_account.id)

        # Create performer dict for GraphQL response
        performer_dict = create_performer_dict(
            id="new_performer_123",
            name="New User",
        )

        # Mock GraphQL HTTP responses
        # v0.7.x: find_performer() makes 3 calls (name + alias + URL search)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: findPerformers by name (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers",
                        create_find_performers_result(count=0, performers=[]),
                    ),
                ),
                # Second call: findPerformers by alias (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers",
                        create_find_performers_result(count=0, performers=[]),
                    ),
                ),
                # Third call: findPerformers by URL (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers",
                        create_find_performers_result(count=0, performers=[]),
                    ),
                ),
                # Fourth call: performerCreate (creates new)
                httpx.Response(
                    200, json=create_graphql_response("performerCreate", performer_dict)
                ),
            ]
        )

        # Initialize client
        await processor.context.get_client()

        # Call process_creator
        async with processor.database.async_session_scope() as session:
            account, performer = await processor.process_creator(session=session)

        # Verify results
        assert account.id == real_account.id
        assert performer is not None
        assert performer.name == "New User"

        # Verify respx was hit 4 times (3 findPerformers: name/alias/URL + 1 performerCreate)
        assert graphql_route.call_count == 4

        # Verify first call was findPerformers
        first_request = json.loads(graphql_route.calls[0].request.content)
        assert "findPerformers" in first_request.get("query", "")

        # Verify fourth call was performerCreate
        fourth_request = json.loads(graphql_route.calls[3].request.content)
        assert "performerCreate" in fourth_request.get("query", "")

    @pytest.mark.asyncio
    async def test_find_existing_studio(self, factory_session, respx_stash_processor):
        """Test _find_existing_studio method with respx HTTP mocking."""
        processor = respx_stash_processor

        # Create real Account in database
        # Note: id is auto-generated UUID
        real_account = AccountFactory(
            username="test_user",
            displayName="Test User",
        )
        factory_session.add(real_account)
        factory_session.commit()

        # Create performer for process_creator_studio
        # Note: Performer ID must be string(int) format until assigned by Stash
        from tests.fixtures.stash.stash_type_factories import PerformerFactory

        performer = PerformerFactory.build(id="123", name="Test User")

        # Studio fix pattern: Add Fansly parent studio
        fansly_dict = create_studio_dict(
            id="fansly_123",
            name="Fansly (network)",
            urls=["https://fansly.com"],
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_dict])

        # Creator studio "not found" (triggers studioCreate)
        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        # Creator studio after creation
        creator_dict = create_studio_dict(
            id="studio_123",
            name="test_user (Fansly)",
            parent_studio=fansly_dict,
        )

        # Create empty responses for cache checks
        empty_tags_result = {"count": 0, "tags": []}

        # Mock GraphQL HTTP responses - match actual call sequence from debug log
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Call 1: findStudios for "Fansly (network)" with name filter
                httpx.Response(
                    200,
                    json=create_graphql_response("findStudios", fansly_result),
                ),
                # Call 2: findStudios for "test_user (Fansly)" with name filter (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios", creator_not_found_result
                    ),
                ),
                # Call 3: studioCreate for creator studio
                httpx.Response(
                    200,
                    json=create_graphql_response("studioCreate", creator_dict),
                ),
            ]
        )

        # Initialize client
        await processor.context.get_client()

        # Call process_creator_studio directly with performer
        # (_find_existing_studio just wraps this with performer=None)
        studio = await processor.process_creator_studio(
            account=real_account,
        )

        # Verify studio came from GraphQL HTTP
        assert studio is not None
        assert studio.name == "test_user (Fansly)"

        # Verify respx was hit multiple times (cache checks + Fansly network + studioCreate)
        assert graphql_route.call_count >= 2

    @pytest.mark.asyncio
    async def test_start_creator_processing_no_stash_context(
        self, factory_session, respx_stash_processor
    ):
        """Test start_creator_processing when stash_context_conn is not configured."""
        processor = respx_stash_processor
        # Remove stash context
        processor.config.stash_context_conn = None

        with patch("stash.processing.base.print_warning") as mock_print_warning:
            # Call start_creator_processing
            await processor.start_creator_processing()

            # Verify warning was printed
            mock_print_warning.assert_called_once()
            assert "not configured" in str(mock_print_warning.call_args)

    @pytest.mark.asyncio
    async def test_start_creator_processing_with_stash_context(
        self, factory_session, respx_stash_processor, tmp_path
    ):
        """Test start_creator_processing orchestrates scan, process, and background tasks.

        This is an integration test that verifies the orchestration logic without mocking
        internal methods. Uses respx to mock GraphQL HTTP responses only.
        """
        processor = respx_stash_processor

        # Ensure stash_context_conn is configured (prevents early return)
        processor.config.stash_context_conn = {
            "scheme": "http",
            "host": "localhost",
            "port": 9999,
            "apikey": "",
        }

        # Setup: Create real Account in database
        real_account = AccountFactory(
            username="test_user",
            displayName="Test User",
        )
        factory_session.add(real_account)
        factory_session.commit()

        # Configure processor state
        processor.state.creator_id = str(real_account.id)
        processor.state.base_path = tmp_path / "creator_folder"
        processor.state.base_path.mkdir(parents=True, exist_ok=True)

        # Create dicts for GraphQL responses
        finished_job_dict = {
            "id": "job_123",
            "status": "FINISHED",
            "description": "Scanning metadata",
            "progress": 100.0,
            "subTasks": [],
            "addTime": datetime.now(UTC).isoformat(),
        }
        performer_dict = create_performer_dict(
            id="performer_123",
            name="Test User",
        )

        # Empty preload results
        empty_performers = create_find_performers_result(count=0, performers=[])
        empty_tags = {"count": 0, "tags": []}
        empty_studios = create_find_studios_result(count=0, studios=[])
        empty_scenes = {"count": 0, "scenes": []}
        empty_images = {"count": 0, "images": []}
        empty_galleries = {"count": 0, "galleries": []}

        # Mock GraphQL HTTP responses for the full workflow
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: connect_async() connection establishment
                httpx.Response(200, json={"data": {}}),
                # === Preload: _preload_stash_entities() ===
                httpx.Response(
                    200,
                    json=create_graphql_response("findPerformers", empty_performers),
                ),
                httpx.Response(
                    200, json=create_graphql_response("findTags", empty_tags)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findStudios", empty_studios)
                ),
                # === scan_creator_folder() (sets base_path, triggers Stash scan) ===
                # metadataScan mutation (returns job ID string)
                httpx.Response(200, json={"data": {"metadataScan": "job_123"}}),
                # findJob query (finished immediately for speed)
                httpx.Response(
                    200, json=create_graphql_response("findJob", finished_job_dict)
                ),
                # === Preload: _preload_creator_media() (after scan) ===
                # Scenes and images only — galleries are cached on-demand
                httpx.Response(
                    200, json=create_graphql_response("findScenes", empty_scenes)
                ),
                httpx.Response(
                    200, json=create_graphql_response("findImages", empty_images)
                ),
                # process_creator: findPerformers query
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers",
                        create_find_performers_result(
                            count=1, performers=[performer_dict]
                        ),
                    ),
                ),
            ]
        )

        # Initialize client
        await processor.context.get_client()

        # Ensure config has background tasks list
        if not hasattr(processor.config, "_background_tasks"):
            processor.config._background_tasks = []

        # Call start_creator_processing - orchestration under test
        await processor.start_creator_processing()

        # === PERMANENT GraphQL call sequence assertions ===
        # 1 connect + 3 entity preload + 2 scan + 2 media preload + 1 findPerformers = 9
        assert len(graphql_route.calls) == 9, (
            f"Expected exactly 9 GraphQL calls, got {len(graphql_route.calls)}"
        )

        # Call 0: connect_async
        # Calls 1-3: _preload_stash_entities (findPerformers, findTags, findStudios)
        # Call 4: metadataScan (scan_creator_folder)
        call4_body = json.loads(graphql_route.calls[4].request.content)
        assert "metadataScan" in call4_body.get("query", "")

        # Call 5: findJob (scan_creator_folder)
        call5_body = json.loads(graphql_route.calls[5].request.content)
        assert "findJob" in call5_body.get("query", "")

        # Calls 6-7: _preload_creator_media (findScenes, findImages)
        # Call 8: findPerformers (process_creator)
        call8_body = json.loads(graphql_route.calls[8].request.content)
        assert "findPerformers" in call8_body.get("query", "")

        # Verify orchestration: background task was created
        assert processor._background_task is not None
        assert processor._background_task in processor.config._background_tasks

        # Clean up background task to avoid warnings
        if processor._background_task and not processor._background_task.done():
            processor._background_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await processor._background_task

    @pytest.mark.asyncio
    async def test_scan_creator_folder(self, respx_stash_processor, tmp_path):
        """Test scan_creator_folder method with respx HTTP mocking.

        Uses respx to mock GraphQL responses for metadataScan mutation and findJob query.
        """
        processor = respx_stash_processor
        # Setup: Ensure base_path exists
        processor.state.base_path = tmp_path / "creator_folder"
        processor.state.base_path.mkdir(parents=True, exist_ok=True)

        # Create job dicts for GraphQL responses
        running_job_dict = {
            "id": "job_123",
            "status": "RUNNING",
            "description": "Scanning metadata",
            "progress": 50.0,
            "subTasks": [],
            "addTime": datetime.now(UTC).isoformat(),
        }
        finished_job_dict = {
            "id": "job_123",
            "status": "FINISHED",
            "description": "Scanning metadata",
            "progress": 100.0,
            "subTasks": [],
            "addTime": datetime.now(UTC).isoformat(),
        }

        # Mock GraphQL HTTP responses
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: connect_async() connection establishment
                httpx.Response(200, json={"data": {}}),
                # Second call: metadataScan mutation (returns job ID as string)
                httpx.Response(200, json={"data": {"metadataScan": "job_123"}}),
                # Third call: findJob query (job still running)
                httpx.Response(
                    200, json=create_graphql_response("findJob", running_job_dict)
                ),
                # Fourth call: findJob query (job finished)
                httpx.Response(
                    200, json=create_graphql_response("findJob", finished_job_dict)
                ),
            ]
        )

        # Initialize client
        await processor.context.get_client()

        # Call scan_creator_folder - uses respx-mocked HTTP
        await processor.scan_creator_folder()

        # Verify respx was hit 4 times (connect_async + metadataScan + 2x findJob)
        assert graphql_route.call_count == 4

        # Verify first call was metadataScan
        first_request = json.loads(graphql_route.calls[1].request.content)
        assert "metadataScan" in first_request.get("query", "")
        # Verify paths were sent
        variables = first_request.get("variables", {})
        input_data = variables.get("input", {})
        assert str(processor.state.base_path) in input_data.get("paths", [])

        # Verify second and third calls were findJob
        second_request = json.loads(graphql_route.calls[2].request.content)
        assert "findJob" in second_request.get("query", "")
        assert second_request["variables"]["input"]["id"] == "job_123"

        third_request = json.loads(graphql_route.calls[3].request.content)
        assert "findJob" in third_request.get("query", "")
        assert third_request["variables"]["input"]["id"] == "job_123"

    @pytest.mark.asyncio
    async def test_scan_creator_folder_metadata_scan_error(
        self, respx_stash_processor, tmp_path
    ):
        """Test scan_creator_folder raises ValueError when metadataScan fails."""
        processor = respx_stash_processor
        # Setup
        processor.state.base_path = tmp_path / "creator_folder"
        processor.state.base_path.mkdir(parents=True, exist_ok=True)

        # Mock GraphQL HTTP error response for metadataScan
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {"metadataScan": None},
                    "errors": [{"message": "Test error"}],
                },
            )
        )

        # Initialize client
        await processor.context.get_client()

        # Expect ValueError with specific message (actual error type from code)
        with pytest.raises(
            ValueError, match=r"Failed to start metadata scan"
        ) as excinfo:
            await processor.scan_creator_folder()

        # Verify error message includes both failure message and original error
        assert "Failed to start metadata scan" in str(excinfo.value)
        assert "Test error" in str(excinfo.value)

        # Verify respx was hit
        assert graphql_route.called

    @pytest.mark.asyncio
    async def test_scan_creator_folder_no_base_path(
        self, respx_stash_processor, tmp_path
    ):
        """Test scan_creator_folder creates and uses download_path when base_path is None."""
        processor = respx_stash_processor
        # Setup: No base_path
        processor.state.base_path = None
        processor.state.download_path = None

        # Create real path for testing
        created_path = tmp_path / "created_path"
        created_path.mkdir(parents=True, exist_ok=True)

        # Create Job instances using factory
        finished_job = JobFactory(
            id="job_456",
            status=JobStatus.FINISHED,
            description="Scanning metadata",
            progress=100.0,
        )
        # Convert Pydantic model to dict for JSON response
        finished_job_dict = json.loads(finished_job.model_dump_json())

        # Mock GraphQL HTTP responses
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: connect_async() connection establishment
                httpx.Response(200, json={"data": {}}),
                # Second call: metadataScan mutation
                httpx.Response(200, json={"data": {"metadataScan": "job_456"}}),
                # Third call: findJob query (finished)
                httpx.Response(
                    200, json=create_graphql_response("findJob", finished_job_dict)
                ),
            ]
        )

        # Initialize client
        await processor.context.get_client()

        # Mock path creation utility (OK to patch utility functions)
        with (
            patch("stash.processing.base.print_info") as mock_print_info,
            patch(
                "stash.processing.base.set_create_directory_for_download"
            ) as mock_set_path,
        ):
            mock_set_path.return_value = created_path

            # Call scan_creator_folder
            await processor.scan_creator_folder()

            # === PERMANENT GraphQL call sequence assertions ===
            assert len(graphql_route.calls) == 3, (
                f"Expected exactly 3 GraphQL calls, got {len(graphql_route.calls)}"
            )

            # Call 2: metadataScan mutation with created path
            call2_body = json.loads(graphql_route.calls[1].request.content)
            assert "metadataScan" in call2_body.get("query", "")
            assert str(created_path) in call2_body["variables"]["input"]["paths"]

            # Verify path creation and state updates
            mock_print_info.assert_any_call(
                "No download path set, attempting to create one..."
            )
            mock_set_path.assert_called_once_with(processor.config, processor.state)
            assert processor.state.download_path == created_path
            # After fix: base_path should be updated to download_path
            assert processor.state.base_path == created_path

    @pytest.mark.asyncio
    async def test_scan_creator_folder_wait_for_job_exception_handling(
        self, respx_stash_processor, tmp_path
    ):
        """Test scan_creator_folder handles exceptions in wait_for_job loop correctly.

        This ensures the exception handling doesn't cause an infinite loop.
        """
        processor = respx_stash_processor
        # Setup
        processor.state.base_path = tmp_path / "creator_folder"
        processor.state.base_path.mkdir(parents=True, exist_ok=True)

        # Create job dicts for GraphQL responses
        error_job_dict = {
            "id": "job_123",
            "status": "RUNNING",
            "description": "Scanning metadata",
            "error": "Temporary error",
            "subTasks": [],
            "addTime": datetime.now(UTC).isoformat(),
        }
        finished_job_dict = {
            "id": "job_123",
            "status": "FINISHED",
            "description": "Scanning metadata",
            "progress": 100.0,
            "subTasks": [],
            "addTime": datetime.now(UTC).isoformat(),
        }

        # Mock GraphQL HTTP responses
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: connect_async() connection establishment
                httpx.Response(200, json={"data": {}}),
                # Second call: metadataScan mutation
                httpx.Response(200, json={"data": {"metadataScan": "job_123"}}),
                # Third call: findJob query (job with error)
                httpx.Response(
                    200, json=create_graphql_response("findJob", error_job_dict)
                ),
                # Fourth call: findJob query (job finished)
                httpx.Response(
                    200, json=create_graphql_response("findJob", finished_job_dict)
                ),
            ]
        )

        # Initialize client
        await processor.context.get_client()

        # Call scan_creator_folder - handles error gracefully and continues
        await processor.scan_creator_folder()

        # Verify respx was hit 4 times (connect_async + metadataScan + 2x findJob)
        assert graphql_route.call_count == 4
