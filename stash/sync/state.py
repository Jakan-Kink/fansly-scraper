"""Sync state management for Stash integration."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from metadata import Base

T = TypeVar("T", bound=Base)


class SyncState:
    """Track sync state for Stash integration.

    This class tracks various states related to syncing metadata with Stash:
    - Dirty state (object needs sync)
    - Last sync time
    - Sync errors
    - Sync stats

    All state is kept in memory only, no database modifications.

    Example:
        ```python
        # Create state tracker
        state = SyncState()

        # Track sync state
        account = await Account.find_by_id(session, 123)
        if account:
            # Mark as needing sync
            state.mark_dirty(account)

            try:
                # Do sync work...
                state.start_sync()
                await sync_account_to_stash(account)
                state.mark_clean(account)
                state.end_sync(success=True)
            except Exception as e:
                state.add_error(account, e)
                state.end_sync(success=False)

            # Check state
            if state.is_dirty(account):
                print("Account needs sync")
            if state.get_errors(account):
                print("Account has sync errors")

            # Get stats
            stats = state.get_stats()
            print(f"Total syncs: {stats['total_syncs']}")
            print(f"Failed: {stats['failed_syncs']}")
        ```
    """

    def __init__(self) -> None:
        """Initialize sync state tracker."""
        # Track dirty state by (type, id)
        self._dirty_objects: dict[tuple[type, int], bool] = {}

        # Track last sync time by (type, id)
        self._last_sync: dict[tuple[type, int], datetime] = {}

        # Track sync errors by (type, id)
        self._sync_errors: dict[tuple[type, int], list[Exception]] = {}

        # Track sync stats
        self._stats = {
            "total_syncs": 0,
            "successful_syncs": 0,
            "failed_syncs": 0,
            "last_sync_start": None,
            "last_sync_end": None,
        }

    def mark_dirty(self, obj: T) -> None:
        """Mark an object as needing sync.

        Args:
            obj: The object to mark as dirty

        Example:
            ```python
            account = await Account.find_by_id(session, 123)
            if account:
                state.mark_dirty(account)  # Mark for sync
            ```
        """
        self._dirty_objects[(type(obj), obj.id)] = True

    def mark_clean(self, obj: T) -> None:
        """Mark an object as synced.

        Args:
            obj: The object to mark as clean

        Example:
            ```python
            account = await Account.find_by_id(session, 123)
            if account:
                await sync_account_to_stash(account)
                state.mark_clean(account)  # Mark as synced
            ```
        """
        key = (type(obj), obj.id)
        self._dirty_objects.pop(key, None)
        self._last_sync[key] = datetime.now()

    def is_dirty(self, obj: T) -> bool:
        """Check if an object needs sync.

        Args:
            obj: The object to check

        Returns:
            True if object needs sync, False otherwise

        Example:
            ```python
            account = await Account.find_by_id(session, 123)
            if account and state.is_dirty(account):
                await sync_account_to_stash(account)
            ```
        """
        return self._dirty_objects.get((type(obj), obj.id), False)

    def get_last_sync(self, obj: T) -> datetime | None:
        """Get last sync time for an object.

        Args:
            obj: The object to check

        Returns:
            Last sync time or None if never synced

        Example:
            ```python
            account = await Account.find_by_id(session, 123)
            if account:
                last_sync = state.get_last_sync(account)
                if last_sync:
                    print(f"Last synced: {last_sync}")
            ```
        """
        return self._last_sync.get((type(obj), obj.id))

    def add_error(self, obj: T, error: Exception) -> None:
        """Add a sync error for an object.

        Args:
            obj: The object that had an error
            error: The error that occurred

        Example:
            ```python
            account = await Account.find_by_id(session, 123)
            if account:
                try:
                    await sync_account_to_stash(account)
                except Exception as e:
                    state.add_error(account, e)
            ```
        """
        key = (type(obj), obj.id)
        if key not in self._sync_errors:
            self._sync_errors[key] = []
        self._sync_errors[key].append(error)
        self._stats["failed_syncs"] += 1

    def get_errors(self, obj: T) -> list[Exception]:
        """Get sync errors for an object.

        Args:
            obj: The object to get errors for

        Returns:
            List of errors for the object

        Example:
            ```python
            account = await Account.find_by_id(session, 123)
            if account:
                errors = state.get_errors(account)
                for error in errors:
                    print(f"Sync error: {error}")
            ```
        """
        return self._sync_errors.get((type(obj), obj.id), [])

    def clear_errors(self, obj: T) -> None:
        """Clear sync errors for an object.

        Args:
            obj: The object to clear errors for

        Example:
            ```python
            account = await Account.find_by_id(session, 123)
            if account:
                state.clear_errors(account)  # Clear any errors
            ```
        """
        self._sync_errors.pop((type(obj), obj.id), None)

    def start_sync(self) -> None:
        """Start a sync operation.

        Example:
            ```python
            state.start_sync()
            try:
                await sync_all_accounts()
                state.end_sync(success=True)
            except Exception:
                state.end_sync(success=False)
            ```
        """
        self._stats["total_syncs"] += 1
        self._stats["last_sync_start"] = datetime.now()

    def end_sync(self, success: bool = True) -> None:
        """End a sync operation.

        Args:
            success: Whether the sync was successful

        Example:
            ```python
            state.start_sync()
            try:
                await sync_all_accounts()
                state.end_sync(success=True)
            except Exception:
                state.end_sync(success=False)
            ```
        """
        if success:
            self._stats["successful_syncs"] += 1
        self._stats["last_sync_end"] = datetime.now()

    def get_stats(self) -> dict[str, Any]:
        """Get sync statistics.

        Returns:
            Dictionary of sync stats:
            - total_syncs: Total number of sync operations
            - successful_syncs: Number of successful syncs
            - failed_syncs: Number of failed syncs
            - last_sync_start: Start time of last sync
            - last_sync_end: End time of last sync

        Example:
            ```python
            stats = state.get_stats()
            print(f"Total syncs: {stats['total_syncs']}")
            print(f"Failed: {stats['failed_syncs']}")
            print(f"Success rate: {stats['successful_syncs'] / stats['total_syncs']:.2%}")
            ```
        """
        return self._stats.copy()

    def get_dirty_objects(self, type_: type[T] | None = None) -> list[tuple[type, int]]:
        """Get all dirty objects.

        Args:
            type_: Optional type to filter by

        Returns:
            List of (type, id) tuples for dirty objects

        Example:
            ```python
            # Get all dirty objects
            all_dirty = state.get_dirty_objects()

            # Get only dirty accounts
            dirty_accounts = state.get_dirty_objects(Account)
            ```
        """
        if type_ is None:
            return list(self._dirty_objects.keys())
        return [k for k in self._dirty_objects.keys() if k[0] == type_]

    async def load_dirty_objects(
        self,
        session: AsyncSession,
        type_: type[T],
    ) -> list[T]:
        """Load all dirty objects of a given type.

        Args:
            session: Database session to use
            type_: Type of objects to load

        Returns:
            List of loaded dirty objects

        Example:
            ```python
            # Load all dirty accounts
            dirty_accounts = await state.load_dirty_objects(session, Account)
            for account in dirty_accounts:
                await sync_account_to_stash(account)
            ```
        """
        dirty_ids = [id_ for t, id_ in self.get_dirty_objects(type_)]
        if not dirty_ids:
            return []

        # Use type's query method to load objects
        return await type_.find_by_ids(session, dirty_ids)

    def reset(self) -> None:
        """Reset all state.

        This clears all tracked state:
        - Dirty objects
        - Last sync times
        - Sync errors
        - Sync stats

        Example:
            ```python
            # Clear all state
            state.reset()
            ```
        """
        self._dirty_objects.clear()
        self._last_sync.clear()
        self._sync_errors.clear()
        self._stats = {
            "total_syncs": 0,
            "successful_syncs": 0,
            "failed_syncs": 0,
            "last_sync_start": None,
            "last_sync_end": None,
        }
