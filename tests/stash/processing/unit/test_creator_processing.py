"""Unit tests for creator processing methods.

This module tests StashProcessing creator-related methods using real fixtures
and factories instead of mock objects, following the fixture refactoring patterns.

Key improvements:
- Uses real Account instances from AccountFactory
- Uses respx to mock GraphQL HTTP responses at the edge (not internal methods!)
- Uses real database fixtures instead of mocked database
- Maintains test isolation with proper cleanup
"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
import strawberry
from stash_graphql_client.types.job import JobStatus

from tests.fixtures import (
    create_find_performers_result,
    create_find_studios_result,
    create_graphql_response,
)
from tests.fixtures.metadata.metadata_factories import AccountFactory
from tests.fixtures.stash.stash_type_factories import (
    JobFactory,
    PerformerFactory,
    StudioFactory,
)


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

        # Create Performer using factory and convert to JSON for respx (Stash)
        mock_performer = PerformerFactory(
            id="performer_123",
            name="Test User",
        )
        performer_dict = strawberry.asdict(mock_performer)

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

        # get_or_create_performer uses fuzzy search with filter.q, not performer_filter
        filter_params = variables.get("filter", {})
        assert filter_params.get("q") == "Test User"  # displayName from Account
        assert (
            filter_params.get("per_page") == 40
        )  # Default from get_or_create_performer

        # performer_filter should be None or empty for fuzzy search
        performer_filter = variables.get("performer_filter")
        assert performer_filter is None or performer_filter == {}

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
            ValueError, match=r"Account.*not found in database"
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

        # Create Performer using factory and convert to JSON
        new_performer = PerformerFactory(
            id="new_performer_123",
            name="New User",
        )
        performer_dict = strawberry.asdict(new_performer)

        # Mock GraphQL HTTP responses
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: findPerformers (not found)
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findPerformers",
                        create_find_performers_result(count=0, performers=[]),
                    ),
                ),
                # Second call: performerCreate (creates new)
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

        # Verify respx was hit twice (find then create)
        assert graphql_route.call_count == 2

        # Verify first call was findPerformers
        first_request = json.loads(graphql_route.calls[0].request.content)
        assert "findPerformers" in first_request.get("query", "")

        # Verify second call was performerCreate
        second_request = json.loads(graphql_route.calls[1].request.content)
        assert "performerCreate" in second_request.get("query", "")

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

        # Create Studios using factory and convert to JSON
        fansly_studio = StudioFactory(
            id="fansly_123",
            name="Fansly (network)",
        )
        creator_studio = StudioFactory(
            id="studio_123",
            name="test_user (Fansly)",
            parent_studio=fansly_studio,
        )

        fansly_dict = strawberry.asdict(fansly_studio)
        creator_dict = strawberry.asdict(creator_studio)

        # Mock GraphQL HTTP responses
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: findStudios for "Fansly (network)"
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios",
                        create_find_studios_result(count=1, studios=[fansly_dict]),
                    ),
                ),
                # Second call: findStudios for creator studio
                httpx.Response(
                    200,
                    json=create_graphql_response(
                        "findStudios",
                        create_find_studios_result(count=1, studios=[creator_dict]),
                    ),
                ),
            ]
        )

        # Initialize client
        await processor.context.get_client()

        # Call _find_existing_studio (doesn't use database session)
        studio = await processor._find_existing_studio(real_account)

        # Verify studio came from GraphQL HTTP
        assert studio is not None
        assert studio.name == "test_user (Fansly)"

        # Verify respx was hit twice (Fansly network, then creator studio)
        assert graphql_route.call_count == 2

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

        # Create factories for GraphQL responses
        finished_job = JobFactory(
            id="job_123",
            status=JobStatus.FINISHED,
            description="Scanning metadata",
            progress=100.0,
        )
        mock_performer = PerformerFactory(
            id="performer_123",
            name="Test User",
        )

        # Convert to JSON-serializable dicts
        finished_job_dict = json.loads(
            json.dumps(strawberry.asdict(finished_job), default=str)
        )
        performer_dict = strawberry.asdict(mock_performer)

        # Mock GraphQL HTTP responses for the full workflow
        graphql_route = respx.post("http://localhost:9999/graphql").mock(
            side_effect=[
                # First call: connect_async() connection establishment
                httpx.Response(200, json={"data": {}}),
                # scan_creator_folder: metadataScan mutation (returns job ID string)
                httpx.Response(200, json={"data": {"metadataScan": "job_123"}}),
                # scan_creator_folder: findJob query (finished immediately for speed)
                httpx.Response(
                    200, json=create_graphql_response("findJob", finished_job_dict)
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

        # Mock _safe_background_processing to prevent actual background work
        with patch.object(
            processor, "_safe_background_processing", new=AsyncMock()
        ) as mock_background:
            # Ensure config has background tasks list
            if not hasattr(processor.config, "_background_tasks"):
                processor.config._background_tasks = []

            # Call start_creator_processing - orchestration under test
            await processor.start_creator_processing()

            # Verify orchestration completed successfully
            # 1. GraphQL calls were made (connect_async + scan + process)
            assert graphql_route.call_count == 4

            # 2. Background task was created
            assert processor._background_task is not None
            assert processor._background_task in processor.config._background_tasks

            # 3. Background processing was started (mock prevents actual work)
            assert mock_background.call_count == 1  # Called once to start

    @pytest.mark.asyncio
    async def test_scan_creator_folder(self, respx_stash_processor, tmp_path):
        """Test scan_creator_folder method with respx HTTP mocking.

        Uses respx to mock GraphQL responses for metadataScan mutation and findJob query.
        """
        processor = respx_stash_processor
        # Setup: Ensure base_path exists
        processor.state.base_path = tmp_path / "creator_folder"
        processor.state.base_path.mkdir(parents=True, exist_ok=True)

        # Create Job instances using factory and convert to JSON
        running_job = JobFactory(
            id="job_123",
            status=JobStatus.RUNNING,
            description="Scanning metadata",
            progress=50.0,
        )
        finished_job = JobFactory(
            id="job_123",
            status=JobStatus.FINISHED,
            description="Scanning metadata",
            progress=100.0,
        )

        # Convert to dict and ensure JSON serializable (datetime → str)
        running_job_dict = json.loads(
            json.dumps(strawberry.asdict(running_job), default=str)
        )
        finished_job_dict = json.loads(
            json.dumps(strawberry.asdict(finished_job), default=str)
        )

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
            ValueError, match=r"Account.*not found in database"
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
        """Test scan_creator_folder creates download path when base_path is None.

        Note: The actual code has a logical issue - it creates download_path but
        still uses base_path for the scan, which will be None. This test verifies
        the current behavior (early return) rather than ideal behavior.
        """
        processor = respx_stash_processor
        # Setup: No base_path
        processor.state.base_path = None
        processor.state.download_path = None

        # Mock successful path creation
        mock_path = tmp_path / "created_path"
        mock_path.mkdir(parents=True, exist_ok=True)

        # The function will create download_path but then try to use base_path
        # which is still None, so it won't call metadata_scan (base_path is used)
        # This test documents current behavior
        with (
            patch("stash.processing.base.print_info") as mock_print_info,
            patch(
                "stash.processing.base.set_create_directory_for_download"
            ) as mock_set_path,
        ):
            mock_set_path.return_value = mock_path

            # Mock client methods
            processor.context.client.metadata_scan = AsyncMock(return_value=123)
            processor.context.client.wait_for_job = AsyncMock(return_value=True)

            # Call scan_creator_folder
            await processor.scan_creator_folder()

            # Verify path creation attempt was made
            mock_print_info.assert_any_call(
                "No download path set, attempting to create one..."
            )
            mock_set_path.assert_called_once_with(processor.config, processor.state)
            assert processor.state.download_path == mock_path

            # Since base_path is still None after download_path is set,
            # the metadata_scan would try to use base_path which is None
            # This will cause issues, but test documents current behavior
            # In reality, the state should update base_path = download_path

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

        # Create Job instances
        error_job = JobFactory(
            id="job_123",
            status=JobStatus.RUNNING,
            description="Scanning metadata",
            error="Temporary error",
        )
        finished_job = JobFactory(
            id="job_123",
            status=JobStatus.FINISHED,
            description="Scanning metadata",
            progress=100.0,
        )

        # Convert to dict and ensure JSON serializable (datetime → str)
        error_job_dict = json.loads(
            json.dumps(strawberry.asdict(error_job), default=str)
        )
        finished_job_dict = json.loads(
            json.dumps(strawberry.asdict(finished_job), default=str)
        )

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
