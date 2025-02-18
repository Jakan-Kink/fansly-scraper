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
    # Test connection failure
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.side_effect = httpx.ConnectError("Connection failed")
        with pytest.raises(ValueError, match="Failed to connect to.*Connection failed"):
            await StashClientBase.create()


@pytest.mark.asyncio
async def test_client_execute() -> None:
    """Test client execute method."""
    client = await StashClientBase.create()

    # Test error response
    mock_response = AsyncMock()
    mock_response.json = AsyncMock(return_value={"errors": ["Invalid query"]})
    mock_response.raise_for_status = AsyncMock()
    mock_post = AsyncMock(return_value=mock_response)

    with patch.object(client.client, "post", new=mock_post):
        with pytest.raises(ValueError, match="GraphQL errors: \\['Invalid query'\\]"):
            await client.execute("invalid { query }")

    # Test successful response
    mock_response = AsyncMock()
    mock_response.json = AsyncMock(
        return_value={
            "data": {
                "findScene": {
                    "id": "123",
                    "title": "Test Scene",
                }
            }
        }
    )
    mock_response.raise_for_status = AsyncMock()
    mock_post = AsyncMock(return_value=mock_response)

    with patch.object(client.client, "post", new=mock_post):
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
    mock_response = AsyncMock()
    mock_response.raise_for_status = AsyncMock(
        side_effect=httpx.HTTPError("Test error")
    )
    mock_response.json = AsyncMock(return_value={"error": "HTTP error"})
    mock_response.text = "Test error"
    mock_response.response = mock_response
    mock_post = AsyncMock(return_value=mock_response)

    with patch.object(client.client, "post", new=mock_post):
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
    mock_response = AsyncMock()
    mock_response.json = AsyncMock(
        return_value={
            "data": {
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
        }
    )
    mock_response.raise_for_status = AsyncMock()
    mock_post = AsyncMock(return_value=mock_response)

    with patch.object(client.client, "post", new=mock_post):
        result = await client.get_configuration_defaults()
        assert isinstance(result, ConfigDefaultSettingsResult)
        assert isinstance(result.scan, ScanMetadataOptions)
        assert isinstance(result.autoTag, AutoTagMetadataOptions)
        assert isinstance(result.generate, GenerateMetadataOptions)
        assert result.deleteFile is False
        assert result.deleteGenerated is False

    # Test empty response (should use defaults)
    mock_response.json = AsyncMock(return_value={"data": {}})
    with patch.object(client.client, "post", new=mock_post):
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
    mock_response = AsyncMock()
    mock_response.json = AsyncMock(
        return_value={"data": {"metadataGenerate": "job123"}}
    )
    mock_response.raise_for_status = AsyncMock()
    mock_post = AsyncMock(return_value=mock_response)

    with patch.object(client.client, "post", new=mock_post):
        job_id = await client.metadata_generate()
        assert job_id == "job123"

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

    with patch.object(client.client, "post", new=mock_post):
        job_id = await client.metadata_generate(options, input_data)
        assert job_id == "job123"

        # Verify the request
        call_args = mock_post.call_args
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

    # Mock configuration defaults
    mock_config_response = AsyncMock()
    mock_config_response.json = AsyncMock(
        return_value={
            "data": {
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
            }
        }
    )
    mock_config_response.raise_for_status = AsyncMock()

    # Mock scan response
    mock_scan_response = AsyncMock()
    mock_scan_response.json = AsyncMock(
        return_value={"data": {"metadataScan": "job123"}}
    )
    mock_scan_response.raise_for_status = AsyncMock()

    # Test with minimal options
    with patch.object(client.client, "post") as mock_post:
        mock_post.side_effect = [mock_config_response, mock_scan_response]
        job_id = await client.metadata_scan()
        assert job_id == "job123"

    # Test with paths and flags
    paths = ["/path/to/scan"]
    flags = {
        "rescan": True,
        "scanGenerateCovers": False,
    }

    with patch.object(client.client, "post") as mock_post:
        mock_post.side_effect = [mock_config_response, mock_scan_response]
        job_id = await client.metadata_scan(paths=paths, flags=flags)
        assert job_id == "job123"

        # Verify the request
        call_args = mock_post.call_args_list[1]  # Second call is the scan
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
    mock_response = AsyncMock()
    mock_response.json = AsyncMock(
        return_value={
            "data": {
                "findJob": {
                    "id": "job123",
                    "status": JobStatus.READY,
                    "subTasks": [],
                    "description": "Test job",
                    "progress": 0,
                    "error": None,
                }
            }
        }
    )
    mock_response.raise_for_status = AsyncMock()
    mock_post = AsyncMock(return_value=mock_response)

    with patch.object(client.client, "post", new=mock_post):
        job = await client.find_job("job123")
        assert isinstance(job, Job)
        assert job.id == "job123"
        assert job.status == JobStatus.READY

    # Test job not found
    mock_response.json = AsyncMock(return_value={"data": {"findJob": None}})
    with patch.object(client.client, "post", new=mock_post):
        job = await client.find_job("job123")
        assert job is None


@pytest.mark.asyncio
async def test_wait_for_job() -> None:
    """Test waiting for a job."""
    client = await StashClientBase.create()

    # Mock job responses
    running_job = {
        "data": {
            "findJob": {
                "id": "job123",
                "status": JobStatus.RUNNING,
                "subTasks": [],
                "description": "Test job",
                "progress": 50,
                "error": None,
            }
        }
    }
    finished_job = {
        "data": {
            "findJob": {
                "id": "job123",
                "status": JobStatus.FINISHED,
                "subTasks": [],
                "description": "Test job",
                "progress": 100,
                "error": None,
            }
        }
    }

    mock_response = AsyncMock()
    mock_response.raise_for_status = AsyncMock()
    mock_post = AsyncMock(return_value=mock_response)

    # Test successful job completion
    with patch.object(client.client, "post", new=mock_post):
        mock_response.json = AsyncMock(side_effect=[running_job, finished_job])
        job = await client.wait_for_job("job123", poll_interval=0.1)
        assert isinstance(job, Job)
        assert job.id == "job123"
        assert job.status == JobStatus.FINISHED
        assert job.progress == 100

    # Test job error
    error_job = {
        "data": {
            "findJob": {
                "id": "job123",
                "status": JobStatus.ERROR,
                "subTasks": [],
                "description": "Test job",
                "progress": 50,
                "error": "Test error",
            }
        }
    }
    with patch.object(client.client, "post", new=mock_post):
        mock_response.json = AsyncMock(side_effect=[running_job, error_job])
        with pytest.raises(ValueError, match="Job failed: Test error"):
            await client.wait_for_job("job123", poll_interval=0.1)


@pytest.mark.asyncio
async def test_context_manager() -> None:
    """Test context manager functionality."""
    async with StashClientBase() as client:
        assert client._initialized is True
        assert hasattr(client, "client")
        assert hasattr(client, "url")

    # Client should be closed after context
    assert client.client.is_closed is True
