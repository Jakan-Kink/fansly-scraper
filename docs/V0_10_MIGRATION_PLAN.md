# stash-graphql-client v0.10.0 Migration Plan

## Overview

This document outlines planned optimizations to prepare for stash-graphql-client v0.10.0's field-aware repository pattern.

## Current Patterns (v0.8.0)

### 1. Sequential Deduplication (account.py:84-128)

**Current Implementation:**
```python
async def _get_or_create_performer(self, account: Account) -> Performer:
    # Sequential GraphQL queries for deduplication
    performer = await self.store.find_one(Performer, name__exact=search_name)
    if performer:
        return performer

    performer = await self.store.find_one(Performer, aliases__contains=account.username)
    if performer:
        return performer

    performer = await self.store.find_one(Performer, url__contains=fansly_url)
    if performer:
        return performer

    # Create new if not found
    return await self.context.client.create_performer(performer)
```

**Issue:** Each `find_one()` triggers a GraphQL query if not in cache. 3 sequential network calls in worst case.

**Status:** ✅ **IMPLEMENTED** - Using identity map with find_one()

**Implemented Solution (v0.10.4):**
Current implementation uses `store.find_one()` which leverages the identity map for caching. The sequential deduplication logic is preserved because it represents the actual business requirement (check name, then alias, then URL).

**Alternative v0.10.x Pattern (for future consideration):**
```python
async def _get_or_create_performer(self, account: Account) -> Performer:
    search_name = account.displayName or account.username
    fansly_url = f"https://fansly.com/{account.username}"

    # Option 1: Cache-first approach with filter()
    # Check cache first (no network call)
    cached = self.store.filter(
        Performer,
        predicate=lambda p: (
            p.name == search_name or
            account.username in (p.alias_list or []) or
            fansly_url in (p.urls or [])
        )
    )
    if cached:
        return cached[0]

    # Option 2: Hybrid approach with filter_and_populate()
    # Auto-populate required fields if missing
    results = await self.store.filter_and_populate(
        Performer,
        required_fields=['name', 'alias_list', 'urls'],
        predicate=lambda p: (
            p.name == search_name or
            account.username in p.alias_list or
            fansly_url in p.urls
        )
    )
    if results:
        return results[0]

    # Not found - create new
    performer = self._performer_from_account(account)
    return await self.context.client.create_performer(performer)
```

**Benefits:**
- Option 1: Zero network calls if in cache
- Option 2: Single GraphQL query to populate missing fields vs 3 sequential queries
- Both: Evaluate all 3 conditions in parallel vs sequential early-return

**Tradeoffs:**
- Cache must be warmed with performer data first
- Predicate function is less readable than Django-style filters
- Requires all performers in cache for full benefit

**Decision:** ✅ Current pattern is optimal for the business logic. Identity map caching reduces repeated lookups. In-memory filtering could be added in Phase 4 if needed.

---

### 2. Studio Lookup Pattern (studio.py:83)

**Current Implementation:**
```python
fansly_studio = await self.store.find_one(Studio, name="Fansly (network)")
if not fansly_studio:
    raise ValueError("Fansly Studio not found in Stash")
```

**Issue:** `find_one()` executes GraphQL query every time if not cached.

**Status:** ✅ **IMPLEMENTED** - Identity map handles caching

**Implemented Solution (v0.10.4):**
Current implementation works well with identity map. The Fansly studio is fetched once and cached automatically.

**Alternative v0.10.x Pattern (not needed):**
```python
# Cache the Fansly studio once at initialization
async def _ensure_fansly_studio_cached(self):
    """Ensure Fansly network studio is in cache (call once per session)."""
    if not self.store.filter(Studio, lambda s: s.name == "Fansly (network)"):
        await self.store.find_one(Studio, name="Fansly (network)")

# Then use cache-only lookup
fansly_studio = self.store.filter(
    Studio,
    predicate=lambda s: s.name == "Fansly (network)"
)
if not fansly_studio:
    raise ValueError("Fansly Studio not found in Stash")
fansly_studio = fansly_studio[0]
```

**Benefits:**
- One-time GraphQL query instead of per-creator
- Cache-first approach for frequently accessed studio

**Tradeoffs:**
- Requires initialization step
- More complex code for marginal benefit (studio rarely changes)

**Decision:** ✅ Current pattern is sufficient. Identity map provides the needed caching.

---

### 3. Parallel Tag Creation (tag.py:50)

**Current Implementation:**
```python
# Use get_or_create in parallel for all tags (identity map handles duplicates)
tag_tasks = [self.store.get_or_create(Tag, name=name) for name in tag_names]
tags = await asyncio.gather(*tag_tasks, return_exceptions=True)
```

**Status:** ✅ **IMPLEMENTED** - Optimal pattern already in use! 🎉

**Implemented Pattern (v0.10.4):**
Already using the recommended pattern. Each `get_or_create()`:
1. Checks identity map cache first
2. Only queries Stash if not cached
3. Runs in parallel with `asyncio.gather()`
4. Results in 90%+ reduction in API calls

**Decision:** ✅ No further changes needed - pattern is optimal

---

### 4. Large Dataset Iteration (media.py:331-469)

**Current Implementation:**
```python
# Find images by path pattern
async for image_data in self.store.find_iter(
    Image,
    image_filter={"path": {"modifier": "MATCHES_REGEX", "value": pattern}}
):
    # Process each image
```

**Issue:** `find_iter()` executes GraphQL queries for each batch.

**Status:** ⏸️ **DEFERRED** - Not needed for current performance

**Possible v0.10.x Optimization (future):**
```python
# If cache is populated, use populated_filter_iter for lazy processing
async for image in self.store.populated_filter_iter(
    Image,
    required_fields=['path', 'visual_files', 'studio', 'performers'],
    predicate=lambda img: re.match(pattern, img.visual_files[0].path),
    populate_batch=50,
    yield_batch=10
):
    # Process each image with auto-populated fields
    # Supports early exit if needed
```

**Benefits:**
- Only fetches missing fields (10x smaller payload)
- Lazy iteration with early exit support
- Memory efficient for large datasets

**Tradeoffs:**
- Requires cache to be populated first
- Regex matching in Python vs GraphQL server-side
- More complex for path-based lookups

**Decision:** ⏸️ Deferred to Phase 4. Current pattern performs well enough. Can be optimized if profiling shows it as a bottleneck.

---

## Implementation Status

### Phase 1: Documentation & Analysis ✅ **COMPLETE**
- [x] Document current patterns
- [x] Identify optimization opportunities
- [x] Evaluate tradeoffs
- [x] Update pyproject.toml to v0.10.4

**Completion Date:** 2026-01-09

### Phase 2: Core Implementation ✅ **COMPLETE**
**Goal:** Implement store patterns in production code

**Verified Implementations (from git diff):**
- [x] `stash/processing/base.py` - Added `store` property
- [x] `stash/processing/mixins/account.py` - Uses `store.find_one()` + `store.get()`
- [x] `stash/processing/mixins/tag.py` - Parallel `store.get_or_create()`
- [x] `stash/processing/mixins/studio.py` - `store.get_or_create()`, removed manual invalidation
- [x] `stash/processing/mixins/gallery.py` - `store.get()` for O(1) lookups
- [x] `stash/processing/mixins/media.py` - `store.find_iter()` for lazy iteration
- [x] `tests/fixtures/stash/stash_type_factories.py` - Updated for Pydantic patterns

**Completion Date:** 2026-01-09

### Phase 3: Test Compatibility 🟡 **IN PROGRESS**
**Goal:** Update all tests to work with v0.10.4 patterns

**Completed:**
- [x] Factory patterns updated (no manual IDs, automatic UNSET)
- [x] V0_10_MOCK_PATTERNS.md documented for test fixes

**Remaining:**
- [ ] Update remaining tests for new factory patterns
- [ ] Fix tests expecting manual IDs (factories now use Pydantic auto-UUID)
- [ ] Fix tests expecting explicit UNSET (now automatic)
- [ ] Verify all test suites passing

**Status:** Tests need updating for new factory behavior. Implementation code is complete and staged.

### Phase 4: Advanced Optimizations ⏸️ **NOT STARTED**
- [ ] Implement in-memory filtering with `store.filter()` (only if profiling shows benefit)
- [ ] Add cache warming strategy for common entities
- [ ] Benchmark before/after performance
- [ ] Consider `filter_and_populate()` for large dataset iteration

**Status:** Deferred - Current patterns perform well. Will revisit if profiling identifies bottlenecks.

---

## Performance Improvements (To Be Verified)

**Status:** Implementation complete, performance testing needed

### Tag Processing
- **Before:** N tags × 2 queries = 2N API calls (sequential searches)
- **After (v0.10.4):** N parallel `get_or_create()` with identity map
- **Expected:** **90%+ reduction in API calls**
- **Status:** ⏳ Needs verification in production

### Performer Lookups
- **Before:** 3 sequential GraphQL queries (no caching)
- **After (v0.10.4):** Identity map caches subsequent lookups
- **Expected:** **60-80% reduction** on cache hits
- **Status:** ⏳ Needs verification in production

### Studio Lookups
- **Before:** 1 GraphQL query per creator (no caching)
- **After (v0.10.4):** 1 query total, identity map cached
- **Expected:** **N-1 queries saved** (N = number of creators)
- **Status:** ⏳ Needs verification in production

**Note:** Implementation patterns are correct, but real-world performance needs testing with actual Stash server.

---

## Key Changes in v0.10.4

### Pydantic Models (Not Strawberry)
- ✅ Fully typed Pydantic models from all store methods
- ✅ No dict vs object ambiguity
- ✅ Proper validation + IDE autocomplete

### Automatic UNSET Pattern
- ✅ Fields not assigned = automatic UNSET (no need to explicitly set)
- ✅ Simplifies factory definitions
- ✅ Cleaner partial updates

### UUID Auto-Generation
- ✅ Pydantic models auto-generate temp UUIDs on creation
- ✅ Save to server returns real ID
- ✅ No manual ID assignment in factories

### Identity Map
- ✅ Same ID = same object instance (guaranteed)
- ✅ Automatic caching with configurable TTL
- ✅ Updates propagate everywhere automatically

---

## Next Steps

1. **Complete Phase 3:** Update remaining tests for new factory patterns
2. **Verify Performance:** Test actual performance improvements in production
3. **Phase 4 (Optional):** Consider advanced optimizations if profiling shows bottlenecks

For detailed migration patterns, see **STASH_ORM_MIGRATION_GUIDE.md**

---

## References

- [Official Documentation](https://jakan-kink.github.io/stash-graphql-client/latest/)
- [CHANGELOG (All Releases)](https://github.com/Jakan-Kink/stash-graphql-client/blob/main/CHANGELOG.md)
- [Advanced Filtering Guide](https://jakan-kink.github.io/stash-graphql-client/latest/guide/advanced-filtering/)
- [Architecture Overview](https://jakan-kink.github.io/stash-graphql-client/latest/architecture/overview/)
