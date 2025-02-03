# Fansly-Stash Integration Progress Report

## Overview

This document analyzes the progress and discrepancies between the current codebase and the proposed Stash integration changes. The proposed changes aim to integrate Fansly metadata with a Stash media management system, but several assumptions and incompatibilities need to be addressed.

## Implementation Analysis

After reviewing both the current implementation and previously proposed changes, we've made the following decisions:

### 1. Metadata Layer (`/metadata/`)

#### Current Metadata Implementation - ✅ Keep As Is

The current metadata layer in `/metadata/` provides:

- Rich utility methods for data handling
  - Timestamp conversion with timezone support
  - Field updates with change tracking
  - Relationship validation
  - Data filtering and processing
- Both sync and async support
  - `get_or_create` and `async_get_or_create`
  - Identity map handling
  - Proper session management
- Clean separation of concerns
  - No Stash-specific code
  - No schema modifications
  - Pure metadata handling
- Proper relationship handling
  - Cascade behaviors
  - Lazy loading strategies
  - Circular dependency handling
  - Collection types
  - Join tables
  - Unique constraints
  - Indexes

#### Previously Proposed Approaches for Metadata/Stash - ❌ Not Used

1. Proposed Metadata Changes:

   - Early prototype that started exploring:
     - Using dataclass features
     - Different relationship patterns
     - Position tracking for media
   - Not used because:
     - Current implementation already handles everything needed
     - Would add complexity without benefits
     - Some features didn't match API (e.g., position tracking)

2. Proposed Stash Integration:

   - Initial attempt that mixed:
     - Stash-specific code in models
     - Direct sync logic in entities
     - Schema modifications for sync state
   - Not used because:
     - Better to keep Stash integration separate
     - No need to modify existing models
     - State tracking should be independent

3. Current Strawberry Stash:
   - Clean implementation that:
     - Keeps models unchanged
     - Uses separate sync state
     - Matches API exactly
     - Proper error handling

**Actual Solution** - ✅ New Design:

1. Separate State Tracking:

   ```python
   class SyncState:
       """Track sync state without modifying models."""

       def __init__(self) -> None:
           # Runtime-only state
           self._dirty_objects: dict[tuple[type, int], bool] = {}
           self._last_sync: dict[tuple[type, int], datetime] = {}
           self._sync_errors: dict[tuple[type, int], list[Exception]] = {}
           self._stats = {
               "total_syncs": 0,
               "successful_syncs": 0,
               "failed_syncs": 0,
               "last_sync_start": None,
               "last_sync_end": None,
           }

       # State tracking
       def mark_dirty(self, obj: T) -> None: ...
       def mark_clean(self, obj: T) -> None: ...
       def is_dirty(self, obj: T) -> bool: ...

       # Error handling
       def add_error(self, obj: T, error: Exception) -> None: ...
       def get_errors(self, obj: T) -> list[Exception]: ...
       def clear_errors(self, obj: T) -> None: ...

       # Sync operations
       def start_sync(self) -> None: ...
       def end_sync(self, success: bool = True) -> None: ...
       def get_stats(self) -> dict[str, Any]: ...

       # Object loading
       def get_dirty_objects(self, type_: type[T]) -> list[tuple[type, int]]: ...
       async def load_dirty_objects(
           self,
           session: AsyncSession,
           type_: type[T],
       ) -> list[T]: ...
   ```

2. Benefits of New Design:

   - No schema modifications
   - Clean separation of concerns
   - Better error handling
   - Proper state management
   - No memory leaks
   - Better performance
   - Type safety
   - Testability

3. Implementation Strategy:

   - Keep models unchanged
   - Track state separately
   - Handle errors properly
   - Manage resources efficiently
   - Support testing
   - Enable monitoring

4. Additional Features:
   - Stats tracking
   - Error aggregation
   - Performance monitoring
   - Resource cleanup
   - Testing support
   - Documentation

### 2. Solution Overview

1. Metadata Layer (`/metadata/`):

   - Keep current implementation unchanged
   - Matches Fansly API exactly
   - Handles all data processing
   - Manages relationships
   - Handles persistence

2. Stash Integration (`/stash/`):

   - New, separate implementation
   - Uses Strawberry for GraphQL
   - Handles sync state independently
   - Clean error handling
   - Progress monitoring

3. Benefits of Separation:
   - Metadata layer stays focused on Fansly data
   - Stash integration can evolve independently
   - Clear boundaries between concerns
   - Better testing of each layer
   - Easier maintenance

## Analysis of Previous Approaches

1. Initial Assumptions:

   - Tried modifying schema directly
   - Mixed concerns in models
   - Complex caching logic
   - Tight coupling
   - No clear migration path

2. Lessons Learned:

   - Keep models unchanged
   - Separate state tracking
   - Clean integration layer
   - Clear migration path
   - Better error handling

3. Key Insights:
   - Runtime state > schema changes
   - Separation > tight coupling
   - Simple > complex
   - Testable > clever
   - Maintainable > feature-rich

## Current State and Next Steps

### 1. Current Implementation

1. Data Flow:

   ```python
   # Main Flow:
   process_creator()
   ├─ _find_account()  # Get account from DB
   ├─ _find_existing_performer()  # Find in Stash
   ├─ _create_performer()  # Create if not found
   └─ _update_performer_avatar()  # Update avatar if needed

   continue_stash_processing()
   ├─ _update_account_stash_id()  # Link account to performer
   ├─ process_creator_studio()  # Create/update studio
   ├─ process_creator_posts()  # Process posts
   └─ process_creator_messages()  # Process messages

   # Content Processing:
   process_creator_posts()
   └─ _process_items_with_gallery()
       └─ _process_item_gallery()
               ├─ _get_or_create_gallery()  # Create gallery in Stash
               ├─ process_creator_attachment()  # Process media files
               └─ _update_gallery_files()  # Link files to gallery
   ```

2. Type Mappings:

   ```python
   # Account -> Performer
   performer = Performer(
       id="new",
       name=account.displayName or account.username,
       disambiguation=account.username,
       details=account.about,
       urls=[f"https://fansly.com/{account.username}/posts"],
       country=account.location,
   )

   # Post/Message -> Gallery
   gallery = Gallery(
       title=item.content,
       url=url_pattern,
       date=item.createdAt,
       performers=[performer],
       studio=studio,
   )
   ```

### 2. Components to Add

1. Sync Interface:

   ````python
   class SyncInterface:
       """High-level sync operations using StashClient."""
       def __init__(self, client: StashClient, state: SyncState) -> None: ...

       # High-level sync operations
       async def sync_performer(self, account: Account) -> None:
           """Sync account to Stash performer with proper error handling."""

       async def sync_gallery(self, post: Post) -> None:
           """Sync post to Stash gallery with proper error handling."""

       async def sync_scene(self, media: Media) -> None:
           """Sync media to Stash scene with proper error handling."""

       # Batch operations
       async def sync_performers(self, accounts: list[Account]) -> None:
           """Sync multiple accounts efficiently."""

       async def sync_galleries(self, posts: list[Post]) -> None:
           """Sync multiple posts efficiently."""

       async def sync_scenes(self, media_items: list[Media]) -> None:
           """Sync multiple media items efficiently."""
           ```

   ````

2. Type Converters:

   ```python
   class PerformerConverter:
       """Convert between Account and Performer."""
       @staticmethod
       def to_performer(account: Account) -> Performer:
           """Convert Account to new Performer."""

       @staticmethod
       def update_performer(account: Account, performer: Performer) -> bool:
           """Update Performer from Account, return True if changed."""

   class GalleryConverter:
       """Convert between Post and Gallery."""
       @staticmethod
       def to_gallery(post: Post) -> Gallery:
           """Convert Post to new Gallery."""

       @staticmethod
       def update_gallery(post: Post, gallery: Gallery) -> bool:
           """Update Gallery from Post, return True if changed."""

   class SceneConverter:
       """Convert between Media and Scene."""
       @staticmethod
       def to_scene(media: Media) -> Scene:
           """Convert Media to new Scene."""

       @staticmethod
       def update_scene(media: Media, scene: Scene) -> bool:
           """Update Scene from Media, return True if changed."""
   ```

3. Batch Processing:

   ```python
   class BatchProcessor:
       """Process items in efficient batches."""
       def __init__(
           self,
           client: StashClient,
           state: SyncState,
           batch_size: int = 25,
       ) -> None: ...

       async def process_batch(
           self,
           items: list[T],
           converter: TypeConverter[T],
           find_method: Callable,
           create_method: Callable,
           update_method: Callable,
       ) -> BatchResult: ...
   ```

4. Monitoring:

   ```python
   class SyncMonitor:
       """Monitor sync operations with UI updates."""
       def __init__(self) -> None:
           self.stats = {
               "started": None,  # Start time
               "finished": None,  # End time
               "processed": 0,   # Items processed
               "created": 0,     # Items created
               "updated": 0,     # Items updated
               "failed": 0,      # Items failed
               "skipped": 0,     # Items skipped
           }
           self.current_operation: str | None = None
           self.progress: float = 0.0
           self.errors: list[tuple[Any, Exception]] = []

       def start_operation(self, name: str) -> None: ...
       def update_progress(self, done: int, total: int) -> None: ...
       def add_error(self, item: Any, error: Exception) -> None: ...
       def get_status(self) -> dict[str, Any]: ...
   ```

### 3. Implementation Plan

1. Phase 1 - Type System:

   - Implement type converters
   - Add conversion tests
   - Document mappings
   - Validate with real data

2. Phase 2 - Core Sync:

   - Build SyncInterface
   - Add BatchProcessor
   - Basic monitoring
   - Error handling

3. Phase 3 - Monitoring:

   - Enhance SyncMonitor
   - Add detailed stats
   - Improve error tracking
   - Progress reporting

4. Phase 4 - Polish:
   - Performance tuning
   - Error recovery
   - Documentation
   - Testing

## Conclusion

After a thorough review of both the current implementation and proposed changes, we have:

1. Analyzed Current Code:

   - Reviewed all models and relationships
   - Checked API compatibility
   - Examined utility functions
   - Verified error handling
   - Confirmed data processing

2. Evaluated Proposed Changes:

   - Found incompatibilities with API
   - Identified unnecessary complexity
   - Discovered potential issues
   - Noted missing functionality
   - Found incorrect assumptions

3. Made Decisions:

   - Keep current models unchanged
   - Remove proposed changes
   - Add Stash integration separately
   - Use SyncState for tracking
   - Maintain clean separation

4. Next Steps:
   - Implement SyncState
   - Add Stash integration
   - Add error tracking
   - Add progress monitoring
   - Add testing

## Integration Approach

1. Data Flow by Layer:

   ```text
   Fansly API JSON → /metadata/ → SQLite DB
                                    ↓
                     /stash/ ← Strawberry Types → Stash GraphQL API
   ```

2. Design Decisions:

   - Using Strawberry GraphQL for type safety and schema generation
   - Requiring newer Stash version (post Movie->Group migration)
   - Using snake_case in Python code (Strawberry converts to camelCase)
   - Keeping core functionality in base classes
   - Separating GraphQL client from business logic

3. Implementation Order:
   a. Complete Stash Types ✅

   - Added all core type definitions
   - Using Strawberry for schema generation
   - Added input type support
   - Removed deprecated Movie support (requiring newer Stash version)

   b. Enhance Interface ✅

   - StashClient implementation:
     - Using Strawberry for type safety
     - No need for manual fragment loading
     - Organized GraphQL fragments in fragments.py
     - Added methods for all types (find/create/update)
     - Added detailed docstrings with examples
     - Added async context manager support
     - Added better error handling
     - Scene methods complete ✅
     - Performer methods complete ✅
     - Studio methods complete ✅
     - Tag methods complete ✅
     - Gallery methods complete ✅
     - Image methods complete ✅
     - Marker methods complete ✅
     - Group methods removed ✅

   c. Create Conversion Layer

   - Implement type converters
   - Add file handling
   - Add utility functions

   d. Add Sync System

   - Implement sync logic
   - Add error handling
   - Add progress tracking
