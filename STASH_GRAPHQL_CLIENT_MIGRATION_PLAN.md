# Stash GraphQL Client Migration Plan

**Branch**: `feature/convert-to-stash-graphql-client`
**Library**: `stash-graphql-client` v0.5.0b2
**Date**: 2025-12-21

## Executive Summary

This migration replaces the custom Stash GraphQL integration (types, client, fragments) with the `stash-graphql-client` library while preserving the high-level business logic in `stash/processing/`.

**Key Benefits:**
- ✅ Replace ~2000 lines of custom code with maintained library
- ✅ Better type safety (Pydantic validation vs manual Strawberry)
- ✅ Entity caching via `StashEntityStore`
- ✅ Reference counting for concurrent task safety
- ✅ GraphQL subscriptions support
- ✅ Versioned dependency on PyPI

**Scope:**
- **Replace**: `stash/types/`, `stash/client/`, `stash/fragments.py`, `stash/context.py`
- **Keep & Adapt**: `stash/processing/`, `stash/logging.py`
- **Effort Estimate**: Medium (2-3 days)

---

## Table of Contents

1. [Current vs New Architecture](#current-vs-new-architecture)
2. [Migration Phases](#migration-phases)
3. [Detailed Phase Instructions](#detailed-phase-instructions)
4. [File-by-File Changes](#file-by-file-changes)
5. [Type System Migration Guide](#type-system-migration-guide)
6. [Testing Strategy](#testing-strategy)
7. [Rollback Plan](#rollback-plan)
8. [Success Criteria](#success-criteria)

---

## Current vs New Architecture

### Before (Custom Implementation)

```
stash/
├── __init__.py
├── types/                    # DELETE - Strawberry types
│   ├── base.py
│   ├── config.py
│   ├── enums.py
│   ├── files.py
│   ├── gallery.py
│   ├── image.py
│   ├── performer.py
│   ├── scene.py
│   ├── studio.py
│   ├── tag.py
│   └── ...
├── client/                   # DELETE - Custom GraphQL client
│   ├── base.py
│   ├── mixins/
│   │   ├── gallery.py
│   │   ├── performer.py
│   │   ├── scene.py
│   │   ├── studio.py
│   │   └── tag.py
│   ├── protocols.py
│   └── utils.py
├── fragments.py              # DELETE - Custom fragments
├── context.py                # DELETE - Custom context
├── logging.py                # KEEP - Logging utilities
└── processing/               # KEEP & ADAPT - Business logic
    ├── __init__.py
    ├── base.py
    └── mixins/
        ├── account.py
        ├── background.py
        ├── content.py
        ├── gallery.py
        ├── media.py
        ├── performer.py
        ├── studio.py
        └── tag.py
```

### After (Library-based)

```
stash/
├── __init__.py               # UPDATED - Import from library
├── logging.py                # KEEP - Logging utilities
└── processing/               # ADAPTED - Business logic
    ├── __init__.py
    ├── base.py               # UPDATED - Use library's StashContext
    └── mixins/               # UPDATED - Use library's types
        ├── account.py
        ├── background.py
        ├── content.py
        ├── gallery.py
        ├── media.py
        ├── performer.py
        ├── studio.py
        └── tag.py

# External dependency (from PyPI)
stash_graphql_client/
├── types/                    # Pydantic models
├── client/                   # gql-based client
├── fragments.py              # GraphQL fragments
├── context.py                # StashContext with ref counting
└── store.py                  # Entity caching
```

**Lines of Code Impact:**
- **Deleted**: ~2000 lines (stash/types/, stash/client/, stash/fragments.py)
- **Modified**: ~500 lines (stash/processing/)
- **Net Reduction**: ~1500 lines

---

## Migration Phases

### Phase 0: Preparation ✅
- [x] Review library documentation
- [x] Compare API compatibility
- [x] Create migration plan

### Phase 1: Dependency Setup
- [ ] Add `stash-graphql-client` to `pyproject.toml`
- [ ] Install and verify library
- [ ] Run initial tests to establish baseline

### Phase 2: Import Layer Migration
- [ ] Update `stash/__init__.py` to re-export library types
- [ ] Create compatibility shims if needed
- [ ] Update all import statements in `stash/processing/`

### Phase 3: Type System Conversion
- [ ] Replace Strawberry → Pydantic patterns
- [ ] Update `from_dict()` calls to Pydantic constructors
- [ ] Fix type annotations
- [ ] Handle special cases (e.g., `visual_files` union types)

### Phase 4: Context Migration
- [ ] Update `StashProcessingBase` to use library's `StashContext`
- [ ] Test context lifecycle (initialization, cleanup)
- [ ] Verify concurrent task safety with reference counting

### Phase 5: Processing Mixins
- [ ] Update `media.py` mixin
- [ ] Update `studio.py` mixin
- [ ] Update `gallery.py` mixin
- [ ] Update `performer.py` mixin
- [ ] Update `tag.py` mixin
- [ ] Update other mixins as needed

### Phase 6: Cleanup
- [ ] Delete `stash/types/`
- [ ] Delete `stash/client/`
- [ ] Delete `stash/fragments.py`
- [ ] Delete `stash/context.py`
- [ ] Update `.gitignore` if needed

### Phase 7: Testing & Validation
- [ ] Run unit tests
- [ ] Run integration tests
- [ ] Manual testing with real Stash instance
- [ ] Performance comparison (before/after)

### Phase 8: Documentation
- [ ] Update README
- [ ] Update CLAUDE.md
- [ ] Add migration notes to CHANGELOG

---

## Detailed Phase Instructions

### Phase 1: Dependency Setup

**1.1 Add library to dependencies**

```bash
cd ~/Developer/fansly-worktrees/stashqlclient
poetry add stash-graphql-client
```

**1.2 Verify installation**

```bash
poetry run python -c "from stash_graphql_client import StashClient, StashContext; print('✓ Library imported successfully')"
```

**1.3 Check library version**

```bash
poetry show stash-graphql-client
```

Expected output: `stash-graphql-client 0.5.0b2` or newer

---

### Phase 2: Import Layer Migration

**2.1 Update `stash/__init__.py`**

**Before:**
```python
# stash/__init__.py
from .client import StashClient
from .context import StashContext
from .types import (
    Gallery,
    Image,
    Performer,
    Scene,
    Studio,
    Tag,
)

__all__ = [
    "StashClient",
    "StashContext",
    "Gallery",
    "Image",
    "Performer",
    "Scene",
    "Studio",
    "Tag",
]
```

**After:**
```python
# stash/__init__.py
"""Stash integration module.

This module provides high-level processing logic for interacting with
Stash media server using the stash-graphql-client library.
"""

# Re-export library types and client for backwards compatibility
from stash_graphql_client import (
    StashClient,
    StashContext,
    # Core types
    Gallery,
    GalleryCreateInput,
    GalleryUpdateInput,
    Image,
    Performer,
    PerformerCreateInput,
    PerformerUpdateInput,
    Scene,
    SceneCreateInput,
    SceneUpdateInput,
    Studio,
    StudioCreateInput,
    StudioUpdateInput,
    Tag,
    TagCreateInput,
    TagUpdateInput,
    # Base types
    StashObject,
    BulkUpdateIds,
    BulkUpdateStrings,
    # File types
    ImageFile,
    VideoFile,
)

# Keep local modules
from .logging import debug_print, processing_logger
from .processing import StashProcessing

__all__ = [
    # Client & Context
    "StashClient",
    "StashContext",
    # Core types
    "Gallery",
    "GalleryCreateInput",
    "GalleryUpdateInput",
    "Image",
    "Performer",
    "PerformerCreateInput",
    "PerformerUpdateInput",
    "Scene",
    "SceneCreateInput",
    "SceneUpdateInput",
    "Studio",
    "StudioCreateInput",
    "StudioUpdateInput",
    "Tag",
    "TagCreateInput",
    "TagUpdateInput",
    # Base types
    "StashObject",
    "BulkUpdateIds",
    "BulkUpdateStrings",
    # File types
    "ImageFile",
    "VideoFile",
    # Local modules
    "StashProcessing",
    "debug_print",
    "processing_logger",
]
```

**2.2 Update imports in `stash/processing/base.py`**

**Before:**
```python
from ..context import StashContext
from ..logging import debug_print
from ..logging import processing_logger as logger
```

**After:**
```python
from stash_graphql_client import StashContext
from ..logging import debug_print
from ..logging import processing_logger as logger
```

**2.3 Search and replace all imports**

```bash
# Find all files importing from stash.types or stash.client
cd ~/Developer/fansly-worktrees/stashqlclient
grep -r "from stash.types import" stash/processing/
grep -r "from stash.client import" stash/processing/
grep -r "from ..types import" stash/processing/
grep -r "from ..client import" stash/processing/
```

Replace with:
```python
from stash_graphql_client import Gallery, Performer, Scene, Studio, Tag, Image
```

---

### Phase 3: Type System Conversion

#### 3.1 Strawberry → Pydantic Patterns

**Pattern 1: Object Construction**

**Before (Strawberry):**
```python
from stash.types import Gallery

# Manual from_dict deserialization
gallery_data = {"id": "123", "title": "Test"}
gallery = Gallery.from_dict(gallery_data)
```

**After (Pydantic):**
```python
from stash_graphql_client import Gallery

# Pydantic automatic validation
gallery_data = {"id": "123", "title": "Test"}
gallery = Gallery(**gallery_data)
# Or: gallery = Gallery.model_validate(gallery_data)
```

**Pattern 2: Serialization**

**Before (Strawberry):**
```python
# Manual serialization or custom to_dict()
data = {
    "id": gallery.id,
    "title": gallery.title,
    # ... manual field copying
}
```

**After (Pydantic):**
```python
# Automatic serialization
data = gallery.model_dump()
# Or with exclusions: gallery.model_dump(exclude={"internal_field"})
```

**Pattern 3: Type Checking**

**Before (Strawberry):**
```python
from stash.types import ImageFile, VideoFile

if hasattr(file_data, "__type_name__") and file_data.__type_name__ == "ImageFile":
    # Handle ImageFile
```

**After (Pydantic):**
```python
from stash_graphql_client import ImageFile, VideoFile

if isinstance(file_data, ImageFile):
    # Handle ImageFile
elif isinstance(file_data, VideoFile):
    # Handle VideoFile
```

#### 3.2 Special Case: `visual_files` Union Type

The library already handles `ImageFile | VideoFile` unions correctly!

**Before (custom logic in `stash/processing/mixins/media.py`):**
```python
# Complex logic to detect VideoFile vs ImageFile based on fields
is_video_file = any(
    field in file_data
    for field in ["format", "duration", "video_codec"]
)
if is_video_file:
    file = VideoFile(**file_data)
else:
    file = ImageFile(**file_data)
```

**After (library handles this):**
```python
# Library's Image type already handles visual_files union
# Just use the Image object directly
image = Image(**image_data)
for visual_file in image.visual_files:
    if isinstance(visual_file, VideoFile):
        # Handle animated GIF (VideoFile)
        pass
    elif isinstance(visual_file, ImageFile):
        # Handle static image
        pass
```

#### 3.3 Update Type Annotations

**Before:**
```python
from stash.types import Gallery, Performer, Studio

async def process_gallery(gallery: Gallery) -> Gallery:
    ...
```

**After:**
```python
from stash_graphql_client import Gallery, Performer, Studio

async def process_gallery(gallery: Gallery) -> Gallery:
    ...
```

No changes needed to function signatures! Just update the import source.

---

### Phase 4: Context Migration

#### 4.1 Update `StashProcessingBase.__init__`

**Before:**
```python
from stash.context import StashContext

class StashProcessingBase:
    def __init__(
        self,
        config: FanslyConfig,
        state: DownloadState,
        context: StashContext,
        database: Database,
        # ...
    ):
        self.context = context
```

**After:**
```python
from stash_graphql_client import StashContext

class StashProcessingBase:
    def __init__(
        self,
        config: FanslyConfig,
        state: DownloadState,
        context: StashContext,
        database: Database,
        # ...
    ):
        self.context = context
```

**No code changes needed!** The library's `StashContext` has the same API.

#### 4.2 Update `from_config` classmethod

**Before:**
```python
from stash.context import StashContext

@classmethod
def from_config(cls, config: FanslyConfig, state: DownloadState) -> Any:
    # Create StashContext
    stash_conn = {
        "Scheme": config.stash_scheme,
        "Host": config.stash_host,
        "Port": config.stash_port,
        "ApiKey": config.stash_api_key,
    }
    context = StashContext(conn=stash_conn)
    # ...
```

**After:**
```python
from stash_graphql_client import StashContext

@classmethod
def from_config(cls, config: FanslyConfig, state: DownloadState) -> Any:
    # Create StashContext (same API!)
    stash_conn = {
        "Scheme": config.stash_scheme,
        "Host": config.stash_host,
        "Port": config.stash_port,
        "ApiKey": config.stash_api_key,
    }
    context = StashContext(conn=stash_conn)
    # ...
```

**No changes needed!** Drop-in replacement.

#### 4.3 Verify Client Access Pattern

**Current pattern (should work as-is):**
```python
# In processing methods
client = self.context.client  # Or self.context.interface
studios = await client.find_studios(q="Fansly")
```

**Library supports both:**
```python
client = self.context.client      # Primary
# or
client = self.context.interface   # Alias (for backwards compat)
```

---

### Phase 5: Processing Mixins

#### 5.1 Media Mixin (`stash/processing/mixins/media.py`)

**Key Changes:**

1. **Import updates:**
   ```python
   # Before
   from stash.types import ImageFile, VideoFile, Image, Scene

   # After
   from stash_graphql_client import ImageFile, VideoFile, Image, Scene
   ```

2. **Remove custom `visual_files` handling:**
   The library's `Image` type already handles `ImageFile | VideoFile` unions.

   **Delete this method entirely:**
   ```python
   def _get_image_file_from_stash_obj(self, stash_obj: Image) -> ImageFile | VideoFile | None:
       # Delete ~100 lines of custom logic
   ```

   **Replace with:**
   ```python
   def _get_image_file_from_stash_obj(self, stash_obj: Image) -> ImageFile | VideoFile | None:
       """Extract ImageFile or VideoFile from Stash Image object.

       The library handles visual_files union types automatically.
       """
       if not stash_obj.visual_files:
           return None
       return stash_obj.visual_files[0]  # First visual file
   ```

3. **Update Pydantic patterns:**
   ```python
   # Before (Strawberry from_dict)
   image = Image.from_dict(image_data)

   # After (Pydantic constructor)
   image = Image(**image_data)
   ```

#### 5.2 Studio Mixin (`stash/processing/mixins/studio.py`)

**Key Changes:**

1. **Import updates:**
   ```python
   # Before
   from stash.types import Studio, Performer

   # After
   from stash_graphql_client import Studio, StudioCreateInput, Performer
   ```

2. **Update studio creation:**
   ```python
   # Before (Strawberry)
   studio = Studio(
       id="new",  # Special sentinel value
       name=creator_studio_name,
       parent_studio=fansly_studio,
       urls=[f"https://fansly.com/{account.username}"],
   )

   # After (Pydantic - same pattern works!)
   studio = Studio(
       id="new",  # Library respects this sentinel
       name=creator_studio_name,
       parent_studio=fansly_studio,
       urls=[f"https://fansly.com/{account.username}"],
   )
   ```

**Note:** The library uses the same `id="new"` pattern for creation!

#### 5.3 Gallery Mixin (`stash/processing/mixins/gallery.py`)

**Key Changes:**

1. **Import updates:**
   ```python
   # Before
   from stash.types import Gallery, GalleryCreateInput

   # After
   from stash_graphql_client import Gallery, GalleryCreateInput
   ```

2. **Pydantic validation benefits:**
   ```python
   # Pydantic will automatically validate required fields
   gallery = Gallery(
       id="new",
       title=title,
       # Missing required field will raise ValidationError automatically
   )
   ```

#### 5.4 Performer Mixin (`stash/processing/mixins/performer.py`)

**Key Changes:**

1. **Import updates:**
   ```python
   # Before
   from stash.types import Performer, PerformerCreateInput

   # After
   from stash_graphql_client import Performer, PerformerCreateInput
   ```

2. **FuzzyDate support (library feature!):**
   ```python
   # Library supports fuzzy dates out of the box
   performer = Performer(
       name=account.username,
       birthdate="1990",  # Year-only (fuzzy date)
   )
   ```

#### 5.5 Tag Mixin (`stash/processing/mixins/tag.py`)

**Key Changes:**

1. **Import updates:**
   ```python
   # Before
   from stash.types import Tag, TagCreateInput

   # After
   from stash_graphql_client import Tag, TagCreateInput
   ```

2. **No other changes needed** - Tag types are simple and compatible.

---

### Phase 6: Cleanup

#### 6.1 Delete Old Code

```bash
cd ~/Developer/fansly-worktrees/stashqlclient

# Remove old types directory
rm -rf stash/types/

# Remove old client directory
rm -rf stash/client/

# Remove old fragments
rm stash/fragments.py

# Remove old context
rm stash/context.py

# Verify deletions
git status
```

#### 6.2 Update `.gitignore` (if needed)

No changes needed - we're removing code, not adding ignored files.

#### 6.3 Update `pyproject.toml`

Verify `stash-graphql-client` is listed in dependencies:

```toml
[tool.poetry.dependencies]
python = "^3.12"
stash-graphql-client = "^0.5.0b2"
# ... other dependencies
```

---

## File-by-File Changes

### Files to Delete (8 files + 2 directories)

```
stash/types/__init__.py
stash/types/base.py
stash/types/config.py
stash/types/enums.py
stash/types/files.py
stash/types/gallery.py
stash/types/image.py
stash/types/performer.py
stash/types/scene.py
stash/types/studio.py
stash/types/tag.py
stash/types/scalars.py
stash/types/filters.py
stash/types/metadata.py
stash/types/markers.py
stash/types/job.py
stash/types/logging.py
stash/types/not_implemented.py
stash/types/group.py

stash/client/__init__.py
stash/client/base.py
stash/client/protocols.py
stash/client/utils.py
stash/client/mixins/gallery.py
stash/client/mixins/performer.py
stash/client/mixins/scene.py
stash/client/mixins/studio.py
stash/client/mixins/tag.py

stash/fragments.py
stash/context.py
```

### Files to Modify

| File | Changes | Complexity |
|------|---------|------------|
| `stash/__init__.py` | Re-export library types | Low |
| `stash/processing/__init__.py` | Update imports | Low |
| `stash/processing/base.py` | Update imports, context usage | Low |
| `stash/processing/mixins/account.py` | Update imports, type hints | Low |
| `stash/processing/mixins/background.py` | Update imports | Low |
| `stash/processing/mixins/content.py` | Update imports, type hints | Medium |
| `stash/processing/mixins/gallery.py` | Update imports, Pydantic patterns | Medium |
| `stash/processing/mixins/media.py` | Update imports, remove custom logic | High |
| `stash/processing/mixins/performer.py` | Update imports, type hints | Low |
| `stash/processing/mixins/studio.py` | Update imports, Pydantic patterns | Medium |
| `stash/processing/mixins/tag.py` | Update imports, type hints | Low |

**Estimated total modifications:** ~500 lines across 11 files

---

## Type System Migration Guide

### Quick Reference Table

| Pattern | Before (Strawberry) | After (Pydantic) |
|---------|---------------------|------------------|
| **Construction** | `Gallery.from_dict(data)` | `Gallery(**data)` |
| **Validation** | Manual checks | `Gallery.model_validate(data)` |
| **Serialization** | Manual or custom | `gallery.model_dump()` |
| **Type checking** | `__type_name__ == "ImageFile"` | `isinstance(obj, ImageFile)` |
| **Field access** | `obj.field` | `obj.field` (no change) |
| **Optional fields** | Manual defaults | Pydantic `Field(default=None)` |
| **Nested objects** | `from_dict` recursively | Automatic via Pydantic |

### Common Pitfalls

❌ **Don't:**
```python
# Don't use from_dict (Strawberry pattern)
gallery = Gallery.from_dict(data)

# Don't manually check __type_name__
if hasattr(obj, "__type_name__"):
    ...
```

✅ **Do:**
```python
# Use Pydantic constructor
gallery = Gallery(**data)

# Use isinstance for type checking
if isinstance(obj, ImageFile):
    ...
```

---

## Testing Strategy

### Phase 7.1: Unit Tests

**Run existing tests to verify compatibility:**

```bash
cd ~/Developer/fansly-worktrees/stashqlclient

# Run all stash-related unit tests
pytest tests/stash/ -v

# Run specific test categories
pytest tests/stash/processing/unit/ -v
pytest tests/stash/client/ -v  # These may need updates/removal
```

**Expected changes to tests:**

1. **Update imports:**
   ```python
   # Before
   from stash.types import Gallery, Studio

   # After
   from stash_graphql_client import Gallery, Studio
   ```

2. **Update factory patterns:**
   ```python
   # Before (Strawberry factories)
   gallery = GalleryFactory.build()

   # After (Pydantic factories)
   gallery = GalleryFactory.build()  # Same API if using factory_boy
   ```

3. **Mock boundaries:**
   - Mock `StashClient.execute()` return values (GraphQL dict responses)
   - Use real Pydantic objects (no need to mock them)

### Phase 7.2: Integration Tests

**Test with real Stash instance:**

```bash
# Set up test Stash instance (Docker)
docker run -d -p 9999:9999 stashapp/stash:latest

# Run integration tests
pytest tests/stash/integration/ -v

# Test specific workflows
pytest tests/stash/integration/test_stash_processing.py -v
```

**Test checklist:**
- [ ] Create performer
- [ ] Create studio with parent
- [ ] Create gallery with images
- [ ] Create scene with performers, studio, tags
- [ ] Update entities
- [ ] Delete entities
- [ ] Verify caching (if using `StashEntityStore`)

### Phase 7.3: Performance Testing

**Compare before/after performance:**

```python
import time
from stash_graphql_client import StashContext

async def benchmark():
    async with StashContext(conn={"Host": "localhost", "Port": 9999}) as client:
        start = time.time()

        # Find 100 studios
        for _ in range(100):
            await client.find_studios()

        duration = time.time() - start
        print(f"100 find_studios calls: {duration:.2f}s")
```

**Expected improvements:**
- Faster due to Pydantic's C extensions
- Better caching with `StashEntityStore`
- More efficient GraphQL queries (library optimizations)

---

## Rollback Plan

### If Migration Fails

**Option 1: Revert to fork-main**

```bash
cd ~/Developer/fansly-worktrees/stashqlclient
git reset --hard fork-main
git clean -fd
```

**Option 2: Keep library, revert processing changes**

```bash
# Keep stash-graphql-client dependency
# Revert only stash/processing/ changes
git checkout fork-main -- stash/processing/
```

**Option 3: Create compatibility shim**

If library incompatibilities are found, create a temporary adapter:

```python
# stash/compat.py
"""Compatibility shims for migration."""

from stash_graphql_client import Gallery as LibGallery

class Gallery(LibGallery):
    """Wrapper with backwards-compatible from_dict."""

    @classmethod
    def from_dict(cls, data: dict):
        """Strawberry-style from_dict for backwards compat."""
        return cls(**data)
```

---

## Success Criteria

### Must Have ✅

- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] No runtime errors with real Stash instance
- [ ] All `stash/types/` and `stash/client/` code removed
- [ ] Code passes ruff linting
- [ ] Code passes mypy type checking

### Should Have ✨

- [ ] Performance is equal or better than before
- [ ] Test coverage maintained or improved
- [ ] Documentation updated (README, CLAUDE.md)
- [ ] No new security warnings from bandit

### Nice to Have 🎁

- [ ] Entity caching enabled via `StashEntityStore`
- [ ] GraphQL subscriptions utilized
- [ ] Fuzzy date support used for performer birthdates
- [ ] Reference counting prevents resource leaks in concurrent tasks

---

## Migration Timeline

| Phase | Estimated Time | Dependencies |
|-------|----------------|--------------|
| Phase 1: Dependencies | 0.5 hours | None |
| Phase 2: Imports | 1 hour | Phase 1 |
| Phase 3: Type System | 3 hours | Phase 2 |
| Phase 4: Context | 1 hour | Phase 2, 3 |
| Phase 5: Mixins | 6 hours | Phase 3, 4 |
| Phase 6: Cleanup | 0.5 hours | Phase 5 |
| Phase 7: Testing | 4 hours | Phase 6 |
| Phase 8: Documentation | 1 hour | Phase 7 |
| **Total** | **17 hours (~2-3 days)** | |

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| API incompatibility | Low | High | Extensive testing, rollback plan |
| Type conversion bugs | Medium | Medium | Comprehensive unit tests |
| Performance regression | Low | Medium | Benchmark before/after |
| Test fixture breakage | Medium | Low | Update fixtures incrementally |
| Missing library features | Low | High | Review library docs thoroughly |

---

## Open Questions

- [ ] Do we need all entities from the library, or just core ones?
- [ ] Should we use `StashEntityStore` for caching immediately?
- [ ] Do we want to use GraphQL subscriptions for real-time updates?
- [ ] Should fuzzy dates be used for performer birthdates?
- [ ] Do we need to maintain any backwards compatibility shims?

---

## References

- [stash-graphql-client GitHub](https://github.com/Jakan-Kink/stash-graphql-client)
- [stash-graphql-client PyPI](https://pypi.org/project/stash-graphql-client/)
- [Stash GraphQL API Docs](https://github.com/stashapp/stash/blob/develop/graphql/schema/schema.graphql)
- [Pydantic Documentation](https://docs.pydantic.dev/)

---

**Next Steps:**

1. Review this plan with stakeholders
2. Set up development environment in `stashqlclient` worktree
3. Begin Phase 1: Dependency Setup
4. Proceed phase-by-phase with testing at each step

---

*Last Updated: 2025-12-21*
