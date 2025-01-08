"""Base module for SQLAlchemy declarative models.

This module provides the base class for all SQLAlchemy models in the application.
It uses the modern declarative base approach from SQLAlchemy 2.0+ with async support.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, TypeVar

from sqlalchemy import DateTime, event
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import DeclarativeBase, Mapper

from logging_utils import json_output

T = TypeVar("T", bound="Base")


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for all SQLAlchemy models.

    This class serves as the declarative base for all database models in the
    application. It inherits from both AsyncAttrs and DeclarativeBase to provide:
    - Async operation support through AsyncAttrs
    - Modern declarative mapping through DeclarativeBase
    - Automatic timezone handling for datetime columns

    All model classes should inherit from this base class to ensure consistent
    behavior and metadata handling, with support for both sync and async operations.

    Example:
        class MyModel(Base):
            __tablename__ = "my_models"
            id: Mapped[int] = mapped_column(primary_key=True)

            # Supports both sync and async operations
            async def async_method(self):
                ...

    Note:
        This base class automatically handles timezone conversion for all datetime
        columns marked with timezone=True. Since SQLite doesn't support timezone-aware
        datetimes natively, we attach UTC timezone information to all datetime values
        when they are loaded from the database.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Register event listener for timezone handling
        event.listen(Base, "load", Base._attach_timezone)

    @staticmethod
    def _attach_timezone(target: Any, _context: Any) -> None:
        """Attach UTC timezone to all timezone-aware datetime columns on load.

        This method is called automatically when an object is loaded from the database.
        It finds all DateTime columns with timezone=True and ensures their values
        have UTC timezone information attached.

        Args:
            target: The model instance being loaded
            _context: SQLAlchemy context (unused)
        """
        # Get all datetime columns with timezone=True
        mapper: Mapper = inspect(target.__class__)
        for column in mapper.columns:
            if isinstance(column.type, DateTime) and column.type.timezone:
                value = getattr(target, column.key)
                if value is not None and value.tzinfo is None:
                    setattr(target, column.key, value.replace(tzinfo=timezone.utc))

    @staticmethod
    def convert_timestamps(data: dict[str, Any], date_fields: Sequence[str]) -> None:
        """Convert timestamp fields in data from milliseconds/seconds to datetime.

        This helper method processes timestamp fields in a data dictionary, converting
        them from either milliseconds or seconds (auto-detected) to datetime objects
        in UTC timezone.

        Args:
            data: Dictionary containing the data with timestamp fields
            date_fields: Sequence of field names to process as timestamps

        Example:
            >>> data = {"createdAt": 1684968827000, "updatedAt": 1733377315}
            >>> Base.convert_timestamps(data, ("createdAt", "updatedAt"))
            >>> print(data["createdAt"])  # Now a datetime object
        """
        for date_field in date_fields:
            if date_field in data and data[date_field]:
                timestamp = data[date_field]
                # Skip if already a datetime
                if isinstance(timestamp, datetime):
                    continue
                # Convert to seconds if in milliseconds (> 1e10)
                # Unix timestamps in seconds are ~1e9 (2023 = 1.7e9)
                # Unix timestamps in milliseconds are ~1e12
                # 1e10 is a good threshold between the two
                if isinstance(timestamp, (int, float)) and timestamp > 1e10:
                    timestamp = timestamp / 1000
                data[date_field] = datetime.fromtimestamp(timestamp, timezone.utc)

    @classmethod
    def process_data(
        cls: type[T],
        data: dict[str, Any],
        known_relations: set[str],
        log_prefix: str,
        convert_timestamps_fields: Sequence[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Process data dictionary for a model, handling filtering and logging.

        This helper method processes a data dictionary for a model by:
        1. Converting timestamps if specified
        2. Identifying and logging unknown attributes
        3. Filtering data to only include valid model columns

        Args:
            data: Dictionary containing the data to process
            known_relations: Set of field names that are handled separately or intentionally ignored
            log_prefix: Prefix for log messages (e.g., "meta/account")
            convert_timestamps_fields: Optional sequence of field names to process as timestamps

        Returns:
            A tuple containing:
            - filtered_data: Dictionary with only valid model columns
            - unknown_attrs: Dictionary with unknown attributes for logging

        Example:
            >>> known_relations = {"timelineStats", "pinnedPosts", "walls"}
            >>> filtered_data, unknown_attrs = Account.process_data(
            ...     data,
            ...     known_relations,
            ...     "meta/account",
            ...     ("createdAt", "updatedAt")
            ... )
        """
        # Get valid column names for the model
        model_columns = {column.name for column in inspect(cls).columns}

        # Convert timestamps if specified
        if convert_timestamps_fields:
            cls.convert_timestamps(data, convert_timestamps_fields)

        # Log truly unknown attributes (not in columns and not handled separately)
        unknown_attrs = {
            k: v
            for k, v in data.items()
            if k not in model_columns and k not in known_relations
        }
        if unknown_attrs:
            json_output(1, f"{log_prefix} - unknown_attributes", unknown_attrs)

        # Filter data to only include valid columns
        filtered_data = {k: v for k, v in data.items() if k in model_columns}

        return filtered_data, unknown_attrs
