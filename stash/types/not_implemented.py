"""Types for NotImplementedMixin."""

from typing import Any

import strawberry


@strawberry.type
class Version:
    """Version information from schema/types/version.graphql."""

    version: str  # String!
    hash: str  # String!
    build_time: str  # String!


@strawberry.type
class LatestVersion:
    """Latest version information from schema/types/version.graphql."""

    version: str  # String!
    shorthash: str  # String!
    release_date: str  # String!
    url: str  # String!
    notes: str | None = None  # String


@strawberry.type
class SQLQueryResult:
    """Result of a SQL query from schema/types/sql.graphql."""

    rows: list[dict[str, Any]]  # [Map!]!
    columns: list[str]  # [String!]!


@strawberry.type
class SQLExecResult:
    """Result of a SQL exec from schema/types/sql.graphql."""

    rows_affected: int  # Int!


@strawberry.type
class StashBoxValidationResult:
    """Result of stash-box validation from schema/types/stash-box.graphql."""

    valid: bool  # Boolean!
    status: str  # String!


@strawberry.type
class DLNAStatus:
    """DLNA status from schema/types/dlna.graphql."""

    running: bool  # Boolean!
    until: str | None = None  # String
    recent_ips: list[str]  # [String!]!


@strawberry.type
class JobStatusUpdate:
    """Job status update from schema/types/job.graphql."""

    type: str  # String!
    message: str | None = None  # String
    progress: float | None = None  # Float
    status: str | None = None  # String
    error: str | None = None  # String
    job: dict[str, Any] | None = None  # Job
