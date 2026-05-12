---
status: current
---

# Architecture Reference

High-level architecture of Fansly Downloader NG as of v0.13.0. This
document is the canonical public reference for the module layout, core
design patterns, and cross-cutting concerns. Contributor-facing LLM
guidance lives alongside this in `.claude/CLAUDE.md` (not tracked in
the repo); that file references this one for architecture details
rather than duplicating them.

## High-Level Flow

1. **Configuration** (`config/`) — Load settings from `config.yaml`
   (legacy `config.ini` auto-migrated), command-line args, and browser
   tokens
2. **API** (`api/`) — Interact with Fansly API (HTTP + WebSocket) to
   fetch creator data
3. **Download** (`download/`) — Download media files with deduplication
4. **Metadata** (`metadata/`) — Store and manage metadata via Pydantic
   models + asyncpg EntityStore
5. **Stash** (`stash/`) — Optional integration to push metadata to a
   Stash media server
6. **Daemon** (`daemon/`) — Post-batch monitoring loop; WebSocket event
   dispatch + timeline/story polling

## Module Structure

### `config/` — Configuration management

- `fanslyconfig.py` — Main configuration class
- `schema.py` — Pydantic `ConfigSchema` + all section models (YAML source of truth)
- `loader.py` — YAML load + one-shot `.ini` → `.yaml` migration with backup
- `args.py` — Command-line argument parsing
- `browser.py` — Browser token extraction
- `modes.py` — Download mode enums
- `validation.py` — Config validation and adjustment

### `api/` — Fansly API client

- `fansly.py` — HTTP API wrapper with rate limiting and session management
- `websocket.py` — `FanslyWebSocket` — bidirectional WS client with cookie
  sync and event handler registration
- `rate_limiter.py` / `rate_limiter_display.py` — Token-bucket rate
  limiter + Rich progress display

### `daemon/` — Post-batch monitoring loop

- `runner.py` — Orchestrator; owns `FanslyWebSocket`, `ActivitySimulator`,
  and the work queue
- `simulator.py` — `ActivitySimulator`; three-tier state machine
  (active → idle → hidden) driving poll cadence (see
  [monitoring-cadence](monitoring-cadence.md) for intervals)
- `polling.py` — `poll_home_timeline()`, `poll_story_states()` —
  lightweight fallback when WS is silent
- `handlers.py` — WebSocket event → `WorkItem` translators
  (`dispatch_ws_event`)
- `filters.py` — `should_process_creator()` — skips creators whose
  timeline's first page hasn't advanced (see
  [monitoring-cadence](monitoring-cadence.md) for the rationale)
- `state.py` — `MonitorState` persistence (`lastHasActiveStories`,
  `lastSeenAtAtLastRun`, `lastRunAt`)
- `bootstrap.py` — Daemon setup + teardown lifecycle
- `dashboard.py` — Rich live dashboard renderable

### `download/` — Download orchestration

- `core.py` — Main download functions for different modes
- `media.py` — Media file downloading
- `downloadstate.py` — Per-creator download state tracking
- `globalstate.py` — Cross-creator statistics

### `metadata/` — Database models and operations

- `models.py` — Pydantic `BaseModel` subclasses with identity map
  (`model_validator(mode="wrap")`), bidirectional relationship sync,
  dirty tracking, auto-coercion of API data (str IDs → int, int
  timestamps → datetime)
- `entity_store.py` — `PostgresEntityStore`; two-tier store (local dict
  identity map + asyncpg pool)
- `tables.py` — SQLAlchemy `Table` definitions (used by Alembic for
  schema migrations, not at runtime)
- `database.py` — Database connection and pool management
- `account.py`, `media.py`, `post.py`, `messages.py`, `story.py`,
  `wall.py`, etc. — Processing functions for each entity type

### `stash/` — Stash integration (optional)

- Uses `stash-graphql-client` (external PyPI package, >= v0.12.0) for
  `StashClient`, Pydantic types, and `StashEntityStore`
- `processing/` — `StashProcessing` with mixin pattern for high-level
  workflows
  - `base.py` — `StashProcessingBase` class
  - `protocols.py` — Protocol definitions
  - `mixins/` — account, batch, content, gallery, media, studio, tag
    operations (7 mixins)
- `logging.py` — Stash-related logging utilities

### `fileio/` — File operations

- `dedupe.py` — Deduplication logic using content hashing
- `mp4.py` — Video hashing (PyAV-based)

### Supporting modules

- `helpers/` — Utility functions
- `textio/` — Console output and logging
- `pathio/` — Path management
- `errors/` — Custom exceptions and exit codes

## Core Design Patterns

### Pydantic + EntityStore database pattern

All entity operations follow this shape:

```python
store = get_store()

# model_validate handles identity map dedup via model_validator(mode="wrap").
# Auto-coerces str IDs → int, int timestamps → datetime. Unknown fields
# from the API are silently ignored (`extra="ignore"`).
obj = Model.model_validate(api_dict)

# Save related entities first if FK constraints require it.
if obj.related_entity:
    await store.save(obj.related_entity)

# save() detects `_is_new` (INSERT) vs. `is_dirty()` (UPDATE dirty fields
# only). Recursive saves for related entities in junction tables are
# handled automatically.
await store.save(obj)
```

#### Unicode coercion — truncated surrogate pairs

Fansly text fields (wall names, post `content`, account `about`, subscription tier `name`, promo `description`, etc.) frequently contain supplementary-plane characters — most often the **U+1D400 block** (mathematical bold, italic, sans-serif bold Latin letters: `𝐘`, `𝘋`, `𝗙`). Each such character is a UTF-16 surrogate pair. When Fansly's backend truncates a string mid-pair, the response carries an unpaired surrogate (e.g., `\uD835` with no matching low half) that asyncpg refuses to encode as UTF-8.

**Reported:** [GH #55 (closed)](https://github.com/Jakan-Kink/fansly-scraper/issues/55) — wall-name persistence failure on a REST API response containing a truncated U+1D400-block character. The issue's attached screenshot shows U+FFFD as the **last** visible character in the affected wall name, consistent with a length-cap truncation that severed a surrogate pair at the exact string boundary (rather than mid-string corruption). The screenshot pins the truncation as upstream of our client (Fansly-side), but the exact source layer — API-response serialization or their database storage — isn't determinable from the available evidence.

`metadata/models.py::_coerce_api_types` handles this for every string field on every model during `model_validate`: encode with `errors="surrogatepass"`, decode with `errors="replace"`. Unpaired surrogates become U+FFFD (`�`); properly-paired surrogates round-trip unchanged.

The render symptom is "two block/replacement glyphs for one character" in terminals — one half of the broken pair rendered as an empty box, paired with a substitution glyph. Not a consumer bug; upstream truncation already happened.

### Identity map via wrap validator

Every model defines:

```python
@model_validator(mode="wrap")
@classmethod
def _identity_map_check(cls, data: Any, handler: ValidatorFunctionWrapHandler):
    # If ID already in the store's identity map, return the cached
    # instance and merge incoming fields into it. Otherwise, construct
    # a new instance and register it.
    ...
```

This means the same entity ID always returns the same Python object
instance across queries — a read of `Account(id=123)` returns the
*same* object whether fetched via `store.get()`, traversed through a
relationship, or rehydrated from a nested API dict.

### Dirty tracking via `__tracked_fields__`

Each model declares a `__tracked_fields__: ClassVar[frozenset[str]]`
covering every field that should participate in change detection
(including relationship fields like `posts`, `avatar`, `variants`,
`hashtags`, `mentions`). On save, only fields that differ from the
snapshot are written. Relationship field changes trigger
`_sync_associations` for junction tables.

### Junction table sync via sync_junction()

A single method handles all junction patterns via kwargs. The primary
mode is DELETE all for the owner + re-INSERT with arbitrary columns
per row, which covers:

- Simple junctions (post_hashtags, post_attachments)
- Ordered junctions (attachments with `pos` column)
- Extra-column junctions (e.g., pinned_posts with `createdAt`)

For scalar 1:1 associations (account_avatar, account_banner),
`_sync_associations` detects `is_list=False` + `assoc_table` and
applies the same DELETE + INSERT pattern against a single-row
association.

### `get_store()` singleton

`get_store()` in `metadata/models.py` returns the `FanslyObject._store`
module-scoped singleton. All processing functions use it instead of
passing a `store` parameter — eliminates threading the store through
every call site. This is a deliberate choice: there is exactly one
`PostgresEntityStore` per process for this codebase.

### `_WRITE_EXCLUDED` — inverse-only fields

Some fields are populated by bidirectional sync but have no corresponding
DB column. These are declared in `_WRITE_EXCLUDED: ClassVar[frozenset[str]]`
and skipped by `save()`. Examples:

- `Account.posts`, `Account.sent_messages`, `Account.received_messages`,
  `Account.timelineStats`, `Account.mediaStoryState`

### Native Pydantic SerDes

API responses go directly into `Model.model_validate(api_dict)` —
there's no intermediate `process_data()` layer. The key enablers:

- `model_config = ConfigDict(extra="ignore")` — unknown API fields
  are silently dropped (matches the shape-evolving Fansly API)
- `_coerce_api_types` `model_validator(mode="before")` — converts
  `*At` int timestamps (both seconds and milliseconds) to `datetime`
- Field aliases for API field renames:
  - `Media.meta_info: Field(alias="metadata")` — `metadata` is a
    reserved attribute on Pydantic BaseModel
  - `Post.fypFlag: Field(alias="fypFlags")` — alias fix for a
    plural→singular inconsistency
- `_extract_video_dimensions` on `Media` — unpacks `width`/`height`/
  `duration` from the JSON `metadata` string into typed fields
- `_enrich_child_dict` in `_process_nested_cache_lookups` — injects
  parent context (accountId, FK columns) into nested relationship
  dicts before delegating to their models

## EntityStore Filter Syntax

Queries mirror the Django ORM / `stash-graphql-client` filter DSL:

```python
store.find(Media, is_downloaded=True)            # EQUALS (default)
store.find(Media, mimetype__contains="video")    # ILIKE
store.find(Media, duration__gte=100)             # >=
store.find(Post, id__in=[1, 2, 3])              # = ANY(...)
store.find(Media, content_hash__null=True)      # IS NULL
store.find(Media, updatedAt__between=(t0, t1))  # BETWEEN
store.find(Account, username__iexact="ALICE")    # ILIKE (case-insensitive exact)
```

Additional operations:

- `store.get(Model, id)` — single-entity lookup with identity map
- `store.get_many(Model, ids=[...])` — bulk lookup
- `store.save(obj)` — INSERT or UPSERT dirty fields
- `store.delete(obj)` / `store.delete_many(Model, ids=...)`
- `store.filter(Model, predicate=lambda m: ...)` — in-memory filter
  over the identity-map cache

## Database Architecture

### Stack

- **Models**: Pydantic `BaseModel` subclasses in `metadata/models.py` —
  identity map, bidirectional relationship sync, dirty tracking
- **Storage**: `PostgresEntityStore` (`metadata/entity_store.py`) — two
  tiers: local dict for sync identity-map access + asyncpg pool for
  stateless DB transport
- **Schema**: SQLAlchemy `Table` definitions in `metadata/tables.py`
  (used by Alembic only, not at runtime)
- **Migrations**: Alembic migrations in `alembic/versions/`

### Design heritage

The local `PostgresEntityStore` is modeled on `stash-graphql-client`'s
`StashEntityStore`, which pioneered the Pydantic identity-map + wrap
validator + dirty-tracking pattern used here. Both stores share the
same conceptual shape:

- Pydantic `BaseModel` subclasses as the canonical in-memory entity type
- A `model_validator(mode="wrap")` that routes construction through a
  per-type identity map, so the same ID always yields the same Python
  instance
- Snapshot-based dirty tracking — `save()` writes only changed fields
- Relationship sync helpers for bidirectional updates

The difference is the backend: `StashEntityStore` talks GraphQL, while
`PostgresEntityStore` talks asyncpg against a local PostgreSQL
database. Both are maintained by the same author
([Jakan-Kink](https://github.com/Jakan-Kink)), and the two projects
have cross-pollinated ideas in both directions over several release
cycles:

- Early identity-map and UNSET-pattern work landed in
  `stash-graphql-client` first, then informed the design of
  `PostgresEntityStore` during the v0.11 PostgreSQL hard-cut here.
- Lessons learned driving fansly-downloader-ng's large-scale batch
  processing (millions of media rows, per-creator orchestration)
  surfaced ergonomic gaps — query filter syntax, `filter()` predicate
  access over the identity-map cache, bulk `get_many` / `delete_many`,
  JSONB codec handling — that fed back into
  `stash-graphql-client`'s public API.
- The SGC v0.12 batched-mutation work (`save_batch`,
  `execute_batch`, `save_all`) was motivated in part by profiling this
  project's per-creator Stash push workload; adoption in
  fansly-downloader-ng is tracked in the
  [Stash ORM Migration Guide Phase 4](../planning/STASH_ORM_MIGRATION_GUIDE.md#phase-4-advanced-features).

If you're learning one store's API, the other's will feel familiar —
aside from the backend-specific operators (GraphQL filter types vs.
Postgres operators), the public surface is intentionally aligned.

### Timestamp handling

Pydantic models auto-coerce timestamps. Fields ending in `At` are
automatically converted from int/float/string to `datetime` via the
`_parse_timestamp()` validator — no manual conversion in processing
code. Both seconds (Fansly API default for some fields) and
milliseconds (WebSocket event timestamps) are accepted.

### Foreign key constraints

Processing modules save related entities **before** the parent to
satisfy FK constraints — e.g., save avatar `Media` before `Account`.
The EntityStore's `save()` also handles recursive related-entity
saves for junction tables automatically.

### `MediaLocation.locationId` is a CDN type code, not a Snowflake ID

`MediaLocation.locationId` values are CDN delivery type enums, **not**
entity IDs:

| Value | Delivery type                                   |
| ----- | ----------------------------------------------- |
| `1`   | Direct CDN (standard HTTP delivery)             |
| `102` | HLS (HTTP Live Streaming)                       |
| `103` | DASH (Dynamic Adaptive Streaming over HTTP)     |

The DB column is `BigInteger` for historical reasons, but actual
values are small integers. This field pairs with the `location` URL
as a composite PK in the `media_locations` table. Do not type this
as `SnowflakeId`; keep it as plain `int` (or refactor to `IntEnum`).

## Stash Integration

### StashClient (external dependency)

Provided by the `stash-graphql-client` PyPI package (>= v0.12.0):

- Async GraphQL client with `gql` + httpx transport
- Pydantic types for all Stash entities (Performer, Scene, Gallery,
  Studio, Tag, etc.)
- `StashEntityStore` — same identity-map + dirty-tracking design as
  the local `PostgresEntityStore`, but backed by GraphQL queries
- Server capability detection via `__schema` introspection at connect
  time; dynamic fragment store rebuilds version-gated fields only
  when supported

### StashProcessing (our mixin layer)

`stash/processing/` wraps the external client with domain-specific
workflows for pushing Fansly metadata into Stash:

- `StashProcessingBase` in `processing/base.py` — core workflows
- Mixins in `processing/mixins/` — `account`, `batch`, `content`,
  `gallery`, `media`, `studio`, `tag`
- `StashProcessing` inherits all mixins for clean composition
- StashClient itself (22 entity-specific mixins) is in the external
  package

See [`docs/planning/STASH_ORM_MIGRATION_GUIDE.md`](../planning/STASH_ORM_MIGRATION_GUIDE.md#phase-4-advanced-features)
for the planned migration to SGC v0.12's batched mutations,
`__side_mutations__` mechanism, and ActiveRecord-style relationship DSL.

## Deduplication System

### Current: database-stored content hash

- Hash stored in `Media.content_hash`
- Filenames are clean (no hash embedded)
- `fileio/dedupe.py` compares hashes to skip re-downloads
- `fileio/mp4.py` handles video hashing via PyAV

### Historical hash formats (migration-aware)

Older filename-based hash formats are automatically migrated:

- `_hash_` — original format
- `_hash1_` — improved video hashing
- `_hash2_` — fixed video hashing (last pre-database format; values
  preserved as trusted when migrating)

### Migration strategy

- Preserve `hash2` values as trusted source (same algorithm as current
  database-stored hashes)
- Recalculate hashes for older formats on discovery
- Support both filename and hash-based lookups during transition

## Async Context Managers

Resource safety patterns used throughout:

- `async with` for database sessions (asyncpg connection acquire)
- `async with` for API rate limiting (token bucket)
- `async with` for WebSocket session lifetime
- `async with` for the StashClient initialization + teardown

## Exit Codes

The downloader uses distinct exit codes for automation integration:

| Code | Meaning              |
| ---- | -------------------- |
| `0`  | Success              |
| `1`  | Config error         |
| `2`  | API error            |
| `3`  | Download error       |
| `4`  | Some users failed    |
| `10` | Unexpected error     |
| `255`| User abort (SIGINT)  |

## Performance Considerations

- EntityStore uses two-tier caching (local dict identity map + asyncpg
  connection pool)
- Batch operations where possible (`get_many()`, `save()` with recursive
  related entities)
- asyncpg connection pooling for concurrent database access
- Dirty tracking avoids unnecessary writes (only changed fields are
  UPSERTed)
- Stash-side batch operations (once adopted per the ORM migration
  guide) will collapse N sequential mutations into single aliased
  HTTP requests
