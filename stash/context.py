"""Context manager for Stash client."""

from __future__ import annotations

from types import TracebackType
from typing import Any

from multidict import CIMultiDict

from .client import StashClient
from .logging import client_logger as logger


class StashContext:
    """Context manager for Stash client.

    This class provides a high-level interface for managing Stash client
    connections, including:
    - Connection configuration
    - Client lifecycle management
    - Interface access

    Example:
        ```python
        # Create context with connection details
        context = StashContext(conn={
            "Scheme": "http",
            "Host": "localhost",
            "Port": 9999,
            "ApiKey": "your_api_key",
        })

        # Use context manager
        async with context as client:
            performer = await client.find_performer("123")

        # Or use directly
        client = context.client
        performer = await client.find_performer("123")
        await context.close()
        ```
    """

    def __init__(
        self,
        conn: dict[str, Any] | None = None,
        verify_ssl: bool = True,
    ) -> None:
        """Initialize context.

        Args:
            conn: Connection details dictionary
            verify_ssl: Whether to verify SSL certificates
        """
        # Convert connection dict to case-insensitive
        self.conn = CIMultiDict(conn or {})
        self.verify_ssl = verify_ssl
        self._client: StashClient | None = None

    @property
    def interface(self) -> StashClient:
        """Get Stash interface (alias for client).

        Returns:
            StashClient instance

        Raises:
            RuntimeError: If client is not initialized
        """
        if self._client is None:
            logger.error("Client not initialized - use get_client() first")
            raise RuntimeError("Client not initialized - use get_client() first")
        return self._client

    async def get_client(self) -> StashClient:
        """Get initialized Stash client.

        Returns:
            StashClient instance

        Raises:
            RuntimeError: If client initialization fails
        """
        logger.debug(
            f"get_client called on {id(self)}, current _client: {self._client}"
        )
        if self._client is None:
            self._client = StashClient(
                conn=self.conn,
                verify_ssl=self.verify_ssl,
            )
            try:
                await self._client.initialize()
                logger.debug(
                    f"Client initialization complete, _client set to {self._client}"
                )
            except Exception as e:
                logger.error(f"Client initialization failed: {e}")
                self._client = None
                raise RuntimeError(f"Failed to initialize Stash client: {e}")
        return self._client

    @property
    def client(self) -> StashClient:
        """Get client instance.

        Returns:
            StashClient instance

        Raises:
            RuntimeError: If client is not initialized
        """
        logger.debug(
            f"client property accessed on {id(self)}, current _client: {self._client}"
        )
        if self._client is None:
            logger.error("Client not initialized - use get_client() first")
            raise RuntimeError("Client not initialized - use get_client() first")
        return self._client

    async def close(self) -> None:
        """Close client connection."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def __aenter__(self) -> StashClient:
        """Enter async context manager."""
        return await self.get_client()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context manager."""
        await self.close()
