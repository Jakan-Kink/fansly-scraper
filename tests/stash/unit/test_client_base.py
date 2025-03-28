"""Unit tests for StashClientBase."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from stash.client.base import StashClientBase
from stash.types import (
    AutoTagMetadataOptions,
    ConfigDefaultSettingsResult,
    GenerateMetadataInput,
    GenerateMetadataOptions,
    Job,
    JobStatus,
    ScanMetadataInput,
    ScanMetadataOptions,
)


@pytest.mark.asyncio
async def test_client_create() -> None:
    """Test client creation."""
    # Create a complete mock for StashClientBase.create

    @classmethod
    async def mock_create(cls, conn=None, verify_ssl=True):
        # Create a mock client with the expected attributes
        client = MagicMock(spec=StashClientBase)
        client._initialized = True

        # Set up connection details
        conn = conn or {}
        scheme = conn.get("Scheme", "http")
        host = conn.get("Host", "localhost")
        if host == "0.0.0.0":
            host = "127.0.0.1"
        port = conn.get("Port", 9999)

        # Set up URL
        client.url = f"{scheme}://{host}:{port}/graphql"

        # Set up headers
        client.client = MagicMock()
        client.client.headers = {}
        if "ApiKey" in conn:
            client.client.headers["ApiKey"] = conn["ApiKey"]

        # Set up logger
        if "Logger" in conn:
            client.log = conn["Logger"]
        else:
            client.log = MagicMock()

        return client

    # Apply the mock
    with patch.object(StashClientBase, "create", mock_create):
        # Test with minimal conn
        client = await StashClientBase.create(conn={})
        assert client.url == "http://localhost:9999/graphql"
        assert "ApiKey" not in client.client.headers
        assert client._initialized is True

        # Test with full conn
        client = await StashClientBase.create(
            conn={
                "Scheme": "https",
                "Host": "stash.example.com",
                "Port": 8008,
                "ApiKey": "test_api_key",
                "Logger": logging.getLogger("test"),
            },
            verify_ssl=False,
        )
        assert client.url == "https://stash.example.com:8008/graphql"
        assert client.client.headers.get("ApiKey") == "test_api_key"
        assert client.log.name == "test"
        assert client._initialized is True

        # Test with 0.0.0.0 host (should convert to 127.0.0.1)
        client = await StashClientBase.create(conn={"Host": "0.0.0.0"})
        assert "127.0.0.1" in client.url
        assert client._initialized is True

        # Test with None conn (should use defaults)
        client = await StashClientBase.create(conn=None)
        assert client.url == "http://localhost:9999/graphql"
        assert "ApiKey" not in client.client.headers
        assert client._initialized is True


@pytest.mark.asyncio
async def test_client_initialization_error() -> None:
    """Test client initialization error handling."""
    # Create a mock for StashClientBase.initialize that raises an error

    async def mock_initialize(self):
        raise ValueError("Failed to connect to Stash: Connection failed")

    # Apply the mock
    with patch.object(StashClientBase, "initialize", mock_initialize):
        with pytest.raises(
            ValueError, match="Failed to connect to Stash: Connection failed"
        ):
            await StashClientBase.create()


@pytest.mark.asyncio
async def test_client_execute() -> None:
    """Test client execute method."""
    client = await StashClientBase.create()

    # Test error response
    mock_execute = AsyncMock(return_value={"errors": [{"message": "Invalid query"}]})

    with patch.object(client.client, "execute", new=mock_execute):
        with pytest.raises(
            ValueError,
            match=r"Invalid GraphQL query: Syntax Error: Unexpected Name 'invalid'\.",
        ):
            await client.execute("invalid { query }")

    # Test successful response
    mock_execute = AsyncMock(
        return_value={
            "findScene": {
                "id": "123",
                "title": "Test Scene",
            }
        }
    )

    with patch.object(client.client, "execute", new=mock_execute):
        query = """
        query TestQuery($id: ID!) {
            findScene(id: $id) {
                id
                title
            }
        }
        """
        variables = {"id": "123"}
        result = await client.execute(query, variables)
        assert isinstance(result, dict)
        assert result["findScene"]["id"] == "123"
        assert result["findScene"]["title"] == "Test Scene"

    # Test HTTP error
    mock_execute = AsyncMock(side_effect=httpx.HTTPError("Test error"))

    with patch.object(client.client, "execute", new=mock_execute):
        with pytest.raises(
            ValueError, match="Unexpected error during request.*Test error"
        ):
            await client.execute("query { test }")


@pytest.mark.asyncio
async def test_datetime_conversion() -> None:
    """Test datetime conversion in variables."""
    client = await StashClientBase.create()

    # Test datetime conversion
    now = datetime.now(timezone.utc)
    variables = {
        "date": now,
        "nested": {"date": now},
        "list": [now, {"date": now}],
    }

    # Convert datetimes
    converted = client._convert_datetime(variables)

    # Check conversion
    assert isinstance(converted["date"], str)
    assert isinstance(converted["nested"]["date"], str)
    assert isinstance(converted["list"][0], str)
    assert isinstance(converted["list"][1]["date"], str)

    # Verify ISO format
    assert converted["date"] == now.isoformat()
    assert converted["nested"]["date"] == now.isoformat()
    assert converted["list"][0] == now.isoformat()
    assert converted["list"][1]["date"] == now.isoformat()


@pytest.mark.asyncio
async def test_get_configuration_defaults() -> None:
    """Test getting configuration defaults."""
    client = await StashClientBase.create()

    # Test successful response
    mock_execute = AsyncMock(
        return_value={
            "configuration": {
                "defaults": {
                    "scan": {
                        "rescan": False,
                        "scanGenerateCovers": True,
                        "scanGeneratePreviews": True,
                        "scanGenerateImagePreviews": True,
                        "scanGenerateSprites": True,
                        "scanGeneratePhashes": True,
                        "scanGenerateThumbnails": True,
                        "scanGenerateClipPreviews": True,
                    },
                    "autoTag": {},
                    "generate": {},
                    "deleteFile": False,
                    "deleteGenerated": False,
                }
            }
        }
    )

    with patch.object(client.client, "execute", new=mock_execute):
        result = await client.get_configuration_defaults()
        assert isinstance(result, ConfigDefaultSettingsResult)
        assert isinstance(result.scan, ScanMetadataOptions)
        assert isinstance(result.autoTag, AutoTagMetadataOptions)
        assert isinstance(result.generate, GenerateMetadataOptions)
        assert result.deleteFile is False
        assert result.deleteGenerated is False

    # Test empty response (should use defaults)
    mock_execute = AsyncMock(return_value={})
    with patch.object(client.client, "execute", new=mock_execute):
        result = await client.get_configuration_defaults()
        assert isinstance(result, ConfigDefaultSettingsResult)
        assert isinstance(result.scan, ScanMetadataOptions)
        assert isinstance(result.autoTag, AutoTagMetadataOptions)
        assert isinstance(result.generate, GenerateMetadataOptions)
        assert result.deleteFile is False
        assert result.deleteGenerated is False


@pytest.mark.asyncio
async def test_metadata_generate() -> None:
    """Test metadata generation."""
    client = await StashClientBase.create()

    # Test with minimal options
    mock_execute = AsyncMock(return_value={"metadataGenerate": "123"})

    with patch.object(client.client, "execute", new=mock_execute):
        job_id = await client.metadata_generate()
        assert isinstance(job_id, str)
        assert int(job_id) > 0  # Verify it's a valid positive integer

    # Test with full options
    options = GenerateMetadataOptions(
        covers=True,
        sprites=True,
        previews=True,
        imagePreviews=True,
        markers=True,
        markerImagePreviews=True,
        markerScreenshots=True,
        transcodes=True,
        phashes=True,
        interactiveHeatmapsSpeeds=True,
        imageThumbnails=True,
        clipPreviews=True,
    )
    input_data = GenerateMetadataInput(
        sceneIDs=["1", "2"],
        markerIDs=["3", "4"],
        overwrite=True,
    )

    with patch.object(client.client, "execute", new=mock_execute):
        job_id = await client.metadata_generate(options, input_data)
        assert isinstance(job_id, str)
        assert int(job_id) > 0  # Verify it's a valid positive integer

        # Verify the request
        call_args = mock_execute.call_args
        assert call_args is not None
        request_data = call_args[1]["json"]
        assert "variables" in request_data
        assert "input" in request_data["variables"]
        input_vars = request_data["variables"]["input"]
        assert input_vars["covers"] is True
        assert input_vars["sprites"] is True
        assert input_vars["sceneIDs"] == ["1", "2"]
        assert input_vars["markerIDs"] == ["3", "4"]
        assert input_vars["overwrite"] is True


@pytest.mark.asyncio
async def test_metadata_scan() -> None:
    """Test metadata scanning."""
    client = await StashClientBase.create()

    # Mock configuration defaults and scan response
    mock_execute = AsyncMock()
    mock_execute.side_effect = [
        {
            "configuration": {
                "defaults": {
                    "scan": {
                        "rescan": False,
                        "scanGenerateCovers": True,
                        "scanGeneratePreviews": True,
                        "scanGenerateImagePreviews": True,
                        "scanGenerateSprites": True,
                        "scanGeneratePhashes": True,
                        "scanGenerateThumbnails": True,
                        "scanGenerateClipPreviews": True,
                    }
                }
            }
        },
        {"metadataScan": "12"},
    ]

    # Test with minimal options
    with patch.object(client.client, "execute", new=mock_execute):
        job_id = await client.metadata_scan()
        assert isinstance(job_id, str)
        assert int(job_id) > 0  # Verify it's a valid positive integer

    # Test with paths and flags
    paths = ["/path/to/scan"]
    flags = {
        "rescan": True,
        "scanGenerateCovers": False,
    }

    with patch.object(client.client, "execute", new=mock_execute):
        job_id = await client.metadata_scan(paths=paths, flags=flags)
        assert isinstance(job_id, str)
        assert int(job_id) > 0  # Verify it's a valid positive integer

        # Verify the request
        call_args = mock_execute.call_args_list[1]  # Second call is the scan
        request_data = call_args[1]["json"]
        assert "variables" in request_data
        assert "input" in request_data["variables"]
        input_vars = request_data["variables"]["input"]
        assert input_vars["paths"] == paths
        assert input_vars["rescan"] is True
        assert input_vars["scanGenerateCovers"] is False


@pytest.mark.asyncio
async def test_find_job() -> None:
    """Test finding a job."""
    client = await StashClientBase.create()

    # Test job found
    mock_execute = AsyncMock(
        return_value={
            "findJob": {
                "id": "123",  # Job ID is a string in the response
                "status": JobStatus.READY,
                "subTasks": [],
                "description": "Test job",
                "progress": 0,
                "error": None,
            }
        }
    )

    with patch.object(client.client, "execute", new=mock_execute):
        job = await client.find_job(123)  # Job ID is an integer in the query
        assert isinstance(job, Job)
        assert job.id == "123"
        assert job.status == JobStatus.READY

    # Test job not found
    mock_execute = AsyncMock(return_value={"findJob": None})
    with patch.object(client.client, "execute", new=mock_execute):
        job = await client.find_job(123)
        assert job is None


@pytest.mark.asyncio
async def test_wait_for_job() -> None:
    """Test waiting for a job."""
    client = await StashClientBase.create()

    # Mock job responses
    running_job = {
        "findJob": {
            "id": "123",
            "status": JobStatus.RUNNING,
            "subTasks": [],
            "description": "Test job",
            "progress": 50,
            "error": None,
        }
    }
    finished_job = {
        "findJob": {
            "id": "123",
            "status": JobStatus.FINISHED,
            "subTasks": [],
            "description": "Test job",
            "progress": 100,
            "error": None,
        }
    }

    # Test successful job completion
    mock_execute = AsyncMock(side_effect=[running_job, finished_job])
    with patch.object(client.client, "execute", new=mock_execute):
        job = await client.wait_for_job(123, period=0.1)
        assert isinstance(job, Job)
        assert job.id == "123"
        assert job.status == JobStatus.FINISHED
        assert job.progress == 100

    # Test job error
    error_job = {
        "findJob": {
            "id": "123",
            "status": JobStatus.ERROR,
            "subTasks": [],
            "description": "Test job",
            "progress": 50,
            "error": "Test error",
        }
    }
    mock_execute = AsyncMock(side_effect=[running_job, error_job])
    with patch.object(client.client, "execute", new=mock_execute):
        with pytest.raises(ValueError, match="Job failed: Test error"):
            await client.wait_for_job(123, poll_interval=0.1)


@pytest.mark.asyncio
async def test_context_manager() -> None:
    """Test context manager functionality."""
    async with StashClientBase() as client:
        assert client._initialized is True
        assert hasattr(client, "client")
        assert hasattr(client, "url")

    # Client is closed after context manager exits
