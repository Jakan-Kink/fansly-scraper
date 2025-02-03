"""Logging types from schema/types/logging.graphql."""

from datetime import datetime
from enum import Enum

import strawberry


@strawberry.enum
class LogLevel(str, Enum):
    """Log level enum from schema/types/logging.graphql."""

    TRACE = "Trace"
    DEBUG = "Debug"
    INFO = "Info"
    PROGRESS = "Progress"
    WARNING = "Warning"
    ERROR = "Error"


@strawberry.type
class LogEntry:
    """Log entry type from schema/types/logging.graphql."""

    time: datetime  # Time!
    level: LogLevel  # LogLevel!
    message: str  # String!
