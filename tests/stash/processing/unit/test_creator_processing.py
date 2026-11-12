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

from helpers.rich_progress import get_progress_manager
from metadata.entity_store import PostgresEntityStore
from stash.processing import StashProcessing
from tests.fixtures.metadata.metadata_factories import AccountFactory
from tests.fixtures.stash import (
    create_find_performers_result,
    create_find_studios_result,
    create_graphql_response,
    create_performer_dict,
    create_studio_dict,
)
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

        # The performer carries the creator's Fansly URL, so the first search hits
        performer_dict = create_performer_dict(
            id="5700",
            name="Test User",
            urls=["https://fansly.com/test_user"],
        )
        by_url = create_graphql_response(
            "findPerformers",
            create_find_performers_result(count=1, performers=[performer_dict]),
        )

        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                httpx.Response(200, json=by_url),  # URL search: count
                httpx.Response(200, json=by_url),  # URL search: fetch
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

        assert graphql_route.call_count == 2

        # Inspect the GraphQL request to verify method/URL stayed correct
        request = graphql_route.calls[0].request
        assert request.method == "POST"
        assert "graphql" in str(request.url)

        # The first search is by the creator's Fansly profile URL
        assert_op_with_vars(
            graphql_route.calls[0],
            "findPerformers",
            performer_filter__url__value="fansly.com/test_user",
            performer_filter__url__modifier="INCLUDES",
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
        try:
            account, performer = await processor.process_creator()
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_process_creator_creates_new_performer"
            )

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

    @pytest.mark.parametrize(
        ("raised_exc", "expected_error_substring", "expected_warning_count"),
        [
            (StashVersionError("Server too old"), "too old", 1),
            (RuntimeError("Connection refused"), "Failed to initialize", None),
        ],
        ids=["stash_version_error", "runtime_error"],
    )
    @pytest.mark.asyncio
    async def test_start_creator_processing_get_client_error(
        self,
        *,
        entity_store: PostgresEntityStore,
        respx_stash_processor: StashProcessing,
        caplog: pytest.LogCaptureFixture,
        raised_exc: Exception,
        expected_error_substring: str,
        expected_warning_count: int | None,
    ) -> None:
        """start_creator_processing logs and returns when get_client raises.

        stash_version_error: Stash server too old (lines 379-382) → exactly one
        ERROR containing "too old" plus exactly one WARNING.
        runtime_error: client init failure (lines 383-385) → exactly one ERROR
        containing "Failed to initialize"; no warning-count column (the
        original test captured at ERROR level only).
        """
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
            side_effect=raised_exc,
        ):
            await processor.start_creator_processing()

        errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
        matching_errors = [m for m in errors if expected_error_substring in m]
        assert len(matching_errors) == 1
        if expected_warning_count is not None:
            warnings = [
                r.getMessage() for r in caplog.records if r.levelname == "WARNING"
            ]
            assert len(warnings) == expected_warning_count

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
            urls=["https://fansly.com/test_user"],
        )
        by_url = create_graphql_response(
            "findPerformers",
            create_find_performers_result(count=1, performers=[performer_dict]),
        )

        # Mock GraphQL HTTP responses for the full workflow
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # Connect: the tag preload
                httpx.Response(
                    200,
                    json=create_graphql_response("findTags", {"count": 0, "tags": []}),
                ),
                # === scan_creator_folder() (sets base_path, triggers Stash scan) ===
                # ConfigurationDefaults query (scan defaults)
                httpx.Response(200, json={"data": {}}),
                # metadataScan mutation (returns job ID string)
                httpx.Response(200, json={"data": {"metadataScan": "job_123"}}),
                # findJob query (finished immediately for speed)
                httpx.Response(
                    200, json=create_graphql_response("findJob", finished_job_dict)
                ),
                httpx.Response(200, json=by_url),  # performer URL search: count
                httpx.Response(200, json=by_url),  # performer URL search: fetch
            ]
        )

        # Initialize client
        await processor.context.get_client()

        # Ensure config has background tasks list
        if not hasattr(processor.config, "_background_tasks"):
            processor.config._background_tasks = []

        # Call start_creator_processing - orchestration under test
        try:
            await processor.start_creator_processing()
        finally:
            dump_graphql_calls(graphql_route.calls, "start_creator_processing")

        # 6 calls: the tag preload, the scan (defaults + metadataScan + findJob),
        # then the performer URL search's count and fetch.
        assert len(graphql_route.calls) == 6, (
            f"Expected exactly 6 GraphQL calls, got {len(graphql_route.calls)}"
        )
        assert_op(graphql_route.calls[0], "findTags")
        assert_op(graphql_route.calls[1], "ConfigurationDefaults")
        assert_op(graphql_route.calls[2], "metadataScan")
        assert_op(graphql_route.calls[3], "findJob")
        assert_op(graphql_route.calls[4], "findPerformers")
        assert_op(graphql_route.calls[5], "findPerformers")

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
                # ConfigurationDefaults query (scan defaults)
                httpx.Response(200, json={"data": {}}),
                # metadataScan mutation (returns job ID as string)
                httpx.Response(200, json={"data": {"metadataScan": "job_123"}}),
                # findJob query (job still running)
                httpx.Response(
                    200, json=create_graphql_response("findJob", running_job_dict)
                ),
                # findJob query (job finished)
                httpx.Response(
                    200, json=create_graphql_response("findJob", finished_job_dict)
                ),
            ]
        )

        # Initialize client
        await processor.context.get_client()

        # Call scan_creator_folder - uses respx-mocked HTTP
        try:
            await processor.scan_creator_folder()
        finally:
            dump_graphql_calls(graphql_route.calls, "test_scan_creator_folder")

        # defaults + metadataScan + 2x findJob
        assert graphql_route.call_count == 4

        # metadataScan carries the creator folder
        assert_op(graphql_route.calls[1], "metadataScan")
        first_request = json.loads(graphql_route.calls[1].request.content)
        input_data = first_request.get("variables", {}).get("input", {})
        assert str(processor.state.base_path) in input_data.get("paths", [])

        # Both polls were findJob with the same job_id
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
        """Test scan_creator_folder creates and uses download_path when base_path is None.

        Exercises the REAL ``set_create_directory_for_download`` helper (no patch):
        with ``base_path``/``download_path`` cleared, production calls the helper,
        which creates a real directory under ``config.download_directory`` and
        returns it. We then assert the real created path landed on
        ``state.download_path``/``state.base_path`` and was sent to metadataScan.
        """
        caplog.set_level(logging.INFO)
        processor = respx_stash_processor
        # Setup: No base_path / download_path so the real path-creation runs.
        processor.state.base_path = None
        processor.state.download_path = None

        # Point the real download root at an isolated tmp dir; the real helper
        # creates the creator subdirectory underneath it on disk.
        download_root = tmp_path / "downloads"
        download_root.mkdir(parents=True, exist_ok=True)
        processor.config.download_directory = download_root

        # Create Job instances using factory
        finished_job = JobFactory.build(
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
                # ConfigurationDefaults query (scan defaults)
                httpx.Response(200, json={"data": {}}),
                # metadataScan mutation
                httpx.Response(200, json={"data": {"metadataScan": "job_456"}}),
                # findJob query (finished)
                httpx.Response(
                    200, json=create_graphql_response("findJob", finished_job_dict)
                ),
            ]
        )

        # Initialize client
        await processor.context.get_client()

        # Call scan_creator_folder — the REAL set_create_directory_for_download
        # runs and creates the creator directory on disk.
        try:
            await processor.scan_creator_folder()
        finally:
            dump_graphql_calls(
                graphql_route.calls, "test_scan_creator_folder_no_base_path"
            )

        # The real helper created the directory under download_root and stored it.
        created_path = processor.state.download_path
        assert created_path is not None
        assert created_path.exists()
        assert created_path.is_dir()
        assert download_root in created_path.parents or created_path == download_root

        # === PERMANENT GraphQL call sequence assertions ===
        assert len(graphql_route.calls) == 3, (
            f"Expected exactly 3 GraphQL calls, got {len(graphql_route.calls)}"
        )

        # metadataScan mutation with the real created path
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
                # ConfigurationDefaults query (scan defaults)
                httpx.Response(200, json={"data": {}}),
                # metadataScan mutation
                httpx.Response(200, json={"data": {"metadataScan": "job_123"}}),
                # findJob query (job with error)
                httpx.Response(
                    200, json=create_graphql_response("findJob", error_job_dict)
                ),
                # findJob query (job finished)
                httpx.Response(
                    200, json=create_graphql_response("findJob", finished_job_dict)
                ),
            ]
        )

        # Initialize client
        await processor.context.get_client()

        # Call scan_creator_folder - handles error gracefully and continues
        try:
            await processor.scan_creator_folder()
        finally:
            dump_graphql_calls(
                graphql_route.calls,
                "test_scan_creator_folder_wait_for_job_exception_handling",
            )

        # defaults + metadataScan + 2x findJob
        assert graphql_route.call_count == 4

    @pytest.mark.asyncio
    async def test_scan_creator_folder_stops_waiting_on_unfinished_job(
        self, respx_stash_processor, tmp_path, caplog, monkeypatch
    ):
        """A scan that cannot simply run to FINISHED is waited out or given up on.

        Eleven scans in a row. Four end early: cancelled, failed, gone from the
        job queue, briefly missing before finishing. Four meet Stash's frozen
        queue (stashapp/stash#7347): one sits READY with nothing running but
        starts before the freeze limit, one is cancelled as frozen and runs when
        queued again, one freezes both times and is given up on, and one Stash
        refuses to cancel. One hits the stall timeout behind a busy queue. Two
        are waiting when shutdown is requested (one still queued, which is
        cancelled in Stash, and one running, which is left).
        """
        caplog.set_level(logging.DEBUG)
        monkeypatch.setattr("stash.processing.base._SCAN_POLL_S", 0.01)
        processor = respx_stash_processor
        processor.config.stash_scan_settle_s = 0
        processor.state.base_path = tmp_path / "creator_folder"
        processor.state.base_path.mkdir(parents=True, exist_ok=True)

        def job(
            job_id: str,
            status: JobStatus,
            description: str = "Scanning...",
            **fields: str | float,
        ) -> dict:
            built = JobFactory.build(
                id=job_id, status=status, description=description, **fields
            )
            return json.loads(built.model_dump_json())

        def find_job(
            job_id: str, status: JobStatus, **fields: str | float
        ) -> httpx.Response:
            return httpx.Response(
                200,
                json=create_graphql_response("findJob", job(job_id, status, **fields)),
            )

        def missing() -> httpx.Response:
            return httpx.Response(200, json={"data": {"findJob": None}})

        def queue(*jobs: dict) -> httpx.Response:
            return httpx.Response(200, json={"data": {"jobQueue": list(jobs) or None}})

        def scan_started(job_id: str) -> list[httpx.Response]:
            return [
                httpx.Response(200, json={"data": {}}),
                httpx.Response(200, json={"data": {"metadataScan": job_id}}),
            ]

        def stopped(cancelled: bool = True) -> httpx.Response:
            return httpx.Response(200, json={"data": {"stopJob": cancelled}})

        def frozen_scan(job_id: str, cancelled: bool = True) -> list[httpx.Response]:
            alone = job(job_id, JobStatus.READY)
            return [
                *scan_started(job_id),
                find_job(job_id, JobStatus.READY),
                queue(alone),
                find_job(job_id, JobStatus.READY),
                queue(alone),
                stopped(cancelled),
            ]

        frozen = job("job_frozen", JobStatus.READY)
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                *scan_started("job_cancelled"),
                find_job("job_cancelled", JobStatus.CANCELLED),
                *scan_started("job_failed"),
                find_job("job_failed", JobStatus.FAILED, error="disk full"),
                *scan_started("job_gone"),
                missing(),
                missing(),
                missing(),
                *scan_started("job_blip"),
                missing(),
                find_job("job_blip", JobStatus.RUNNING, progress=0.5),
                find_job("job_blip", JobStatus.RUNNING, progress=0.5),
                find_job("job_blip", JobStatus.FINISHED, progress=1.0),
                # READY with nothing else running, but started before the freeze
                # limit: left alone.
                *scan_started("job_slow_start"),
                find_job("job_slow_start", JobStatus.READY),
                queue(job("job_slow_start", JobStatus.READY)),
                find_job("job_slow_start", JobStatus.READY),
                queue(job("job_slow_start", JobStatus.READY)),
                find_job("job_slow_start", JobStatus.FINISHED),
                # Freeze limit at zero: cancelled on the second idle poll in a
                # row; a job seen running in between restarts the count.
                *scan_started("job_frozen"),
                find_job("job_frozen", JobStatus.READY),
                queue(frozen),
                find_job("job_frozen", JobStatus.READY),
                queue(frozen, job("generate", JobStatus.RUNNING, "Generating...")),
                find_job("job_frozen", JobStatus.READY),
                queue(frozen),
                find_job("job_frozen", JobStatus.READY),
                queue(frozen),
                stopped(),
                # ...and the scan is queued again and runs.
                *scan_started("job_requeued"),
                find_job("job_requeued", JobStatus.FINISHED),
                # Frozen again after being queued again: given up on.
                *frozen_scan("job_frozen_a"),
                *frozen_scan("job_frozen_b"),
                # Stash refuses the cancel: not queued again.
                *frozen_scan("job_kept", cancelled=False),
                # Stall timeout: READY behind a busy queue is left in Stash.
                *scan_started("job_stalled"),
                find_job("job_stalled", JobStatus.READY, progress=0.0),
                queue(
                    job("job_stalled", JobStatus.READY),
                    job("other_scan", JobStatus.RUNNING),
                ),
                find_job("job_stalled", JobStatus.READY, progress=0.0),
                # Shutdown: the queued scan is cancelled, the running one is left.
                *scan_started("job_queued"),
                find_job("job_queued", JobStatus.READY, progress=0.0),
                stopped(),
                *scan_started("job_running"),
                find_job("job_running", JobStatus.RUNNING, progress=0.4),
            ]
        )

        await processor.context.get_client()

        try:
            for _ in range(5):
                await processor.scan_creator_folder()
            monkeypatch.setattr("stash.processing.base._SCAN_FROZEN_S", 0.0)
            for _ in range(3):
                await processor.scan_creator_folder()
            processor.config.stash_scan_stall_timeout_s = 1e-6
            await processor.scan_creator_folder()
            processor.config.stash_scan_stall_timeout_s = 1800.0
            processor.config._shutdown_event = asyncio.Event()
            processor.config._shutdown_event.set()
            for _ in range(2):
                await processor.scan_creator_folder()
        finally:
            dump_graphql_calls(
                graphql_route.calls,
                "test_scan_creator_folder_stops_waiting_on_unfinished_job",
            )

        start = ["ConfigurationDefaults", "metadataScan"]
        expected = [
            ("job_cancelled", [*start, "findJob"]),
            ("job_failed", [*start, "findJob"]),
            ("job_gone", [*start, *["findJob"] * 3]),
            ("job_blip", [*start, *["findJob"] * 4]),
            ("job_slow_start", [*start, *["findJob", "jobQueue"] * 2, "findJob"]),
            ("job_frozen", [*start, *["findJob", "jobQueue"] * 4, "stopJob"]),
            ("job_requeued", [*start, "findJob"]),
            ("job_frozen_a", [*start, *["findJob", "jobQueue"] * 2, "stopJob"]),
            ("job_frozen_b", [*start, *["findJob", "jobQueue"] * 2, "stopJob"]),
            ("job_kept", [*start, *["findJob", "jobQueue"] * 2, "stopJob"]),
            ("job_stalled", [*start, "findJob", "jobQueue", "findJob"]),
            ("job_queued", [*start, "findJob", "stopJob"]),
            ("job_running", [*start, "findJob"]),
        ]
        assert graphql_route.call_count == sum(len(ops) for _, ops in expected)
        calls = iter(graphql_route.calls)
        for job_id, ops in expected:
            for op in ops:
                call = next(calls)
                if op == "findJob":
                    assert_op_with_vars(call, "findJob", input__id=job_id)
                elif op == "stopJob":
                    assert_op_with_vars(call, "stopJob", job_id=job_id)
                else:
                    assert_op(call, op)

        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        carry_on = "; continuing with what Stash has indexed"
        idle = "stayed READY for 0 s with nothing running in Stash"
        assert [w for w in warnings if "Scan job" in w or "queued again" in w] == [
            f"Scan job job_cancelled ended as CANCELLED{carry_on}",
            f"Scan job job_failed ended as FAILED: disk full{carry_on}",
            f"Scan job job_gone is not in Stash's job queue{carry_on}",
            f"Scan job job_frozen {idle}; cancelled it",
            f"Scan job job_frozen_a {idle}; cancelled it",
            f"Scan job job_frozen_b {idle}; cancelled it",
            f"Stash did not start the scan after it was queued again{carry_on}",
            f"Scan job job_kept {idle}; Stash did not cancel it",
            f"Scan job job_stalled has not changed from READY in 1e-06 s{carry_on}",
            "Scan job job_queued was still queued at shutdown; cancelled it in Stash",
            "Scan job job_running is RUNNING at shutdown; no longer waiting on it",
        ]
        assert "stash_scan" not in get_progress_manager().active_tasks
        debug = [r.getMessage() for r in caplog.records if r.levelname == "DEBUG"]
        assert debug.count("Scan job job_blip: RUNNING, progress 0.5") == 1
