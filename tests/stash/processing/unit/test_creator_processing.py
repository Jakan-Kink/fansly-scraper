"""Unit tests for creator processing methods.

This module tests StashProcessing creator-related methods using real fixtures
and factories instead of mock objects, following the fixture refactoring patterns.

Key improvements:
- Uses real Account instances from AccountFactory
- Uses respx to mock GraphQL HTTP responses at the edge (not internal methods!)
- Uses entity_store for database persistence (Pydantic EntityStore)
- Maintains test isolation with proper cleanup
"""

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from stash_graphql_client.errors import StashVersionError
from stash_graphql_client.types.job import JobStatus

from tests.fixtures import (
    create_find_performers_result,
    create_find_studios_result,
    create_graphql_response,
    create_performer_dict,
    create_studio_dict,
)
from tests.fixtures.metadata.metadata_factories import AccountFactory
from tests.fixtures.stash.stash_api_fixtures import (
    assert_op,
    assert_op_with_vars,
    dump_graphql_calls,
)
from tests.fixtures.stash.stash_type_factories import JobFactory, PerformerFactory


# ============================================================================
# Test Class - Uses respx_stash_processor for unit testing with mocked HTTP
# ============================================================================


class TestCreatorProcessing:
    """Test the creator processing methods of StashProcessing.

    Uses real fixtures and factories instead of mocks where possible.
    Maintains unit test isolation by mocking external dependencies (Stash API).
    """

    @pytest.mark.asyncio
    async def test_process_creator(self, entity_store, respx_stash_processor):
        """Test process_creator method with real Account and respx HTTP mocking."""
        processor = respx_stash_processor

        # Create real Account in entity_store (production code uses get_store())
        real_account = AccountFactory.build(
            username="test_user",
            displayName="Test User",
            stash_id=123,  # stash_id is integer field
        )
        await entity_store.save(real_account)

        # Configure processor state to find the account
        processor.state.creator_id = real_account.id

        # Create performer dict for GraphQL response
        performer_dict = create_performer_dict(
            id="5700",
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

        # Call process_creator (no session= parameter)
        try:
            account, performer = await processor.process_creator()
        finally:
            dump_graphql_calls(graphql_route.calls, "test_process_creator")

        # Verify Account came from real database
        assert account.id == real_account.id
        assert account.username == "test_user"

        # Verify Performer came from GraphQL HTTP (respx mock)
        assert performer is not None
        assert performer.name == "Test User"

        # Verify respx was hit - check the HTTP call was made
        assert graphql_route.called
        assert graphql_route.call_count == 1

        # Inspect the GraphQL request to verify method/URL stayed correct
        request = graphql_route.calls[0].request
        assert request.method == "POST"
        assert "graphql" in str(request.url)

        # Verify findPerformers with name search (EQUALS) on displayName
        # New library: find_performer uses performer_filter with name/alias searches
        # Note: Library uses per_page=1 for find_one (not -1), don't assert on implementation details
        assert_op_with_vars(
            graphql_route.calls[0],
            "findPerformers",
            performer_filter__name__value="Test User",  # displayName from Account
            performer_filter__name__modifier="EQUALS",
        )

    @pytest.mark.asyncio
    async def test_process_creator_no_account_raises_error(
        self, entity_store, respx_stash_processor
    ):
        """Test process_creator raises ValueError when no account found.

        Uses real database behavior - no Account created means _find_account returns None,
        which raises ValueError with proper error message.
        """
        processor = respx_stash_processor

        # Configure processor state with non-existent creator_id
        processor.state.creator_id = 99999  # No account with this ID exists
        processor.state.creator_name = "test_user"  # For error message

        # Expect ValueError when account not found
        with pytest.raises(
            ValueError, match=r"No account found for creator"
        ) as excinfo:
            await processor.process_creator()

        # Verify error message includes creator details
        assert "No account found" in str(excinfo.value)
        assert "99999" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_process_creator_creates_new_performer(
        self, entity_store, respx_stash_processor
    ):
        """Test process_creator creates new performer when not found in Stash."""
        processor = respx_stash_processor

        # Create real Account in entity_store (no stash_id yet)
        real_account = AccountFactory.build(
            username="new_user",
            displayName="New User",
            stash_id=None,
        )
        await entity_store.save(real_account)

        # Configure processor state
        processor.state.creator_id = real_account.id

        # Create performer dict for GraphQL response
        performer_dict = create_performer_dict(
            id="5701",
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

        # Call process_creator (no session= parameter)
        account, performer = await processor.process_creator()

        # Verify results
        assert account.id == real_account.id
        assert performer is not None
        assert performer.name == "New User"

        # Verify respx was hit 4 times (3 findPerformers: name/alias/URL + 1 performerCreate)
        assert graphql_route.call_count == 4

        # Verify first call was findPerformers
        assert_op(graphql_route.calls[0], "findPerformers")

        # Verify fourth call was performerCreate
        assert_op(graphql_route.calls[3], "performerCreate")

    @pytest.mark.asyncio
    async def test_find_existing_studio(self, entity_store, respx_stash_processor):
        """Test _find_existing_studio method with respx HTTP mocking."""
        processor = respx_stash_processor

        # Create real Account in entity_store
        real_account = AccountFactory.build(
            username="test_user",
            displayName="Test User",
        )
        await entity_store.save(real_account)

        # Create performer for process_creator_studio
        # Note: Performer ID must be string(int) format until assigned by Stash

        performer = PerformerFactory.build(id="123", name="Test User")

        # Studio fix pattern: Add Fansly parent studio
        fansly_dict = create_studio_dict(
            id="10400",
            name="Fansly (network)",
            urls=["https://fansly.com"],
        )
        fansly_result = create_find_studios_result(count=1, studios=[fansly_dict])

        # Creator studio "not found" (triggers studioCreate)
        creator_not_found_result = create_find_studios_result(count=0, studios=[])

        # Creator studio after creation
        creator_dict = create_studio_dict(
            id="10401",
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
        try:
            studio = await processor.process_creator_studio(
                account=real_account,
            )
        finally:
            dump_graphql_calls(graphql_route.calls, "test_find_existing_studio")

        # Verify studio came from GraphQL HTTP
        assert studio is not None
        assert studio.name == "test_user (Fansly)"

        # Verify respx was hit exactly 3 times (Fansly network find + creator find + studioCreate)
        assert graphql_route.call_count == 3

    @pytest.mark.asyncio
    async def test_start_creator_processing_no_stash_context(
        self, entity_store, respx_stash_processor, caplog
    ):
        """Test start_creator_processing when stash_context_conn is not configured."""
        caplog.set_level(logging.WARNING)
        processor = respx_stash_processor
        # Remove stash context
        processor.config.stash_context_conn = None

        await processor.start_creator_processing()

        # Verify the "not configured" warning was emitted via print_warning → loguru.
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        not_configured = [m for m in warnings if "not configured" in m]
        assert len(not_configured) == 1

    @pytest.mark.asyncio
    async def test_start_creator_processing_stash_version_error(
        self, entity_store, respx_stash_processor, caplog
    ):
        """Test start_creator_processing when Stash server is too old (lines 379-382)."""
        caplog.set_level(logging.WARNING)
        processor = respx_stash_processor
        processor.config.stash_context_conn = {
            "scheme": "http",
            "host": "localhost",
            "port": "9999",
            "apikey": "",
        }

        with patch.object(
            processor.context,
            "get_client",
            new_callable=AsyncMock,
            side_effect=StashVersionError("Server too old"),
        ):
            await processor.start_creator_processing()

        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        too_old_errors = [m for m in errors if "too old" in m]
        assert len(too_old_errors) == 1
        assert len(warnings) == 1

    @pytest.mark.asyncio
    async def test_start_creator_processing_runtime_error(
        self, entity_store, respx_stash_processor, caplog
    ):
        """Test start_creator_processing when client init fails (lines 383-385)."""
        caplog.set_level(logging.ERROR)
        processor = respx_stash_processor
        processor.config.stash_context_conn = {
            "scheme": "http",
            "host": "localhost",
            "port": "9999",
            "apikey": "",
        }

        with patch.object(
            processor.context,
            "get_client",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Connection refused"),
        ):
            await processor.start_creator_processing()

        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        init_errors = [m for m in errors if "Failed to initialize" in m]
        assert len(init_errors) == 1

    @pytest.mark.asyncio
    async def test_start_creator_processing_with_stash_context(
        self, entity_store, respx_stash_processor, tmp_path
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

        # Setup: Create real Account in entity_store
        real_account = AccountFactory.build(
            username="test_user",
            displayName="Test User",
        )
        await entity_store.save(real_account)

        # Configure processor state
        processor.state.creator_id = real_account.id
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
            id="5700",
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
        assert_op(graphql_route.calls[4], "metadataScan")

        # Call 5: findJob (scan_creator_folder)
        assert_op(graphql_route.calls[5], "findJob")

        # Calls 6-7: _preload_creator_media (findScenes, findImages)
        # Call 8: findPerformers (process_creator)
        assert_op(graphql_route.calls[8], "findPerformers")

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

        # Verify first call was metadataScan with the correct paths
        assert_op(graphql_route.calls[1], "metadataScan")
        first_request = json.loads(graphql_route.calls[1].request.content)
        input_data = first_request.get("variables", {}).get("input", {})
        assert str(processor.state.base_path) in input_data.get("paths", [])

        # Verify second and third calls were findJob with the same job_id
        assert_op_with_vars(graphql_route.calls[2], "findJob", input__id="job_123")
        assert_op_with_vars(graphql_route.calls[3], "findJob", input__id="job_123")

    @pytest.mark.asyncio
    async def test_scan_creator_folder_metadata_scan_error(
        self, respx_stash_processor, tmp_path
    ):
        """scan_creator_folder re-raises GraphQL failures as RuntimeError.

        The lib's ``metadata_scan`` raises ``ValueError("Failed to start
        metadata scan: ...")`` on transport/GraphQL errors (see
        ``stash_graphql_client/client/mixins/metadata.py:201-203``). The
        production except at ``stash/processing/base.py:346`` catches both
        ``RuntimeError`` and ``ValueError`` and re-raises as the documented
        ``RuntimeError("Failed to process metadata: ...")`` with the
        original exception chained via ``raise ... from e``.
        """
        processor = respx_stash_processor
        processor.state.base_path = tmp_path / "creator_folder"
        processor.state.base_path.mkdir(parents=True, exist_ok=True)

        # scan_creator_folder() first fetches ConfigurationDefaults (to get scan
        # defaults), then issues the MetadataScan mutation. Both will hit this
        # error response, and the code falls back to hardcoded defaults before
        # raising on the scan itself. So exactly 2 GraphQL calls are expected.
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "data": {"metadataScan": None},
                        "errors": [{"message": "Test error"}],
                    },
                )
            ]
            * 2
        )

        await processor.context.get_client()

        try:
            with pytest.raises(
                RuntimeError, match=r"Failed to process metadata"
            ) as excinfo:
                await processor.scan_creator_folder()
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_scan_creator_folder_metadata_scan_error"
            )

        # Outer message wraps the lib's ValueError via ``raise ... from e``.
        assert "Failed to process metadata" in str(excinfo.value)
        assert "Failed to start metadata scan" in str(excinfo.value)
        assert "Test error" in str(excinfo.value)
        # Cause is preserved as the lib's ValueError.
        assert isinstance(excinfo.value.__cause__, ValueError)
        assert "Failed to start metadata scan" in str(excinfo.value.__cause__)

        # Exact count + per-call request + response verification.
        assert len(graphql_route.calls) == 2, (
            f"Expected exactly 2 GraphQL calls "
            f"(ConfigurationDefaults + MetadataScan), got {len(graphql_route.calls)}"
        )
        assert_op(graphql_route.calls[0], "ConfigurationDefaults")
        resp0 = graphql_route.calls[0].response.json()
        assert resp0["errors"][0]["message"] == "Test error"

        assert_op_with_vars(
            graphql_route.calls[1],
            "MetadataScan",
            input__paths=[str(processor.state.base_path)],
        )
        resp1 = graphql_route.calls[1].response.json()
        assert resp1["errors"][0]["message"] == "Test error"

    @pytest.mark.asyncio
    async def test_scan_creator_folder_no_base_path(
        self, respx_stash_processor, tmp_path, caplog
    ):
        """Test scan_creator_folder creates and uses download_path when base_path is None."""
        caplog.set_level(logging.INFO)
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

        # set_create_directory_for_download is a leaf utility that creates an
        # actual directory on disk; patching it lets the test stay hermetic.
        with patch(
            "stash.processing.base.set_create_directory_for_download"
        ) as mock_set_path:
            mock_set_path.return_value = created_path

            # Call scan_creator_folder
            await processor.scan_creator_folder()

        # === PERMANENT GraphQL call sequence assertions ===
        assert len(graphql_route.calls) == 3, (
            f"Expected exactly 3 GraphQL calls, got {len(graphql_route.calls)}"
        )

        # Call 2: metadataScan mutation with created path
        assert_op(graphql_route.calls[1], "metadataScan")
        call2_body = json.loads(graphql_route.calls[1].request.content)
        assert str(created_path) in call2_body["variables"]["input"]["paths"]

        # Verify the "No download path set" info log was emitted via print_info → loguru.
        info_messages = [
            r.getMessage() for r in caplog.records if r.levelname == "INFO"
        ]
        assert any(
            "No download path set, attempting to create one..." in m
            for m in info_messages
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
