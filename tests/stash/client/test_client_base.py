"""Unit tests for StashClientBase.

These tests mock at the HTTP boundary using respx, allowing real code execution
through the entire GraphQL client stack.
"""

import logging
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
import respx

from errors import StashConnectionError
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


@respx.mock
@pytest.mark.asyncio
async def test_client_create() -> None:
    """Test client creation."""
    # Mock any potential HTTP requests from gql client initialization
    # GQL clients might make introspection or connection check requests
    respx.post(url__regex=r".*graphql").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )

    # Test with minimal conn
    client = await StashClientBase.create(conn={})
    assert client.url == "http://localhost:9999/graphql"
    assert client._initialized is True
    await client.close()

    # Test with full conn
    client = await StashClientBase.create(
        conn={
            "Scheme": "http",
            "Host": "localhost",
            "Port": 9999,
            "ApiKey": "",
            "Logger": logging.getLogger("test"),
        },
        verify_ssl=False,
    )
    assert client.url == "http://localhost:9999/graphql"
    assert client.log.name == "test"
    assert client._initialized is True
    await client.close()

    # Test with 0.0.0.0 host (should convert to 127.0.0.1)
    bad_host = "0.0.0.0"  # noqa: S104 - testing host conversion
    client = await StashClientBase.create(conn={"Host": bad_host})
    assert "127.0.0.1" in client.url
    assert client._initialized is True
    await client.close()

    # Test with None conn (should use defaults)
    client = await StashClientBase.create(conn=None)
    assert client.url == "http://localhost:9999/graphql"
    assert client._initialized is True
    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_client_initialization_error() -> None:
    """Test client initialization error handling.

    When the HTTP connection fails during client initialization,
    the error should propagate up from create().

    Note: We temporarily enable fetch_schema_from_transport to trigger
    an actual HTTP call during initialization (normally disabled due to
    Stash schema validation issues).
    """
    from gql import Client

    # Mock the GraphQL endpoint to return a connection error
    # This simulates a Stash server that is not running
    respx.post("http://localhost:9999/graphql").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    # Patch Client to enable schema fetching during init
    # This makes connect_async() actually make an HTTP call
    original_init = Client.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["fetch_schema_from_transport"] = True
        return original_init(self, *args, **kwargs)

    with (
        patch.object(Client, "__init__", patched_init),
        pytest.raises(Exception),
    ):
        await StashClientBase.create()


@respx.mock
@pytest.mark.asyncio
async def test_client_execute() -> None:
    """Test client execute method."""
    # Mock GraphQL endpoint for initialization
    graphql_route = respx.post("http://localhost:9999/graphql")
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))

    # Create client
    client = await StashClientBase.create()

    # Test successful response with hello query
    graphql_route.mock(
        return_value=httpx.Response(200, json={"data": {"hello": "world"}})
    )
    result = await client.execute("query { hello }")
    # client.execute() returns just the data portion, not the full response
    assert result == {"hello": "world"}

    # Test successful response with variables
    graphql_route.mock(
        return_value=httpx.Response(
            200, json={"data": {"findScene": {"id": "123", "title": "Test Scene"}}}
        )
    )

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
    # client.execute() returns just the data portion
    assert isinstance(result, dict)
    assert "findScene" in result
    assert result["findScene"]["id"] == "123"
    assert result["findScene"]["title"] == "Test Scene"

    # Test HTTP network error
    graphql_route.mock(side_effect=httpx.NetworkError("Connection failed"))

    with pytest.raises(
        StashConnectionError,
        match=r"Failed to connect to .*/graphql: Connection failed",
    ):
        await client.execute("query { hello }")

    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_datetime_conversion() -> None:
    """Test datetime conversion in variables."""
    # Mock GraphQL endpoint for initialization
    graphql_route = respx.post("http://localhost:9999/graphql")
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))

    # Create client
    client = await StashClientBase.create()

    # Test datetime conversion
    now = datetime.now(UTC)
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

    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_get_configuration_defaults() -> None:
    """Test getting configuration defaults."""
    # Mock GraphQL endpoint for initialization
    graphql_route = respx.post("http://localhost:9999/graphql")
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))

    # Create client
    client = await StashClientBase.create()

    # Test with complete configuration
    graphql_route.mock(
        return_value=httpx.Response(
            200,
            json={
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
                            "autoTag": {
                                "performers": ["name", "alias"],
                                "studios": ["name"],
                                "tags": ["name", "alias"],
                            },
                            "generate": {
                                "covers": True,
                                "previews": True,
                                "imagePreviews": True,
                                "sprites": True,
                                "phashes": True,
                                "markers": True,
                                "transcodes": True,
                                "markerImagePreviews": True,
                                "markerScreenshots": True,
                                "interactiveHeatmapsSpeeds": True,
                                "imageThumbnails": True,
                                "clipPreviews": True,
                            },
                            "deleteFile": False,
                            "deleteGenerated": True,
                        }
                    }
                }
            },
        )
    )

    result = await client.get_configuration_defaults()

    # Test result types
    assert isinstance(result, ConfigDefaultSettingsResult)
    assert isinstance(result.scan, ScanMetadataOptions)
    assert isinstance(result.autoTag, AutoTagMetadataOptions)
    assert isinstance(result.generate, GenerateMetadataOptions)

    # Test scan options
    assert result.scan.rescan is False
    assert result.scan.scanGenerateCovers is True
    assert result.scan.scanGeneratePreviews is True
    assert result.scan.scanGenerateImagePreviews is True
    assert result.scan.scanGenerateSprites is True
    assert result.scan.scanGeneratePhashes is True
    assert result.scan.scanGenerateThumbnails is True
    assert result.scan.scanGenerateClipPreviews is True

    # Test autoTag options
    assert result.autoTag.performers == ["name", "alias"]
    assert result.autoTag.studios == ["name"]
    assert result.autoTag.tags == ["name", "alias"]

    # Test generate options
    assert result.generate.covers is True
    assert result.generate.previews is True
    assert result.generate.imagePreviews is True
    assert result.generate.sprites is True
    assert result.generate.phashes is True
    assert result.generate.markers is True
    assert result.generate.transcodes is True
    assert result.generate.markerImagePreviews is True
    assert result.generate.markerScreenshots is True
    assert result.generate.interactiveHeatmapsSpeeds is True
    assert result.generate.imageThumbnails is True
    assert result.generate.clipPreviews is True

    # Test delete options
    assert result.deleteFile is False
    assert result.deleteGenerated is True

    # Test with different deleteGenerated value
    graphql_route.mock(
        return_value=httpx.Response(
            200,
            json={
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
                            "autoTag": {
                                "performers": ["name", "alias"],
                                "studios": ["name"],
                                "tags": ["name", "alias"],
                            },
                            "generate": {
                                "covers": True,
                                "previews": True,
                                "imagePreviews": True,
                                "sprites": True,
                                "phashes": True,
                                "markers": True,
                                "transcodes": True,
                                "markerImagePreviews": True,
                                "markerScreenshots": True,
                                "interactiveHeatmapsSpeeds": True,
                                "imageThumbnails": True,
                                "clipPreviews": True,
                            },
                            "deleteFile": False,
                            "deleteGenerated": False,
                        }
                    }
                }
            },
        )
    )
    result = await client.get_configuration_defaults()
    assert isinstance(result, ConfigDefaultSettingsResult)
    assert isinstance(result.scan, ScanMetadataOptions)
    assert isinstance(result.autoTag, AutoTagMetadataOptions)
    assert isinstance(result.generate, GenerateMetadataOptions)

    # Check default values are set correctly
    assert result.scan.rescan is False
    assert result.scan.scanGenerateCovers is True
    assert result.scan.scanGeneratePreviews is True
    assert result.scan.scanGenerateImagePreviews is True
    assert result.scan.scanGenerateSprites is True
    assert result.scan.scanGeneratePhashes is True
    assert result.scan.scanGenerateThumbnails is True
    assert result.scan.scanGenerateClipPreviews is True

    assert result.autoTag.performers == ["name", "alias"]
    assert result.autoTag.studios == ["name"]
    assert result.autoTag.tags == ["name", "alias"]

    assert result.generate.covers is True
    assert result.generate.previews is True
    assert result.generate.imagePreviews is True
    assert result.generate.sprites is True
    assert result.generate.phashes is True
    assert result.generate.markers is True
    assert result.generate.transcodes is True
    assert result.generate.markerImagePreviews is True
    assert result.generate.markerScreenshots is True
    assert result.generate.interactiveHeatmapsSpeeds is True
    assert result.generate.imageThumbnails is True
    assert result.generate.clipPreviews is True

    assert result.deleteFile is False
    assert result.deleteGenerated is False

    # Test error handling
    graphql_route.mock(side_effect=ValueError("Test error"))
    with pytest.raises(StashConnectionError, match="Failed to connect"):
        await client.get_configuration_defaults()

    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_metadata_generate() -> None:
    """Test metadata generation."""
    # Mock GraphQL endpoint for initialization
    graphql_route = respx.post("http://localhost:9999/graphql")
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))

    # Create client
    client = await StashClientBase.create()

    # Test with minimal options
    graphql_route.mock(
        return_value=httpx.Response(200, json={"data": {"metadataGenerate": "123"}})
    )
    job_id = await client.metadata_generate()
    assert isinstance(job_id, str)
    assert job_id == "123"

    # Test with all possible generate options (without previewOptions to avoid enum serialization issues)
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
        covers=True,
        sprites=True,
        previews=True,
        imagePreviews=True,
        markers=True,
        markerImagePreviews=True,
        markerScreenshots=True,
        transcodes=True,
        forceTranscodes=True,
        phashes=True,
        interactiveHeatmapsSpeeds=True,
        imageThumbnails=True,
        clipPreviews=True,
        sceneIDs=["1", "2"],  # Using proper numeric IDs as strings
        markerIDs=["1", "2"],  # Using proper numeric IDs as strings
        overwrite=True,
    )

    graphql_route.mock(
        return_value=httpx.Response(200, json={"data": {"metadataGenerate": "123"}})
    )
    job_id = await client.metadata_generate(options=options, input_data=input_data)
    assert job_id == "123"

    # Test job status monitoring with multiple sequential responses
    call_count = 0

    def mock_response(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json={"data": {"metadataGenerate": "123"}})
        if call_count == 2:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "findJob": {
                            "id": "123",
                            "status": "RUNNING",
                            "description": "Generating metadata",
                            "progress": 50,
                            "error": None,
                            "addTime": "2024-04-06T00:00:00Z",
                            "subTasks": [],
                        }
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "findJob": {
                        "id": "123",
                        "status": "FINISHED",
                        "description": "Metadata generation complete",
                        "progress": 100,
                        "error": None,
                        "addTime": "2024-04-06T00:00:00Z",
                        "subTasks": [],
                    }
                }
            },
        )

    graphql_route.mock(side_effect=mock_response)

    job_id = await client.metadata_generate()
    assert job_id == "123"
    success = await client.wait_for_job(job_id, period=0.1)
    assert success is True

    # Test error cases - no job ID returned
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))
    with pytest.raises(ValueError, match="No job ID returned from server"):
        await client.metadata_generate()

    # Test network error
    graphql_route.mock(side_effect=httpx.NetworkError("Connection failed"))
    with pytest.raises(StashConnectionError, match="Failed to connect"):
        await client.metadata_generate()

    # Test invalid options
    graphql_route.mock(
        return_value=httpx.Response(200, json={"data": {"metadataGenerate": "123"}})
    )
    with pytest.raises(TypeError):
        await client.metadata_generate(options="invalid")

    # Test invalid input data
    with pytest.raises(TypeError):
        await client.metadata_generate(input_data="invalid")

    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_metadata_scan() -> None:
    """Test metadata scanning."""
    # Mock GraphQL endpoint for initialization
    graphql_route = respx.post("http://localhost:9999/graphql")
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))

    # Create client
    client = await StashClientBase.create()

    # Test basic scan
    graphql_route.mock(
        return_value=httpx.Response(200, json={"data": {"metadataScan": "123"}})
    )
    job_id = await client.metadata_scan()
    assert job_id == "123"

    # Test with paths and flags
    graphql_route.mock(
        return_value=httpx.Response(200, json={"data": {"metadataScan": "123"}})
    )
    paths = ["/test/path"]
    flags = ScanMetadataInput(paths=paths, rescan=True)
    job_id = await client.metadata_scan(paths=paths, flags=flags.__dict__)
    assert job_id == "123"

    # Test error handling - None returned
    graphql_route.mock(
        return_value=httpx.Response(200, json={"data": {"metadataScan": None}})
    )
    with pytest.raises(ValueError, match="Failed to start metadata scan"):
        await client.metadata_scan()

    # Test network error
    graphql_route.mock(side_effect=httpx.NetworkError("Connection failed"))
    with pytest.raises(ValueError, match="Failed to start metadata scan"):
        await client.metadata_scan()

    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_find_job() -> None:
    """Test finding a job."""
    # Mock GraphQL endpoint for initialization
    graphql_route = respx.post("http://localhost:9999/graphql")
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))

    # Create client
    client = await StashClientBase.create()

    # Test complete job
    graphql_route.mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "findJob": {
                        "id": "123",
                        "status": "READY",
                        "subTasks": [],
                        "description": "Test job",
                        "progress": 0,
                        "addTime": "2024-04-06T00:00:00Z",
                        "error": None,
                    }
                }
            },
        )
    )

    job = await client.find_job("123")
    assert isinstance(job, Job)
    assert job.id == "123"
    assert job.status == "READY"
    assert job.description == "Test job"
    assert job.progress == 0
    assert job.error is None
    assert job.addTime == "2024-04-06T00:00:00Z"
    assert isinstance(job.subTasks, list)
    assert len(job.subTasks) == 0

    # Test job with all possible statuses
    for status in JobStatus:
        graphql_route.mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "findJob": {
                            "id": "123",
                            "status": status,
                            "subTasks": [],
                            "description": "Test job",
                            "progress": 50,
                            "addTime": "2024-04-06T00:00:00Z",
                            "error": None,
                        }
                    }
                },
            )
        )
        job = await client.find_job("123")
        assert job is not None
        assert job.status == status

    # Test job with error
    graphql_route.mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "findJob": {
                        "id": "123",
                        "status": JobStatus.FAILED,
                        "subTasks": [],
                        "description": "Test job",
                        "progress": 50,
                        "addTime": "2024-04-06T00:00:00Z",
                        "error": "Test error message",
                    }
                }
            },
        )
    )
    job = await client.find_job("123")
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert job.error == "Test error message"

    # Test job with subtasks
    graphql_route.mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "findJob": {
                        "id": "123",
                        "status": JobStatus.READY,
                        "subTasks": [
                            {
                                "description": "Subtask 1",
                                "status": JobStatus.READY,
                                "progress": 0,
                                "error": None,
                            }
                        ],
                        "description": "Test job",
                        "progress": 0,
                        "addTime": "2024-04-06T00:00:00Z",
                        "error": None,
                    }
                }
            },
        )
    )
    job = await client.find_job("123")
    assert job is not None
    assert len(job.subTasks) == 1
    assert isinstance(job.subTasks[0], dict)  # Subtasks remain as dictionaries
    assert job.subTasks[0]["description"] == "Subtask 1"
    assert job.subTasks[0]["status"] == JobStatus.READY

    # Test job with empty ID
    job = await client.find_job("")
    assert job is None

    # Test job not found
    graphql_route.mock(
        return_value=httpx.Response(200, json={"data": {"findJob": None}})
    )
    job = await client.find_job("123")
    assert job is None

    # Test error response
    graphql_route.mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"findJob": None},
                "errors": [{"message": "Invalid job ID"}],
            },
        )
    )
    job = await client.find_job("123")
    assert job is None

    # Test network error
    graphql_route.mock(side_effect=httpx.NetworkError("Connection failed"))
    job = await client.find_job("123")
    assert job is None

    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_wait_for_job() -> None:
    """Test waiting for a job."""
    # Mock GraphQL endpoint for initialization
    graphql_route = respx.post("http://localhost:9999/graphql")
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))

    # Create client
    client = await StashClientBase.create()

    # Test complete job
    graphql_route.mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "findJob": {
                        "id": "123",
                        "status": "FINISHED",
                        "description": "Test job",
                        "progress": 100,
                        "error": None,
                        "addTime": "2024-04-06T00:00:00Z",
                        "subTasks": [],
                    }
                }
            },
        )
    )
    result = await client.wait_for_job("123", period=0.1)
    assert result is True

    # Test job that finishes with different status
    graphql_route.mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "findJob": {
                        "id": "123",
                        "status": "CANCELLED",
                        "description": "Test job",
                        "progress": 50,
                        "error": "Test error",
                        "addTime": "2024-04-06T00:00:00Z",
                        "subTasks": [],
                    }
                }
            },
        )
    )
    result = await client.wait_for_job("123", period=0.1)
    assert result is False

    # Test job not found
    graphql_route.mock(
        return_value=httpx.Response(200, json={"data": {"findJob": None}})
    )
    with pytest.raises(TypeError, match="Job 123 not found"):
        await client.wait_for_job("123", period=0.1)

    # Test timeout
    graphql_route.mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "findJob": {
                        "id": "123",
                        "status": "RUNNING",
                        "description": "Test job",
                        "progress": 50,
                        "error": None,
                        "addTime": "2024-04-06T00:00:00Z",
                        "subTasks": [],
                    }
                }
            },
        )
    )
    with pytest.raises(TimeoutError):
        await client.wait_for_job("123", period=0.1, timeout_seconds=0.2)

    await client.close()


@respx.mock
@pytest.mark.asyncio
async def test_context_manager() -> None:
    """Test context manager functionality."""
    # Mock GraphQL endpoint for initialization
    graphql_route = respx.post("http://localhost:9999/graphql")
    graphql_route.mock(return_value=httpx.Response(200, json={"data": {}}))

    # Create client
    client = await StashClientBase.create()

    # Set up mock response for hello query
    graphql_route.mock(
        return_value=httpx.Response(200, json={"data": {"hello": "world"}})
    )

    # Test context manager with a valid query
    async with client:
        result = await client.execute("query { hello }")
        # client.execute() returns just the data portion
        assert result == {"hello": "world"}

    # Note: Cleanup happens automatically when exiting the context manager
    # The client.close() is called by __aexit__
