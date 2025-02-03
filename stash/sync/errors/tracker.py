"""Error tracking for sync operations."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from metadata.base import Base

T = TypeVar("T", bound=Base)


class SyncError(Exception):
    """Base class for sync errors."""

    def __init__(self, message: str, obj: Base | None = None) -> None:
        """Initialize sync error.

        Args:
            message: Error message
            obj: Optional object that caused the error
        """
        super().__init__(message)
        self.obj = obj
        self.timestamp = datetime.now()


class ValidationError(SyncError):
    """Error during data validation."""

    pass


class NetworkError(SyncError):
    """Error during network operations."""

    pass


class DatabaseError(SyncError):
    """Error during database operations."""

    pass


class ErrorTracker:
    """Track and aggregate errors during sync.

    This class tracks errors that occur during sync operations,
    providing:
    - Error aggregation by type
    - Error history
    - Error statistics
    - Error recovery hints

    Example:
        ```python
        tracker = ErrorTracker()

        try:
            await sync_account(account)
        except Exception as e:
            tracker.add_error(account, e)

        # Get all errors for accounts
        account_errors = tracker.get_errors(Account)

        # Get error stats
        stats = tracker.get_stats()
        print(f"Total errors: {stats['total']}")

        # Clear errors
        tracker.clear_errors(account)
        ```
    """

    def __init__(self) -> None:
        """Initialize error tracker."""
        # Track errors by (type, id)
        self._errors: dict[tuple[type, int], list[Exception]] = defaultdict(list)

        # Track error counts by type
        self._counts: dict[type[Exception], int] = defaultdict(int)

        # Track error timestamps
        self._timestamps: dict[tuple[type, int], list[datetime]] = defaultdict(list)

    def add_error(self, obj: T, error: Exception) -> None:
        """Add an error for an object.

        Args:
            obj: Object that had the error
            error: The error that occurred
        """
        key = (type(obj), obj.id)
        self._errors[key].append(error)
        self._counts[type(error)] += 1
        self._timestamps[key].append(datetime.now())

    def get_errors(self, type_: type[T]) -> dict[int, list[Exception]]:
        """Get all errors for a type.

        Args:
            type_: Type to get errors for

        Returns:
            Dictionary mapping object IDs to lists of errors
        """
        errors = {}
        for (obj_type, obj_id), error_list in self._errors.items():
            if obj_type == type_:
                errors[obj_id] = error_list
        return errors

    def get_object_errors(self, obj: T) -> list[Exception]:
        """Get all errors for an object.

        Args:
            obj: Object to get errors for

        Returns:
            List of errors for the object
        """
        return self._errors.get((type(obj), obj.id), [])

    def clear_errors(self, obj: T) -> None:
        """Clear all errors for an object.

        Args:
            obj: Object to clear errors for
        """
        key = (type(obj), obj.id)
        if key in self._errors:
            # Update counts
            for error in self._errors[key]:
                self._counts[type(error)] -= 1
            # Clear errors and timestamps
            del self._errors[key]
            del self._timestamps[key]

    def get_stats(self) -> dict[str, Any]:
        """Get error statistics.

        Returns:
            Dictionary containing:
            - total: Total number of errors
            - by_type: Errors grouped by exception type
            - by_object: Errors grouped by object type
        """
        return {
            "total": sum(self._counts.values()),
            "by_type": dict(self._counts),
            "by_object": {
                t.__name__: len(self.get_errors(t))
                for t in {obj_type for obj_type, _ in self._errors.keys()}
            },
        }

    def get_recovery_hints(self, obj: T) -> list[str]:
        """Get recovery hints for an object's errors.

        Args:
            obj: Object to get hints for

        Returns:
            List of recovery hint strings
        """
        hints = []
        for error in self.get_object_errors(obj):
            if isinstance(error, ValidationError):
                hints.append("Fix invalid data and retry")
            elif isinstance(error, NetworkError):
                hints.append("Check network connection and retry")
            elif isinstance(error, DatabaseError):
                hints.append("Check database connection and retry")
            else:
                hints.append("Unknown error - check logs")
        return hints

    def reset(self) -> None:
        """Reset all error tracking state."""
        self._errors.clear()
        self._counts.clear()
        self._timestamps.clear()
