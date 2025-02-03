"""Context manager for Stash client."""

from __future__ import annotations

from typing import Any

from requests.structures import CaseInsensitiveDict

from .client import StashClient


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
        self.conn = CaseInsensitiveDict(conn or {})
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
        return self.client

    @property
    def client(self) -> StashClient:
        """Get Stash client.

        Returns:
            StashClient instance

        Raises:
            RuntimeError: If client is not initialized
        """
        if self._client is None:
            self._client = StashClient(
                conn=self.conn,
                verify_ssl=self.verify_ssl,
            )
        return self._client

    async def close(self) -> None:
        """Close client connection."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def __aenter__(self) -> StashClient:
        """Enter async context manager."""
        return self.client

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context manager."""
        await self.close()
