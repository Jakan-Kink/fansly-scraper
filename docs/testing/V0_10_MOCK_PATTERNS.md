# v0.10.4 Mock Patterns for stash-graphql-client Tests

## Overview

Documented patterns for updating tests to match stash-graphql-client v0.10.4 behavior.

**Current Version:** v0.10.4 (patterns apply to v0.10.3+)

Based on: Fixing test_creator_processing.py (10/10 tests passing) and factory pattern updates

---

## Pattern 1: `store.get_or_create()` + `store.save()` Creates New Entity

**Behavior:** `get_or_create()` attempts to create immediately, not find first.

### ❌ Old (Broken):

```python
# Test expected: findStudios → findStudios (two searches)
graphql_route = respx.post("http://localhost:9999/graphql").mock(
    side_effect=[
        # findStudios for Fansly network
        httpx.Response(200, json=create_graphql_response("findStudios", fansly_result)),
        # findStudios for creator studio (WRONG - doesn't happen!)
        httpx.Response(200, json=create_graphql_response("findStudios", creator_result)),
    ]
)
```

### ✅ New (Correct):

```python
# Actual behavior: findStudios → studioCreate (search then create)
graphql_route = respx.post("http://localhost:9999/graphql").mock(
    side_effect=[
        # findStudios for Fansly network
        httpx.Response(200, json=create_graphql_response("findStudios", fansly_result)),
        # studioCreate for creator studio (get_or_create tries to create)
        httpx.Response(200, json=create_graphql_response("studioCreate", creator_dict)),
    ]
)
```

**Why:** `store.get_or_create()` doesn't check identity map cache first. It tries to create, then Stash returns existing if duplicate. `store.save()` sends the create mutation.

**Files Fixed:**

- `test_creator_processing.py::test_find_existing_studio`

---

## Pattern 2: Don't Assert on Library Implementation Details

**Behavior:** Library defaults changed between versions.

### ❌ Old (Broken):

```python
# This broke when library changed per_page default from -1 to 1
filter_params = variables.get("filter", {})
assert filter_params.get("per_page") == -1  # Hardcoded expectation
```

### ✅ New (Correct):

```python
# Test business logic, not library implementation
performer_filter = variables.get("performer_filter", {})
name_filter = performer_filter.get("name", {})
assert name_filter.get("value") == "Test User"  # Business logic
assert name_filter.get("modifier") == "EQUALS"
# Don't assert on per_page - that's library implementation
```

**Why:** Library internals can change. Tests should validate business logic (correct queries, correct data), not implementation details (pagination defaults).

**Files Fixed:**

- `test_creator_processing.py::test_process_creator`

---

## Pattern 3: Use Debug Logging to Discover Actual Behavior

**Method:** Add try/finally block to print all GraphQL calls.

```python
# Add this to any failing test to see actual GraphQL calls
try:
    # ... test code ...
    result = await processor.some_method()
finally:
    print("\n" + "="*80)
    print("DEBUG: GraphQL Calls Made")
    print("="*80)
    for index, call in enumerate(graphql_route.calls):
        req = json.loads(call.request.content)
        print(f"\nCall {index}:")
        print(f"  Request query: {req.get('query', '')[:200]}...")
        print(f"  Request variables: {req.get('variables', {})}")
        print(f"  Response: {call.response.json()}")
    print("="*80 + "\n")
```

**Why:** Don't guess what the library does - observe it! This shows:

- Exact query types sent (`findStudios`, `studioCreate`, etc.)
- Variable values used
- Response keys expected
- Call sequence and count

**Example Output:**

```
Call 0:
  Request query: query FindPerformers...
  Request variables: {'filter': {'page': 1, 'per_page': 1}, ...}
  Response: {'data': {'findPerformers': {...}}}

Call 1:
  Request query: mutation CreateStudio...
  Request variables: {'input': {'name': 'test_user (Fansly)', ...}}
  Response: {'data': {'findStudios': {...}}}  # ← BUG! Should be 'studioCreate'
```

---

## Pattern 4: Performer Deduplication (Sequential Searches)

**Behavior:** `_get_or_create_performer()` tries 3 sequential searches before creating.

### ✅ Correct (Unchanged):

```python
graphql_route = respx.post("http://localhost:9999/graphql").mock(
    side_effect=[
        # Search 1: By exact name (not found)
        httpx.Response(200, json=create_graphql_response("findPerformers", empty_result)),
        # Search 2: By alias (not found)
        httpx.Response(200, json=create_graphql_response("findPerformers", empty_result)),
        # Search 3: By URL (not found)
        httpx.Response(200, json=create_graphql_response("findPerformers", empty_result)),
        # Create new performer
        httpx.Response(200, json=create_graphql_response("performerCreate", performer_dict)),
    ]
)
```

**Why:** This pattern is CORRECT and still works. Don't change it.

**Files Working:**

- `test_creator_processing.py::test_process_creator_creates_new_performer`

---

## Pattern 5: Media Processing with Variants

**Behavior:** Each file processes independently with full lookups.

### ✅ Correct Pattern:

```python
# For 3 files (1 image + 2 scenes):
# Expected: 14 GraphQL calls total
graphql_route = respx.post("http://localhost:9999/graphql").mock(
    side_effect=[
        # Calls 0-1: Find all files by path
        httpx.Response(200, json=create_graphql_response("findImages", images_result)),
        httpx.Response(200, json=create_graphql_response("findScenes", scenes_result)),

        # FILE 1 (image): Calls 2-5
        httpx.Response(200, json=create_graphql_response("findPerformers", empty)),
        httpx.Response(200, json=create_graphql_response("findStudios", fansly)),
        httpx.Response(200, json=create_graphql_response("studioCreate", creator)),
        httpx.Response(200, json=create_graphql_response("imageUpdate", updated_image)),

        # FILE 2 (scene): Calls 6-9
        httpx.Response(200, json=create_graphql_response("findPerformers", empty)),
        httpx.Response(200, json=create_graphql_response("findStudios", fansly)),
        httpx.Response(200, json=create_graphql_response("studioCreate", creator)),  # Tries again!
        httpx.Response(200, json=create_graphql_response("sceneUpdate", updated_scene)),

        # FILE 3 (scene variant): Calls 10-13
        httpx.Response(200, json=create_graphql_response("findPerformers", empty)),
        httpx.Response(200, json=create_graphql_response("findStudios", fansly)),
        httpx.Response(200, json=create_graphql_response("studioCreate", creator)),  # Tries again!
        httpx.Response(200, json=create_graphql_response("sceneUpdate", updated_variant)),
    ]
)
```

**Why:** Each file gets independent performer/studio lookups. Identity map doesn't prevent duplicate `studioCreate` attempts.

**Files Fixed:**

- `tests/stash/processing/unit/media_mixin/test_media_processing.py::test_process_media_with_variants`

---

## Common Failures & Fixes

### Failure: "Missing 'studioCreate' in response"

**Cause:** Mock returns `findStudios` but code sends `studioCreate`
**Fix:** Change mock response key from `findStudios` to `studioCreate`

### Failure: "assert 1 == -1" (per_page)

**Cause:** Asserting on library default that changed
**Fix:** Remove assertion on `per_page`, test business logic instead

### Failure: "assert 'findScenesByPathRegex' in query"

**Cause:** GraphQL operation name changed in library
**Fix:** Change assertion to `'findScenes'` (without ByPathRegex)

---

## Testing Checklist

When updating a test:

- [ ] Add debug logging to see actual GraphQL calls
- [ ] Match mock response keys to actual mutations (`studioCreate` not `findStudios`)
- [ ] Don't assert on library defaults (`per_page`, `page`, etc.)
- [ ] Test business logic (query type, variables, data flow)
- [ ] Remove debug logging after test passes
- [ ] Update comments to reflect actual behavior

---

## Success Metrics

- **test_creator_processing.py**: 10/10 passing ✅
- **test_media_processing.py**: 3/3 passing ✅

**Patterns work!** Apply to remaining test failures.

---

## Pattern 6: Factory Updates for v0.10.4

**Updated:** 2026-01-09

### UUID Auto-Generation

Factories no longer manually assign IDs. Pydantic models auto-generate temp UUIDs:

```python
# ✅ CORRECT (v0.10.4)
performer = PerformerFactory(name="Test")  # Gets temp UUID automatically
# performer.id = "f47ac10b-58cc-..." (temp UUID)

# To simulate server-returned object (override temp UUID):
existing = PerformerFactory(id="123", name="Test")  # Real ID from server
```

### Automatic UNSET Pattern

Fields not assigned in factories = automatic UNSET (don't explicitly set):

```python
# ✅ CORRECT (v0.10.4)
scene = SceneFactory(title="Test")  # Only title set
# All other fields are automatic UNSET (not sent in mutation)

# ❌ OLD (v0.10.3)
scene = SceneFactory(title="Test", paths=UNSET)  # Don't do this
```

### Test Updates Needed

Tests expecting specific IDs from factories need updating:

```python
# ❌ OLD (will fail)
performer = PerformerFactory()
assert performer.id == "100"  # Fails - gets temp UUID now

# ✅ CORRECT
performer = PerformerFactory(id="100")  # Override temp UUID
assert performer.id == "100"

# OR test the business logic, not the ID:
performer = PerformerFactory(name="Test")
assert performer.name == "Test"  # Test what matters
```

**Status:** Factory patterns updated. Tests need updating for new behavior.
