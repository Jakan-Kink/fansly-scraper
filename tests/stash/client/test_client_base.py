"""Unit tests for StashClientBase."""

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from graphql import (
    GraphQLArgument,
    GraphQLField,
    GraphQLID,
    GraphQLInputField,
    GraphQLInputObjectType,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLSchema,
    GraphQLString,
)

from stash.client.base import StashClientBase
from stash.types import (
    AutoTagMetadataOptions,
    ConfigDefaultSettingsResult,
    GenerateMetadataInput,
    GenerateMetadataOptions,
    GeneratePreviewOptions,
    Job,
    JobStatus,
    PreviewPreset,
    ScanMetadataInput,
    ScanMetadataOptions,
)


@pytest.fixture
def base_client(mock_transport, mock_client):
    with (
        patch("gql.Client", return_value=mock_client),
        patch("gql.transport.httpx.HTTPXAsyncTransport", return_value=mock_transport),
        patch(
            "gql.transport.websockets.WebsocketsTransport", return_value=mock_transport
        ),
    ):
        client = StashClientBase()
        client.client = mock_client
        client._initialized = True
        client.http_transport = mock_transport
        client.ws_transport = mock_transport
        return client


@pytest.mark.asyncio
async def test_client_create(mock_session, mock_client) -> None:
    """Test client creation."""
    # Create a basic schema that we'll use to mock the schema fetch
    query_type = GraphQLObjectType(
        name="Query",
        fields={"test": GraphQLField(type_=GraphQLString)},
    )
    mock_schema = GraphQLSchema(query=query_type)
    mock_session.client = MagicMock(schema=mock_schema)

    # Set up mock client to return our session
    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()  # Add mock for close_async

    # Create mock transport
    mock_transport = MagicMock()
    mock_transport.headers = {}

    with (
        patch("gql.Client", return_value=mock_client),
        patch("gql.transport.httpx.HTTPXAsyncTransport", return_value=mock_transport),
        patch("gql.transport.websockets.WebsocketsTransport"),
    ):
        # Test with minimal conn
        client = await StashClientBase.create(conn={})
        assert client.url == "http://localhost:9999/graphql"
        assert not any(h.get("ApiKey") for h in [getattr(client, "headers", {}), {}])
        assert client._initialized is True

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
        assert not any(h.get("ApiKey") for h in [getattr(client, "headers", {}), {}])
        assert client.log.name == "test"
        assert client._initialized is True

        # Test with 0.0.0.0 host (should convert to 127.0.0.1)
        bad_host = "0.0.0.0"  # noqa: S104 - testing host conversion
        client = await StashClientBase.create(conn={"Host": bad_host})
        assert "127.0.0.1" in client.url
        assert client._initialized is True

        # Test with None conn (should use defaults)
        client = await StashClientBase.create(conn=None)
        assert client.url == "http://localhost:9999/graphql"
        assert not any(h.get("ApiKey") for h in [getattr(client, "headers", {}), {}])
        assert client._initialized is True


@pytest.mark.asyncio
async def test_client_initialization_error() -> None:
    """Test client initialization error handling."""
    # Create a mock for initialize that raises an error
    with (
        patch.object(
            StashClientBase,
            "initialize",
            AsyncMock(
                side_effect=ValueError("Failed to connect to Stash: Connection failed")
            ),
        ),
        pytest.raises(
            ValueError, match="Failed to connect to Stash: Connection failed"
        ),
    ):
        await StashClientBase.create()


@pytest.mark.asyncio
async def test_client_execute(mock_session, mock_client) -> None:
    """Test client execute method."""
    # Create a basic schema with ID type and findScene field for testing
    query_type = GraphQLObjectType(
        name="Query",
        fields={
            "hello": GraphQLField(type_=GraphQLString),
            "findScene": GraphQLField(
                type_=GraphQLObjectType(
                    name="Scene",
                    fields={
                        "id": GraphQLField(type_=GraphQLString),
                        "title": GraphQLField(type_=GraphQLString),
                    },
                ),
                args={"id": GraphQLArgument(type_=GraphQLNonNull(GraphQLID))},
            ),
        },
    )
    mock_schema = GraphQLSchema(query=query_type)
    mock_session.client = MagicMock(schema=mock_schema)
    mock_client.schema = mock_schema  # Set schema on client directly

    # Set up mock client with session
    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()

    # Create mock transport
    mock_transport = MagicMock()
    mock_transport.headers = {}

    with (
        patch("gql.Client", return_value=mock_client),
        patch("gql.transport.httpx.HTTPXAsyncTransport", return_value=mock_transport),
        patch("gql.transport.websockets.WebsocketsTransport"),
    ):
        client = await StashClientBase.create()
        client._ensure_initialized = MagicMock()
        client.log = MagicMock()
        client.client = mock_client
        client.schema = mock_schema

        # Test successful response with hello query
        mock_session.execute = AsyncMock(return_value={"data": {"hello": "world"}})
        result = await client.execute("query { hello }")
        assert result == {"data": {"hello": "world"}}

        # Test successful response with variables
        mock_session.execute = AsyncMock(
            return_value={"data": {"findScene": {"id": "123", "title": "Test Scene"}}}
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
        assert isinstance(result, dict)
        assert "data" in result
        assert "findScene" in result["data"]
        assert result["data"]["findScene"]["id"] == "123"
        assert result["data"]["findScene"]["title"] == "Test Scene"

        # Test GraphQL validation error
        mock_session.execute = AsyncMock(
            side_effect=ValueError(
                "Invalid GraphQL query: Syntax Error: Unexpected Name 'invalid'."
            )
        )

        with pytest.raises(
            ValueError,
            match=r"Invalid GraphQL query: Syntax Error: Unexpected Name 'invalid'\.",
        ):
            await client.execute("invalid { query }")

        # Test schema validation error
        with pytest.raises(
            ValueError,
            match=r"Invalid GraphQL query: Schema validation errors: \[.*Cannot query field 'invalid' on type 'Query'.*\]",
        ):
            await client.execute("query { invalid }")

        # Test HTTP error last since it's most catastrophic
        mock_session.execute = AsyncMock(
            side_effect=httpx.NetworkError("Connection failed")
        )

        with pytest.raises(
            ValueError,
            match=r"Unexpected error during request \(NetworkError\): Connection failed",
        ):
            await client.execute("query { hello }")


@pytest.mark.asyncio
async def test_datetime_conversion(mock_session, mock_client) -> None:
    """Test datetime conversion in variables."""
    # Create a basic schema for initialization
    query_type = GraphQLObjectType(
        name="Query",
        fields={"test": GraphQLField(type_=GraphQLString)},
    )
    mock_schema = GraphQLSchema(query=query_type)
    mock_session.client = MagicMock(schema=mock_schema)

    # Set up mock client
    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()

    # Create mock transport
    mock_transport = MagicMock()
    mock_transport.headers = {}

    with (
        patch("gql.Client", return_value=mock_client),
        patch("gql.transport.httpx.HTTPXAsyncTransport", return_value=mock_transport),
        patch("gql.transport.websockets.WebsocketsTransport"),
    ):
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


@pytest.mark.asyncio
async def test_get_configuration_defaults(mock_session, mock_client) -> None:
    """Test getting configuration defaults."""
    # Create a basic schema for initialization
    query_type = GraphQLObjectType(
        name="Query",
        fields={"test": GraphQLField(type_=GraphQLString)},
    )
    mock_schema = GraphQLSchema(query=query_type)
    mock_session.client = MagicMock(schema=mock_schema)

    # Set up mock client
    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()

    # Create mock transport
    mock_transport = MagicMock()
    mock_transport.headers = {}

    with (
        patch("gql.Client", return_value=mock_client),
        patch("gql.transport.httpx.HTTPXAsyncTransport", return_value=mock_transport),
        patch("gql.transport.websockets.WebsocketsTransport"),
    ):
        client = await StashClientBase.create()
        client.client = mock_client

        # Create client mock with complete configuration
        mock_session.execute = AsyncMock(
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

        # Test empty configuration (should use defaults from base.py)
        mock_session.execute = AsyncMock(
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
        mock_session.execute = AsyncMock(side_effect=ValueError("Test error"))
        with pytest.raises(ValueError, match="Test error"):
            await client.get_configuration_defaults()


@pytest.mark.asyncio
async def test_metadata_generate(mock_session, mock_client) -> None:
    """Test metadata generation."""
    # Create basic schema for metadata generation with proper types
    query_type = GraphQLObjectType(
        name="Query",
        fields={
            "findJob": GraphQLField(
                type_=GraphQLObjectType(
                    name="Job",
                    fields={
                        "id": GraphQLField(type_=GraphQLString),
                        "status": GraphQLField(type_=GraphQLString),
                        "description": GraphQLField(type_=GraphQLString),
                        "progress": GraphQLField(type_=GraphQLID),
                        "error": GraphQLField(type_=GraphQLString),
                        "addTime": GraphQLField(type_=GraphQLString),
                        "subTasks": GraphQLField(type_=GraphQLString),
                    },
                )
            )
        },
    )
    mutation_type = GraphQLObjectType(
        name="Mutation",
        fields={
            "metadataGenerate": GraphQLField(
                type_=GraphQLString,
                args={
                    "input": GraphQLArgument(
                        type_=GraphQLInputObjectType(
                            name="GenerateMetadataInput",
                            fields={
                                "covers": GraphQLInputField(type_=GraphQLString),
                                "sprites": GraphQLInputField(type_=GraphQLString),
                                "previews": GraphQLInputField(type_=GraphQLString),
                                "imagePreviews": GraphQLInputField(type_=GraphQLString),
                                "previewOptions": GraphQLInputField(
                                    type_=GraphQLString
                                ),
                                "markers": GraphQLInputField(type_=GraphQLString),
                                "markerImagePreviews": GraphQLInputField(
                                    type_=GraphQLString
                                ),
                                "markerScreenshots": GraphQLInputField(
                                    type_=GraphQLString
                                ),
                                "transcodes": GraphQLInputField(type_=GraphQLString),
                                "phashes": GraphQLInputField(type_=GraphQLString),
                                "interactiveHeatmapsSpeeds": GraphQLInputField(
                                    type_=GraphQLString
                                ),
                                "imageThumbnails": GraphQLInputField(
                                    type_=GraphQLString
                                ),
                                "clipPreviews": GraphQLInputField(type_=GraphQLString),
                                "sceneIDs": GraphQLInputField(type_=GraphQLString),
                                "markerIDs": GraphQLInputField(type_=GraphQLString),
                                "overwrite": GraphQLInputField(type_=GraphQLString),
                            },
                        )
                    )
                },
            ),
        },
    )
    mock_schema = GraphQLSchema(query=query_type, mutation=mutation_type)
    mock_session.client = MagicMock(schema=mock_schema)

    # Set up mock client
    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()

    # Create mock transport
    mock_transport = MagicMock()
    mock_transport.headers = {}

    with (
        patch("gql.Client", return_value=mock_client),
        patch("gql.transport.httpx.HTTPXAsyncTransport", return_value=mock_transport),
        patch("gql.transport.websockets.WebsocketsTransport"),
    ):
        client = await StashClientBase.create()
        client.client = mock_client

        # Test with minimal options - response includes data wrapper
        mock_session.execute = AsyncMock(return_value={"metadataGenerate": "123"})
        job_id = await client.metadata_generate()
        assert isinstance(job_id, str)
        assert job_id == "123"

        # Test with all possible generate options
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
            previewOptions=GeneratePreviewOptions(
                previewSegments=12,
                previewSegmentDuration=10.0,
                previewExcludeStart="00:00:10",
                previewExcludeEnd="00:00:05",
                previewPreset=PreviewPreset.SLOW,
            ),
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

        job_id = await client.metadata_generate(options=options, input_data=input_data)
        assert job_id == "123"

        # Verify the mock was called with correct parameters
        call_args = mock_session.execute.call_args_list[-1]
        assert "variable_values" in call_args[1]
        assert "input" in call_args[1]["variable_values"]
        input_vars = call_args[1]["variable_values"]["input"]

        # Verify all input parameters were passed correctly
        assert input_vars["covers"] is True
        assert input_vars["sprites"] is True
        assert input_vars["previews"] is True
        assert input_vars["imagePreviews"] is True
        assert input_vars["markers"] is True
        assert input_vars["markerImagePreviews"] is True
        assert input_vars["markerScreenshots"] is True
        assert input_vars["transcodes"] is True
        assert input_vars["forceTranscodes"] is True
        assert input_vars["phashes"] is True
        assert input_vars["interactiveHeatmapsSpeeds"] is True
        assert input_vars["imageThumbnails"] is True
        assert input_vars["clipPreviews"] is True
        assert input_vars["sceneIDs"] == ["1", "2"]
        assert input_vars["markerIDs"] == ["1", "2"]
        assert input_vars["overwrite"] is True

        # Test job status monitoring
        mock_session.execute = AsyncMock()
        mock_session.execute.side_effect = [
            {"metadataGenerate": "123"},
            {
                "findJob": {
                    "id": "123",
                    "status": "RUNNING",
                    "description": "Generating metadata",
                    "progress": 50,
                    "error": None,
                    "addTime": "2024-04-06T00:00:00Z",
                    "subTasks": [],
                }
            },
            {
                "findJob": {
                    "id": "123",
                    "status": "FINISHED",
                    "description": "Metadata generation complete",
                    "progress": 100,
                    "error": None,
                    "addTime": "2024-04-06T00:00:00Z",
                    "subTasks": [],
                }
            },
        ]

        job_id = await client.metadata_generate()
        assert job_id == "123"
        success = await client.wait_for_job(job_id, period=0.1)
        assert success is True

        # Test error cases with proper response structure
        mock_session.execute = AsyncMock(return_value={"data": {}})
        with pytest.raises(ValueError, match="No job ID returned from server"):
            await client.metadata_generate()

        mock_session.execute = AsyncMock(
            side_effect=httpx.NetworkError("Connection failed")
        )
        with pytest.raises(ValueError, match="Unexpected error during request"):
            await client.metadata_generate()

        # Test invalid options
        with pytest.raises(TypeError):
            await client.metadata_generate(options="invalid")

        # Test invalid input data
        with pytest.raises(TypeError):
            await client.metadata_generate(input_data="invalid")


@pytest.mark.asyncio
async def test_metadata_scan(mock_session, mock_client) -> None:
    """Test metadata scanning."""
    # Create basic schema with metadata mutation
    query_type = GraphQLObjectType(
        name="Query",
        fields={
            "configuration": GraphQLField(
                type_=GraphQLObjectType(
                    name="Configuration",
                    fields={
                        "defaults": GraphQLField(
                            type_=GraphQLObjectType(
                                name="ConfigDefaultSettings",
                                fields={
                                    "scan": GraphQLField(
                                        type_=GraphQLObjectType(
                                            name="ScanMetadataOptions",
                                            fields={
                                                "rescan": GraphQLField(
                                                    type_=GraphQLString
                                                ),
                                                "scanGenerateCovers": GraphQLField(
                                                    type_=GraphQLString
                                                ),
                                                "scanGeneratePreviews": GraphQLField(
                                                    type_=GraphQLString
                                                ),
                                                "scanGenerateImagePreviews": GraphQLField(
                                                    type_=GraphQLString
                                                ),
                                                "scanGenerateSprites": GraphQLField(
                                                    type_=GraphQLString
                                                ),
                                                "scanGeneratePhashes": GraphQLField(
                                                    type_=GraphQLString
                                                ),
                                                "scanGenerateThumbnails": GraphQLField(
                                                    type_=GraphQLString
                                                ),
                                                "scanGenerateClipPreviews": GraphQLField(
                                                    type_=GraphQLString
                                                ),
                                            },
                                        )
                                    ),
                                },
                            )
                        ),
                    },
                )
            ),
        },
    )

    # Define the input type for metadata scan
    scan_input_type = GraphQLInputObjectType(
        name="ScanMetadataInput",
        fields={
            "paths": GraphQLInputField(type_=GraphQLString),
            "scanGenerateCovers": GraphQLInputField(type_=GraphQLString),
            "scanGeneratePreviews": GraphQLInputField(type_=GraphQLString),
            "scanGenerateImagePreviews": GraphQLInputField(type_=GraphQLString),
            "scanGenerateSprites": GraphQLInputField(type_=GraphQLString),
            "scanGeneratePhashes": GraphQLInputField(type_=GraphQLString),
            "scanGenerateThumbnails": GraphQLInputField(type_=GraphQLString),
            "scanGenerateClipPreviews": GraphQLInputField(type_=GraphQLString),
            "rescan": GraphQLInputField(type_=GraphQLString),
        },
    )

    mutation_type = GraphQLObjectType(
        name="Mutation",
        fields={
            "metadataScan": GraphQLField(
                type_=GraphQLString,
                args={"input": GraphQLArgument(type_=scan_input_type)},
            ),
        },
    )
    mock_schema = GraphQLSchema(query=query_type, mutation=mutation_type)
    mock_session.client = MagicMock(schema=mock_schema)

    # Set up mock client
    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()

    # Create mock transport with headers
    mock_transport = MagicMock()
    mock_transport.headers = {}

    with (
        patch("gql.Client", return_value=mock_client),
        patch("gql.transport.httpx.HTTPXAsyncTransport", return_value=mock_transport),
        patch("gql.transport.websockets.WebsocketsTransport"),
    ):
        client = await StashClientBase.create()
        client.client = mock_client
        client.schema = mock_schema

        # Test basic scan
        mock_session.execute = AsyncMock(return_value={"metadataScan": "123"})
        job_id = await client.metadata_scan()
        assert job_id == "123"

        # Test with paths and flags
        mock_session.execute = AsyncMock(return_value={"metadataScan": "123"})
        paths = ["/test/path"]
        flags = ScanMetadataInput(paths=paths, rescan=True)
        job_id = await client.metadata_scan(paths=paths, flags=flags.__dict__)
        assert job_id == "123"

        # Test error handling
        mock_session.execute = AsyncMock(return_value={"metadataScan": None})
        with pytest.raises(ValueError, match="Failed to start metadata scan"):
            await client.metadata_scan()

        mock_session.execute = AsyncMock(
            side_effect=httpx.NetworkError("Connection failed")
        )
        with pytest.raises(ValueError, match="Failed to start metadata scan"):
            await client.metadata_scan()


@pytest.mark.asyncio
async def test_find_job(mock_session, mock_client) -> None:
    """Test finding a job."""
    # Create a basic schema with proper Job type
    query_type = GraphQLObjectType(
        name="Query",
        fields={
            "findJob": GraphQLField(
                type_=GraphQLObjectType(
                    name="Job",
                    fields={
                        "id": GraphQLField(type_=GraphQLString),
                        "status": GraphQLField(type_=GraphQLString),
                        "description": GraphQLField(type_=GraphQLString),
                        "progress": GraphQLField(
                            type_=GraphQLID
                        ),  # GraphQL sends numbers as strings
                        "error": GraphQLField(type_=GraphQLString),
                        "addTime": GraphQLField(type_=GraphQLString),
                        "subTasks": GraphQLField(type_=GraphQLString),
                    },
                )
            )
        },
    )
    mock_schema = GraphQLSchema(query=query_type)
    mock_session.client = MagicMock(schema=mock_schema)
    mock_client.schema = mock_schema  # Ensure schema is set on client

    # Set up mock client
    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()

    # Create mock transport
    mock_transport = MagicMock()
    mock_transport.headers = {}

    with (
        patch("gql.Client", return_value=mock_client),
        patch("gql.transport.httpx.HTTPXAsyncTransport", return_value=mock_transport),
        patch("gql.transport.websockets.WebsocketsTransport"),
    ):
        client = await StashClientBase.create()
        client.client = mock_client

        # Test complete job
        mock_session.execute = AsyncMock(
            return_value={
                "findJob": {
                    "id": "123",
                    "status": "READY",  # Status string as returned by GraphQL
                    "subTasks": [],
                    "description": "Test job",
                    "progress": 0,
                    "addTime": "2024-04-06T00:00:00Z",
                    "error": None,
                }
            }
        )

        job = await client.find_job("123")
        assert isinstance(
            job, Job
        )  # Should now pass as the response matches Job type structure
        assert job.id == "123"
        assert job.status == "READY"  # Compare string since that's what GraphQL returns
        assert job.description == "Test job"
        assert job.progress == 0
        assert job.error is None
        assert job.addTime == "2024-04-06T00:00:00Z"
        assert isinstance(job.subTasks, list)
        assert len(job.subTasks) == 0

        # Test job with all possible statuses
        for status in JobStatus:
            mock_session.execute = AsyncMock(
                return_value={
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
            )
            job = await client.find_job("123")
            assert job is not None
            assert job.status == status

        # Test job with error
        mock_session.execute = AsyncMock(
            return_value={
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
        )
        job = await client.find_job("123")
        assert job is not None
        assert job.status == JobStatus.FAILED
        assert job.error == "Test error message"

        # Test job with subtasks
        mock_session.execute = AsyncMock(
            return_value={
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
        mock_session.execute = AsyncMock(return_value={"findJob": None})
        job = await client.find_job("123")
        assert job is None

        # Test error response
        mock_session.execute = AsyncMock(
            return_value={
                "errors": [{"message": "Invalid job ID"}],
                "findJob": None,  # GraphQL errors usually also include a null data field
            }
        )
        job = await client.find_job("123")
        assert job is None

        # Test network error
        mock_session.execute = AsyncMock(
            side_effect=httpx.NetworkError("Connection failed")
        )
        job = await client.find_job("123")
        assert job is None


@pytest.mark.asyncio
async def test_wait_for_job(mock_session, mock_client) -> None:
    """Test waiting for a job."""
    # Create basic schema
    query_type = GraphQLObjectType(
        name="Query",
        fields={
            "findJob": GraphQLField(
                type_=GraphQLObjectType(
                    name="Job",
                    fields={
                        "id": GraphQLField(type_=GraphQLString),
                        "status": GraphQLField(type_=GraphQLString),
                        "description": GraphQLField(type_=GraphQLString),
                        "progress": GraphQLField(type_=GraphQLString),
                        "error": GraphQLField(type_=GraphQLString),
                        "addTime": GraphQLField(type_=GraphQLString),
                        "subTasks": GraphQLField(type_=GraphQLString),
                    },
                )
            )
        },
    )
    mock_schema = GraphQLSchema(query=query_type)
    mock_session.client = MagicMock(schema=mock_schema)

    # Set up mock client
    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()

    # Create mock transport with headers
    mock_transport = MagicMock()
    mock_transport.headers = {}

    with (
        patch("gql.Client", return_value=mock_client),
        patch("gql.transport.httpx.HTTPXAsyncTransport", return_value=mock_transport),
        patch("gql.transport.websockets.WebsocketsTransport"),
    ):
        client = await StashClientBase.create()
        client.client = mock_client

        # Test complete job
        mock_session.execute = AsyncMock(
            return_value={
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
        )
        result = await client.wait_for_job("123", period=0.1)
        assert result is True

        # Test job that finishes with different status
        mock_session.execute = AsyncMock(
            return_value={
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
        )
        result = await client.wait_for_job("123", period=0.1)
        assert result is False

        # Test job not found
        mock_session.execute = AsyncMock(return_value={"findJob": None})
        with pytest.raises(ValueError, match="Job 123 not found"):
            await client.wait_for_job("123", period=0.1)

        # Test timeout
        mock_session.execute = AsyncMock(
            return_value={
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
        )
        with pytest.raises(TimeoutError):
            await client.wait_for_job("123", period=0.1, timeout=0.2)


@pytest.mark.asyncio
async def test_context_manager(mock_session, mock_client) -> None:
    """Test context manager functionality."""
    # Create a basic schema for initialization
    query_type = GraphQLObjectType(
        name="Query",
        fields={
            "hello": GraphQLField(type_=GraphQLString),
        },
    )
    mock_schema = GraphQLSchema(query=query_type)
    mock_session.client = MagicMock(schema=mock_schema)
    mock_client.schema = mock_schema

    # Set up mock client with proper session handling
    mock_client.__aenter__.return_value = mock_session
    mock_client.close_async = AsyncMock()

    # Create mock transport with close method
    mock_transport = MagicMock()
    mock_transport.headers = {}
    mock_transport.close = AsyncMock()

    with (
        patch("gql.Client", return_value=mock_client),
        patch("gql.transport.httpx.HTTPXAsyncTransport", return_value=mock_transport),
        patch("gql.transport.websockets.WebsocketsTransport"),
    ):
        client = await StashClientBase.create()
        client.client = mock_client
        client.http_transport = mock_transport
        client.ws_transport = mock_transport
        client.schema = mock_schema

        # Set up mock response for hello query
        mock_session.execute = AsyncMock(return_value={"data": {"hello": "world"}})

        # Test context manager with a valid query
        async with client:
            result = await client.execute("query { hello }")
            assert result == {"data": {"hello": "world"}}

        # Verify cleanup happened
        assert mock_client.close_async.called
        assert mock_transport.close.called
