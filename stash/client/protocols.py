"""Protocol definitions for Stash client."""

import logging
from typing import Any, Protocol

from ..types import Job


class StashClientProtocol(Protocol):
    """Protocol defining required methods for Stash client mixins."""

    # Properties
    log: logging.Logger
    fragments: Any  # Module containing GraphQL fragments

    # Core methods
    async def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL query or mutation.

        Args:
            query: GraphQL query or mutation string
            variables: Optional query variables dictionary

        Returns:
            Query response data dictionary
        """
        ...

    def _parse_obj_for_id(self, param: Any, str_key: str = "name") -> Any:
        """Parse an object into an ID.

        Args:
            param: Object to parse (str, int, dict)
            str_key: Key to use when converting string to dict

        Returns:
            Parsed ID or filter dict
        """
        ...

    # Job methods
    async def find_job(self, job_id: str) -> Job | None:
        """Find a job by ID.

        Args:
            job_id: Job ID to find

        Returns:
            Job object if found, None otherwise
        """
        ...

    async def wait_for_job(
        self,
        job_id: str,
        status: Any = None,  # JobStatus.FINISHED
        period: float = 1.5,
        timeout_seconds: float = 120,
    ) -> bool | None:
        """Wait for a job to reach a specific status.

        Args:
            job_id: Job ID to wait for
            status: Status to wait for
            period: Time between checks in seconds
            timeout_seconds: Maximum time to wait in seconds

        Returns:
            True if job reached desired status
            False if job finished with different status
            None if job not found
        """
        ...
