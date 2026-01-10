# Stash GraphQL Client - Testing Requirements

**Goal**: Ensure comprehensive test coverage with mocking only at external boundaries (HTTP/GraphQL).

**Created**: 2025-12-02
**Project**: stash-graphql-client

---

## Overview

### Testing Philosophy

This GraphQL client library follows strict testing principles adapted from production-tested patterns:

- ✅ **Mock at HTTP boundary only** - Use `respx` to mock GraphQL HTTP responses
- ✅ **Test real code paths** - Execute actual client methods, serialization, deserialization
- ✅ **Verify request AND response** - Every GraphQL call must assert both query/variables AND response data
- ✅ **Use real Pydantic models** - Test actual type validation and model construction
- ❌ **Never mock internal methods** - No mocking of client mixin methods, helper functions, or type conversions

---

## Core Testing Patterns

### Pattern 1: HTTP-Only Mocking with RESPX

All tests mock GraphQL responses at the HTTP boundary using `respx`:

```python
import respx
import httpx
import pytest
from stash_graphql_client import StashClient

@pytest.mark.asyncio
async def test_find_scene(respx_mock):
    """Test finding a scene by ID."""

    # Mock the GraphQL HTTP response
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=httpx.Response(
            200,
            json={
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
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                    }
                }
            }
        )
    )

    # Execute real client code
    client = StashClient(url="http://localhost:9999/graphql")
    scene = await client.find_scene("123")

    # Verify the result is a real Scene object
    assert scene is not None
    assert scene.id == "123"
    assert scene.title == "Test Scene"

    # REQUIRED: Verify GraphQL request
    assert len(graphql_route.calls) == 1
    request = graphql_route.calls[0].request
    body = json.loads(request.content)

    assert "findScene" in body["query"]
    assert body["variables"]["id"] == "123"
```

### Pattern 2: Multiple GraphQL Calls

When client methods make sequential GraphQL calls, provide responses for ALL calls:

```python
@pytest.mark.asyncio
async def test_create_and_update_scene(respx_mock):
    """Test creating then updating a scene."""

    # Provide responses for EACH GraphQL call in sequence
    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            # Call 1: sceneCreate mutation
            httpx.Response(200, json={
                "data": {"sceneCreate": {
                    "id": "new_123",
                    "title": "New Scene",
                    # ... other required fields
                }}
            }),
            # Call 2: sceneUpdate mutation
            httpx.Response(200, json={
                "data": {"sceneUpdate": {
                    "id": "new_123",
                    "title": "Updated Scene",
                    # ... other required fields
                }}
            }),
        ]
    )

    client = StashClient(url="http://localhost:9999/graphql")

    # Execute real code
    scene = await client.create_scene(Scene(title="New Scene"))
    scene.title = "Updated Scene"
    updated = await client.update_scene(scene)

    # Verify both calls were made
    assert len(graphql_route.calls) == 2

    # Verify first call (create)
    req0 = json.loads(graphql_route.calls[0].request.content)
    assert "sceneCreate" in req0["query"]
    assert req0["variables"]["input"]["title"] == "New Scene"

    # Verify second call (update)
    req1 = json.loads(graphql_route.calls[1].request.content)
    assert "sceneUpdate" in req1["query"]
    assert req1["variables"]["input"]["title"] == "Updated Scene"
```

### Pattern 3: Testing Type Validation (Pydantic Models)

Test that Pydantic models correctly validate GraphQL responses:

```python
@pytest.mark.asyncio
async def test_scene_model_validation(respx_mock):
    """Test Scene model validates required fields."""

    # Missing required field (title)
    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={
            "data": {"findScene": {
                "id": "123",
                # Missing title - should cause validation error
                "url": "https://example.com",
                "organized": True,
            }}
        })
    )

    client = StashClient(url="http://localhost:9999/graphql")

    # Should raise ValidationError from Pydantic
    with pytest.raises(ValidationError) as exc_info:
        await client.find_scene("123")

    assert "title" in str(exc_info.value)
```

### Pattern 4: Testing Error Handling

Test real GraphQL errors from the server:

```python
@pytest.mark.asyncio
async def test_graphql_error_handling(respx_mock):
    """Test handling of GraphQL errors."""

    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={
            "errors": [{
                "message": "Scene not found",
                "path": ["findScene"]
            }]
        })
    )

    client = StashClient(url="http://localhost:9999/graphql")

    with pytest.raises(Exception, match="Scene not found"):
        await client.find_scene("nonexistent")
```

---

## Critical Requirements

### Requirement 1: GraphQL Call Assertions (MANDATORY)

**Every test with GraphQL calls MUST verify:**

1. **Call count** - Exact number of GraphQL requests made
2. **Request content** - Query structure AND variables for each call
3. **Response data** - Returned data structure and key fields

```python
# ❌ WRONG: No assertion on GraphQL calls
async def test_bad_example(respx_mock):
    respx.post("http://localhost:9999/graphql").mock(...)
    result = await client.find_scene("123")
    assert result.id == "123"  # Missing call verification!

# ✅ CORRECT: Complete verification
async def test_good_example(respx_mock):
    graphql_route = respx.post("http://localhost:9999/graphql").mock(...)

    result = await client.find_scene("123")

    # REQUIRED: Verify exact call count
    assert len(graphql_route.calls) == 1

    # REQUIRED: Verify request
    request = json.loads(graphql_route.calls[0].request.content)
    assert "findScene" in request["query"]
    assert request["variables"]["id"] == "123"

    # REQUIRED: Verify response was used correctly
    assert result.id == "123"
```

### Requirement 2: Complete Type Schemas

All Pydantic model test data must include ALL required fields:

```python
# ❌ WRONG: Incomplete Scene data
"data": {
    "findScene": {
        "id": "123",
        "title": "Test"
        # Missing required fields!
    }
}

# ✅ CORRECT: Complete Scene schema
"data": {
    "findScene": {
        "id": "123",
        "title": "Test Scene",
        "urls": [],
        "date": None,
        "details": None,
        "rating100": None,
        "organized": False,
        "files": [],
        "tags": [],
        "performers": [],
        "studios": [],
        "galleries": [],
        "groups": [],
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }
}
```

### Requirement 3: No Internal Method Mocking

Never mock internal client methods:

```python
# ❌ WRONG: Mocking internal method
from unittest.mock import patch, AsyncMock

with patch.object(client, '_execute_query', AsyncMock(return_value={})):
    result = await client.find_scene("123")

# ✅ CORRECT: Mock only HTTP
respx.post("http://localhost:9999/graphql").mock(
    return_value=httpx.Response(200, json={"data": {...}})
)
result = await client.find_scene("123")
```

### Requirement 4: Test Real Serialization/Deserialization

Test the full data flow from GraphQL JSON → Pydantic models:

```python
@pytest.mark.asyncio
async def test_complex_type_deserialization(respx_mock):
    """Test Scene with nested relationships deserializes correctly."""

    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={
            "data": {"findScene": {
                "id": "123",
                "title": "Test",
                "urls": [],
                "organized": True,
                "tags": [
                    {"id": "tag1", "name": "Tag 1"},
                    {"id": "tag2", "name": "Tag 2"}
                ],
                "performers": [
                    {
                        "id": "perf1",
                        "name": "Performer 1",
                        "aliases": [],
                        "tags": [],
                        "stash_ids": [],
                        "favorite": False,
                        "ignore_auto_tag": False,
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
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            }}
        })
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

## Test Organization

### Directory Structure

```
tests/
├── conftest.py                    # Shared fixtures
├── fixtures/
│   ├── graphql_responses.py      # Sample GraphQL response data
│   └── models.py                  # Sample Pydantic model instances
├── client/
│   ├── test_base.py              # Base client tests
│   ├── test_scene_mixin.py       # Scene operations
│   ├── test_performer_mixin.py   # Performer operations
│   ├── test_studio_mixin.py      # Studio operations
│   ├── test_tag_mixin.py         # Tag operations
│   ├── test_gallery_mixin.py     # Gallery operations
│   ├── test_image_mixin.py       # Image operations
│   ├── test_marker_mixin.py      # Marker operations
│   ├── test_file_mixin.py        # File operations
│   └── test_subscription.py      # Subscription operations
├── types/
│   ├── test_scene.py             # Scene model tests
│   ├── test_performer.py         # Performer model tests
│   ├── test_filters.py           # Filter type tests
│   └── test_base.py              # Base type tests
└── integration/
    └── test_real_stash.py        # Optional: tests against real Stash
```

### Test Categories

#### Unit Tests (Primary Focus)

Mock all HTTP/GraphQL calls using `respx`:

- Test client methods construct correct GraphQL queries
- Test response deserialization to Pydantic models
- Test error handling
- Test type validation

#### Type Tests

Test Pydantic model behavior:

- Field validation
- Type conversions
- Model methods (to_input, etc.)
- Change tracking

#### Integration Tests

Tests against real Stash instance (Docker container):

- Verify real GraphQL schema compatibility
- Test actual create/update/delete operations
- Validate error responses from real server

---

## Fixtures

### Required Fixtures

```python
# tests/conftest.py

import pytest
from stash_graphql_client import StashClient

@pytest.fixture
def stash_client():
    """Stash client with test URL."""
    return StashClient(url="http://localhost:9999/graphql")

@pytest.fixture
def sample_scene_data():
    """Complete Scene GraphQL response data."""
    return {
        "id": "123",
        "title": "Test Scene",
        "urls": ["https://example.com"],
        "date": "2024-01-01",
        "details": "Test details",
        "rating100": 85,
        "organized": True,
        "files": [],
        "tags": [],
        "performers": [],
        "studios": [],
        "galleries": [],
        "groups": [],
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }

@pytest.fixture
def sample_performer_data():
    """Complete Performer GraphQL response data."""
    return {
        "id": "456",
        "name": "Test Performer",
        "aliases": [],
        "tags": [],
        "stash_ids": [],
        "favorite": False,
        "ignore_auto_tag": False,
        "urls": [],
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
```

---

## Advanced Testing Patterns

### Spy Pattern for Internal Orchestration Tests

For tests that need to verify internal method coordination (e.g., early returns, call counts, orchestration logic) while still executing real code:

```python
# ❌ WRONG: Mocking internal method with return_value/side_effect
with patch.object(client, "_internal_helper", AsyncMock(return_value=False)):
    result = await client.public_method(data)

# ✅ CORRECT: Spy pattern with wraps - real code executes
original_helper = client._internal_helper
call_count = 0

async def spy_helper(item):
    nonlocal call_count
    call_count += 1
    return await original_helper(item)  # Real code executes!

with patch.object(client, "_internal_helper", wraps=spy_helper):
    result = await client.public_method([item1, item2])

    # Verify orchestration
    assert result is False
    assert call_count == 2  # Both items checked
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

While the general rule is "mock only at HTTP boundaries," there are legitimate exceptions:

### Exception 1: Edge Case Coverage

Testing error handling paths that are difficult to trigger via external API:

```python
# ✅ ACCEPTABLE: Simulating rare failure condition
original_method = client._parse_response

async def failing_parse(response):
    raise Exception("Unexpected parse failure")

with patch.object(client, "_parse_response", side_effect=failing_parse):
    with pytest.raises(Exception, match="Unexpected parse failure"):
        await client.find_scene("123")
```

**Justification**: Some error conditions (disk failures, memory errors, parse exceptions) are difficult to simulate via HTTP responses.

### Exception 2: Deep Call Trees (3-4+ layers)

When setup would require extremely complex external state:

```python
# ✅ ACCEPTABLE: Testing deep call tree without complex setup
with patch.object(client, "_validate_nested_relationships"):
    result = await client.create_complex_entity(data)
```

**Justification**: Setting up HTTP responses for 4+ nested GraphQL calls may be impractical.

### Exception 3: Test Infrastructure

Unit tests for pure data conversion, change tracking, or utility functions:

```python
# ✅ ACCEPTABLE: Testing utility function behavior
def test_model_change_tracking():
    scene = Scene(id="123", title="Original")
    scene.title = "Updated"
    assert scene.is_dirty() is True
```

**Justification**: Pure logic tests don't need external boundaries.

---

### Review Process for Exceptions

**Important**: Exceptions require deliberate consideration and documentation and human review/approval.

**Before mocking internal methods, ask:**

1. ☑ Can this be tested with respx at the HTTP boundary?
2. ☑ Does this test actually need to verify orchestration (spy pattern)?
3. ☑ Is the complexity of HTTP mocking truly prohibitive?
4. ☑ Have I documented WHY mocking is necessary?

**Example - Why Human Review Matters:**

When testing client initialization errors, the naive approach would mock `StashClientBase.initialize`:

```python
# ❌ WRONG: Automated guess would mock internal method
with patch.object(StashClientBase, "initialize", side_effect=Exception("Connection refused")):
    with pytest.raises(Exception):
        client = StashClient(url="http://localhost:9999/graphql")
```

But with human review, a better solution emerges - patch the **external** library initialization:

```python
# ✅ CORRECT: Human-guided solution patches external library
from gql import Client as GQLClient

def patched_gql_init(self, *args, **kwargs):
    # Enable schema fetching to trigger HTTP call during init
    kwargs["fetch_schema_from_transport"] = True
    return original_gql_init(self, *args, **kwargs)

with patch.object(GQLClient, "__init__", patched_gql_init):
    respx.post("http://localhost:9999/graphql").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    with pytest.raises(Exception, match="Connection refused"):
        client = StashClient(url="http://localhost:9999/graphql")
```

**Key difference**: The second approach still tests real code execution paths and real error handling, just with a variable change in external library initialization.

**This is why exceptions require user review** - humans can identify solutions that preserve real code execution while making tests practical.

---

## Common Test Patterns

### Testing Create Operations

```python
@pytest.mark.asyncio
async def test_create_scene(respx_mock, stash_client, sample_scene_data):
    """Test creating a new scene."""

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={
            "data": {"sceneCreate": sample_scene_data}
        })
    )

    scene = Scene(title="Test Scene", urls=["https://example.com"])
    result = await stash_client.create_scene(scene)

    # Verify call
    assert len(graphql_route.calls) == 1
    request = json.loads(graphql_route.calls[0].request.content)

    assert "sceneCreate" in request["query"]
    assert request["variables"]["input"]["title"] == "Test Scene"

    # Verify result
    assert result.id == "123"
    assert result.title == "Test Scene"
```

### Testing Update Operations

```python
@pytest.mark.asyncio
async def test_update_scene(respx_mock, stash_client, sample_scene_data):
    """Test updating an existing scene."""

    updated_data = {**sample_scene_data, "title": "Updated Title"}

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={
            "data": {"sceneUpdate": updated_data}
        })
    )

    scene = Scene(**sample_scene_data)
    scene.title = "Updated Title"

    result = await stash_client.update_scene(scene)

    # Verify call
    assert len(graphql_route.calls) == 1
    request = json.loads(graphql_route.calls[0].request.content)

    assert "sceneUpdate" in request["query"]
    assert request["variables"]["input"]["id"] == "123"
    assert request["variables"]["input"]["title"] == "Updated Title"

    # Verify result
    assert result.title == "Updated Title"
```

### Testing Delete Operations

```python
@pytest.mark.asyncio
async def test_destroy_scene(respx_mock, stash_client):
    """Test deleting a scene."""

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={
            "data": {"sceneDestroy": True}
        })
    )

    result = await stash_client.scene_destroy("123")

    # Verify call
    assert len(graphql_route.calls) == 1
    request = json.loads(graphql_route.calls[0].request.content)

    assert "sceneDestroy" in request["query"]
    assert request["variables"]["input"]["id"] == "123"

    # Verify result
    assert result is True
```

### Testing Find Operations with Filters

```python
@pytest.mark.asyncio
async def test_find_scenes_with_filter(respx_mock, stash_client, sample_scene_data):
    """Test finding scenes with filters."""

    graphql_route = respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={
            "data": {
                "findScenes": {
                    "count": 1,
                    "scenes": [sample_scene_data]
                }
            }
        })
    )

    filter_dict = {
        "scene_filter": {
            "title": {"value": "Test", "modifier": "INCLUDES"}
        },
        "filter": {"page": 1, "per_page": 25}
    }

    result = await stash_client.find_scenes(**filter_dict)

    # Verify call
    assert len(graphql_route.calls) == 1
    request = json.loads(graphql_route.calls[0].request.content)

    assert "findScenes" in request["query"]
    assert request["variables"]["scene_filter"]["title"]["value"] == "Test"
    assert request["variables"]["filter"]["page"] == 1

    # Verify result
    assert result.count == 1
    assert len(result.scenes) == 1
    assert result.scenes[0].title == "Test Scene"
```

---

## Anti-Patterns to Avoid

### ❌ Anti-Pattern 1: Incomplete GraphQL Verification

```python
# WRONG: Only verifies result, not the GraphQL call
async def test_bad(respx_mock, stash_client):
    respx.post(...).mock(...)
    result = await stash_client.find_scene("123")
    assert result.id == "123"  # Missing call verification!
```

### ❌ Anti-Pattern 2: Mocking Internal Methods

```python
# WRONG: Mocking internal client method
with patch.object(stash_client, 'find_scene', AsyncMock(...)):
    result = await stash_client.update_scene(scene)
```

### ❌ Anti-Pattern 3: Incomplete Type Data

```python
# WRONG: Missing required fields
"data": {
    "findScene": {
        "id": "123",
        "title": "Test"
        # Missing urls, organized, files, etc.
    }
}
```

### ❌ Anti-Pattern 4: Not Testing Error Cases

```python
# INCOMPLETE: Only tests success case
async def test_find_scene(respx_mock, stash_client):
    respx.post(...).mock(return_value=success_response)
    result = await stash_client.find_scene("123")
    assert result is not None

# ALSO TEST: GraphQL errors, validation errors, not found cases
```

---

### Coverage Verification

```bash
# Run tests with coverage
pytest --cov=stash_graphql_client --cov-report=html

```

---

## Summary

This testing strategy ensures:

1. ✅ Tests execute real code paths (no internal mocking)
2. ✅ GraphQL serialization/deserialization is thoroughly tested
3. ✅ Type validation catches errors early
4. ✅ All GraphQL calls are verified (request + response)
5. ✅ Tests document expected behavior
6. ✅ Future refactors won't break functionality
