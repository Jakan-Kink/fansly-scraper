# Fansly Downloader NG - Testing Requirements

**Goal**: Ensure comprehensive test coverage with mocking only at external boundaries (HTTP/GraphQL for Stash, real database for SQLAlchemy).

**Created**: 2026-01-09
**Project**: fansly-downloader-ng
**Version**: v0.10.4+ (stash-graphql-client)

---

## Overview

### Testing Philosophy

This project follows strict testing principles adapted from production-tested patterns:

- ✅ **Mock at HTTP boundary only (Stash GraphQL)** - Use `respx` to mock GraphQL HTTP responses
- ✅ **Use real database (SQLAlchemy)** - PostgreSQL UUID fixtures provide real database constraints
- ✅ **Test real code paths** - Execute actual methods, serialization, deserialization
- ✅ **Verify request AND response** - Every GraphQL call must assert both query/variables AND response data
- ✅ **Use real Pydantic models** - Test actual type validation and model construction (Stash types)
- ❌ **Never mock internal methods** - No mocking of client methods, helper functions, or SQLAlchemy sessions
- ❌ **Never mock SQLAlchemy** - Use real database sessions with proper fixtures

### Dual Testing Architecture

This project has **two distinct testing domains** that require different strategies:

#### 1. SQLAlchemy Tests (Fansly Metadata)

**Domain**: Fansly API data → Local SQLite database
**Models**: `metadata/` (Account, Media, Post, Message, Story, etc. defined in account.py, media.py, post.py, etc.)
**Strategy**: Real PostgreSQL UUID fixtures - **NO mocking of database or sessions**

```python
# ✅ CORRECT: Real database with fixtures
async def test_media_processing(session, session_sync):
    account = AccountFactory.build(id=12345, username="test_user")
    session_sync.add(account)
    session_sync.commit()

    # Real SQL query execution
    result = await session.execute(select(Account).where(Account.id == 12345))
    found = result.scalar_one()
    assert found.username == "test_user"
```

**Why**: Mocking SQLAlchemy hides bugs in SQL queries, constraints, and relationships. Real database tests catch these issues.

#### 2. Stash GraphQL Tests (Stash Integration)

**Domain**: Local metadata → Stash media server via GraphQL
**Models**: `stash_graphql_client.types` (Performer, Studio, Scene, Tag, Gallery)
**Strategy**: HTTP-only mocking with `respx` - **NO mocking of client methods**

```python
# ✅ CORRECT: Mock only HTTP boundary
@pytest.mark.asyncio
async def test_create_performer(respx_mock, stash_client):
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json={
                "data": {"performerCreate": {
                    "id": "123",
                    "name": "Test Performer",
                    "alias_list": [],
                    "tags": [],
                    # ... all required fields
                }}
            })
        ]
    )

    performer = Performer(name="Test Performer")
    result = await stash_client.create_performer(performer)

    assert result.id == "123"
    assert len(graphql_route.calls) == 1
```

**Why**: StashClient is a thin wrapper around GraphQL - mock the HTTP layer, not the client logic.

---

## SQLAlchemy Testing Patterns (Brief)

### Pattern: Real Database with Fixtures

**Always use real database sessions from fixtures:**

```python
from tests.fixtures.database.database_type_factories import (
    AccountFactory, MediaFactory, PostFactory
)

async def test_account_relationships(session, session_sync):
    """Test Account → Media relationship with real database."""

    # Create account with factory
    account = AccountFactory.build(id=12345, username="test_user")
    session_sync.add(account)
    session_sync.commit()

    # Create related media
    media = MediaFactory.build(id=67890, accountId=12345)
    session_sync.add(media)
    session_sync.commit()

    # Real SQL query with JOIN
    result = await session.execute(
        select(Media).join(Account).where(Account.username == "test_user")
    )
    found_media = result.scalars().all()

    assert len(found_media) == 1
    assert found_media[0].accountId == 12345
```

### Key Principles for SQLAlchemy Tests

1. **Use PostgreSQL UUID fixtures** (`session`, `session_sync` from `conftest.py`)
2. **Never mock SQLAlchemy sessions or models** - use factories instead
3. **Test real SQL queries** - joins, constraints, foreign keys
4. **Test both sync and async sessions** where applicable
5. **Clean up with proper teardown** (fixtures handle this automatically)

### Valid Exceptions for SQLAlchemy

While we don't mock database sessions, these are acceptable:

- ✅ **External API calls** (Fansly API) - use `respx` to mock HTTP responses
- ✅ **File I/O for hashing** - use real temporary files, but mock imagehash library calls if slow
- ✅ **Time-dependent tests** - mock `datetime.now()` for reproducible timestamps

**Reference**: See `## Valid Exceptions to Mock-Free Testing` for detailed guidelines.

---

## Stash GraphQL Testing Patterns

### Pattern 1: HTTP-Only Mocking with RESPX

All Stash tests mock GraphQL responses at the HTTP boundary using `respx`:

```python
import respx
import httpx
import pytest
from stash_graphql_client import StashClient
from stash_graphql_client.types import Scene

@pytest.mark.asyncio
async def test_find_scene(respx_mock):
    """Test finding a scene by ID."""

    # Mock the GraphQL HTTP response
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json={
                "data": {
                    "findScene": {
                        "id": "123",
                        "title": "Test Scene",
                        "url": "https://example.com/scene",
                        "date": "2024-01-01",
                        "details": "Test details",
                        "rating100": 85,
                        "organized": True,
                        "urls": ["https://example.com/scene"],
                        "files": [],
                        "tags": [],
                        "performers": [],
                        "studio": None,
                        "galleries": [],
                        "groups": [],
                        "stash_ids": [],
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                    }
                }
            })
        ]
    )

    # Execute real client code
    client = StashClient(url="http://localhost:9999/graphql")
    scene = await client.find_scene("123")

    # Verify the result is a real Scene Pydantic object
    assert scene is not None
    assert scene.id == "123"
    assert scene.title == "Test Scene"

    # REQUIRED: Verify GraphQL request
    assert len(graphql_route.calls) == 1
    request = json.loads(graphql_route.calls[0].request.content)

    assert "findScene" in request["query"]
    assert request["variables"]["id"] == "123"
```

### Pattern 2: Multiple GraphQL Calls

When methods make sequential GraphQL calls, provide responses for ALL calls:

```python
@pytest.mark.asyncio
async def test_create_and_update_performer(respx_mock):
    """Test creating then updating a performer."""

    # Provide responses for EACH GraphQL call in sequence
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # Call 1: performerCreate mutation
            httpx.Response(200, json={
                "data": {"performerCreate": {
                    "id": "new_123",
                    "name": "New Performer",
                    "alias_list": [],
                    "tags": [],
                    "stash_ids": [],
                    "urls": [],
                    "favorite": False,
                    "ignore_auto_tag": False,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                }}
            }),
            # Call 2: performerUpdate mutation
            httpx.Response(200, json={
                "data": {"performerUpdate": {
                    "id": "new_123",
                    "name": "Updated Performer",
                    "alias_list": [],
                    "tags": [],
                    "stash_ids": [],
                    "urls": [],
                    "favorite": False,
                    "ignore_auto_tag": False,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                }}
            }),
        ]
    )

    client = StashClient(url="http://localhost:9999/graphql")

    # Execute real code
    performer = Performer(name="New Performer")
    created = await client.create_performer(performer)

    created.name = "Updated Performer"
    updated = await client.update_performer(created)

    # Verify both calls were made
    assert len(graphql_route.calls) == 2

    # Verify first call (create)
    req0 = json.loads(graphql_route.calls[0].request.content)
    assert "performerCreate" in req0["query"]
    assert req0["variables"]["input"]["name"] == "New Performer"

    # Verify second call (update)
    req1 = json.loads(graphql_route.calls[1].request.content)
    assert "performerUpdate" in req1["query"]
    assert req1["variables"]["input"]["name"] == "Updated Performer"
```

### Pattern 3: Testing Type Validation (Pydantic Models)

Test that Pydantic models correctly validate GraphQL responses:

```python
from pydantic import ValidationError

@pytest.mark.asyncio
async def test_performer_model_validation(respx_mock):
    """Test Performer model validates required fields."""

    # Missing required field (name)
    respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json={
                "data": {"findPerformer": {
                    "id": "123",
                    # Missing name - should cause validation error
                    "alias_list": [],
                    "tags": [],
                }}
            })
        ]
    )

    client = StashClient(url="http://localhost:9999/graphql")

    # Should raise ValidationError from Pydantic
    with pytest.raises(ValidationError) as exc_info:
        await client.find_performer("123")

    assert "name" in str(exc_info.value)
```

### Pattern 4: Testing Error Handling

Test real GraphQL errors from the server:

```python
@pytest.mark.asyncio
async def test_graphql_error_handling(respx_mock):
    """Test handling of GraphQL errors."""

    respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json={
                "errors": [{
                    "message": "Performer not found",
                    "path": ["findPerformer"]
                }]
            })
        ]
    )

    client = StashClient(url="http://localhost:9999/graphql")

    with pytest.raises(Exception, match="Performer not found"):
        await client.find_performer("nonexistent")
```

---

## Critical Requirements

### Requirement 1: GraphQL Call Assertions (MANDATORY)

**Every Stash GraphQL test MUST verify:**

1. **Call count** - Exact number of GraphQL requests made
2. **Request content** - Query structure AND variables for each call
3. **Response data** - Returned data structure and key fields

```python
# ❌ WRONG: No assertion on GraphQL calls
async def test_bad_example(respx_mock):
    respx.post("http://localhost:9999/graphql").mock(side_effect=[...])
    result = await client.find_performer("123")
    assert result.id == "123"  # Missing call verification!

# ✅ CORRECT: Complete verification
async def test_good_example(respx_mock):
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[...]
    )

    result = await client.find_performer("123")

    # REQUIRED: Verify exact call count
    assert len(graphql_route.calls) == 1

    # REQUIRED: Verify request
    request = json.loads(graphql_route.calls[0].request.content)
    assert "findPerformer" in request["query"]
    assert request["variables"]["id"] == "123"

    # REQUIRED: Verify response was used correctly
    assert result.id == "123"
```

### Requirement 2: Complete Type Schemas

All Pydantic model test data must include ALL required fields:

```python
# ❌ WRONG: Incomplete Performer data
"data": {
    "findPerformer": {
        "id": "123",
        "name": "Test"
        # Missing required fields!
    }
}

# ✅ CORRECT: Complete Performer schema
"data": {
    "findPerformer": {
        "id": "123",
        "name": "Test Performer",
        "alias_list": [],
        "tags": [],
        "stash_ids": [],
        "urls": [],
        "favorite": False,
        "ignore_auto_tag": False,
        "gender": None,
        "birthdate": None,
        "ethnicity": None,
        "country": None,
        "eye_color": None,
        "height_cm": None,
        "measurements": None,
        "fake_tits": None,
        "penis_length": None,
        "circumcised": None,
        "career_length": None,
        "tattoos": None,
        "piercings": None,
        "image_path": None,
        "details": None,
        "death_date": None,
        "hair_color": None,
        "weight": None,
        "rating100": None,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }
}
```

### Requirement 3: No Internal Method Mocking

Never mock internal client methods or SQLAlchemy sessions:

```python
# ❌ WRONG: Mocking internal StashClient method
from unittest.mock import patch, AsyncMock

with patch.object(client, '_execute_query', AsyncMock(return_value={})):
    result = await client.find_performer("123")

# ❌ WRONG: Mocking SQLAlchemy session
mock_session = AsyncMock(spec=AsyncSession)
with patch('metadata.database.get_session', return_value=mock_session):
    result = await process_account(account_data)

# ✅ CORRECT: Mock only HTTP for Stash (use side_effect always)
respx.post("http://localhost:9999/graphql").mock(
    side_effect=[httpx.Response(200, json={"data": {...}})]
)
result = await client.find_performer("123")

# ✅ CORRECT: Use real database session for SQLAlchemy
async def test_function(session, session_sync):
    account = AccountFactory.build(id=12345)
    session_sync.add(account)
    session_sync.commit()

    result = await process_account(session, account_data)
```

### Requirement 4: Test Real Serialization/Deserialization

Test the full data flow from GraphQL JSON → Pydantic models:

```python
@pytest.mark.asyncio
async def test_complex_type_deserialization(respx_mock):
    """Test Scene with nested relationships deserializes correctly."""

    respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json={
                "data": {"findScene": {
                    "id": "123",
                    "title": "Test Scene",
                    "urls": ["https://example.com"],
                    "organized": True,
                    "tags": [
                        {"id": "tag1", "name": "Tag 1"},
                        {"id": "tag2", "name": "Tag 2"}
                    ],
                    "performers": [
                        {
                            "id": "perf1",
                            "name": "Performer 1",
                            "alias_list": [],
                            "tags": [],
                            "stash_ids": [],
                            "urls": [],
                            "favorite": False,
                            "ignore_auto_tag": False,
                            "created_at": "2024-01-01T00:00:00Z",
                            "updated_at": "2024-01-01T00:00:00Z",
                        }
                    ],
                    "files": [
                        {
                            "id": "file1",
                            "path": "/path/to/file.mp4",
                            "basename": "file.mp4",
                            "size": 1024000,
                            "parent_folder_id": None,
                            "format": "mp4",
                            "width": 1920,
                            "height": 1080,
                            "duration": 120.0,
                            "video_codec": "h264",
                            "audio_codec": "aac",
                            "frame_rate": 30.0,
                            "bit_rate": 5000000,
                            "mod_time": "2024-01-01T00:00:00Z",
                            "fingerprints": [],
                        }
                    ],
                    "studio": None,
                    "galleries": [],
                    "groups": [],
                    "stash_ids": [],
                    "date": None,
                    "details": None,
                    "rating100": None,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                }}
            })
        ]
    )

    client = StashClient(url="http://localhost:9999/graphql")
    scene = await client.find_scene("123")

    # Verify nested objects deserialized correctly
    assert len(scene.tags) == 2
    assert scene.tags[0].name == "Tag 1"

    assert len(scene.performers) == 1
    assert scene.performers[0].name == "Performer 1"

    assert len(scene.files) == 1
    assert scene.files[0].format == "mp4"
    assert scene.files[0].duration == 120.0
```

---

## Factory Organization

This project uses **two separate factory modules** for the dual testing architecture:

### SQLAlchemy Factories

**Location**: `tests/fixtures/database/database_type_factories.py`

**Models**: Fansly metadata (Account, Media, Post, Message, Story, etc.)

```python
from tests.fixtures.database.database_type_factories import (
    AccountFactory,
    MediaFactory,
    PostFactory,
    MessageFactory,
)

# Create SQLAlchemy models with factories
account = AccountFactory.build(id=12345, username="test_user")
media = MediaFactory.build(id=67890, accountId=12345)
```

### Stash Pydantic Factories

**Location**: `tests/fixtures/stash/stash_type_factories.py`

**Models**: stash-graphql-client Pydantic types (Performer, Studio, Scene, Tag, Gallery)

**IMPORTANT**: v0.10.4+ behavior (see docs/V0_10_MOCK_PATTERNS.md):
- **UUID auto-generation**: Don't manually assign IDs - Pydantic generates temp UUIDs automatically
- **Automatic UNSET**: Omitted fields = UNSET (don't explicitly set `field = UNSET`)

```python
from tests.fixtures.stash.stash_type_factories import (
    PerformerFactory,
    StudioFactory,
    SceneFactory,
    TagFactory,
)

# Create Pydantic models with factories (temp UUID auto-generated)
performer = PerformerFactory(name="Test Performer")
print(performer.id)  # e.g., "f47ac10b-58cc-..." (temp UUID)

# Simulate server-returned object (override temp UUID with real ID)
existing_performer = PerformerFactory(id="123", name="Test Performer")
print(existing_performer.id)  # "123" (real server ID)
```

### Factory Usage Guidelines

1. **SQLAlchemy factories**: Use with `session_sync.add()` and `session_sync.commit()`
2. **Pydantic factories**: Use to create test objects, but HTTP responses should be raw dicts
3. **Don't mix factory types**: SQLAlchemy factories ≠ Pydantic factories
4. **Reference v0.10.4 patterns**: See `docs/V0_10_MOCK_PATTERNS.md` for factory patterns

---

## Cleanup Enforcement (Stash Tests)

### stash_cleanup_tracker Fixture

All Stash integration tests **MUST** use the `stash_cleanup_tracker` fixture to prevent resource leaks:

```python
@pytest.mark.asyncio
async def test_create_performer(stash_cleanup_tracker, respx_mock):
    """Test creating a performer with cleanup tracking."""

    # Mock the create response
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json={
                "data": {"performerCreate": {
                    "id": "123",
                    "name": "Test Performer",
                    # ... complete schema
                }}
            })
        ]
    )

    client = StashClient(url="http://localhost:9999/graphql")
    performer = await client.create_performer(Performer(name="Test Performer"))

    # Register for cleanup (if test creates real resources)
    stash_cleanup_tracker.register("performer", performer.id)

    assert performer.id == "123"
```

### Why Cleanup Tracking Matters

- ✅ Prevents resource leaks in integration tests
- ✅ Ensures test isolation (no state bleed between tests)
- ✅ Automatic cleanup even if test fails or errors
- ✅ Documents what resources were created during test

### When to Use stash_cleanup_tracker

- **Required**: All tests that create/update Stash entities (real HTTP calls)
- **Optional**: Unit tests with `respx` mocks (no real resources created)

---

## Test Organization

### Directory Structure

```
tests/
├── conftest.py                               # Global fixtures (sessions, clients)
├── fixtures/
│   ├── database/
│   │   └── database_type_factories.py        # SQLAlchemy model factories
│   └── stash/
│       └── stash_type_factories.py           # Pydantic model factories (v0.10.4+)
├── config/
│   └── unit/
│       └── test_fanslyconfig.py              # Config validation tests
├── metadata/
│   ├── unit/
│   │   ├── test_account.py                   # Account processing (SQLAlchemy)
│   │   ├── test_media.py                     # Media processing (SQLAlchemy)
│   │   └── test_post.py                      # Post processing (SQLAlchemy)
│   └── integration/
│       └── test_database_operations.py       # Full database workflows
├── stash/
│   ├── processing/
│   │   └── unit/
│   │       ├── test_account_mixin.py         # Account → Performer (Stash GraphQL)
│   │       ├── test_studio_mixin.py          # Studio creation (Stash GraphQL)
│   │       ├── test_tag_mixin.py             # Tag processing (Stash GraphQL)
│   │       ├── test_media_mixin.py           # Media → Scene/Image (Stash GraphQL)
│   │       └── test_gallery_methods.py       # Gallery creation (Stash GraphQL)
│   └── client/
│       └── unit/
│           └── test_stash_client.py          # StashClient HTTP mocking
└── download/
    └── unit/
        └── test_download_core.py             # Download orchestration
```

### Test Categories

#### Unit Tests (Primary Focus)

**SQLAlchemy (metadata/):**
- Use real PostgreSQL UUID database fixtures
- Test database models, relationships, constraints
- Test processing functions with real sessions

**Stash GraphQL (stash/):**
- Mock all HTTP/GraphQL calls using `respx`
- Test client methods construct correct GraphQL queries
- Test response deserialization to Pydantic models
- Test error handling and type validation

#### Integration Tests

**Database Integration:**
- Test full workflows across multiple models
- Test transaction handling and rollbacks
- Test database migrations with Alembic

**Stash Integration:**
- Tests against real Stash instance (Docker container)
- Verify real GraphQL schema compatibility
- Test actual create/update/delete operations
- Validate error responses from real server

---

## Fixtures

### Required Fixtures (SQLAlchemy)

```python
# tests/conftest.py

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, Session
from metadata.base import Base

@pytest.fixture
async def session(db_url):
    """Async database session for SQLAlchemy tests."""
    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        yield session
        await session.rollback()

    await engine.dispose()

@pytest.fixture
def session_sync(db_url_sync):
    """Sync database session for SQLAlchemy tests."""
    from sqlalchemy import create_engine

    engine = create_engine(db_url_sync, echo=False)
    Base.metadata.create_all(engine)

    session = Session(engine)
    yield session
    session.rollback()
    session.close()

    Base.metadata.drop_all(engine)
    engine.dispose()
```

### Required Fixtures (Stash GraphQL)

```python
# tests/conftest.py

import pytest
from stash.client import StashClient

@pytest.fixture
def stash_client():
    """Stash client with test URL."""
    return StashClient(url="http://localhost:9999/graphql")

@pytest.fixture
def sample_performer_data():
    """Complete Performer GraphQL response data."""
    return {
        "id": "123",
        "name": "Test Performer",
        "alias_list": [],
        "tags": [],
        "stash_ids": [],
        "urls": [],
        "favorite": False,
        "ignore_auto_tag": False,
        "gender": None,
        "birthdate": None,
        "ethnicity": None,
        "country": None,
        "eye_color": None,
        "height_cm": None,
        "measurements": None,
        "fake_tits": None,
        "penis_length": None,
        "circumcised": None,
        "career_length": None,
        "tattoos": None,
        "piercings": None,
        "image_path": None,
        "details": None,
        "death_date": None,
        "hair_color": None,
        "weight": None,
        "rating100": None,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }

@pytest.fixture
def sample_studio_data():
    """Complete Studio GraphQL response data."""
    return {
        "id": "456",
        "name": "Test Studio",
        "url": "https://example.com/studio",
        "parent_studio": None,
        "child_studios": [],
        "stash_ids": [],
        "rating100": None,
        "details": None,
        "aliases": [],
        "tags": [],
        "image_path": None,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }
```

---

## Advanced Testing Patterns

### Spy Pattern for Internal Orchestration Tests

For tests that need to verify internal method coordination (e.g., early returns, call counts) while still executing real code:

```python
# ❌ WRONG: Mocking internal method with return_value/side_effect
with patch.object(processor, "_process_single_media", AsyncMock(return_value=False)):
    result = await processor.process_media_batch(media_list)

# ✅ CORRECT: Spy pattern with wraps - real code executes
original_process = processor._process_single_media
call_count = 0

async def spy_process(media):
    nonlocal call_count
    call_count += 1
    return await original_process(media)  # Real code executes!

with patch.object(processor, "_process_single_media", wraps=spy_process):
    result = await processor.process_media_batch([media1, media2])

    # Verify orchestration
    assert result is True
    assert call_count == 2  # Both media items processed
```

**Why this matters:**

- ✅ Real code path executes (not mocked behavior)
- ✅ Tests actual orchestration logic (early returns, loops, error handling)
- ✅ Catches regressions in internal coordination
- ✅ Similar concept to GraphQL call verification but for internal methods

**When to use:**

- Testing method call sequences (e.g., "method A calls B twice, then C once")
- Verifying early return conditions
- Confirming loop iteration counts
- Validating error propagation through method chains

---

## Valid Exceptions to Mock-Free Testing

While the general rule is "mock only at HTTP/external boundaries," there are legitimate exceptions:

### Exception 1: Edge Case Coverage

Testing error handling paths that are difficult to trigger via external API:

```python
# ✅ ACCEPTABLE: Simulating rare failure condition
original_method = client._parse_graphql_response

async def failing_parse(response):
    raise Exception("Unexpected parse failure")

with patch.object(client, "_parse_graphql_response", side_effect=failing_parse):
    with pytest.raises(Exception, match="Unexpected parse failure"):
        await client.find_performer("123")
```

**Justification**: Some error conditions (disk failures, memory errors, parse exceptions) are difficult to simulate via HTTP responses.

### Exception 2: Deep Call Trees (3-4+ layers)

When setup would require extremely complex external state:

```python
# ✅ ACCEPTABLE: Testing deep call tree without complex setup
with patch.object(processor, "_validate_nested_relationships"):
    result = await processor.process_complex_account(account_data)
```

**Justification**: Setting up HTTP responses for 4+ nested GraphQL calls may be impractical.

### Exception 3: Test Infrastructure

Unit tests for pure data conversion, change tracking, or utility functions:

```python
# ✅ ACCEPTABLE: Testing utility function behavior
def test_timestamp_conversion():
    from metadata.base import Base
    result = Base.convert_timestamps({"createdAt": 1704067200000}, ("createdAt",))
    assert result["createdAt"] == datetime(2024, 1, 1, 0, 0, 0)
```

**Justification**: Pure logic tests don't need external boundaries.

### Exception 4: Slow External Library Calls

Mocking computationally expensive operations:

```python
# ✅ ACCEPTABLE: Mocking imagehash for performance
with patch('imagehash.phash', return_value=imagehash.hex_to_hash('0' * 16)):
    hash_result = get_image_hash(image_path)
```

**Justification**: Tests should run quickly; mocking slow image hashing is pragmatic.

---

### Review Process for Exceptions

**Important**: Exceptions require deliberate consideration, documentation, and human review/approval.

**Before mocking internal methods, ask:**

1. ☑ Can this be tested with respx at the HTTP boundary (Stash) or real DB (SQLAlchemy)?
2. ☑ Does this test actually need to verify orchestration (spy pattern)?
3. ☑ Is the complexity of boundary mocking truly prohibitive?
4. ☑ Have I documented WHY mocking is necessary?

**Example - Why Human Review Matters:**

When testing database connection errors, the naive approach would mock `AsyncSession.execute`:

```python
# ❌ WRONG: Automated guess would mock SQLAlchemy internals
with patch.object(AsyncSession, "execute", side_effect=Exception("Connection refused")):
    with pytest.raises(Exception):
        await process_account(session, account_data)
```

But with human review, a better solution emerges - use real database with connection failures:

```python
# ✅ CORRECT: Human-guided solution uses real database fixtures
async def test_db_connection_error(db_url):
    # Use invalid connection string to trigger real connection error
    engine = create_async_engine("postgresql+asyncpg://invalid:invalid@localhost/invalid")

    with pytest.raises(Exception, match="connection"):
        async with AsyncSession(engine) as session:
            await session.execute(select(Account))
```

**Key difference**: The second approach still tests real code execution paths and real error handling, just with invalid connection parameters.

**This is why exceptions require user review** - humans can identify solutions that preserve real code execution while making tests practical.

---

## Common Test Patterns

### Testing Create Operations (Stash GraphQL)

```python
@pytest.mark.asyncio
async def test_create_performer(respx_mock, stash_client, sample_performer_data):
    """Test creating a new performer."""

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json={
                "data": {"performerCreate": sample_performer_data}
            })
        ]
    )

    performer = Performer(name="Test Performer")
    result = await stash_client.create_performer(performer)

    # Verify call
    assert len(graphql_route.calls) == 1
    request = json.loads(graphql_route.calls[0].request.content)

    assert "performerCreate" in request["query"]
    assert request["variables"]["input"]["name"] == "Test Performer"

    # Verify result
    assert result.id == "123"
    assert result.name == "Test Performer"
```

### Testing Update Operations (Stash GraphQL)

```python
@pytest.mark.asyncio
async def test_update_performer(respx_mock, stash_client, sample_performer_data):
    """Test updating an existing performer."""

    updated_data = {**sample_performer_data, "name": "Updated Name"}

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json={
                "data": {"performerUpdate": updated_data}
            })
        ]
    )

    performer = Performer(**sample_performer_data)
    performer.name = "Updated Name"

    result = await stash_client.update_performer(performer)

    # Verify call
    assert len(graphql_route.calls) == 1
    request = json.loads(graphql_route.calls[0].request.content)

    assert "performerUpdate" in request["query"]
    assert request["variables"]["input"]["id"] == "123"
    assert request["variables"]["input"]["name"] == "Updated Name"

    # Verify result
    assert result.name == "Updated Name"
```

### Testing Database Operations (SQLAlchemy)

```python
async def test_account_creation(session, session_sync):
    """Test creating and querying an account."""

    # Create account with factory
    account = AccountFactory.build(id=12345, username="test_user")
    session_sync.add(account)
    session_sync.commit()

    # Query with async session
    result = await session.execute(
        select(Account).where(Account.username == "test_user")
    )
    found = result.scalar_one()

    # Verify account
    assert found.id == 12345
    assert found.username == "test_user"
```

### Testing Relationships (SQLAlchemy)

```python
async def test_account_media_relationship(session, session_sync):
    """Test Account → Media relationship."""

    # Create account
    account = AccountFactory.build(id=12345, username="test_user")
    session_sync.add(account)
    session_sync.commit()

    # Create media for account
    media1 = MediaFactory.build(id=67890, accountId=12345)
    media2 = MediaFactory.build(id=67891, accountId=12345)
    session_sync.add_all([media1, media2])
    session_sync.commit()

    # Query with JOIN
    result = await session.execute(
        select(Media).join(Account).where(Account.username == "test_user")
    )
    found_media = result.scalars().all()

    # Verify relationship
    assert len(found_media) == 2
    assert all(m.accountId == 12345 for m in found_media)
```

### Testing Find Operations with Filters (Stash GraphQL)

```python
@pytest.mark.asyncio
async def test_find_performers_with_filter(respx_mock, stash_client, sample_performer_data):
    """Test finding performers with filters."""

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json={
                "data": {
                    "findPerformers": {
                        "count": 1,
                        "performers": [sample_performer_data]
                    }
                }
            })
        ]
    )

    filter_dict = {
        "performer_filter": {
            "name": {"value": "Test", "modifier": "INCLUDES"}
        },
        "filter": {"page": 1, "per_page": 25}
    }

    result = await stash_client.find_performers(**filter_dict)

    # Verify call
    assert len(graphql_route.calls) == 1
    request = json.loads(graphql_route.calls[0].request.content)

    assert "findPerformers" in request["query"]
    assert request["variables"]["performer_filter"]["name"]["value"] == "Test"
    assert request["variables"]["filter"]["page"] == 1

    # Verify result
    assert result.count == 1
    assert len(result.performers) == 1
    assert result.performers[0].name == "Test Performer"
```

---

## Anti-Patterns to Avoid

### ❌ Anti-Pattern 1: Incomplete GraphQL Verification

```python
# WRONG: Only verifies result, not the GraphQL call
async def test_bad(respx_mock, stash_client):
    respx.post(...).mock(side_effect=[...])
    result = await stash_client.find_performer("123")
    assert result.id == "123"  # Missing call verification!
```

### ❌ Anti-Pattern 2: Mocking Internal Methods

```python
# WRONG: Mocking internal client method
with patch.object(stash_client, 'find_performer', AsyncMock(...)):
    result = await stash_client.update_performer(performer)
```

### ❌ Anti-Pattern 3: Mocking SQLAlchemy Sessions

```python
# WRONG: Mocking database session
mock_session = AsyncMock(spec=AsyncSession)
with patch('metadata.database.get_session', return_value=mock_session):
    result = await process_account(account_data)

# CORRECT: Use real session fixture
async def test_function(session, session_sync):
    account = AccountFactory.build(...)
    session_sync.add(account)
    session_sync.commit()
    result = await process_account(session, account_data)
```

### ❌ Anti-Pattern 4: Incomplete Type Data

```python
# WRONG: Missing required fields
"data": {
    "findPerformer": {
        "id": "123",
        "name": "Test"
        # Missing alias_list, tags, stash_ids, etc.
    }
}
```

### ❌ Anti-Pattern 5: Using return_value Instead of side_effect

```python
# WRONG: Using return_value for respx mocks
respx.post("http://localhost:9999/graphql").mock(
    return_value=httpx.Response(200, json={"data": {...}})
)

# CORRECT: Always use side_effect with list
respx.post("http://localhost:9999/graphql").mock(
    side_effect=[
        httpx.Response(200, json={"data": {...}})
    ]
)
```

### ❌ Anti-Pattern 6: Not Testing Error Cases

```python
# INCOMPLETE: Only tests success case
async def test_find_performer(respx_mock, stash_client):
    respx.post(...).mock(side_effect=[success_response])
    result = await stash_client.find_performer("123")
    assert result is not None

# ALSO TEST: GraphQL errors, validation errors, not found cases
```

---

## Coverage Verification

```bash
# Run all tests with coverage
pytest --cov=. --cov-report=html

# Run specific test category
pytest tests/metadata/unit/  # SQLAlchemy tests
pytest tests/stash/processing/unit/  # Stash GraphQL tests

# Run with markers
pytest -m unit  # Unit tests only
pytest -m integration  # Integration tests only
pytest -m "not slow"  # Skip slow tests

# Run in parallel for speed
pytest -n auto
```

---

## Summary

This testing strategy ensures:

1. ✅ Tests execute real code paths (no internal mocking)
2. ✅ SQLAlchemy tests use real database with proper constraints
3. ✅ Stash GraphQL serialization/deserialization is thoroughly tested
4. ✅ Type validation catches errors early (Pydantic models)
5. ✅ All GraphQL calls are verified (request + response)
6. ✅ Tests document expected behavior
7. ✅ Future refactors won't break functionality
8. ✅ Dual testing architecture respects different domains (metadata vs Stash)

### Key Takeaways

- **SQLAlchemy domain**: Real database, no session mocking, PostgreSQL UUID fixtures
- **Stash GraphQL domain**: HTTP-only mocking with respx (ALWAYS use `side_effect=[]`), no client method mocking
- **Factory separation**: database_type_factories.py vs stash_type_factories.py
- **Cleanup enforcement**: stash_cleanup_tracker for Stash integration tests
- **v0.10.4 patterns**: UUID auto-generation, automatic UNSET (see docs/V0_10_MOCK_PATTERNS.md)
- **Critical pattern**: NEVER use `return_value` for respx mocks - ALWAYS use `side_effect=[]` even for single responses
