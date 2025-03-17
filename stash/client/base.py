"""Base Stash client class."""

import asyncio
import re
import time
from datetime import datetime
from typing import Any, TypeVar

from gql import Client, gql
from gql.dsl import DSLSchema
from gql.transport.aiohttp import AIOHTTPTransport
from gql.transport.exceptions import (
    TransportError,
    TransportQueryError,
    TransportServerError,
)
from gql.transport.websockets import WebsocketsTransport
from graphql import parse as parse_graphql
from graphql import validate as validate_graphql

from .. import fragments
from ..client_helpers import str_compare
from ..types import (
    AutoTagMetadataOptions,
    ConfigDefaultSettingsResult,
    FindJobInput,
    GenerateMetadataInput,
    GenerateMetadataOptions,
    Job,
    JobStatus,
    ScanMetadataInput,
    ScanMetadataOptions,
)

T = TypeVar("T")


class StashClientBase:
    """Base GraphQL client for Stash."""

    def __init__(
        self,
        conn: dict[str, Any] = None,
        verify_ssl: bool = True,
    ) -> None:
        """Initialize client.

        Args:
            conn: Connection details dictionary with:
                - Scheme: Protocol (default: "http")
                - Host: Hostname (default: "localhost")
                - Port: Port number (default: 9999)
                - ApiKey: Optional API key
                - Logger: Optional logger instance
            verify_ssl: Whether to verify SSL certificates
        """
        if not hasattr(self, "_initialized"):
            self._initialized = False
        if not hasattr(self, "_init_args"):
            self._init_args = (conn, verify_ssl)

    @classmethod
    async def create(
        cls,
        conn: dict[str, Any] = None,
        verify_ssl: bool = True,
    ) -> "StashClientBase":
        """Create and initialize a new client.

        Args:
            conn: Connection details dictionary
            verify_ssl: Whether to verify SSL certificates

        Returns:
            Initialized client instance
        """
        client = cls(conn, verify_ssl)
        await client.initialize()
        return client

    async def initialize(self) -> None:
        """Initialize the client.

        This is called by the context manager if not already initialized.
        """
        if self._initialized:
            return

        conn, verify_ssl = self._init_args
        conn = conn or {}

        # Set up logging - use fansly.stash.client hierarchy
        from ..logging import client_logger

        self.log = conn.get("Logger", client_logger)

        # Build URLs
        scheme = conn.get("Scheme", "http")
        ws_scheme = "ws" if scheme == "http" else "wss"
        host = conn.get("Host", "localhost")
        if host == "0.0.0.0":  # nosec B104 - Converting all-interfaces to localhost
            host = "127.0.0.1"
        port = conn.get("Port", 9999)

        self.url = f"{scheme}://{host}:{port}/graphql"
        self.ws_url = f"{ws_scheme}://{host}:{port}/graphql"

        # Set up headers
        headers = {}
        if api_key := conn.get("ApiKey"):
            self.log.debug("Using API key authentication")
            headers["ApiKey"] = api_key
        else:
            self.log.warning("No API key provided")

        # Set up transports
        self.http_transport = AIOHTTPTransport(
            url=self.url,
            headers=headers,
            ssl=verify_ssl,
            timeout=30.0,
        )
        self.ws_transport = WebsocketsTransport(
            url=self.ws_url,
            headers=headers,
            ssl=verify_ssl,
        )

        # Store transport configuration for creating clients
        self.transport_config = {
            "url": self.url,
            "headers": headers,
            "ssl": verify_ssl,
            "timeout": 30.0,
        }

        self.log.debug(f"Using Stash endpoint at {self.url}")
        self.log.debug(f"Using WebSocket endpoint at {self.ws_url}")
        self.log.debug(f"Client headers: {headers}")
        self.log.debug(f"SSL verification: {verify_ssl}")

        # Test connection and fetch schema
        try:
            self.log.debug("Testing connection and fetching schema...")
            test_query = gql("query { __schema { queryType { name } } }")

            # Create a client just for schema fetching
            transport = AIOHTTPTransport(**self.transport_config)
            self.client = Client(
                transport=transport,
                fetch_schema_from_transport=True,
            )
            async with self.client as session:
                result = await session.execute(test_query)
                self.schema = session.client.schema  # Store schema for validation
                self.log.debug("Schema fetched successfully")
                self.log.debug(f"Test query result: {result}")
                self.log.debug("Connection test successful")
        except Exception as e:
            self.log.error(f"Connection test failed: {e}")
            raise ValueError(f"Failed to connect to Stash at {self.url}: {e}")

        self._initialized = True

    def _ensure_initialized(self) -> None:
        """Ensure transport configuration is properly initialized."""
        if not hasattr(self, "log"):
            from ..logging import client_logger

            self.log = client_logger

        if not hasattr(self, "_initialized") or not self._initialized:
            raise RuntimeError("Client not initialized - use get_client() first")

        if not hasattr(self, "transport_config"):
            raise RuntimeError("Transport configuration not initialized")

        if not hasattr(self, "url"):
            raise RuntimeError("URL not initialized")

    def _handle_gql_error(self, e: Exception) -> None:
        """Handle gql errors with appropriate error messages."""
        if isinstance(e, TransportQueryError):
            # GraphQL query error (e.g. validation error)
            raise ValueError(f"GraphQL query error: {e.errors}")
        elif isinstance(e, TransportServerError):
            # Server error (e.g. 500)
            raise ValueError(f"GraphQL server error: {e}")
        elif isinstance(e, TransportError):
            # Network/connection error
            raise ValueError(f"Failed to connect to {self.url}: {e}")
        elif isinstance(e, asyncio.TimeoutError):
            raise ValueError(f"Request to {self.url} timed out")
        else:
            raise ValueError(
                f"Unexpected error during request ({type(e).__name__}): {e}"
            )

    async def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL query or mutation.

        Args:
            query: GraphQL query string
            variables: Optional variables for the query

        Returns:
            Query result as a dictionary

        Raises:
            ValueError: If query validation fails or execution fails
        """
        self._ensure_initialized()

        try:
            # Parse and validate query
            try:
                # First parse the query string into an AST
                operation = gql(query)

                # Then validate against schema if available
                if hasattr(self, "schema") and self.schema:
                    # Parse into raw GraphQL AST for validation
                    ast = parse_graphql(query)
                    # Validate against schema
                    validation_errors = validate_graphql(self.schema, ast)
                    if validation_errors:
                        raise ValueError(
                            f"Schema validation errors: {validation_errors}"
                        )
                    self.log.debug("Query validated against schema")
            except Exception as e:
                self.log.error(f"Query validation failed: {e}")
                raise ValueError(f"Invalid GraphQL query: {e}")

            # Process variables
            processed_vars = self._convert_datetime(variables or {})

            # Execute with fresh client and transport
            self.log.debug(f"Executing query with variables: {processed_vars}")
            self.log.debug(f"Query: {query}")
            transport = AIOHTTPTransport(**self.transport_config)
            async with Client(
                transport=transport,
                fetch_schema_from_transport=False,  # Already have schema
            ) as session:
                result = await session.execute(
                    operation, variable_values=processed_vars
                )
                self.log.debug("Query executed successfully")
                self.log.debug(f"Result: {result}")
                return result

        except Exception as e:
            self._handle_gql_error(e)  # This will raise ValueError
        finally:
            # Ensure transport is cleaned up
            await transport.close()

    def _convert_datetime(self, obj: Any) -> Any:
        """Convert datetime objects to ISO format strings."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {k: self._convert_datetime(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._convert_datetime(x) for x in obj]
        return obj

    def _parse_obj_for_ID(self, param, str_key="name"):
        if isinstance(param, str):
            try:
                return int(param)
            except ValueError:
                return {str_key: param.strip()}
        elif isinstance(param, dict):
            if param.get("stored_id"):
                return int(param["stored_id"])
            if param.get("id"):
                return int(param["id"])
        return param

    async def get_configuration_defaults(self) -> ConfigDefaultSettingsResult:
        """Get default configuration settings."""
        self.log.debug("Getting configuration defaults...")
        try:
            self.log.debug("Executing CONFIG_DEFAULTS_QUERY")
            result = await self.execute(fragments.CONFIG_DEFAULTS_QUERY)
            self.log.debug(f"Got configuration result: {result}")

            if not result:
                self.log.debug("Result is None")
                self.log.warning(
                    "No result from configuration query, using hardcoded defaults"
                )
                return ConfigDefaultSettingsResult(
                    scan=ScanMetadataOptions(
                        rescan=False,
                        scanGenerateCovers=True,
                        scanGeneratePreviews=True,
                        scanGenerateImagePreviews=True,
                        scanGenerateSprites=True,
                        scanGeneratePhashes=True,
                        scanGenerateThumbnails=True,
                        scanGenerateClipPreviews=True,
                    ),
                    autoTag=AutoTagMetadataOptions(),
                    generate=GenerateMetadataOptions(),
                    deleteFile=False,
                    deleteGenerated=False,
                )

            if defaults := result.get("configuration", {}).get("defaults"):
                self.log.debug(f"Using server defaults: {defaults}")
                return ConfigDefaultSettingsResult(**defaults)

            self.log.warning("No defaults in response, using hardcoded values")
            return ConfigDefaultSettingsResult(
                scan=ScanMetadataOptions(
                    rescan=False,
                    scanGenerateCovers=True,
                    scanGeneratePreviews=True,
                    scanGenerateImagePreviews=True,
                    scanGenerateSprites=True,
                    scanGeneratePhashes=True,
                    scanGenerateThumbnails=True,
                    scanGenerateClipPreviews=True,
                ),
                autoTag=AutoTagMetadataOptions(),
                generate=GenerateMetadataOptions(),
                deleteFile=False,
                deleteGenerated=False,
            )
        except Exception as e:
            self.log.error(f"Failed to get configuration defaults: {e}")
            raise

    async def metadata_generate(
        self,
        options: GenerateMetadataOptions | dict[str, Any] = {},
        input_data: GenerateMetadataInput | dict[str, Any] | None = None,
    ) -> str:
        """Generate metadata.

        Args:
            options: GenerateMetadataOptions object or dictionary of what to generate:
                - covers: bool - Generate covers
                - sprites: bool - Generate sprites
                - previews: bool - Generate previews
                - imagePreviews: bool - Generate image previews
                - previewOptions: GeneratePreviewOptionsInput
                    - previewSegments: int - Number of segments in a preview file
                    - previewSegmentDuration: float - Duration of each segment in seconds
                    - previewExcludeStart: str - Duration to exclude from start
                    - previewExcludeEnd: str - Duration to exclude from end
                    - previewPreset: PreviewPreset - Preset when generating preview
                - markers: bool - Generate markers
                - markerImagePreviews: bool - Generate marker image previews
                - markerScreenshots: bool - Generate marker screenshots
                - transcodes: bool - Generate transcodes
                - forceTranscodes: bool - Generate transcodes even if not required
                - phashes: bool - Generate phashes
                - interactiveHeatmapsSpeeds: bool - Generate interactive heatmaps speeds
                - imageThumbnails: bool - Generate image thumbnails
                - clipPreviews: bool - Generate clip previews
            input_data: Optional GenerateMetadataInput object or dictionary to specify what to process:
                - sceneIDs: list[str] - List of scene IDs to generate for (default: all)
                - markerIDs: list[str] - List of marker IDs to generate for (default: all)
                - overwrite: bool - Overwrite existing media (default: False)

        Returns:
            Job ID for the generation task

        Raises:
            ValueError: If the input data is invalid
            gql.TransportError: If the request fails
        """
        try:
            # Convert GenerateMetadataOptions to dict if needed
            options_dict = (
                options.__dict__
                if isinstance(options, GenerateMetadataOptions)
                else options
            )

            # Convert GenerateMetadataInput to dict if needed
            input_dict = {}
            if input_data is not None:
                input_dict = (
                    input_data.__dict__
                    if isinstance(input_data, GenerateMetadataInput)
                    else input_data
                )

            # Combine options and input data
            combined_input = {**options_dict, **input_dict}

            result = await self.execute(
                fragments.METADATA_GENERATE_MUTATION,
                {"input": combined_input},
            )
            return result.get("metadataGenerate")
        except Exception as e:
            self.log.error(f"Failed to generate metadata: {e}")
            raise

    async def metadata_scan(
        self,
        paths: list[str] = [],
        flags: dict[str, Any] = {},
    ) -> str:
        """Start a metadata scan job.

        Args:
            paths: List of paths to scan (empty for all paths)
            flags: Optional scan flags matching ScanMetadataInput schema:
                - rescan: bool
                - scanGenerateCovers: bool
                - scanGeneratePreviews: bool
                - scanGenerateImagePreviews: bool
                - scanGenerateSprites: bool
                - scanGeneratePhashes: bool
                - scanGenerateThumbnails: bool
                - scanGenerateClipPreviews: bool
                - filter: ScanMetaDataFilterInput

        Returns:
            Job ID for the scan operation
        """
        # Get scan input object with defaults from config
        try:
            defaults = await self.get_configuration_defaults()
            scan_input = ScanMetadataInput(
                paths=paths,
                rescan=getattr(defaults.scan, "rescan", False),
                scanGenerateCovers=getattr(defaults.scan, "scanGenerateCovers", True),
                scanGeneratePreviews=getattr(
                    defaults.scan, "scanGeneratePreviews", True
                ),
                scanGenerateImagePreviews=getattr(
                    defaults.scan, "scanGenerateImagePreviews", True
                ),
                scanGenerateSprites=getattr(defaults.scan, "scanGenerateSprites", True),
                scanGeneratePhashes=getattr(defaults.scan, "scanGeneratePhashes", True),
                scanGenerateThumbnails=getattr(
                    defaults.scan, "scanGenerateThumbnails", True
                ),
                scanGenerateClipPreviews=getattr(
                    defaults.scan, "scanGenerateClipPreviews", True
                ),
            )
        except Exception as e:
            self.log.warning(
                f"Failed to get scan defaults: {e}, using hardcoded defaults"
            )
            scan_input = ScanMetadataInput(
                paths=paths,
                rescan=False,
                scanGenerateCovers=True,
                scanGeneratePreviews=True,
                scanGenerateImagePreviews=True,
                scanGenerateSprites=True,
                scanGeneratePhashes=True,
                scanGenerateThumbnails=True,
                scanGenerateClipPreviews=True,
            )

        # Override with any provided flags
        if flags:
            for key, value in flags.items():
                setattr(scan_input, key, value)

        # Convert to dict for GraphQL
        variables = {"input": scan_input.__dict__}
        try:
            result = await self.execute(fragments.METADATA_SCAN_MUTATION, variables)
            job_id = result.get("metadataScan")
            if not job_id:
                raise ValueError("Failed to start metadata scan - no job ID returned")
            return job_id
        except Exception as e:
            self.log.error(f"Failed to start metadata scan: {e}")
            raise ValueError(f"Failed to start metadata scan: {e}")

    async def find_job(self, job_id: str) -> Job | None:
        """Find a job by ID.

        Args:
            job_id: Job ID to find

        Returns:
            Job object if found, None otherwise

        Examples:
            Find a job and check its status:
            ```python
            job = await client.find_job("123")
            if job:
                print(f"Job status: {job.status}")
            ```
        """
        result = await self.execute(
            fragments.FIND_JOB_QUERY,
            {"input": FindJobInput(id=job_id).__dict__},
        )
        if job_data := result.get("findJob"):
            return Job(**job_data)
        return None

    async def wait_for_job(
        self,
        job_id: str,
        status: JobStatus = JobStatus.FINISHED,
        period: float = 1.5,
        timeout: float = 120,
    ) -> bool | None:
        """Wait for a job to reach a specific status.

        Args:
            job_id: Job ID to wait for
            status: Status to wait for (default: JobStatus.FINISHED)
            period: Time between checks in seconds (default: 1.5)
            timeout: Maximum time to wait in seconds (default: 120)

        Returns:
            True if job reached desired status
            False if job finished with different status
            None if job not found

        Raises:
            TimeoutError: If timeout is reached
        """
        timeout_value = time.time() + timeout
        while time.time() < timeout_value:
            job = await self.find_job(job_id)
            if not job:
                return None

            # Only log through stash's logger
            self.log.info(
                f"Waiting for Job:{job_id} Status:{job.status} Progress:{job.progress}"
            )

            if job.status == status:
                return True
            if job.status in [JobStatus.FINISHED, JobStatus.CANCELLED]:
                return False

            await asyncio.sleep(period)

        raise TimeoutError("Hit timeout waiting for Job to complete")

    async def close(self) -> None:
        """Close the HTTP client and clean up resources.

        This method should be called when you're done with the client
        to properly clean up resources. You can also use the client
        as an async context manager to automatically handle cleanup.

        Examples:
            Manual cleanup:
            ```python
            client = StashClient("http://localhost:9999/graphql")
            try:
                # Use client...
                scene = await client.find_scene("123")
            finally:
                await client.close()
            ```

            Using async context manager:
            ```python
            async with StashClient("http://localhost:9999/graphql") as client:
                # Client will be automatically closed after this block
                scene = await client.find_scene("123")
            ```
        """
        # Close transports and client
        if hasattr(self, "http_transport") and hasattr(self.http_transport, "close"):
            await self.http_transport.close()
        if hasattr(self, "ws_transport") and hasattr(self.ws_transport, "close"):
            await self.ws_transport.close()
        if hasattr(self, "client"):
            # gql.Client has close_async() for async contexts
            if hasattr(self.client, "close_async"):
                await self.client.close_async()
            # Fallback to close_sync() if available
            elif hasattr(self.client, "close_sync"):
                self.client.close_sync()

    async def __aenter__(self) -> "StashClientBase":
        """Enter async context manager."""
        if not self._initialized:
            await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context manager."""
        await self.close()
