# Stash Test Migration TODO

**Goal**: Migrate all Stash tests to use proper testing patterns (Pattern 2: `respx_stash_processor` for unit tests OR `real_stash_processor` + `stash_cleanup_tracker` for integration tests).

**Created**: 2025-11-15
**Status**: 🔴 NOT STARTED
**Enforcement**: ✅ ENABLED (see `tests/conftest.py:50-100`)

---

## 📋 Summary Statistics - ACTUAL SCOPE

⚠️ **CRITICAL DISCOVERY**: The mock violation is MUCH larger than initially reported!

### Real Numbers:

- **Total Test Files with Mocks**: 40+ files (out of 47 total stash test files)
- **Total Test Functions**: 305 functions
- **Total Mock Violations**: 564 occurrences (excluding tests/stash/types/)
  - StashClient method mocking: 8 occurrences in 5 files
  - StashClient.execute mocking: 27 occurrences in 9 files
  - Internal method mocking (patch.object): 21 occurrences in 7 files
  - Strawberry object mocking (MagicMock/AsyncMock): 182 occurrences in 34 files
  - Other mocking patterns: 1900+ occurrences in 49 files

### Breakdown by Test Category:

- **Client Tests**: 12 files, 119 test functions - NEARLY ALL use `patch.object` to mock StashClient methods
- **Processing Unit Tests**: 25 files, 138 test functions - HEAVILY use AsyncMock/MagicMock for internal methods
- **Processing Integration Tests**: 10 files, 48 test functions - Mix of patterns, some with manual cleanup

### Previously Identified (Incomplete):

- **Pattern 3 (Manual Cleanup)**: 3 tests in 1 file ✅ Accurate
- **Pattern 1 (Mocked Methods)**: 12+ tests in 4 files ❌ VASTLY UNDERSTATED
- **Local Fixtures Bypassing Enforcement**: 11 fixtures in 7 files ✅ Accurate

### Revised Estimate:

- **Total Estimated Effort**: ~60-100 hours (not 8-12!)
- **Complexity**: HIGH - Nearly complete test suite rewrite required

---

## 🎯 Migration Patterns

### ⚠️ Valid Exceptions to Mock-Free Testing

**When mocking internal methods IS acceptable (case-by-case basis):**

1. **Edge Case Coverage**: Testing error handling or unusual code paths that are difficult to trigger via external API

   ```python
   # ✅ ACCEPTABLE: Testing error recovery when internal method fails
   @respx.mock
   async def test_handles_stash_file_lookup_failure(respx_stash_processor):
       # Mock the internal method to simulate rare failure condition
       respx_stash_processor._find_stash_files_by_path = AsyncMock(side_effect=Exception("Disk failure"))
       # Test that processor handles the exception gracefully
   ```

2. **Deep Call Trees (3-4+ layers)**: When testing would require setting up complex external state through multiple layers

3. **Test Infrastructure (tests/stash/types/)**: Unit tests for data conversion, change tracking, field processing
   ```python
   # ✅ ACCEPTABLE: MockTag, MockField used in tests/stash/types/ for testing type system internals
   class MockObjWithDict:
       def __init__(self, value):
           self.value = value
   ```

**When mocking is NOT acceptable:**

- ❌ Mocking StashClient public methods (`create_*`, `update_*`, `find_*`)
- ❌ Mocking single-layer internal methods that could use respx instead
- ❌ Mocking Strawberry objects (Performer, Studio, Scene) - use factories instead
- ❌ Mocking to "make tests faster" - use unit/integration split properly

**Review Process for Exceptions:**

- Requires user confirmation, not assumption/automated guesses
- Consider if respx at HTTP boundary could work instead
- Require real fixtures/factories unless not plausible
- Document WHY mocking is needed (edge case? deep call tree?)

### Pattern 2A: Unit Tests (No Real Stash)

```python
# BEFORE: Uses real_stash_processor with mocked methods ❌
async def test_example(real_stash_processor):
    real_stash_processor._find_stash_files_by_path = AsyncMock()

# AFTER: Uses respx_stash_processor with HTTP mocking ✅
@respx.mock
async def test_example(respx_stash_processor):
    respx.post("http://localhost:9999/graphql").mock(
        return_value=httpx.Response(200, json={"data": {...}})
    )
```

### Pattern 2B: Integration Tests (Real Stash)

```python
# BEFORE: Manual try/finally cleanup ❌
async def test_example(stash_client):
    scene_id = None
    try:
        scene = await stash_client.create_scene(...)
        scene_id = scene.id
    finally:
        if scene_id:
            await stash_client.execute("mutation DeleteScene...")

# AFTER: Automatic cleanup with context manager ✅
async def test_example(stash_client, stash_cleanup_tracker):
    async with stash_cleanup_tracker(stash_client) as cleanup:
        scene = await stash_client.create_scene(...)
        cleanup["scenes"].append(scene.id)
        # Automatic cleanup on exit
```

---

## 🚨 COMPREHENSIVE VIOLATION INVENTORY

### Category A: StashClient Tests (12 files, ~119 test functions)

**ALL of these tests use `patch.object` to mock StashClient methods directly**

Files requiring complete rewrite:

1. `tests/stash/client/test_gallery_mixin.py` - 30 mock violations
2. `tests/stash/client/test_tag_mixin_new.py` - 30 mock violations
3. `tests/stash/client/test_scene_mixin.py` - 21 mock violations
4. `tests/stash/client/test_marker_mixin.py` - 18 mock violations
5. `tests/stash/client/test_image_mixin.py` - 16 mock violations
6. `tests/stash/client/test_studio_mixin.py` - 15 mock violations
7. `tests/stash/client/test_tag_mixin.py` - 14 mock violations
8. `tests/stash/client/test_performer_mixin.py` - 7 mock violations
9. `tests/stash/client/test_subscription.py` - 5 mock violations
10. `tests/stash/client/test_client_base.py` - 1 mock violation
11. `tests/stash/client/client_test_helpers.py` - 4 mock violations (helper file)
12. `tests/stash/client/test_client.py` - violations present

**Pattern**: Nearly every test does `with patch.object(stash_client, "method_name", new_callable=AsyncMock, return_value=...)`

**Required Migration**:

- **Option 1 (Integration)**: Use `stash_client` + `stash_cleanup_tracker` + real Docker Stash
- **Option 2 (Unit)**: Use `respx_stash_processor` with HTTP mocking at edge

### Category B: Processing Unit Tests (25 files, ~138 test functions)

Files heavily using AsyncMock/MagicMock on internal methods:

1. `tests/stash/processing/unit/content/test_message_processing.py` - 37 violations
2. `tests/stash/processing/unit/content/test_post_processing.py` - 36 violations
3. `tests/stash/processing/unit/media_mixin/test_metadata_update.py` - 30 violations
4. `tests/stash/processing/unit/gallery/test_gallery_creation.py` - 26 violations
5. `tests/stash/processing/unit/test_stash_processing.py` - 23 violations
6. `tests/stash/processing/unit/test_base.py` - 20 violations
7. `tests/stash/processing/unit/content/test_content_collection.py` - 15 violations
8. `tests/stash/processing/unit/test_background_processing.py` - 14 violations
9. `tests/stash/processing/unit/test_gallery_methods.py` - 13 violations
10. `tests/stash/processing/unit/media_mixin/async_mock_helper.py` - 13 violations
11. `tests/stash/processing/unit/gallery/test_gallery_lookup.py` - 11 violations
12. `tests/stash/processing/unit/content/test_batch_processing.py` - 9 violations
13. `tests/stash/processing/unit/gallery/test_media_detection.py` - 8 violations
14. `tests/stash/processing/unit/media_mixin/test_file_handling.py` - 7 violations
15. `tests/stash/processing/unit/test_media_mixin.py` - 6 violations
16. `tests/stash/processing/unit/test_account_mixin.py` - 6 violations
17. `tests/stash/processing/unit/test_studio_mixin.py` - 5 violations
18. `tests/stash/processing/unit/test_creator_processing.py` - 5 violations
19. `tests/stash/processing/unit/test_tag_mixin.py` - 4 violations
20. `tests/stash/processing/unit/gallery/test_process_item_gallery.py` - 3 violations
21. `tests/stash/processing/unit/test_gallery_mixin.py` - 1 violation
22. Additional files with violations...

**Pattern**: Tests mock internal methods like `_setup_worker_pool`, `_process_items_with_gallery`, `create_studio`, etc.

**Required Migration**:

- Use `respx_stash_processor` with HTTP mocking
- Use real factories for Strawberry objects instead of MagicMock
- Mock only at HTTP boundary with respx

### Category C: Processing Integration Tests (10 files, ~48 test functions)

Files with mix of patterns (manual cleanup, mocked methods, some valid):

1. `tests/stash/processing/integration/test_media_processing.py` - 22 violations
2. `tests/stash/processing/integration/test_message_processing.py` - 18 violations
3. `tests/stash/processing/integration/test_timeline_processing.py` - 17 violations
4. `tests/stash/processing/integration/test_full_workflow/test_integration.py` - 14 violations
5. `tests/stash/processing/integration/test_media_variants.py` - 12 violations (KNOWN - in original TODO)
6. `tests/stash/processing/integration/test_content_processing.py` - 9 violations
7. `tests/stash/processing/integration/test_stash_processing.py` - 5 violations
8. `tests/stash/processing/integration/test_metadata_update_integration.py` - 3 violations (KNOWN - Pattern 3)
9. Additional integration test files...

**Pattern**: Mix of manual cleanup, mocked methods, and some proper integration tests

**Required Migration**:

- Use `real_stash_processor` + `stash_cleanup_tracker`
- Remove all method mocking
- Ensure proper cleanup after each test

### Category D: Other Stash Tests

1. `tests/stash/integration/test_stash_processing_integration.py` - 14 violations

---

## 🔴 CRITICAL: Pattern 3 - Manual Cleanup Migration

**Priority**: HIGH
**Impact**: Medium (only 1 file, but true integration tests)
**Effort**: 2-3 hours

### File: `tests/stash/processing/integration/test_metadata_update_integration.py`

- [ ] **Line 122-201**: `test_update_stash_metadata_real_scene`

  - Currently: Manual `try/finally` with `sceneDestroy` mutation
  - Action: Replace with `async with stash_cleanup_tracker(stash_client) as cleanup:`
  - Cleanup: Scene + Account + Post (database)

- [ ] **Line 204-305**: `test_update_stash_metadata_preserves_earliest_date`

  - Currently: Manual `try/finally` with `sceneDestroy` mutation
  - Action: Replace with `async with stash_cleanup_tracker(stash_client) as cleanup:`
  - Cleanup: Scene + 2 Posts + Account (database)

- [ ] **Line 308-374**: `test_update_stash_metadata_skips_organized`
  - Currently: Manual `try/finally` with `sceneDestroy` mutation
  - Action: Replace with `async with stash_cleanup_tracker(stash_client) as cleanup:`
  - Cleanup: Scene + Post + Account (database)

**Notes**:

- These tests already use `stash_client` directly
- Already have proper database cleanup
- Just need to migrate Stash object cleanup to use tracker

---

## ✅ IMPORTANT: Pattern 1 - Mocked Methods Migration

**Priority**: HIGH
**Impact**: High (12+ tests across 4 files)
**Effort**: 4-6 hours

**Status**: ✅ COMPLETE

### 🎓 Key Learnings from Migration

#### Why This Migration Matters

The original tests were **faux integration tests** that provided false confidence:

- ❌ Claimed to use `real_stash_processor`, but mocked away anything that would have called it
- ❌ Mocked internal methods with `AsyncMock` (bypassed actual code execution)
- ❌ Never tested real async session handling, GraphQL serialization, or database relationships
- ❌ **HID CRITICAL BUGS** like sync/async session mismatches that caused `MissingGreenlet` errors in production code paths

The migrated tests are **true unit tests with complete flow coverage**:

- ✅ Use `respx_stash_processor` with HTTP mocking at the edge boundary
- ✅ Execute the ENTIRE real code path through all internal methods
- ✅ Use real database objects, real async sessions, real relationship loading
- ✅ Mock ONLY external HTTP calls to Stash GraphQL API
- ✅ **IMMEDIATELY EXPOSED** the greenlet bug that mocks completely hid

**Impact**: Migration immediately caught a production bug on first test run. This validates the entire migration effort.

#### Critical Technical Patterns

**1. respx `.mock(side_effect=[...])` Pattern**

When code makes MULTIPLE sequential GraphQL calls, you MUST provide a response for EACH call:

```python
# ❌ WRONG: Only one response, but code makes 5 GraphQL calls
respx.post("http://localhost:9999/graphql").mock(
    return_value=httpx.Response(200, json={"data": {"findScenes": {...}}})
)
# Result: StopIteration error when code tries to make 2nd call

# ✅ CORRECT: List of responses matching the request sequence
respx.post("http://localhost:9999/graphql").mock(
    side_effect=[  # Note: LIST, not single value
        httpx.Response(200, json={"data": {"findScenes": {...}}}),       # Call 1
        httpx.Response(200, json={"data": {"findPerformers": {...}}}),   # Call 2
        httpx.Response(200, json={"data": {"findStudios": {...}}}),      # Call 3
        httpx.Response(200, json={"data": {"findStudios": {...}}}),      # Call 4
        httpx.Response(200, json={"data": {"sceneUpdate": {...}}}),      # Call 5
    ]
)
```

**2. Permanent GraphQL Call Assertions (REQUIRED)**

**⚠️ CRITICAL PATTERN**: Every respx test MUST include assertions verifying both request and response for each GraphQL call.

This is NOT temporary debug code - it's a permanent regression protection pattern that:

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

# ... verify ALL calls with both request AND response checks
```

**Why this matters:**

- ✅ **Caught `@async_lru_cache` behavior**: Test explicitly verifies performer lookups are skipped on 2nd media object
- ✅ **Prevents bad requests**: Validates we're sending correct variables, not just accepting any response
- ✅ **Documents flow**: Reading assertions shows exact API interaction sequence
- ✅ **Regression protection**: Any change to call order/count immediately fails the test

**Example - Verifying cache behavior:**

```python
# CRITICAL: Verify @async_lru_cache skips performer lookups after first media object
performer_calls_after_first_update = [
    i
    for i in range(8, 12)  # Range after first imageUpdate
    if "findPerformers" in json.loads(calls[i].request.content)["query"]
]
assert (
    len(performer_calls_after_first_update) == 0
), f"Found unexpected findPerformers calls at indices: {performer_calls_after_first_update}"
```

**See example implementation**: `tests/stash/processing/unit/media_mixin/test_media_processing.py`

**Error Testing Pattern - Testing Real Constraint Violations**

When adding error case tests to TRUE integration tests, focus on testing REAL constraint violations that trigger actual GraphQL errors from Stash, not edge cases where the API silently accepts invalid data.

```python
# ❌ WRONG: Edge case that Stash silently accepts
# (Setting cover to non-existent image ID - Stash returns True but ignores it)
result = await stash_client.set_gallery_cover(gallery_id, image_id="99999")
assert result is False  # FAILS - Stash returns True!

# ✅ CORRECT: Real constraint violation that triggers GraphQL error
# (Creating chapter on gallery with NO images - violates schema constraint)
with capture_graphql_calls(stash_client) as calls:
    with pytest.raises(Exception, match="Image # must greater than zero"):
        await stash_client.gallery_chapter_create(
            gallery_id=empty_gallery_id,
            title="Invalid Chapter",
            image_index=1,  # No images in gallery!
        )
    # Verify the GraphQL call was captured even though exception was raised
    assert len(calls) == 1
    assert "galleryChapterCreate" in calls[0]["query"]
```

**Key principle**: Inspect both what you send AND what you receive in `capture_graphql_calls` to match your assumptions with actual API behavior.

**Fixture improvement**: The `capture_graphql_calls` fixture now uses try/finally to ensure calls are logged even when exceptions are raised:

```python
async def capture_execute(query, variables=None):
    """Capture the call details and execute the real query.

    Uses try/finally to ensure call is logged even if exception is raised.
    """
    try:
        result = await original_execute(query, variables)
        return result
    finally:
        # Always log the call, even if it raised an exception
        calls.append({
            "query": query,
            "variables": variables,
            "result": result if 'result' in locals() else None
        })
```

**See example implementation**: `tests/stash/client/test_gallery_mixin.py` - `test_gallery_chapter_error_cases`

**3. Understand the FULL Request Flow**

Trace through the code to understand ALL GraphQL calls made:

```python
# Example from test_process_hls_variant - 5 sequential calls:
# 1. findScenes      - _find_stash_files_by_path() searches for scene by file path
# 2. findPerformers  - _update_stash_metadata() finds main performer (account)
# 3. findStudios     - _find_existing_studio() finds "Fansly (network)" parent studio
# 4. findStudios     - _find_existing_studio() finds creator-specific child studio
# 5. sceneUpdate     - stash_obj.save() persists updated scene metadata

# Missing ANY of these = StopIteration error
```

**4. VideoFile/ImageFile Schema Validation**

Stash types have strict required fields that WILL fail if missing:

```python
# ❌ WRONG: Minimal file dict
files=[{"path": "/path/to/file.mp4"}]
# Result: TypeError - missing 9 required fields

# ✅ CORRECT: Complete VideoFile schema
files=[{
    "id": "file_123",
    "path": "/path/to/file.mp4",
    "basename": "file.mp4",
    "size": 1024,
    # Required VideoFile fields:
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

# ✅ CORRECT: Complete ImageFile schema (different required fields)
visual_files=[{
    "id": "file_789",
    "path": "/path/to/image.jpg",
    "basename": "image.jpg",
    "size": 512000,
    # Required ImageFile fields:
    "parent_folder_id": None,
    "mod_time": "2024-01-01T00:00:00Z",
    "fingerprints": [],
    "width": 1920,  # Required for ImageFile
    "height": 1080,  # Required for ImageFile
}]
```

**Field Filtering**: Strawberry types use `_filter_init_args` (base.py:102) to strip unknown fields via `__strawberry_definition__.fields`. Server-only fields like `created_at`, `updated_at` are automatically filtered out. Deprecated fields (e.g., `files`) conflict with new fields (`visual_files`) and cause TypeError - must remove from test helpers.

#### Async Session Patterns

```python
# ❌ WRONG: Using sync session in async test
session_sync.add(media)
session_sync.commit()
test_media.variants = {variant}  # BOOM: MissingGreenlet error

# ✅ CORRECT: Use async session with proper awaits
session.add(media)
await session.commit()
await session.refresh(test_media, attribute_names=["variants"])  # Load relationships
test_media.variants = {variant}  # Now safe
```

The greenlet error was HIDDEN by mocks because they never executed the real relationship loading code.

---

### File 1: `tests/stash/processing/unit/test_media_variants.py` (MOVED from integration/)

**Issue**: Uses `real_stash_processor` but mocks internal methods `_find_stash_files_by_path` and `_update_stash_metadata`
**Solution**: Migrate to `respx_stash_processor` with HTTP mocking
**Status**: ✅ COMPLETE

- [x] **COMPLETED**: `test_process_hls_variant`

  - Migrated to respx with 5 GraphQL responses (findScenes, findPerformers, 2x findStudios, sceneUpdate)
  - Fixed sync/async session bug (greenlet error)
  - Added complete VideoFile required fields
  - Changed fixture: `real_stash_processor` → `respx_stash_processor`
  - Changed session: `session_sync` → `session` (async)
  - File moved: `integration/test_media_variants.py` → `unit/test_media_variants.py`

- [x] **COMPLETED**: `test_process_dash_variant`

  - Applied same pattern as HLS test
  - 5 GraphQL responses with proper DASH VideoFile schema
  - Path must use variant ID: `f"/path/to/media_{dash_variant.id}"`

- [x] **COMPLETED**: `test_process_preview_variant`

  - 6 GraphQL responses (images processed BEFORE scenes)
  - ImageFile schema requires: `width`, `height` (VideoFile: `duration`, `frame_rate`, etc.)
  - Removed deprecated `files` field from `create_image_dict` helper
  - **Key Discovery**: `_find_stash_files_by_path` processes images at line 492, scenes at line 575

- [x] **COMPLETED**: `test_process_bundle_ordering`
- [x] **COMPLETED**: `test_process_bundle_with_preview`
- [x] **COMPLETED**: `test_bundle_permission_inheritance`

---

### File 2: `tests/stash/processing/unit/test_background_processing.py`

**Issue**: Uses `real_stash_processor` with mocked `create_studio` method
**Solution**: Migrate to `respx_stash_processor`

- [x] **COMPLETED**: All tests migrated (see section below at line 525)
  - Removed all AsyncMock usage
  - Added respx with GraphQL responses
  - Changed fixture: `processor` → `respx_stash_processor`
  - 9 tests passing with real database queries

---

### File 3: `tests/stash/processing/integration/test_stash_processing.py`

**Issue**: Uses `real_stash_processor` with mocked `create_studio`
**Solution**: Migrate to `respx_stash_processor`

- [x] **COMPLETED**: All tests migrated

---

### File 4: `tests/stash/processing/unit/media_mixin/test_metadata_update.py`

**Issue**: Uses `real_stash_processor` with mocked `create_studio`
**Solution**: Migrate to `respx_stash_processor`

- [x] **COMPLETED**: All tests migrated

---

## 🟠 MODERATE: Local Fixture Migration

**Priority**: MEDIUM
**Impact**: Medium (prevents future violations)
**Effort**: 2-3 hours

### High-Risk Local Fixtures (Create StashClient/Context)

#### File 1: `tests/stash/client/test_tag_mixin_new.py`

- [ ] **Line 14-31**: Remove local `stash_client()` fixture
  - Action: Use global `stash_client` from `tests/fixtures/stash/stash_api_fixtures.py`
  - Update: Add `stash_cleanup_tracker` to all test signatures

#### File 2: `tests/stash/client/test_tag_mixin.py`

- [ ] **Line 15-121**: Remove helper functions `create_mock_client()`, `add_tag_find_methods()`, `add_tag_modification_methods()`
- [ ] **Line 124-146**: Remove local `tag_mixin_client()` and `stash_client()` fixtures
  - Action: Use global `stash_client` + `stash_cleanup_tracker`
  - OR: Use `respx_stash_processor` if tests are unit tests

#### File 3: `tests/stash/client/test_scene_mixin.py`

- [ ] **Line 16-155**: Remove helper functions `create_mock_client()`, `add_scene_find_methods()`, `add_scene_update_methods()`, `add_scene_filename_methods()`
- [ ] **Line 167-190**: Remove local `scene_mixin_client()` and `stash_client()` fixtures
  - Action: Use global `stash_client` + `stash_cleanup_tracker`
  - OR: Use `respx_stash_processor` if tests are unit tests

#### File 4: `tests/stash/processing/unit/test_stash_processing.py`

- [ ] **Line 24-56**: Remove local `mock_context()`, `mock_database()`, `processor()` fixtures
  - Action: Use `respx_stash_processor` fixture instead
  - Impact: Requires updating all test signatures

#### File 5: `tests/stash/processing/unit/test_base.py`

- [ ] **Line 52-91**: Remove local `mock_context()`, `base_processor()` fixtures
  - Action: Use `respx_stash_processor` fixture instead
  - Impact: Requires updating all test signatures

#### File 6: `tests/stash/processing/unit/test_background_processing.py`

- [x] **COMPLETED**: Removed local `processor()` fixture
  - Migrated all tests to use `respx_stash_processor` fixture
  - Uses respx for GraphQL HTTP mocking
  - Tests real database queries with PostgreSQL
  - Note: `test_continue_stash_processing_stash_id_update` patches `async_session_scope` to work around SERIALIZABLE isolation behavior

#### File 7: `tests/stash/processing/integration/test_metadata_update_integration.py`

- [ ] **Line 43-46**: Remove local `media_mixin()` fixture
  - Action: Use `real_stash_processor` + `stash_cleanup_tracker`
  - Note: Partially covered in Pattern 3 migration above

---

## ✅ Verification Checklist

After completing migrations, verify:

- [ ] All tests in `tests/stash/` (except `tests/stash/types/`) use one of:

  - `respx_stash_processor` (unit tests with HTTP mocking)
  - `stash_client` + `stash_cleanup_tracker` (integration tests)
  - `real_stash_processor` + `stash_cleanup_tracker` (integration tests)

- [ ] No local `stash_client`, `stash_context`, or `processor` fixtures in test files

- [ ] No manual `try/finally` cleanup with destroy mutations

- [ ] No mocking of internal methods (methods starting with `_`)

- [ ] No mocking of StashClient public methods (`create_*`, `update_*`, etc.)

- [ ] Run full test suite: `pytest tests/stash/ -v`

- [ ] Check for new xfails: `pytest tests/stash/ -v | grep XFAIL`

---

## 🎓 Reference Documentation

**Enforcement Hook**: `tests/conftest.py:50-100`
**Cleanup Summary**: `tests/stash/CLEANUP_ENFORCEMENT_SUMMARY.md`
**Fixture Definitions**:

- Global stash_client: `tests/fixtures/stash/stash_api_fixtures.py:70`
- respx_stash_processor: `tests/fixtures/stash/stash_integration_fixtures.py:225`
- real_stash_processor: `tests/fixtures/stash/stash_integration_fixtures.py:190`
- stash_cleanup_tracker: `tests/fixtures/stash/cleanup_fixtures.py`

---

## 📊 Progress Tracking

### Original Estimate (INCORRECT):

- **Total Tasks**: 38
- **Estimated Effort**: 8-12 hours

### REVISED REALITY:

- **Total Test Files Affected**: 40+ files
- **Total Test Functions**: ~305 functions
- **Total Mock Violations**: 2100+ occurrences

### Breakdown by Category:

- **Category A (Client Tests)**: 12 files, ~119 test functions - Complete rewrite needed
- **Category B (Processing Unit)**: 25 files, ~138 test functions - Complete rewrite needed
- **Category C (Processing Integration)**: 10 files, ~48 test functions - Heavy refactoring needed
- **Category D (Other)**: 1+ files - Needs migration

### Realistic Effort Estimate:

- **Category A**: 20-30 hours (client tests)
- **Category B**: 30-40 hours (processing unit tests)
- **Category C**: 10-20 hours (processing integration tests)
- **Local Fixtures**: 2-3 hours
- **Manual Cleanup**: 2-3 hours
- **TOTAL**: 64-96 hours

**Status**: 🔴 NOT STARTED
**Completed**: 0
**In Progress**: 0
**Not Started**: 305 test functions

**Last Updated**: 2025-11-15 (updated with accurate scope)
