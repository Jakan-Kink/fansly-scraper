"""Scalar types from schema/types/scalars.graphql."""

from datetime import datetime, timedelta

import strawberry


@strawberry.scalar(
    name="Time",
    description="An RFC3339 timestamp",
    serialize=lambda v: v.isoformat() if isinstance(v, datetime) else v,
    parse_value=lambda v: datetime.fromisoformat(v) if isinstance(v, str) else v,
)
class Time:
    """Time scalar type."""


@strawberry.scalar(
    name="Timestamp",
    description=(
        "Timestamp is a point in time. It is always output as RFC3339-compatible time points. "
        "It can be input as a RFC3339 string, or as '<4h' for '4 hours in the past' or '>5m' "
        "for '5 minutes in the future'"
    ),
    serialize=lambda v: v.isoformat() if isinstance(v, datetime) else v,
    parse_value=lambda v: _parse_timestamp(v) if isinstance(v, str) else v,
)
class Timestamp:
    """Timestamp scalar type from schema/types/scalars.graphql.

    Can be input as:
    - RFC3339 string (e.g., "2023-12-31T23:59:59Z")
    - Relative time in past (e.g., "<4h" for 4 hours ago)
    - Relative time in future (e.g., ">5m" for 5 minutes from now)"""


def _parse_timestamp(value: str) -> datetime:
    """Parse timestamp from string.

    Args:
        value: String to parse. Can be:
            - RFC3339 string (e.g., "2023-12-31T23:59:59Z")
            - Relative time in past (e.g., "<4h" for 4 hours ago)
            - Relative time in future (e.g., ">5m" for 5 minutes from now)

    Returns:
        Parsed datetime
    """
    # Handle relative times
    if value.startswith("<") or value.startswith(">"):
        direction = -1 if value.startswith("<") else 1
        amount = value[1:-1]  # Remove direction and unit
        unit = value[-1]  # Get unit (h/m)

        # Convert to seconds
        if unit == "h":
            seconds = int(amount) * 3600
        elif unit == "m":
            seconds = int(amount) * 60
        else:
            raise ValueError(
                f"Invalid time unit: {unit}. Only 'h' (hours) and 'm' (minutes) are supported."
            )

        # Add/subtract from now
        return datetime.now() + timedelta(seconds=direction * seconds)

    # Handle RFC3339 string
    return datetime.fromisoformat(value)


@strawberry.scalar(
    name="Map",
    description="A String -> Any map",
    serialize=lambda v: v,
    parse_value=lambda v: v,
)
class Map:
    """Map scalar type."""


@strawberry.scalar(
    name="BoolMap",
    description="A String -> Boolean map",
    serialize=lambda v: v,
    parse_value=lambda v: v,
)
class BoolMap:
    """BoolMap scalar type."""


@strawberry.scalar(
    name="PluginConfigMap",
    description="A plugin ID -> Map (String -> Any map) map",
    serialize=lambda v: v,
    parse_value=lambda v: v,
)
class PluginConfigMap:
    """PluginConfigMap scalar type."""


@strawberry.scalar(
    name="Any",
    serialize=lambda v: v,
    parse_value=lambda v: v,
)
class Any:
    """Any scalar type."""


@strawberry.scalar(
    name="Int64",
    description="A 64-bit integer",
    serialize=lambda v: int(v),
    parse_value=lambda v: int(v),
)
class Int64:
    """Int64 scalar type from schema/types/scalars.graphql.

    A 64-bit integer that can represent values from -2^63 to 2^63-1."""
