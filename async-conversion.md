# Async Conversion Plan

## Phase 1: Dependency Analysis

### 1. Module Map

#### Core Database Modules
```
metadata/
├── database.py (Core Database Layer)
│   ├── Database class
│   ├── Session management
│   └── Event handlers
├── base.py (Base Models)
│   ├── Base class
│   └── Common model utilities
└── decorators.py
    ├── require_database_config
    └── with_database_session

```

#### Model Modules (Need Async)
```
metadata/
├── account.py (Account Management)
│   ├── Account model
│   ├── process_account_data
│   └── Account relationships
├── media.py (Media Management)
│   ├── Media model
│   ├── Media processing
│   └── Media relationships
├── messages.py (Message Management)
│   ├── Message model
│   └── Message processing
├── post.py (Post Management)
│   ├── Post model
│   └── Post relationships
├── wall.py (Wall Management)
│   ├── Wall model
│   └── Wall relationships
├── attachment.py (Attachments)
│   ├── Attachment model
│   └── Attachment processing
├── hashtag.py (Hashtags)
│   ├── Hashtag model
│   └── Hashtag relationships
└── story.py (Stories)
    ├── Story model
    └── Story relationships
```

#### Download Modules (Need Async)
```
download/
├── account.py (Already Async)
│   ├── get_following_accounts
│   └── get_creator_account_info
├── media.py
│   ├── download_media
│   └── process_media
├── messages.py
│   ├── download_messages
│   └── process_messages
└── wall.py
    ├── download_wall
    └── process_wall
```

#### File Processing Modules (Need Async)
```
fileio/
├── dedupe.py (Being Converted)
│   ├── get_or_create_media
│   ├── dedupe_init
│   └── dedupe_media_file
├── mp4.py
│   └── MP4 processing
└── normalize.py
    └── Filename normalization
```

#### API Modules (Already Async)
```
api/
└── fansly.py
    ├── API client
    └── API methods
```

#### Main Application (Partially Async)
```
fansly_downloader_ng.py
├── main()
└── _async_main()
```

#### Test Modules (Need Async Updates)
```
tests/
├── metadata/
│   ├── integration/
│   │   ├── test_database_operations.py
│   │   └── [other integration tests]
│   └── unit/
│       ├── test_database.py
│       └── [other unit tests]
└── conftest.py
    └── Test fixtures
```

### 2. Module Dependencies and Conversion Order

#### Layer 1: Core Database
```
metadata/database.py
├── Session management
└── Used by: All other database modules
```

#### Layer 2: Base Models and Decorators
```
metadata/
├── base.py
│   └── Used by: All model modules
└── decorators.py
    └── Used by: All database-using modules
```

#### Layer 3: Core Models
```
metadata/
├── account.py (Primary entity)
│   └── Used by: messages.py, wall.py, post.py
├── media.py (Primary entity)
│   └── Used by: account.py, messages.py, post.py
└── hashtag.py (Independent entity)
```

#### Layer 4: Relationship Models
```
metadata/
├── messages.py
│   ├── Depends on: account.py, media.py
│   └── Used by: download/messages.py
├── post.py
│   ├── Depends on: account.py, media.py
│   └── Used by: download/timeline.py
└── wall.py
    ├── Depends on: account.py
    └── Used by: download/wall.py
```

#### Layer 5: Download Modules
```
download/
├── account.py (Already async)
├── media.py
│   └── Depends on: metadata/media.py
├── messages.py
│   └── Depends on: metadata/messages.py
└── wall.py
    └── Depends on: metadata/wall.py
```

#### Layer 6: File Processing
```
fileio/
├── dedupe.py
│   ├── Depends on: metadata/media.py
│   └── Being converted to async
└── mp4.py
    └── Independent
```

#### Conversion Order with Complexity Notes:

1. Core Database Layer:
   ```
   metadata/database.py:
   - Session
   - High complexity: OptimizedSQLiteMemory.close_sync() -> 13
   - Priority: High (Core functionality)
   ```

2. Independent Models:
   ```
   metadata/:
   - hashtag.py (No dependencies)
   - story.py (No dependencies)
   - Priority: Medium (Independent, good starting point)
   ```

3. Primary Models:
   ```
   metadata/:
   - media.py
     * Core entity
     * High complexity: parse_media_info() -> 13
     * Priority: High (Many dependencies)
   - account.py
     * Core entity
     * Priority: High (Many dependencies)
   ```

4. Relationship Models:
   ```
   metadata/:
   - messages.py
     * Depends on: account.py, media.py
     * Priority: Medium
   - post.py
     * Depends on: account.py, media.py
     * Priority: Medium
   - wall.py
     * Depends on: account.py
     * Priority: Medium
   ```

5. Download Layer:
   ```
   download/:
   - media.py
     * High complexity: download_media() -> 20
     * Priority: High
   - messages.py
     * High complexity: download_messages() -> 14
     * Priority: Medium
   - wall.py
     * High complexity: download_wall() -> 18
     * Priority: Medium
   - m3u8.py
     * High complexity: download_m3u8() -> 17
     * Priority: Medium
   - timeline.py
     * High complexity: download_timeline() -> 17
     * Priority: Medium
   ```

6. File Processing:
   ```
   fileio/:
   - dedupe.py (In progress)
     * High complexity:
       - get_or_create_media() -> 25
       - dedupe_init() -> 13
       - dedupe_media_file() -> 37
     * Priority: High (Already started)
   - fnmanip.py
     * High complexity: add_hash_to_other_content() -> 13
     * Priority: Medium
   ```

7. Configuration & Validation:
   ```
   config/:
   - validation.py
     * High complexity: validate_adjust_token() -> 20
     * Priority: Low (Less database interaction)
   helpers/:
   - web.py
     * High complexity: guess_user_agent() -> 19
     * Priority: Low (Independent functionality)
   ```

8. Main Application:
   ```
   fansly_downloader_ng.py:
   - High complexity: main() -> 25
   - Priority: Last (Depends on all other modules)
   ```

9. Tests:
   ```
   tests/metadata/:
   - High complexity:
     * utils.py: create_test_data_set() -> 13
     * utils.py: verify_relationship_integrity() -> 19
     * test_media_processing.py: test_process_video_from_timeline() -> 18
   - Priority: Parallel (Update as modules are converted)
   ```

10. Logging:
    ```
    textio/logging.py:
    - High complexity:
      * SizeAndTimeRotatingFileHandler.doRollover() -> 16
      * SizeAndTimeRotatingFileHandler._compress_file() -> 18
    - Priority: Low (Independent functionality)
    ```

#### Implementation Strategy:
1. Start with core database layer
2. Move to independent models
3. Progress through dependencies
4. Handle high complexity functions as we encounter them
5. Keep tests in sync with changes
6. Leave independent high-complexity functions for later

### 3. Common Patterns and Challenges

#### Common Patterns to Convert

1. Session Management:
```python
# From
with session.begin():
    # code

# To
async with session.begin():
    # code
```

2. Database Operations:
```python
# From
session.execute(query).scalar_one()

# To
await session.execute(query).scalar_one()
```

3. Relationship Loading:
```python
# From
account.messages

# To
await account.awaitable_attrs.messages
```

4. Transaction Management:
```python
# From
try:
    session.begin()
    # code
    session.commit()
except:
    session.rollback()

# To
async with session.begin():
    # code  # rollback happens automatically
```

#### Common Challenges

1. Mixed Sync/Async Code:
   - Functions calling both sync and async code
   - Event handlers needing both versions
   - Decorators handling both types

2. Transaction Management:
   - Nested transactions in async context
   - Savepoints in async mode
   - Error handling across async boundaries

3. Session Lifecycle:
   - Session cleanup in async context
   - Connection pooling with async
   - Resource management

4. Testing:
   - Async fixtures
   - Transaction isolation in tests
   - Mock async calls

5. Error Handling:
   - Async stack traces
   - Transaction rollback in async
   - Resource cleanup on errors

#### Solutions and Patterns

1. Session Management:
```python
@with_database_session(async_session=True)
async def process_data(
    data: dict,
    session: AsyncSession | None = None,
) -> None:
    async with session.begin():
        # All operations use await
        result = await session.execute(query)
        # Handle relationships
        await obj.awaitable_attrs.related
```

2. Error Handling:
```python
async def safe_operation():
    try:
        async with session.begin():
            await do_work()
    except SQLAlchemyError as e:
        # Handle database errors
        raise
    except AsyncIOError as e:
        # Handle async-specific errors
        raise
    finally:
        # Cleanup
        await cleanup()
```

3. Testing:
```python
@pytest.mark.asyncio
async def test_async_operation():
    async with AsyncSession() as session:
        async with session.begin():
            # Test code
```

### 4. Main Entry Points
1. `fansly_downloader_ng.py`:
   - `main()` -> async
   - `_async_main()` -> already async

2. Primary Functions:
   - `get_following_accounts()` (download/account.py) -> already async
   - `process_account_data()` (metadata/account.py) -> needs async
   - `get_creator_account_info()` (download/account.py) -> already async

### 2. Module Dependencies

#### Core Processing Chain
```
fansly_downloader_ng.py
├── download/account.py
│   ├── get_following_accounts (async)
│   └── get_creator_account_info (async)
└── metadata/account.py
    ├── process_account_data
    ├── process_avatar
    ├── process_banner
    ├── process_media_bundles_data
    └── process_media_bundles_for_account
```

#### Database Operations
```
metadata/database.py (Core Database Functionality)
├── Database class
│   ├── sync_session
│   ├── async_session
│   └── transaction management
└── Decorators
    ├── require_database_config
    └── with_database_session
```

#### Media Processing Chain
```
metadata/media.py
├── _process_media_item_dict_inner
├── _process_media_variants
└── Media model operations

metadata/media_utils.py
├── link_media_to_bundle
└── validate_media_id
```

#### Other Database-Using Modules
```
metadata/
├── wall.py
├── hashtag.py
├── attachment.py
├── messages.py
├── relationship_logger.py
└── post.py
```

### 3. Test Coverage
```
tests/metadata/
├── unit/
│   ├── test_database.py
│   ├── test_account.py
│   ├── test_media.py
│   └── [other unit tests]
└── integration/
    ├── test_database_operations.py
    ├── test_account_processing.py
    └── [other integration tests]
```

### 4. Complexity Reduction

#### High Complexity Functions

1. Core Application Logic (25+):
```python
# fansly_downloader_ng.py
main() -> 25  # Main application flow
# fileio/dedupe.py
dedupe_media_file() -> 37  # File deduplication
get_or_create_media() -> 25  # Media record management
```

2. Download Operations (14-20):
```python
# download/
media.py: download_media() -> 20
timeline.py: download_timeline() -> 17
wall.py: download_wall() -> 18
m3u8.py: download_m3u8() -> 17
messages.py: download_messages() -> 14
```

3. Configuration & Validation (13-20):
```python
# config/validation.py
validate_adjust_token() -> 20
# helpers/web.py
guess_user_agent() -> 19
```

4. Database & File Operations (13):
```python
# metadata/database.py
OptimizedSQLiteMemory.close_sync() -> 13
# fileio/fnmanip.py
add_hash_to_other_content() -> 13
# media/media.py
parse_media_info() -> 13
```

5. Test Code (13-19):
```python
# tests/metadata/
utils.py: create_test_data_set() -> 13
utils.py: verify_relationship_integrity() -> 19
integration/test_media_processing.py:
    test_process_video_from_timeline() -> 18
```

6. Logging Operations (16-18):
```python
# textio/logging.py
SizeAndTimeRotatingFileHandler:
    doRollover() -> 16
    _compress_file() -> 18
```

#### Complexity Reduction Strategies

1. Command Pattern (For Core Logic):
```python
# Before
def main():
    if condition1:
        if condition2:
            # Nested logic
    # More conditions...

# After
class DownloadCommand:
    def execute(self): ...

class MessageDownloadCommand(DownloadCommand):
    def execute(self): ...

def main():
    commands = [
        MessageDownloadCommand(),
        TimelineDownloadCommand(),
        # etc.
    ]
    for cmd in commands:
        cmd.execute()
```

2. State Pattern (For Download Operations):
```python
# Before
def download_media():
    if state == "pending":
        if type == "video":
            # Complex logic
    elif state == "downloading":
        # More logic

# After
class MediaDownloadState:
    def handle(self): ...

class PendingState(MediaDownloadState):
    def handle(self): ...

class DownloadingState(MediaDownloadState):
    def handle(self): ...
```

3. Strategy Pattern (For File Operations):
```python
# Before
def dedupe_media_file():
    if file_type == "video":
        # Complex video logic
    elif file_type == "image":
        # Complex image logic

# After
class DedupeStrategy:
    def dedupe(self): ...

class VideoDedupeStrategy(DedupeStrategy):
    def dedupe(self): ...

class ImageDedupeStrategy(DedupeStrategy):
    def dedupe(self): ...
```

4. Chain of Responsibility (For Validation):
```python
# Before
def validate_adjust_token():
    if not basic_check():
        return False
    if not format_check():
        return False
    # More checks...

# After
class ValidationHandler:
    def set_next(self, handler): ...
    def handle(self, token): ...

class BasicValidationHandler(ValidationHandler): ...
class FormatValidationHandler(ValidationHandler): ...
```

5. Template Method (For File Processing):
```python
# Before
def process_file():
    # Complex logic with many steps

# After
class FileProcessor:
    def process(self):
        self.validate()
        self.prepare()
        self.execute()
        self.cleanup()

    def validate(self): ...
    def prepare(self): ...
    def execute(self): ...
    def cleanup(self): ...
```

6. Observer Pattern (For Logging):
```python
# Before
class SizeAndTimeRotatingFileHandler:
    def doRollover(self):
        # Complex rollover logic

# After
class RolloverSubject:
    def notify(self): ...

class SizeRollover(RolloverObserver):
    def update(self): ...

class TimeRollover(RolloverObserver):
    def update(self): ...
```

#### Example: Reducing Complexity in dedupe.py

Original Complex Function:
```python
def get_or_create_media(session, file_path, media_id, mimetype, state, file_hash=None, trust_filename=False):
    # Complex nested conditionals
    if media_id:
        if media:
            if media.local_filename == filename:
                if media.content_hash:
                    # More nesting...
```

Refactored into Smaller Functions:
```python
@with_database_session()
def get_or_create_media(file_path, media_id, mimetype, state, session=None):
    # High-level flow control
    if media_id:
        media = find_media_by_id(media_id, session=session)
        if media:
            return handle_existing_media(media, file_path, ...)

@with_database_session()
def handle_existing_media(media, file_path, filename, session=None):
    # Specific case handling
    if media.local_filename == filename:
        return handle_exact_filename_match(...)
```

Benefits:
1. Reduced Complexity:
   - Each function has a single responsibility
   - Clearer error boundaries
   - Easier testing

2. Better Async Support:
   - Decorator handles session management
   - Consistent error handling
   - Clear transaction boundaries

3. Improved Maintainability:
   - Functions are self-documenting
   - Easier to modify individual cases
   - Better type hints

4. Better Testing:
   - Each function can be tested independently
   - Clearer test cases
   - Easier mocking

5. Remaining Functions to Convert:
```python
# Pure Functions (No Changes Needed)
get_filename_only()
paths_match()
safe_rglob()
calculate_file_hash()

# Database Functions (Need Async)
@with_database_session()
def get_account_id()  # Done

@with_database_session()
def find_media_by_id()  # Done

@with_database_session()
def find_media_by_hash()  # Done

@with_database_session()
def update_media_record()  # Done

@with_database_session()
def create_media_record()  # Done

@with_database_session()
def handle_exact_filename_match()  # Done

@with_database_session()
def handle_missing_filename()  # Done

@with_database_session()
def handle_path_normalization()  # Done

@with_database_session()
def update_media_with_hash()  # Done

@with_database_session()
def handle_existing_media()  # Done

@with_database_session()
def get_or_create_media()  # Done

@require_database_config
@with_database_session()
def migrate_full_paths_to_filenames()  # Done
```

6. Next Steps:
   - Add tests for each function
   - Add proper error handling
   - Add type hints for async versions
   - Update docstrings for async
   - Add transaction management

### 5. Transaction Patterns

1. Direct Transaction Management:
```python
# Explicit transaction control
session.begin()
session.commit()
session.rollback()
session.flush()
```

2. Context Manager Usage:
```python
# Context manager patterns
with session.begin():
    # code
async with session.begin():
    # code
```

3. Nested Transactions:
```python
# Nested transaction patterns
with session.begin_nested():
    # code
```

4. Common Patterns by Module:
- `account.py`: Heavy use of flush() for relationship management
- `media.py`: Mix of explicit commits and context managers
- `messages.py`: Frequent flush() calls for relationship updates
- `wall.py`: Explicit commit/flush pattern
- `database.py`: Core transaction management, sync notifications

5. Issues to Address:
- Mixed sync/async transaction handling
- Inconsistent use of context managers
- Manual commit/rollback management
- Nested transaction support in async mode
- Session lifecycle management
- Transaction boundary definition

6. Event Handlers to Update:
```python
# Engine Events (database.py)
@event.listens_for(Engine, "engine_disposed")
@event.listens_for(Engine, "close")
@event.listens_for(self.sync_engine, "connect")
@event.listens_for(self.sync_engine, "begin")
@event.listens_for(self.sync_engine, "checkin")
@event.listens_for(self.sync_engine, "engine_connect")

# Model Events (account.py)
@event.listens_for(Account, "after_delete")
```

Key Considerations:
- Event handlers need async versions
- Engine events need both sync and async support
- Model events need to work with both session types
- Event propagation in async context
- Error handling in event callbacks

7. Context Managers to Update:
```python
# In database.py
@contextmanager
def _safe_session_factory(self) -> Generator[Session]:
    """Internal context manager for sync sessions."""

@asynccontextmanager
async def _safe_session_factory_async(self) -> AsyncGenerator[AsyncSession]:
    """Internal context manager for async sessions."""

@contextmanager
def get_sync_session(self) -> Generator[Session]:
    """Public context manager for sync sessions."""

@asynccontextmanager
async def get_async_session(self) -> AsyncGenerator[AsyncSession]:
    """Public context manager for async sessions."""
```

Key Considerations:
- Consistent error handling between sync/async
- Resource cleanup in both paths
- Transaction management in context managers
- Session lifecycle in context managers
- Error propagation patterns

### 5. Conversion Priority

1. Core Database Layer:
   - `metadata/database.py` - Session & transaction management
   - Database decorators and utilities

2. Utility Layer:
   - `metadata/media_utils.py` - Basic media operations
   - Other utility functions

3. Model Operations:
   - `metadata/media.py` - Media processing
   - Model-specific operations

4. Business Logic:
   - `metadata/account.py` - Account processing
   - Other business logic modules

5. Entry Points:
   - `download/account.py` - Already mostly async
   - `fansly_downloader_ng.py` - Main application logic

6. Tests:
   - Update test infrastructure
   - Convert test cases to async
   - Add async markers

## Phase 2: Session & Transaction Updates
1. Update session types:
```python
# Change from
from sqlalchemy.orm import Session
# To
from sqlalchemy.ext.asyncio import AsyncSession
```

2. Update transaction patterns:
```python
# Change from
with session.begin():
    # code
# To
async with session.begin():
    # code
```

3. Update session factories:
```python
# In Database class
async_session_factory = async_sessionmaker(
    bind=async_engine,
    expire_on_commit=False
)
```

## Phase 3: Function Conversion (Bottom-Up)
1. Utility Functions (Lowest Level):
```python
# metadata/media_utils.py
async def link_media_to_bundle(...)
async def validate_media_id(...)
```

2. Media Processing:
```python
# metadata/media.py
async def _process_media_variants(...)
async def _process_media_item_dict_inner(...)
```

3. Account Processing:
```python
# metadata/account.py
async def process_avatar(...)
async def process_banner(...)
async def process_media_bundles(...)
```

## Phase 4: Test Updates
1. Test Infrastructure:
```python
# Update fixtures
@pytest.fixture
async def async_session(...)

# Update test database setup
@pytest.fixture
async def test_database(...)
```

2. Test Cases:
```python
# Convert test cases
@pytest.mark.asyncio
async def test_process_account_data(...)
```

## Phase 5: Integration Points
1. Update Callers:
```python
# Change from
result = process_account_data(...)
# To
result = await process_account_data(...)
```

2. Update Context Managers:
```python
# Change from
with get_session() as session:
# To
async with get_session() as session:
```

## Phase 6: Error Handling & Cleanup
1. Update Error Handling:
```python
try:
    async with session.begin():
        await process_data()
except SQLAlchemyError:
    # Handle database errors
except AsyncIOError:
    # Handle async-specific errors
```

2. Update Resource Cleanup:
```python
async def cleanup():
    await engine.dispose()
```

## Implementation Strategy:
1. Replace Core Components:
   - Start with database layer
   - Replace one component at a time
   - Add tests before replacing
   - Verify functionality

2. Convert Modules Bottom-Up:
   - Start with utility functions
   - Move up to media processing
   - Finally update entry points

3. For Each Module:
   - Add async imports
   - Convert functions to async
   - Update session handling
   - Update transaction management
   - Update tests immediately
   - Update all callers

4. Testing:
   - Write tests before changes
   - Convert test files with modules
   - Add async markers
   - Update fixtures
   - Run tests frequently

5. Documentation:
   - Update docstrings for async functions
   - Document new async patterns
   - Update README

## Progress Tracking
- [x] Phase 1: Dependency Analysis
  - [x] Map all modules
  - [x] Identify entry points
  - [x] Document dependencies

- [ ] Phase 2: Session & Transaction Updates
  - [ ] Update imports
  - [ ] Update session factories

- [ ] Phase 3: Function Conversion
  - [ ] media_utils.py
  - [ ] media.py
  - [ ] account.py
  - [ ] [other modules]

- [ ] Phase 4: Test Updates
  - [ ] Update test infrastructure
  - [ ] Convert test cases
  - [ ] Add async markers

- [ ] Phase 5: Integration
  - [ ] Update callers
  - [ ] Update context managers
  - [ ] Verify all paths

- [ ] Phase 6: Cleanup
  - [ ] Update error handling
  - [ ] Update resource cleanup
  - [ ] Final verification
