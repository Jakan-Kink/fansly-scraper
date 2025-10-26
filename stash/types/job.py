"""Job types from schema/types/job.graphql."""

from datetime import datetime
from enum import Enum

import strawberry
from strawberry import ID


@strawberry.enum
class JobStatus(str, Enum):
    """Job status enum from schema/types/job.graphql."""

    READY = "READY"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    STOPPING = "STOPPING"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@strawberry.type
class Job:
    """Job type from schema/types/job.graphql."""

    id: ID  # ID!
    status: JobStatus  # JobStatus!
    subTasks: list[str]  # [String!]
    description: str  # String!
    progress: float | None = None  # Float
    startTime: datetime | None = None  # Time
    endTime: datetime | None = None  # Time
    addTime: datetime  # Time!
    error: str | None = None  # String


@strawberry.input
class FindJobInput:
    """Input for finding jobs from schema/types/job.graphql."""

    id: ID  # ID!


@strawberry.enum
class JobStatusUpdateType(str, Enum):
    """Job status update type enum from schema/types/job.graphql."""

    ADD = "ADD"
    REMOVE = "REMOVE"
    UPDATE = "UPDATE"


@strawberry.type
class JobStatusUpdate:
    """Job status update type from schema/types/job.graphql."""

    type: JobStatusUpdateType  # JobStatusUpdateType!
    job: Job  # Job!
