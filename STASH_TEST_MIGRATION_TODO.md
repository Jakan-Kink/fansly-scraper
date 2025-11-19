# Stash Test Refactor Guide

**Goal**: Migrate all Stash tests to mock only at external boundaries (HTTP/GraphQL), not internal methods.

**Created**: 2025-11-18
**Enforcement**: `tests/conftest.py:50-100`

---

## Overview

### The Problem

Tests were mocking internal methods, creating "faux integration tests" that:

- ❌ Claimed to use real processors but mocked away actual code execution
- ❌ Never tested real async session handling, GraphQL serialization, or database relationships
- ❌ **HID CRITICAL PRODUCTION BUGS** (see Lessons Learned below)

### The Solution

Mock only at true external boundaries:

- ✅ **Fansly API**: RESPX for HTTP responses
- ✅ **Stash GraphQL**: RESPX for HTTP responses (not the Python dict from `execute()`)
- ✅ Use real database sessions, real Strawberry objects, real factories

---

## Test Categories

All tests organized by directory location. Each needs migration to one of two patterns.

### Category A: StashClient Tests

**Location**: `tests/stash/client/`
**Files**: 12 | **Functions**: ~119
**Migration**: → `stash_client` + `stash_cleanup_tracker` (integration) OR respx (unit)

| File                      | Violations | Status |
| ------------------------- | ---------- | ------ |
| `test_gallery_mixin.py`   | 30         | ✅     |
| `test_tag_mixin_new.py`   | 30         | ✅     |
| `test_scene_mixin.py`     | 21         | ✅     |
| `test_marker_mixin.py`    | 18         | ✅     |
| `test_image_mixin.py`     | 16         | ❌     |
| `test_studio_mixin.py`    | 15         | ✅     |
| `test_tag_mixin.py`       | 14         | ✅     |
| `test_performer_mixin.py` | 7          | ❌     |
| `test_subscription.py`    | 5          | ❌     |
| `test_client_base.py`     | 1          | ❌     |
| `client_test_helpers.py`  | 4          | ❌     |
| `test_client.py`          | -          | ❌     |

### Category B: Processing Unit Tests

**Location**: `tests/stash/processing/unit/`
**Files**: 25 | **Functions**: ~138
**Migration**: → `respx_stash_processor` with HTTP mocking

| File                                   | Violations | Status |
| -------------------------------------- | ---------- | ------ |
| `test_media_variants.py`               | -          | ✅     |
| `test_background_processing.py`        | 14         | ✅     |
| `test_stash_processing.py`             | 23         | ✅     |
| `test_base.py`                         | 20         | ✅     |
| `content/test_message_processing.py`   | 37         | ❌     |
| `content/test_post_processing.py`      | 36         | ❌     |
| `media_mixin/test_metadata_update.py`  | 30         | ✅     |
| `gallery/test_gallery_creation.py`     | 26         | ❌     |
| `content/test_content_collection.py`   | 15         | ❌     |
| `test_gallery_methods.py`              | 13         | ❌     |
| `media_mixin/async_mock_helper.py`     | 13         | ❌     |
| `gallery/test_gallery_lookup.py`       | 11         | ❌     |
| `content/test_batch_processing.py`     | 9          | ❌     |
| `gallery/test_media_detection.py`      | 8          | ❌     |
| `media_mixin/test_file_handling.py`    | 7          | ❌     |
| `test_media_mixin.py`                  | 6          | ❌     |
| `test_account_mixin.py`                | 6          | ❌     |
| `test_studio_mixin.py`                 | 5          | ❌     |
| `test_creator_processing.py`           | 5          | ❌     |
| `test_tag_mixin.py`                    | 4          | ❌     |
| `gallery/test_process_item_gallery.py` | 3          | ❌     |
| `test_gallery_mixin.py`                | 1          | ❌     |

### Category C: Processing Integration Tests

**Location**: `tests/stash/processing/integration/`
**Files**: 10 | **Functions**: ~48
**Migration**: → `real_stash_processor` + `stash_cleanup_tracker`

| File                                     | Violations | Status |
| ---------------------------------------- | ---------- | ------ |
| `test_base_processing.py`                | -          | ✅     |
| `test_metadata_update_integration.py`    | 3          | ✅     |
| `test_media_processing.py`               | 22         | ❌     |
| `test_message_processing.py`             | 18         | ❌     |
| `test_timeline_processing.py`            | 17         | ❌     |
| `test_full_workflow/test_integration.py` | 14         | ❌     |
| `test_media_variants.py`                 | 12         | ❌     |
| `test_content_processing.py`             | 9          | ❌     |
| `test_stash_processing.py`               | 5          | ❌     |

### Category D: Other

**Location**: `tests/stash/integration/`

| File                                   | Violations | Status |
| -------------------------------------- | ---------- | ------ |
| `test_stash_processing_integration.py` | 14         | ❌     |

---

## Migration Patterns

### Unit Tests → `respx_stash_processor`

```python
# BEFORE: Mocked internal method ❌
async def test_example(real_stash_processor):
    real_stash_processor._find_stash_files_by_path = AsyncMock(return_value=[])

# AFTER: Mock at HTTP boundary ✅
async def test_example(respx_stash_processor):
    respx.post("http://localhost:9999/graphql").mock(
        side_effect=[
            httpx.Response(200, json={"data": {"findScenes": {"scenes": [], "count": 0}}}),
            httpx.Response(200, json={"data": {"findPerformers": {"performers": [], "count": 0}}}),
        ]
    )
```

### Integration Tests → `real_stash_processor` + `stash_cleanup_tracker`

```python
# BEFORE: Manual try/finally cleanup ❌
async def test_example(stash_client):
    scene_id = None
    try:
        scene = await stash_client.create_scene(...)
        scene_id = scene.id
    finally:
        if scene_id:
            await stash_client.execute("mutation { sceneDestroy(...) }")

# AFTER: Automatic cleanup ✅
async def test_example(stash_client, stash_cleanup_tracker):
    async with stash_cleanup_tracker(stash_client) as cleanup:
        scene = await stash_client.create_scene(...)
        cleanup["scenes"].append(scene.id)
        # Automatic cleanup on exit
```

---

## Critical Technical Patterns

### 1. Multiple GraphQL Calls Need Multiple Responses

When code makes sequential GraphQL calls, provide a response for EACH:

```python
# ❌ WRONG: Only one response for 5 calls → StopIteration error
respx.post("http://localhost:9999/graphql").mock(
    return_value=httpx.Response(200, json={"data": {"findScenes": {...}}})
)

# ✅ CORRECT: List of responses matching call sequence
respx.post("http://localhost:9999/graphql").mock(
    side_effect=[
        httpx.Response(200, json={"data": {"findScenes": {...}}}),       # Call 1
        httpx.Response(200, json={"data": {"findPerformers": {...}}}),   # Call 2
        httpx.Response(200, json={"data": {"findStudios": {...}}}),      # Call 3
        httpx.Response(200, json={"data": {"findStudios": {...}}}),      # Call 4
        httpx.Response(200, json={"data": {"sceneUpdate": {...}}}),      # Call 5
    ]
)
```

### 2. Permanent GraphQL Call Assertions (REQUIRED)

**Every respx test MUST verify both request AND response for each GraphQL call.**

This is NOT debug code - it's permanent regression protection that:

- Documents expected call sequence in the test itself
- Catches call order changes or unexpected calls
- Reveals caching behavior (e.g., `@async_lru_cache` skipping calls)
- Verifies correct request variables, not just that responses work

```python
import json

# After calling the method under test:
await respx_stash_processor._process_media(media, item, account, result)

# REQUIRED: Assert exact call count
assert len(graphql_route.calls) == 7, "Expected exactly 7 GraphQL calls"

calls = graphql_route.calls

# REQUIRED: Verify EACH call's request and response
# Call 0: findImage (by stash_id)
req0 = json.loads(calls[0].request.content)
assert "findImage" in req0["query"]
assert req0["variables"]["id"] == "stash_456"
resp0 = calls[0].response.json()
assert "findImage" in resp0["data"]

# Call 1: findPerformers (by name)
req1 = json.loads(calls[1].request.content)
assert "findPerformers" in req1["query"]
assert req1["variables"]["performer_filter"]["name"]["value"] == account.username
resp1 = calls[1].response.json()
assert resp1["data"]["findPerformers"]["count"] == 0

# ... verify ALL calls
```

**Example - Verifying cache behavior:**

```python
# Verify @async_lru_cache skips performer lookups after first media object
performer_calls_after_first = [
    i for i in range(8, 12)
    if "findPerformers" in json.loads(calls[i].request.content)["query"]
]
assert len(performer_calls_after_first) == 0, (
    f"Found unexpected findPerformers calls at indices: {performer_calls_after_first}"
)
```

### 3. VideoFile/ImageFile Schema Validation

Stash types have strict required fields:

```python
# ❌ WRONG: Minimal file dict → TypeError
files=[{"path": "/path/to/file.mp4"}]

# ✅ CORRECT: Complete VideoFile schema
files=[{
    "id": "file_123",
    "path": "/path/to/file.mp4",
    "basename": "file.mp4",
    "size": 1024,
    "parent_folder_id": None,
    "format": "mp4",
    "width": 1920,
    "height": 1080,
    "duration": 120.0,
    "video_codec": "h264",
    "audio_codec": "aac",
    "frame_rate": 30.0,
    "bit_rate": 5000000,
}]

# ✅ CORRECT: Complete ImageFile schema
visual_files=[{
    "id": "file_789",
    "path": "/path/to/image.jpg",
    "basename": "image.jpg",
    "size": 512000,
    "parent_folder_id": None,
    "mod_time": "2024-01-01T00:00:00Z",
    "fingerprints": [],
    "width": 1920,
    "height": 1080,
}]
```

### 4. Testing Real Constraint Violations

For integration tests, test REAL constraint violations that trigger actual GraphQL errors:

```python
# ❌ WRONG: Stash silently accepts invalid data
result = await stash_client.set_gallery_cover(gallery_id, image_id="99999")
assert result is False  # FAILS - Stash returns True!

# ✅ CORRECT: Real constraint violation triggers GraphQL error
with capture_graphql_calls(stash_client) as calls:
    with pytest.raises(Exception, match="Image # must greater than zero"):
        await stash_client.gallery_chapter_create(
            gallery_id=empty_gallery_id,
            title="Invalid Chapter",
            image_index=1,  # No images in gallery!
        )
    assert len(calls) == 1
    assert "galleryChapterCreate" in calls[0]["query"]
```

---

## Lessons Learned: Production Bugs Hidden by Mocks

### The MissingGreenlet Bug

**What happened**: Production code had sync/async session mismatches causing `MissingGreenlet` errors.

**Why mocks hid it**:

```python
# Test with mocks - NEVER executed real relationship loading
real_stash_processor._find_stash_files_by_path = AsyncMock(return_value=[scene])
# ↑ This bypass meant we never hit the actual code that loaded relationships
```

**What the migrated test caught immediately**:

```python
# ❌ WRONG: Using sync session in async test
session_sync.add(media)
session_sync.commit()
test_media.variants = {variant}  # BOOM: MissingGreenlet error

# ✅ CORRECT: Use async session with proper awaits
session.add(media)
await session.commit()
await session.refresh(test_media, attribute_names=["variants"])
test_media.variants = {variant}  # Now safe
```

**Impact**: Migration caught this production bug on first test run. This validates the entire migration effort.

### Why This Migration Matters

The original tests were "faux integration tests" that provided false confidence:

- ❌ Claimed to use `real_stash_processor`, but mocked away anything that would have called it
- ❌ Mocked internal methods with `AsyncMock` (bypassed actual code execution)
- ❌ Never tested real async session handling, GraphQL serialization, or database relationships

The migrated tests are true unit tests with complete flow coverage:

- ✅ Use `respx_stash_processor` with HTTP mocking at the edge boundary
- ✅ Execute the ENTIRE real code path through all internal methods
- ✅ Use real database objects, real async sessions, real relationship loading
- ✅ Mock ONLY external HTTP calls to Stash GraphQL API

---

## Valid Exceptions to Mock-Free Testing

When mocking internal methods IS acceptable:

1. **Edge Case Coverage**: Testing error handling paths difficult to trigger via external API

   ```python
   # ✅ ACCEPTABLE: Simulating rare failure condition
   respx_stash_processor._find_stash_files_by_path = AsyncMock(
       side_effect=Exception("Disk failure")
   )
   ```

2. **Deep Call Trees (3-4+ layers)**: When setup would require complex external state

3. **Test Infrastructure** (`tests/stash/types/`): Unit tests for data conversion, change tracking

**Review Process for Exceptions**:

- Requires user confirmation, not automated guesses
- Consider if respx at HTTP boundary could work instead
- Document WHY mocking is needed

---

## Progress Summary

### Completed: 13 files

- `tests/stash/processing/unit/test_media_variants.py`
- `tests/stash/processing/unit/test_background_processing.py`
- `tests/stash/processing/unit/test_stash_processing.py`
- `tests/stash/processing/unit/test_base.py`
- `tests/stash/processing/unit/media_mixin/test_metadata_update.py`
- `tests/stash/processing/integration/test_base_processing.py`
- `tests/stash/processing/integration/test_metadata_update_integration.py`
- `tests/stash/client/test_tag_mixin_new.py`
- `tests/stash/client/test_tag_mixin.py`
- `tests/stash/client/test_scene_mixin.py`
- `tests/stash/client/test_gallery_mixin.py`
- `tests/stash/client/test_marker_mixin.py`
- `tests/stash/client/test_studio_mixin.py`

### Remaining: ~34 files

| Category        | Files  | Est. Effort     |
| --------------- | ------ | --------------- |
| A (Client)      | 6      | 13-22 hours     |
| B (Unit)        | 20     | 30-40 hours     |
| C (Integration) | 7      | 10-20 hours     |
| D (Other)       | 1      | 1-2 hours       |
| **Total**       | **34** | **54-85 hours** |

---

## Reference

**Fixture Definitions**:

- `stash_client`: `tests/fixtures/stash/stash_api_fixtures.py:70`
- `respx_stash_processor`: `tests/fixtures/stash/stash_integration_fixtures.py:225`
- `real_stash_processor`: `tests/fixtures/stash/stash_integration_fixtures.py:190`
- `stash_cleanup_tracker`: `tests/fixtures/stash/cleanup_fixtures.py`

**Enforcement Hook**: `tests/conftest.py:50-100`

**Last Updated**: 2025-11-18
