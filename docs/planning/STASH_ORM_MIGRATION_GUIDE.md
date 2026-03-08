# Stash GraphQL Client ORM Migration Guide

> **Goal:** Refactor codebase to leverage stash-graphql-client ORM features for cleaner code, better performance, and improved maintainability.
>
> **Status:** Phase 1 ✅ Complete | Phase 2 ✅ Complete | Phase 3 ✅ Complete | Phase 4 ⏸️ Not Started
>
> **Current Version:** v0.11.0b5 (Pydantic models, server capability detection, dynamic fragment store)

## Table of Contents

1. [Why Migrate?](#why-migrate)
2. [Key Concepts](#key-concepts)
3. [Migration Patterns](#migration-patterns)
4. [Phased Migration Plan](#phased-migration-plan)
5. [Testing Strategy](#testing-strategy)
6. [Breaking Changes & Compatibility](#breaking-changes--compatibility)

---

## Why Migrate?

### Current State: Using Library as Raw GraphQL Client

- 🔴 **20+ unique client methods** called throughout codebase
- 🔴 **Manual filter dict construction** (verbose, error-prone)
- 🔴 **N+1 query problems** (sequential searches for same entities)
- 🔴 **Manual cache invalidation** (`Studio._store.invalidate_type()`)
- 🔴 **Manual retry logic** scattered across multiple files
- 🔴 **Race condition handling** with string matching on error messages
- 🔴 **Complex nested OR construction** (40+ lines for simple operations)

### After Migration: Using Library as ORM

- ✅ **50-70% reduction in API calls** (identity map + batching)
- ✅ **20-30% less code** (simplified filters, relationship helpers)
- ✅ **Type-safe Django-style queries** with autocomplete
- ✅ **Automatic dirty tracking** (only save changed fields)
- ✅ **Bidirectional relationship sync** (set once, updated everywhere)
- ✅ **Built-in retry logic** with exponential backoff
- ✅ **Identity map** ensures same ID = same object instance

---

> **Key Concepts** (identity map, UNSET pattern, relationship helpers, Django-style filtering) are documented in the [stash-graphql-client library docs](https://jakan-kink.github.io/stash-graphql-client/latest/). This guide focuses on project-specific migration patterns.

---

## Migration Patterns

> **Reading guide:** The "Current Code" examples below show the **pre-migration** state (now deleted). The "Migrated Code" examples show the **current** implementation.

### Pattern 1: Replace Sequential Searches with `store.find()`

**Priority:** 🔴 HIGH - Eliminates N+1 queries

#### Current Code (account.py:101-137)

```python
async def _get_or_create_performer(self, account: Account) -> Performer:
    """Get existing performer or create from account."""
    search_name = account.displayName or account.username
    fansly_url = f"https://fansly.com/{account.username}"

    # ❌ Query 1: By name
    result = await self.context.client.find_performers(
        performer_filter={"name": {"value": search_name, "modifier": "EQUALS"}}
    )
    if is_set(result.count) and result.count > 0:
        return result.performers[0]

    # ❌ Query 2: By alias
    result = await self.context.client.find_performers(
        performer_filter={"aliases": {"value": account.username, "modifier": "INCLUDES"}}
    )
    if is_set(result.count) and result.count > 0:
        return result.performers[0]

    # ❌ Query 3: By URL
    result = await self.context.client.find_performers(
        performer_filter={"url": {"value": fansly_url, "modifier": "INCLUDES"}}
    )
    if is_set(result.count) and result.count > 0:
        return result.performers[0]

    # Not found - create
    performer = self._performer_from_account(account)
    return await self.context.client.create_performer(performer)
```

#### Migrated Code (BEST: Using get_or_create)

```python
async def _get_or_create_performer(self, account: Account) -> Performer:
    """Get existing performer or create from account."""
    search_name = account.displayName or account.username
    fansly_url = f"https://fansly.com/{account.username}"

    # ✅ BEST: Try name with get_or_create (1 call instead of 3!)
    try:
        performer = await self.context.store.get_or_create(
            Performer,
            name=search_name,
            # These fields used only if creating:
            alias_list=[account.username],
            urls=[fansly_url],
            details=account.about or "",
        )
        return performer
    except Exception:
        # Fall back to alias search if name search fails
        pass

    # Try by alias (rare fallback)
    performer = await self.context.store.find_one(
        Performer,
        aliases__contains=account.username
    )
    if performer:
        return performer

    # Try by URL (last resort)
    return await self.context.store.find_one(
        Performer,
        url__contains=fansly_url
    )
```

**Key improvement:** `store.get_or_create()` signature:

```python
store.get_or_create(
    entity_type: type[T],
    create_if_missing: bool = True,
    **search_params: Any  # Search criteria + creation fields
) -> T
```

**Benefits:**

- Cleaner filter syntax (no manual dict construction)
- Identity map prevents duplicate fetches
- Automatic caching reduces API calls
- Type-safe with IDE autocomplete

---

### Pattern 2: Use `store.get()` for Single Entity Lookups

**Priority:** 🔴 HIGH - Leverages identity map caching

#### Current Code (account.py:300-314)

```python
async def _find_existing_performer(self, account: Account) -> Performer | None:
    """Find performer by stash_id or username."""
    if account.stash_id:
        try:
            # ❌ Bypasses identity map
            performer_data = await self.context.client.find_performer(account.stash_id)
            return performer_data
        except Exception:
            pass

    # Fallback to username search
    performer_data = await self.context.client.find_performer(account.username)
    return performer_data
```

#### Migrated Code

```python
async def _find_existing_performer(self, account: Account) -> Performer | None:
    """Find performer by stash_id or username."""
    if account.stash_id:
        try:
            # ✅ Uses identity map - instant return if cached
            performer = await self.context.store.get(Performer, account.stash_id)
            if performer:
                return performer
        except Exception:
            pass

    # Fallback to username search (also checks cache)
    return await self.context.store.find_one(
        Performer,
        name=account.username
    )
```

**Benefits:**

- Identity map returns cached object instantly
- No duplicate network requests for same ID
- Subsequent gets are O(1) dictionary lookups

---

### Pattern 3: Eliminate Manual Cache Invalidation

**Priority:** 🔴 HIGH - Reduces brittleness

#### Current Code (studio.py:132-143)

```python
try:
    studio = await self.context.client.create_studio(studio)
    return studio
except Exception as e:
    print_error(f"Failed to create studio: {e}")
    logger.exception("Failed to create studio", exc_info=e)

    # ❌ Manual cache invalidation (accessing private API)
    Studio._store.invalidate_type(Studio)

    # Re-query after invalidation
    studio_data = await self.context.client.find_studios(q=creator_studio_name)
    if studio_data.count == 0:
        return None
    return studio_data.studios[0]
```

---User Note: ✅ IMPLEMENTED — but uses `find_one()+save()` not `get_or_create()` as the migrated example suggests. Actual code at `studio.py:103` uses `store.find_one(Studio, name=...)` then `store.save(studio)`. Note: `studio.py:142-149` contains dead code (unreachable after `return studio` on line 140), and the docstring at line 64 inaccurately says "get_or_create".

#### Migrated Code

```python
try:
    # ✅ Store handles conflicts automatically
    studio = await self.context.store.create(
        Studio,
        name=creator_studio_name,
        parent_studio=fansly_studio,
        urls=[f"https://fansly.com/{account.username}"],
        performers=[performer] if performer else [],
    )
    return studio
except Exception as e:
    # Check if it's a "already exists" error
    if "already exists" in str(e):
        # ✅ Store automatically refreshes cache on conflict
        return await self.context.store.find_one(
            Studio,
            name=creator_studio_name
        )
    raise
```

**Benefits:**

- No manual cache invalidation needed
- Library handles cache coherency
- Cleaner error handling
- Race conditions handled automatically

---

### Pattern 4: Batch Operations for Tags

**Priority:** 🔴 HIGH - 90% reduction in API calls

#### Current Code (tag.py:32-84)

```python
async def _process_hashtags_to_tags(
    self,
    hashtags: list[Any],
) -> list[Tag]:
    """Process hashtags into Stash tags."""
    tags = []

    # ❌ N * 2 API calls (N hashtags × 2 searches each)
    for hashtag in hashtags:
        tag_name = hashtag.value.lower()
        found_tag = None

        # Query 1: By name
        name_results = await self.context.client.find_tags(
            tag_filter={"name": {"value": tag_name, "modifier": "EQUALS"}},
        )
        if name_results.count > 0:
            found_tag = name_results.tags[0]
        else:
            # Query 2: By alias
            alias_results = await self.context.client.find_tags(
                tag_filter={"aliases": {"value": tag_name, "modifier": "INCLUDES"}},
            )
            if alias_results.count > 0:
                found_tag = alias_results.tags[0]

        if found_tag:
            tags.append(found_tag)
        else:
            # Create new tag
            new_tag = Tag(name=tag_name, id="new")
            created_tag = await self.context.client.create_tag(new_tag)
            tags.append(created_tag)

    return tags
```

---User Note: ✅ IMPLEMENTED — uses parallel `get_or_create()` with `asyncio.gather()` in `tag.py`. 90%+ reduction in API calls achieved.

#### Migrated Code

```python
async def _process_hashtags_to_tags(
    self,
    hashtags: list[Any],
) -> list[Tag]:
    """Process hashtags into Stash tags."""
    tag_names = [h.value.lower() for h in hashtags]
    tags = []

    # ✅ Batch fetch all tags at once
    existing_tags = await self.context.store.find(
        Tag,
        # Assuming store.find() supports OR queries for batching
        # Otherwise, fetch all tags and filter in-memory
    )

    # ✅ Build lookup dict from existing tags
    tag_lookup = {}
    for tag in existing_tags:
        tag_lookup[tag.name.lower()] = tag
        if tag.aliases:
            for alias in tag.aliases:
                tag_lookup[alias.lower()] = tag

    # ✅ Create only missing tags in batch
    missing_names = [name for name in tag_names if name not in tag_lookup]
    if missing_names:
        # Batch create (if library supports)
        new_tags = await asyncio.gather(*[
            self.context.store.create(Tag, name=name)
            for name in missing_names
        ])
        for tag in new_tags:
            tag_lookup[tag.name.lower()] = tag

    # ✅ Return tags in order
    return [tag_lookup[name] for name in tag_names if name in tag_lookup]
```

**Benefits:**

- From N×2 queries to 1-2 queries total
- 90%+ reduction in API calls for tagging
- In-memory lookup after initial fetch
- Identity map prevents duplicate tag objects

---

### Pattern 5: Simplify Filter Construction

**Priority:** 🟡 MEDIUM - Improves maintainability

#### Current Code (media.py:98-141)

```python
def _create_nested_path_or_conditions(
    self,
    media_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Create nested OR conditions for path filters.

    ❌ 40+ lines of manual nested dict construction
    """
    if len(media_ids) == 1:
        return {
            "path": {
                "modifier": "INCLUDES",
                "value": media_ids[0],
            }
        }

    # For multiple IDs, create nested structure
    result = {
        "path": {
            "modifier": "INCLUDES",
            "value": media_ids[0],
        }
    }

    # Add remaining conditions as nested OR
    for media_id in media_ids[1:]:
        result = {
            "OR": {
                "path": {
                    "modifier": "INCLUDES",
                    "value": media_id,
                },
                "OR": result,
            }
        }

    return result
```

---User Note: ✅ IMPLEMENTED — uses `path__regex` via `store.find_iter()` in `media.py`. Regex replaced the 40-line nested OR builder entirely.

#### Migrated Code (Option A: Django-style)

```python
async def _find_images_by_paths(
    self,
    media_ids: Sequence[str],
) -> list[Image]:
    """Find images by media IDs in path."""
    # ✅ Let store handle OR logic (if supported)
    images = await self.context.store.find(
        Image,
        path__in=media_ids  # Django-style "IN" operator
    )
    return images
```

#### Migrated Code (Option B: Fetch all then filter in-memory)

```python
async def _find_images_by_paths(
    self,
    media_ids: Sequence[str],
) -> list[Image]:
    """Find images by media IDs in path."""
    # ✅ Fetch all images once, then filter in-memory
    # (Only viable if total image count is manageable)
    all_images = await self.context.store.find(Image)

    # ✅ Filter in-memory (no additional queries)
    return self.context.store.filter(
        Image,
        lambda img: any(mid in img.path for mid in media_ids)
    )
```

**Benefits:**

- 40 lines → 3-10 lines
- Type-safe filter construction
- No manual dict nesting
- Easier to read and maintain

---

--User Note: ✅ CONFIRMED — automatic dirty tracking in v0.10.4+ means no explicit UNSET needed. Library only saves changed fields.

### Pattern 6: Use UNSET for Partial Updates

**Priority:** 🟡 MEDIUM - Prevents accidental overwrites

#### Current Code (media.py:620-644)

```python
async def _update_stash_metadata(...):
    # ❌ Updates all fields, even if some weren't loaded
    stash_obj.title = self._generate_title_from_content(...)
    stash_obj.details = item.content
    stash_obj.date = item_date.strftime("%Y-%m-%d")
    stash_obj.code = str(media_id)

    # ❌ What if organized field wasn't loaded? This overwrites it!
    await stash_obj.save(self.context.client)
```

#### Migrated Code

```python
from stash_graphql_client.types import UNSET

async def _update_stash_metadata(...):
    # ✅ Only update fields we explicitly want to change
    stash_obj.title = self._generate_title_from_content(...)
    stash_obj.details = item.content
    stash_obj.date = item_date.strftime("%Y-%m-%d")
    stash_obj.code = str(media_id)

    # ✅ Don't touch fields we didn't load
    if not hasattr(stash_obj, '_received_fields') or 'organized' not in stash_obj._received_fields:
        stash_obj.organized = UNSET  # Preserves server value

    # ✅ Save sends only changed fields
    await stash_obj.save(self.context.client)
    # Mutation: { id: "123", title: "...", details: "...", date: "...", code: "..." }
    # The 'organized' field is NOT in the mutation (preserved)
```

**Benefits:**

- Prevents accidental data overwrites
- Explicit about what changes
- Safe partial updates
- Race condition prevention

---

--User Note: ✅ IMPLEMENTED — uses `add_performer()`, `add_tag()`, `set_studio()` relationship helpers in production code.

### Pattern 7: Use Relationship Helpers

**Priority:** 🟡 MEDIUM - Cleaner code, automatic sync

#### Current Code (media.py:661-734)

```python
async def _update_stash_metadata(...):
    # ❌ Manual performer list management
    performers = []
    if main_performer := await self._find_existing_performer(account):
        performers.append(main_performer)

    # ❌ Manual mention processing with race condition handling
    if mentions:
        for mention in mentions:
            mention_performer = await self._find_existing_performer(mention)

            if not mention_performer:
                try:
                    mention_performer = self._performer_from_account(mention)
                    await mention_performer.save(self.context.client)
                except Exception as e:
                    # String matching on error message
                    if "performer with name" in str(e) and "already exists" in str(e):
                        mention_performer = await self._find_existing_performer(mention)
                        if not mention_performer:
                            raise
                    else:
                        raise

            if mention_performer:
                performers.append(mention_performer)

    # ❌ Manual assignment (no bidirectional sync)
    if performers:
        stash_obj.performers = performers

    # ❌ Manual studio assignment
    if studio := await self._find_existing_studio(account):
        stash_obj.studio = studio

    # ❌ Tag overwrite (comment notes this is wrong)
    if tags:
        stash_obj.tags = tags  # Overwrites existing tags!

    await stash_obj.save(self.context.client)
```

#### Migrated Code

```python
async def _update_stash_metadata(...):
    # ✅ Add main performer (bidirectional sync)
    if main_performer := await self._find_existing_performer(account):
        await stash_obj.add_performer(main_performer)
        # main_performer.scenes automatically updated!

    # ✅ Add mentioned performers with simplified creation
    if mentions:
        for mention in mentions:
            # Get or create in one helper
            mention_performer = await self._get_or_create_performer(mention)
            await stash_obj.add_performer(mention_performer)
            # Automatic bidirectional sync

    # ✅ Set studio (relationship helper)
    if studio := await self._find_existing_studio(account):
        stash_obj.studio = studio
        # Or: await stash_obj.set_studio(studio) if helper exists

    # ✅ Add tags without overwriting existing
    if tags:
        for tag in tags:
            await stash_obj.add_tag(tag)
            # Preserves existing tags, adds new ones

    await stash_obj.save(self.context.client)
```

**Benefits:**

- Bidirectional relationship sync automatic
- No manual list management
- Cleaner tag addition (no overwrite)
- Race conditions handled by store

---

### Pattern 8: Eliminate Manual Retry Logic

**Priority:** 🟢 LOW - Library may handle retries internally

#### Current Code (gallery.py:681-747)

```python
if all_images:
    images_added_successfully = False
    last_error = None

    # ❌ Manual retry loop with exponential backoff
    for attempt in range(3):
        try:
            success = await self.context.client.add_gallery_images(
                gallery_id=gallery.id,
                image_ids=[img.id for img in all_images],
            )
            if success:
                images_added_successfully = True
                break

            if attempt < 2:
                await asyncio.sleep(2**attempt)
        except Exception as e:
            last_error = e
            logger.exception(f"Failed to add gallery images (attempt {attempt + 1}/3)")
            if attempt < 2:
                await asyncio.sleep(2**attempt)

    if not images_added_successfully:
        print_error(f"Failed to add {len(all_images)} images after 3 attempts")
```

#### Migrated Code

```python
# ✅ Rely on library's built-in retry logic
# (HTTPXAsyncTransport has automatic retry with backoff)
try:
    success = await self.context.client.add_gallery_images(
        gallery_id=gallery.id,
        image_ids=[img.id for img in all_images],
    )
    if not success:
        logger.error(f"Failed to add {len(all_images)} images to gallery")
except Exception as e:
    logger.exception(f"Error adding images to gallery: {e}")
    raise
```

**Benefits:**

- Less code to maintain
- Consistent retry behavior across codebase
- Library handles transient failures automatically
- Exponential backoff with jitter built-in

---

## Phased Migration Plan

### Phases 1-2: Foundation + High-Impact Wins ✅ **COMPLETE** (2026-01-09)

Updated to stash-graphql-client v0.10.4 (Pydantic models). Key changes:

- Added `store` property to `StashProcessingBase` for ORM access
- Tag processing: N×2 queries → N parallel `get_or_create()` (90%+ reduction)
- Performer lookup: 3 sequential queries → identity map cached lookups
- Studio creation: Manual cache invalidation removed, race conditions handled
- All core entity lookups use identity map
- Files: `tag.py`, `account.py`, `studio.py`, `gallery.py`, `base.py`, `pyproject.toml`, factory fixtures

---

### Phase 3: v0.11 Upgrade + Cache Optimization ✅ **COMPLETE** (2026-03-07)

**Goal:** Upgrade to v0.11, fix breaking changes, optimize cache for long-running batch processing

**Completed:**

- ✅ Upgraded dependency to `stash-graphql-client>=0.11.0b5`
- ✅ Fixed breaking change: `gallery.destroy()` → `gallery.delete()` (auto-invalidates cache)
- ✅ Two-tier cache strategy: global preload (TTL=None) + per-creator preload/invalidate
- ✅ Added `capabilities` property for version-aware processing
- ✅ Added `StashVersionError` handling at initialization (minimum Stash v0.30.0)
- ✅ Added warning filters for v0.11 `DeprecationWarning` and `StashUnmappedFieldWarning`
- ✅ Informational logging for `GenerateMetadataInput.paths` capability
- ✅ Updated respx test fixtures with v0.11 capability detection mock
- ✅ `stash/processing/mixins/media.py` - Updated for identity map patterns

**v0.11 Breaking Changes Addressed:**

- `gallery.destroy(client)` → `gallery.delete(client)` — renamed entity lifecycle method (auto-invalidates cache)
- `set_ttl(type, seconds)` → `set_ttl(type, timedelta|int|None)` — TTL accepts None for no expiry
- `clear_type()` → `invalidate_type()` — renamed for clarity

**Files:**

- ✅ `pyproject.toml` — Version bump
- ✅ `stash/processing/base.py` — TTL, capabilities, error handling, warning filters
- ✅ `stash/processing/__init__.py` — Per-creator preload + invalidate lifecycle
- ✅ `stash/processing/mixins/gallery.py` — `destroy()` → `delete()`
- ✅ `tests/fixtures/stash/stash_api_fixtures.py` — Capability detection mock
- ✅ `tests/fixtures/stash/stash_integration_fixtures.py` — Capability detection mock

---

### Phase 4: Advanced Features ⏸️ **NOT STARTED**

**Goal:** Leverage advanced ORM features

**Planned Tasks:**

1. ⏳ Use `store.filter()` for in-memory filtering
2. ⏳ Expand relationship helper usage (`add_performer`, `add_tag`, etc.)
3. ⏳ Remove any remaining manual retry logic
4. ⏳ Consider preloading relationships for complex queries

**Files:**

- All processing mixins

**Success Criteria:**

- ⏳ Maximum leverage of ORM features
- ⏳ Minimal manual state management
- ⏳ Clean, maintainable codebase

---

## Testing Strategy

### Unit Tests

**Update fixture usage:**

```python
# Before
@pytest.fixture
async def mock_client(respx_stash_processor):
    return respx_stash_processor.context.client

# After - also provide store
@pytest.fixture
async def stash_store(respx_stash_processor):
    return respx_stash_processor.context.store

@pytest.fixture
async def stash_client(respx_stash_processor):
    return respx_stash_processor.context.client
```

**Test identity map:**

```python
@pytest.mark.asyncio
async def test_identity_map_deduplication(stash_store):
    """Verify same ID returns same object instance."""
    performer1 = await stash_store.get(Performer, "123")
    performer2 = await stash_store.get(Performer, "123")

    assert performer1 is performer2  # Same object instance

    # Update one, reflected in both
    performer1.name = "Updated Name"
    assert performer2.name == "Updated Name"
```

**Test UNSET pattern:**

```python
@pytest.mark.asyncio
async def test_unset_preserves_fields(stash_store):
    """Verify UNSET doesn't overwrite server values."""
    from stash_graphql_client.types import UNSET

    scene = await stash_store.get(Scene, "123")

    # Only update specific fields
    scene.title = "New Title"
    scene.organized = UNSET  # Don't touch this field

    # Mock save to verify mutation
    with patch.object(scene, 'save') as mock_save:
        await scene.save(client)

        # Verify 'organized' not in mutation
        call_args = mock_save.call_args
        # Assert mutation only includes changed fields
```

### Integration Tests

**Add performance benchmarks:**

```python
@pytest.mark.integration
async def test_tag_batching_performance():
    """Verify tag batching reduces API calls."""
    hashtags = [create_hashtag(f"tag{i}") for i in range(20)]

    with count_api_calls() as counter:
        tags = await processor._process_hashtags_to_tags(hashtags)

    # Before migration: ~40 API calls (20 tags × 2 queries each)
    # After migration: ~2-3 API calls (1 batch fetch + 1 batch create)
    assert counter.total_calls <= 5
    assert len(tags) == 20
```

**Test cache coherency:**

```python
@pytest.mark.integration
async def test_identity_map_coherency():
    """Verify identity map keeps objects synchronized."""
    # Create performer
    performer = await store.create(Performer, name="Test")

    # Fetch scene that includes this performer
    scene = await store.find_one(Scene, code="test-scene")

    # Verify same performer object
    assert scene.performers[0] is performer

    # Update performer
    performer.name = "Updated"

    # Verify update reflected in scene
    assert scene.performers[0].name == "Updated"
```

---

## Success Metrics

### Performance Metrics

- **API call reduction:** Target 50-70% fewer calls
- **Response time:** 30-50% faster processing (less network overhead)
- **Memory usage:** Slight increase due to identity map caching

### Code Quality Metrics

- **Lines of code:** Target 20-30% reduction
- **Cyclomatic complexity:** Reduce by simplifying filter logic
- **Code duplication:** Eliminate repeated filter construction

### Monitoring

**Add logging to track migration progress:**

```python
# In each migrated method, add:
logger.debug(f"Using store.find() for {entity_type.__name__} - migration complete")
```

**Before/after comparison:**

```bash
# Count API calls
grep "GraphQL query" logs/processing.log | wc -l

# Before: ~150-200 calls per run
# After: ~50-100 calls per run
```

---

> **Library API reference** (StashEntityStore methods, relationship helpers, cheat sheet) — see the [stash-graphql-client docs](https://jakan-kink.github.io/stash-graphql-client/latest/).

---

## Optimization Patterns Analysis (from v0.10.0 Migration Plan)

> _Merged from `V0_10_MIGRATION_PLAN.md` — documents optimization patterns evaluated during v0.10.x adoption._

### Sequential Deduplication (account.py)

**Status:** ✅ **IMPLEMENTED** — Using identity map with `find_one()`

Current implementation uses `store.find_one()` which leverages the identity map for caching. The sequential deduplication logic (check name → alias → URL) is preserved because it represents the actual business requirement.

**Alternative cache-first pattern (for future consideration):**

```python
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
```

**Decision:** Current pattern is optimal. Identity map caching reduces repeated lookups. In-memory filtering could be added in Phase 4 if needed.

### Studio Lookup Caching

**Status:** ✅ **IMPLEMENTED** — Identity map handles caching

The Fansly studio is fetched once via `store.find_one()` and cached automatically by the identity map. No explicit caching initialization needed.

### Parallel Tag Creation

**Status:** ✅ **IMPLEMENTED** — Optimal pattern already in use

```python
tag_tasks = [self.store.get_or_create(Tag, name=name) for name in tag_names]
tags = await asyncio.gather(*tag_tasks, return_exceptions=True)
```

Each `get_or_create()` checks identity map cache first, only queries Stash if not cached, and runs in parallel with `asyncio.gather()`.

### Large Dataset Iteration

**Status:** ⏸️ **DEFERRED** — Not needed for current performance

Current `store.find_iter()` pattern performs well. A future optimization could use `populated_filter_iter()` for lazy iteration with field-aware fetching, but profiling hasn't identified this as a bottleneck.

### Performance Improvements Summary

| Operation         | Before                            | After (v0.10.4+)                               | Reduction                |
| ----------------- | --------------------------------- | ---------------------------------------------- | ------------------------ |
| Tag Processing    | N tags × 2 queries = 2N API calls | N parallel `get_or_create()` with identity map | **90%+**                 |
| Performer Lookups | 3 sequential GraphQL queries      | Identity map cached lookups                    | **60-80%** on cache hits |
| Studio Lookups    | 1 GraphQL query per creator       | 1 query total, identity map cached             | **N-1 queries saved**    |

### Key v0.10.4 Changes

- **Pydantic Models:** Fully typed models from all store methods (not Strawberry)
- **Automatic UNSET:** Fields not assigned = automatic UNSET (no explicit import needed)
- **UUID Auto-Generation:** Pydantic models auto-generate temp UUIDs on creation
- **Identity Map:** Same ID = same object instance, with configurable TTL

### References

- [Official Documentation](https://jakan-kink.github.io/stash-graphql-client/latest/)
- [CHANGELOG (All Releases)](https://github.com/Jakan-Kink/stash-graphql-client/blob/main/CHANGELOG.md)
- [Advanced Filtering Guide](https://jakan-kink.github.io/stash-graphql-client/latest/guide/advanced-filtering/)
- [Architecture Overview](https://jakan-kink.github.io/stash-graphql-client/latest/architecture/overview/)

---

## Questions & Support

**For questions about this migration:**

1. Check stash-graphql-client docs: https://jakan-kink.github.io/stash-graphql-client/latest/
2. Review CHANGELOG for all release notes: https://github.com/Jakan-Kink/stash-graphql-client/blob/main/CHANGELOG.md
3. Test changes incrementally
4. Keep detailed logs during migration

**Migration started:** 2025-12-XX (exact date TBD)
**Current phase:** Phase 4 - Advanced Features ⏸️ Not Started
**Phase 1 & 2 completed:** 2026-01-09
**Phase 3 completed:** 2026-03-07 (v0.11 upgrade)
**Current version:** v0.11.0b5
