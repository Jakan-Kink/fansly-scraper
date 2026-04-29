---
status: audit-complete
date: 2026-04-24
method: 46-agent parallel fan-out, cross-validated across directory-scoped and concern-scoped audits
---

# Pass 2 Master Audit Report — tests-to-100 Branch

## Executive Summary

- **Branch mission**: (1) remove all internal mocks, (2) 100% passing tests, (3) 100% coverage
- **Scale audited**: 221 test files, ~71K lines of test code, ~2,092 test functions
- **Agents deployed**: 46 parallel audits (14 directory-scoped, 12 concern-scoped, 20 deep-dive + spot-checks)
- **Raw violation reports**: ~1,200+ findings
- **Deduplicated & cross-validated violations**: ~670 actionable items (post user review, 3 DbSyncAsyncMix findings withdrawn as stale, 1 fixture-level `return_value` reclassified as intentional)
- **TODO-doc reconciliation**: Existing migration TODOs (Nov 2025) systematically undercounted violations. GraphQL assertion compliance alone was claimed at 53+; true count is ~150+ (3× undercounted).
- **Stale concept purged**: `DbSyncAsyncMix` category retired — it was a SQLAlchemy-era concern made obsolete by the Pydantic + asyncpg `PostgresEntityStore` migration. See Category E notes.

## Policy: all fixtures live under `tests/fixtures/`

**RULE**: every pytest fixture, helper class/factory, fake/stand-in object,
or reusable test utility MUST be placed inside the `tests/fixtures/`
directory tree — **never inlined in a `test_*.py` file** (beyond
single-test-scoped locals). Even if the helper starts its life serving a
single test file, the moment another file could reasonably want it,
inlining becomes a duplication magnet.

**Why this matters**:

- Inlined fakes + fixtures get re-invented per test file, silently diverge
  (e.g., `_FakeApi` vs `_FakeFanslyApi` vs `FakeApi` with slightly
  different attribute sets), and block cross-file reuse.
- Shadowed/orphaned fixtures are invisible when they live in test files —
  but the audit of `tests/fixtures/` catches them (see the Wave 1 finding
  where three fixtures in `conftest.py` silently returned empty dicts for
  years because their JSON paths were wrong).
- `tests/fixtures/` is the agreed single source of truth — imports from it
  are trusted to be validated, exercised across the suite, and maintained
  in one place.

**Organization inside `tests/fixtures/`**:

- `tests/fixtures/<domain>/<domain>_factories.py` — FactoryBoy factories
- `tests/fixtures/<domain>/<domain>_fixtures.py` — pytest fixtures
- `tests/fixtures/utils/` — cross-cutting helpers (concurrency,
  isolation, cleanup, ID generation) — imported by many domains
- `tests/fixtures/<domain>/__init__.py` — explicit re-exports; tests
  import from the package root (`from tests.fixtures.utils import X`)
  rather than reaching into submodules.

**Concrete example**: during Wave 2.3 (`test_m3u8.py` rewrite, 2026-04-24)
a `_SyncExecutor` helper was first defined inline. User caught it, flagged
for promotion: now lives at `tests/fixtures/utils/concurrency.py` as
`SyncExecutor`. Any future test that needs a synchronous
`ThreadPoolExecutor` drop-in imports from one place.

**When to review**: every Wave 2 follow-up that introduces a new fake,
factory, or fixture MUST check whether it belongs in `tests/fixtures/`
before committing. If the PR body doesn't mention the fixture path, it's
a red flag — fixtures that land in `test_*.py` files become technical
debt.

## Follow-up: audit `tests/fixtures/` for shadowed/orphaned fixtures

**Status**: scheduled. Run before the final branch close-out.

**Motivation**: Wave 1.5 caught three fixtures in `tests/conftest.py`
(`json_timeline_data`, `json_messages_group_data`, `json_conversation_data`)
that had been silently returning empty dicts for years because the JSON
filenames referenced didn't match the real files. A broader audit across
`tests/fixtures/` and `tests/conftest.py` is likely to surface more.

**Scope**:

1. Enumerate every `@pytest.fixture`, `@pytest_asyncio.fixture`,
   `class ...(Factory)`, and top-level helper exported via `__all__` in
   the entire `tests/fixtures/**` + `tests/conftest.py` tree.
2. For each: cross-reference against usage in `tests/**/*.py`. Flag any
   fixture with zero consumers (orphaned) or where multiple definitions
   exist with the same name across different files (shadowed).
3. For shadowed pairs, determine which is authoritative; delete the
   duplicate. For orphans, delete unless the fixture is obviously
   infrastructure-level (e.g., cleanup-scoped autouse fixtures that
   don't need explicit consumers).
4. Also check for fixtures that silently fail (e.g., the "JSON file not
   found → return `{}`" pattern) and either make them raise or rename
   them to match the file they're supposed to load.

**Expected effort**: 2-4 hours. Output: a list of deletions/renames,
each with a brief justification, applied in a single commit.

## Top-Line Findings

### Confirmed clean (multiple agents, high confidence)

- `tests/download/unit/test_downloadstate.py`, `test_globalstate.py`, `test_pagination_duplication.py` — still fully compliant (TODO doc accurate)
- `tests/stash/client/` — properly deleted (commit `684cb1f61`, 2025-12-29)
- `tests/stash/types/` — empty directory, acceptable
- `tests/metadata/unit/*` — generally clean (4 minor violations, 12 acceptable infrastructure patterns, 2 intentional shallow)
- `tests/fileio/test_dedupe.py` — compliant (1,259 lines, clean)
- `tests/daemon/unit/test_simulator.py`, `test_handlers.py`, `test_dashboard.py`, `test_state.py`, `test_filters.py`, `test_polling.py` — clean
- `tests/conftest.py:68-127` enforcement hook — **INTACT and functional**
- `tests/fixtures/database/database_fixtures.py:108-180` PG skip mechanism — **INTACT**; safe cleanup, proper skip-when-unavailable
- `tests/fixtures/metadata/metadata_factories.py` — 17/17 Pydantic model factories present, factory-boy on real classes
- `tests/fixtures/stash/stash_type_factories.py` — 10/10 stash-graphql-client types have factories

### Critical broken-claim findings (discovered, NOT in existing TODOs)

1. **`tests/fixtures/stash/stash_mixin_fixtures.py:133-191`** — 7 async fixtures use `@pytest.fixture` decorator instead of `@pytest_asyncio.fixture`. Under `asyncio_mode="auto"` this is undefined behavior; fixture setup/teardown may silently fail. Affected fixtures: `account_mixin`, `batch_mixin`, `content_mixin`, `gallery_mixin`, `media_mixin`, `studio_mixin`, `tag_mixin`.
2. **`tests/crypto.py`** — Contains 2 parametrized test functions (`test_cyrb53`, `test_imul32`) but filename doesn't match `test_*.py` pattern. These tests are **silently undiscovered by pytest**. Rename to `tests/test_crypto.py`.
3. ~~**`tests/fixtures/database/database_fixtures.py:370-395`** — `test_engine` and `test_database_sync` are `scope="session"`.~~ **FALSE POSITIVE** — verified 2026-04-24 during Wave 1.5: both fixtures are function-scoped (no `scope=` argument defaults to function scope). Agent misread `test_data_dir`/`timeline_data`/`json_conversation_data`/`conversation_data` at lines 370-396 (all legitimately session-scoped read-only JSON loaders) as the database fixtures. The real `test_engine` is at `database_fixtures.py:426` (`@pytest_asyncio.fixture`, function scope) and `test_database_sync` is at `database_fixtures.py:594` (`@pytest.fixture`, function scope). No action needed.
4. **`tests/core/unit/test_fansly_downloader_ng.py`** — **9 of 11 main-test cases mock `fansly_downloader_ng.main` itself**, defeating integration testing entirely. Estimated 55 violations; 7 tests patch 7+ internal functions simultaneously.
5. **`tests/config/unit/test_validation.py:26-41`** — `mock_config` fixture uses `MagicMock(spec=FanslyConfig)` and is consumed by 30+ tests, shallowing all of them. Token validation short-circuits (`token_is_valid.return_value = True`) — real `token_is_valid()` (requires ≥50 chars) is NEVER exercised.
6. **`tests/daemon/unit/test_runner_wiring.py:179,197`** — Direct assignment `_runner._worker_loop = _instrumented_worker` outside `patch()` context manager; no auto-restoration on test failure; real worker-loop orchestration never tested.
7. **`tests/daemon/unit/test_runner_wiring.py:446,450,476,480,792,964,994`** — 7 "spy" functions named `_spy_*` that **do NOT delegate to the real function**. These are FakeSpies (MockInternal in disguise); real orchestration logic untested.

---

## Master Violations Inventory (by category)

### Category A: respx `return_value` — CRITICAL (12 confirmed)

Rule scope **refined after review**: `.mock(return_value=...)` NEVER in **per-test routes** — tests must use `side_effect=[...]`. **Fixture-scaffolding routes are exempt** — they legitimately need to respond to every call of a type (e.g., CORS preflight, default GraphQL no-op) and `side_effect=[Response(200)]` would exhaust after one call. Per-test `return_value` causes infinite response loops, defeats retry-budget accounting, masks call-sequence bugs.

Per-test violations (actionable):

| File                                  | Lines                             | Count |
| ------------------------------------- | --------------------------------- | ----- |
| `tests/api/unit/test_fansly_api.py`   | 229, 230, 324, 344, 366, 425, 447 | 7     |
| `tests/download/unit/test_account.py` | 127, 156, 161, 546, 610           | 5     |

Total: **12 violations, 2 files**. Fix complexity: trivial (mechanical replacement).

**Known intentional fixture-scaffolding uses (DO NOT FLAG)** — 9 `return_value` calls in `tests/fixtures/**` are by design:

- `tests/fixtures/api/api_fixtures.py:21, 56, 193, 225, 272` — CORS OPTIONS preflight (fires on every request) + default account/timeline responses that tests opt into
- `tests/fixtures/stash/stash_api_fixtures.py:237, 259` — default Stash GraphQL responses
- `tests/fixtures/stash/stash_integration_fixtures.py:307` — integration default
- `tests/fixtures/stash/stash_graphql_fixtures.py:27` — GraphQL fixture default

These should be noted in fixture docstrings as "intentional `return_value` — this is a fixture default; per-test routes must use `side_effect`."

### Category B: respx missing try/finally + dump — HIGH (24 files)

Rule (clarified): wrap **the function(s) being tested** in `try:`, put `dump_fansly_calls(route.calls)` (or `dump_graphql_calls(...)`) in the `finally:`, then place assertions **after** the try/finally block. Ordering matters — when an assertion fails the dump has already printed, so you see the exact calls made. Wrapping the assertions themselves defeats the purpose.

Canonical pattern (from `dump_fansly_calls` docstring at api_fixtures.py:282-286):

```python
route = respx.get(...).mock(side_effect=[...])
try:
    await function_under_test()
finally:
    dump_fansly_calls(route.calls)
# assertions here — dump has already printed if anything below fails
assert ...
```

Files missing the pattern (enumerated):

- `tests/api/unit/test_fansly_api.py` (40 tests, 19 respx uses, 0 dumps)
- `tests/api/unit/test_fansly_api_callback.py` (1 test, 1 respx, 0 dumps)
- `tests/download/unit/test_account.py` (27 tests, 5 respx, 0 dumps)
- `tests/download/unit/test_stories.py` (5 tests, 3 respx, 0 dumps)
- `tests/download/unit/test_single_download.py`, `test_media_download.py`, `test_messages_download.py`, `test_collections.py`, `test_wall_download.py`, `test_timeline_download.py` (respx use, no dumps)
- `tests/download/integration/test_m3u8_integration.py` (6 tests, 5 respx, 0 dumps)
- `tests/helpers/unit/test_checkkey.py` (66 tests, 7 respx, 0 dumps)
- `tests/metadata/integration/test_account_processing.py` (4 tests, 1 respx, 0 dumps)
- `tests/stash/integration/test_stash_processing_integration.py` (3 tests, 1 respx, 0 dumps)
- All 14 `tests/stash/processing/unit/*/test_*.py` files using `respx_stash_processor` — 0 `dump_graphql_calls` anywhere

Total: **24 files** require retrofitting. Fix complexity: medium (mechanical wrapping, per test).

### Category C: respx route overbreadth — MEDIUM

- `tests/download/unit/test_account_coverage.py:73` — `url__regex=r".*"` (intercepts EVERY HTTP call)
- `tests/api/unit/test_fansly_api_additional.py:23,49,85,90,115,120,144,149,619,622` — loose `.*account/media.*`, `.*timelinenew.*`, `.*account/me.*` patterns that allow path suffix injection
- `tests/download/integration/test_m3u8_integration.py:82,156,231,267,316` — could use `url__startswith`

Total: **16 occurrences, 3 files**.

### Category D: MockInternal on orchestration — CRITICAL (~150 confirmed)

Rule: never mock internal functions, wrappers, config, DB, print/logger, progress, sleep.

**Highest-impact clusters:**

1. `tests/core/unit/test_fansly_downloader_ng.py`: ~28 MockInternal + ~8 DbMockModel + ~7 MockLoggingTiming = **55+ violations** (lines 36–816)
   - Patches `fansly_downloader_ng.main` itself (lines 560, 604, 655, 694, 743, 797) — **7 tests mock the entrypoint**
   - Patches `asyncio.wait_for`, `asyncio.gather`, `asyncio.create_task` as internal orchestration
   - `mock_database`, `mock_alembic` fixtures replace entire DB/migration system
2. `tests/core/unit/test_fansly_downloader_ng_extra.py`: 7+ violations; mocks `sys.exit` (should be `pytest.raises(SystemExit)`), print functions, `process_account_data`
3. `tests/config/unit/test_validation.py`: **102 violations** (43 MockInternal + 11 MockLoggingTiming + 7 PathMock + 36 ShallowTest-via-fixture + 4 httpx-edge + 1 MockConfigFixture)
4. `tests/config/unit/test_fanslyconfig.py`: MockInternal on `FanslyApi`, `StashContext`, `RateLimiter`
5. `tests/download/unit/test_m3u8.py`: **26 violations** (8 MockInternal + 13 PathMock + 5 config-Mock) — TODO said 28, close match
6. `tests/download/unit/test_timeline_download.py`: 24 MockInternal (all 7 tests mock `process_timeline_data`, `process_timeline_media`, etc.)
7. `tests/download/unit/test_messages_download.py`: 18 MockInternal
8. `tests/download/unit/test_collections.py`: 3 MockInternal (all 4 tests)
9. `tests/download/unit/test_single_download.py`: 4 MockInternal
10. ~~`tests/download/unit/test_stories.py:190-231`: `gate_invocations` fixture uses `monkeypatch.setattr` to replace 6 internal functions~~ — **CLOSED in a prior "Wave 2.5" session**, see file docstring at `tests/download/unit/test_stories.py:1-34`. Replaced with real-pipeline + respx; gate verified via real `/api/v1/mediastory/view` HTTP calls (`respx_route.call_count`) instead of FakeSpy on `_mark_stories_viewed`. 11/11 tests pass. Verified 2026-04-25.
11. `tests/stash/processing/unit/media_mixin/test_error_handlers.py:45,62,224,252`: Patches `client.find_image`, `client.find_scene` instead of respx at HTTP layer
12. `tests/stash/processing/integration/test_base_processing.py:28,52,571`: Patches internal StashClient methods (`metadata_scan`, `wait_for_job`)
13. `tests/stash/processing/integration/test_message_processing.py:225,230,235`: Spies on internal processor methods `_collect_media_from_attachments`, `_process_media_batch_by_mimetype`, `_process_batch_internal` — but spies here do wrap via `await original_*(...)` so these are TrueSpies ✅ (cross-validated with spy-pattern audit)
14. `tests/daemon/unit/test_runner_wiring.py`: 7 FakeSpies (no delegation) + 2 direct `_runner._worker_loop = ...` assignments
15. `tests/stash/processing/unit/media_mixin/test_attachment_processing.py,test_batch_processing.py`: Mock internal `_process_batch_internal` (15 instances across 2 files)
16. `tests/fixtures/download/download_fixtures.py:110,128`: `mock_process_media_download`, `mock_process_media_bundles` — fixture-level mocks of internal metadata functions

### Category E: DbMockModel — HIGH (DbSyncAsyncMix category RETIRED as stale)

**DbSyncAsyncMix findings have been withdrawn.** That category was a SQLAlchemy-era concern: the original MissingGreenlet bug (STASH_TEST_MIGRATION_TODO Lessons Learned §1) happened when production code accessed a lazy-loaded relationship on a sync-committed SQLAlchemy model from an async context. **The Pydantic + asyncpg migration eliminated that bug class** — runtime code in `metadata/` and `stash/` no longer uses SQLAlchemy `Session`/`AsyncSession`; `PostgresEntityStore` talks to asyncpg directly and models are Pydantic `BaseModel` subclasses with no lazy relationship loaders. SQLAlchemy is imported only in `metadata/tables.py` (Alembic schema) and `metadata/database.py` (engine-for-migrations).

The `session_sync` usages flagged in `test_metadata_update_integration.py` are just FactoryBoy bulk-inserting test fixtures into the test DB before the async code under test runs. Mixing scopes there is fine because the async code path exercises asyncpg via EntityStore, not SQLAlchemy relationships.

Remaining actionable DbMockModel violations:

- `tests/pathio/test_pathio.py:264,299,334,371,402,422` — 6 × `MagicMock(spec=Media)` — use MediaFactory
- `tests/download/unit/test_transaction_recovery.py:11,35,66` — 11 findings; uses shared `test_database` fixture instead of `uuid_test_db_factory` (no isolation). Evaluate: is this still a real concern post-asyncpg migration, or another stale finding?
- `tests/core/unit/test_fansly_downloader_ng.py:215,272` — `mock_database`, `mock_alembic` fixtures
- `tests/metadata/unit/test_entity_store_comprehensive.py:783` — `MagicMock(spec=asyncpg.Pool)` — acceptable per infrastructure exception (can't run without event loop)
- `tests/metadata/unit/test_transaction_rollback.py:134,163,206` — `monkeypatch.setattr(Session, "execute"/"rollback")` — **also suspect post-Pydantic-migration**; check whether this file is exercising code that no longer uses SQLAlchemy Session at all

### Category F: PathMock (should use tmp_path) — MEDIUM

- `tests/download/unit/test_m3u8.py:424-426,537-538,647-649,697-748` — 13 `@patch("pathlib.Path.*")`
- `tests/download/integration/test_m3u8_integration.py:107,108,182-184,248,338,339,107-109,249,340` — 4 PathMocks + 4 `builtins.open` mocks
- `tests/config/unit/test_version.py:18,30,39` — 3 `mock_open()` + `Path.open` patches (use real tmp_path TOML files)
- `tests/config/unit/test_validation.py:246,288` — Path mocks (should use tmp_path)
- `tests/pathio/test_pathio.py:52-114` — patches `pathio.pathio.Path` constructor
- `tests/utils/unit/test_semaphore_monitor.py:197` — patches `Path.unlink`

**Legitimate exceptions (don't fix)**: `test_logging.py:405,655,679,690,787,808` (OSError/PermissionError simulation), `test_version.py:30` (FileNotFoundError), `test_semaphore_monitor.py:213` (OSError on unlink).

Total actionable: **~30 violations**; legitimate exceptions: ~11.

### Category G: GraphQL assertion compliance (Pass 2 main focus) — 150 violations

TODO claimed 53+; actual count is **3× higher**.

**GraphQLAssertWeak (104 violations)** — verifies only query name, not variables + response body:

- `tests/stash/processing/unit/test_media_variants.py`: 38 violations (lines 366-377, 550-563, 764-779, 961-966, 1157-1168)
- `tests/stash/processing/unit/media_mixin/test_metadata_update.py`: 30 violations (lines 141-146, 367-372, 616-628, 713-717, 848-856, 965-971)
- `tests/stash/processing/unit/test_background_processing.py`: 12 violations (lines 138-141, 307-309, 408-410, 515-517)
- `tests/stash/processing/unit/content/test_message_processing.py:117-122,246`: tautological `"query" in req or "mutation" in req.get(...)`
- `tests/stash/processing/unit/content/test_post_processing.py:109-112,219`: same tautology
- `tests/stash/processing/unit/content/test_content_collection.py:447-463`: loop without variable verification

**GraphQLCallCount (46 violations)** — `>= N` instead of exact `== N`:

- `tests/stash/processing/integration/test_media_processing.py`: 10 violations (lines 184-824)
- `tests/stash/processing/integration/test_message_processing.py`: 11 violations
- `tests/stash/processing/integration/test_timeline_processing.py`: 7 violations
- `tests/stash/processing/integration/test_content_processing.py`: 6 violations
- `tests/stash/processing/unit/test_media_mixin.py`: 8 violations (lines 205,215,221,230,406,416,422,432)
- `tests/stash/processing/unit/test_stash_processing.py`: 3 violations (lines 421,433,531)
- `tests/stash/processing/unit/gallery/test_gallery_creation.py`: 3 violations (lines 243,443,705)
- `tests/stash/processing/unit/test_gallery_methods.py:718`
- `tests/stash/processing/unit/content/test_message_processing.py:117,246` (`> 0` is also weak)
- `tests/stash/processing/unit/content/test_post_processing.py:106,219`
- `tests/stash/processing/unit/content/test_content_collection.py:444`

### Category H: MockLoggingTiming — 117 violations, many LEGITIMATE

- **Print patches (35)** — HIGH priority fix: `test_fansly_downloader_ng.py:117,501-503,505`; `test_fansly_downloader_ng_extra.py:24,40,284-285`; `test_common.py:285-286`; many others in stash processing tests
- **Sleep patches (47)** — MIXED: some legitimate for fast tests, some hide timing bugs
  - `tests/api/unit/test_rate_limiter.py:256,266,288` — timing-accurate tests, acceptable
  - `tests/config/unit/test_browser.py:342,358,375,389` — internal `config.browser.sleep` should not be patched
  - `tests/download/unit/test_account.py:480,514,690,847` — `asyncio.sleep` mocking, edge-of-acceptable
  - `tests/api/unit/test_websocket.py:420,442,467` — `timing_jitter` patches on anti-detection helper — **violates "sleep/timing MUST NOT be mocked"**
- **Logger patches (35)** — `tests/config/unit/test_validation.py:149,207,228,239,251,263,276` (8 instances), `test_browser.py:306`, others — use `caplog` fixture instead

### Category I: Inline imports (PEP 8/810) — 119 violations

Memory: `feedback_pep8_imports.md — All imports at file top, NEVER inline — most frequent correction`

Top offenders:

- `tests/alembic/test_migrations.py`: 24 inline imports (lines 739, 761, 823, 904, 2295, 2372, 2458, 2584-2585, 2608-2609, 2639-2641, 2677-2679, 2709-2711, 3173)
- `tests/stash/processing/unit/test_media_variants.py`: 12 inline
- `tests/stash/processing/unit/test_stash_processing.py`: 6
- `tests/api/unit/test_websocket.py:101,453,461,557,558`: 5
- `tests/daemon/unit/test_runner_wiring.py:148-171`: 4
- `tests/stash/processing/integration/test_stash_processing_integration.py:34,58,141,165`: 4
- `tests/config/unit/test_config_init.py:12,23,37`: 3
- `tests/conftest.py:60,507,532`: 3
- `tests/api/unit/test_fansly_api_additional.py` (via config_fixtures): `api_fixtures.py:64,88,124`: 3
- Metadata integration: `test_account_processing.py:110` (`import copy`), `test_message_processing.py:39,175`, `test_pinned_posts_fk.py:100,133`, `test_metadata_package.py:140`

### Category J: ShallowTest — 609 findings

- `tests/helpers/unit/*`: 124 shallow
- `tests/config/unit/*`: 113 shallow
- `tests/metadata/unit/*`: 98 shallow
- `tests/api/unit/*`: 46 shallow
- `tests/daemon/unit/*`: 45 shallow
- `tests/download/unit/*`: 44 shallow

**High-impact consolidation targets** (parametrize into multi-path tests):

- `tests/metadata/unit/test_hashtag.py` — 15 tests, all on single `extract_hashtags` function → consolidate to 1 parametrized
- `tests/daemon/unit/test_handlers.py` — 45 tests, each dispatching one event → parametrized matrix
- `tests/config/unit/test_*.py` (113 tests) — multi-rule validation tests missing
- `tests/api/unit/test_fansly_api.py` — timestamp/utility functions parametrizable

### Category K: FakeSpy (MockInternal in spy disguise) — 11 violations

Named `spy_*` but body does NOT delegate to real function:

1. `tests/daemon/integration/test_runner.py:131` — `_spy_handle_work_item` never calls real handler
2. `tests/daemon/unit/test_runner_wiring.py:446,450,476,480,792,964,994` — 7 FakeSpies
3. `tests/download/unit/test_stories.py:228` — `spy_mark` replaces `_mark_stories_viewed` entirely
4. `tests/metadata/unit/test_database_lifecycle.py:243,267` — `spy_is_set` lower severity (state introspection)

**TrueSpies (GOOD, keep as reference):** 11+ instances in `tests/stash/processing/unit/gallery/test_media_detection.py` and `tests/stash/processing/integration/test_*.py` — all correctly use `wraps=` or `await original_*(...)` delegation.

### Category L: Fixture issues — 11 shadowing + 2 scope bugs (post-correction)

**Shadowed (root conftest.py wins):**

- `test_config` (conftest.py:374 + config_fixtures.py:34)
- `temp_config_dir` (conftest.py:383 + config_fixtures.py:50)
- `config_parser` (conftest.py:412 + config_fixtures.py:89)
- `mock_config_file` (conftest.py:418 + config_fixtures.py:100)
- `valid_api_config` (conftest.py:462 + config_fixtures.py:142)
- `download_state` (conftest.py:518 + download_fixtures.py:18)
- `test_downloads_dir` (conftest.py:543 + download_fixtures.py:35)
- `mock_download_dir`, `mock_metadata_dir`, `mock_temp_dir` (same pattern)
- `json_conversation_data` (function vs session scope!) — **session version never used**

**Scope bugs:**

- ~~`test_engine` (session) + `test_database_sync` (session) → parallel isolation breaks~~ — **false positive, retracted in Broken-claim #3**: both fixtures are already function-scoped; audit agent misread adjacent session-scoped JSON loaders.
- `timeline_data`, `conversation_data` (session) → mutable dicts returned from `json.load()` with no `copy.deepcopy`; any test mutating them risks leaking state across the session. (Verified still session-scoped 2026-04-29.)

### Category M: Misc

- `tests/config/functional/test_logging_functional.py:47` — `os.environ["TESTING"] = "1"` direct write (use `monkeypatch.setenv`)
- `tests/config/unit/test_browser.py:204-276` — nested MagicMock on sqlite3 (cursor/conn chain)
- `tests/fileio/unit/test_mp4.py:212,239,262,291,337,368` — patches `get_boxes`/`hash_mp4box` internal wrappers (should use real BytesIO MP4 structures, pattern already established in TestMP4Box/TestHashMP4Box)
- `tests/stash/test_logging.py:44-88` — 5 MockLoggingTiming on `stash_logger`, `client_logger`, `processing_logger` — but these **test the loggers themselves**, so arguably acceptable

---

## Coverage Gaps (fresh `.coverage` dated 2026-04-24 01:10)

**Overall: 88.11% statement coverage.** Target: 100%.

| Module                        | Coverage | Missing   | Notes                                                                 |
| ----------------------------- | -------- | --------- | --------------------------------------------------------------------- |
| `metadata/story.py`           | 25.9%    | 20 / 77   | **Critical gap**; tiny module, mostly untested                        |
| `fansly_downloader_ng.py`     | 33.1%    | 295 / 891 | Main entry orchestration untested (mocked to death in tests)          |
| `daemon/bootstrap.py`         | 54.5%    | 25 / 55   | Backfill + teardown sequencing                                        |
| `daemon/runner.py`            | 55.1%    | 188 / 419 | Error-budget, hard-fatal, five-loop coordination                      |
| `api/rate_limiter_display.py` | 60.6%    | 43 / 109  | Rich rendering                                                        |
| `download/m3u8.py`            | 60.9%    | 115 / 294 | Variant selection, strategy orchestration (hidden by mocks in tests!) |
| `download/messages.py`        | 65.9%    | 29 / 85   | Pagination edges                                                      |
| `api/websocket.py`            | 66.7%    | 155 / 466 | Reconnect, envelope parsing                                           |
| `helpers/rich_progress.py`    | 73.7%    | 77 / 293  | Terminal width handling                                               |

**Suspicious exclusion in `pyproject.toml:385`**: `if TESTING:` in `exclude_also` hides branches. Audit `grep -r "if TESTING:" <src>` to verify coverage.

**Coverage-visibility feedback loop**: many of the low-coverage numbers above are **directly caused** by Category D MockInternal violations. E.g., `fansly_downloader_ng.py:main()` is ~70% uncovered because `test_fansly_downloader_ng.py` mocks `main` itself rather than exercising it. Fixing Category D will auto-improve coverage for the orchestration modules.

---

## Flakiness / Currently-Failing-or-Risky Tests

### Will HANG without proper mocks

- `tests/stash/processing/unit/content/test_batch_processing.py:535` — `asyncio.sleep(999)` in `test_batch_queue_join_timeout`
- `test_batch_processing.py:668` — `asyncio.sleep(999)` in `test_batch_cancellation_cleans_up_workers`
- `test_batch_processing.py:729` — `asyncio.sleep(60)` in `test_batch_cancellation_before_queue_join`

### Real-time timing tests (flaky on slow CI)

- `tests/helpers/unit/test_timer.py` — 17× `time.sleep(0.01–1.0)` tests
- `tests/api/unit/test_rate_limiter_display.py` — 4× `time.sleep(0.3–0.4)`
- `tests/config/functional/test_logging_functional.py` — 5× `time.sleep(0.1)` for log rotation

### Non-deterministic time (need freezegun)

- 22 instances of `datetime.now(UTC)` without time freezing across `test_polling.py`, `test_state.py`, `test_runner_e2e.py`, `test_runner_wiring.py`, `test_creator_processing.py`

### Environment-dependent skips

- 5 tests in `test_browser.py:395-460` skip when `plyvel` not installed
- ~14 Stash integration tests skip without Docker Stash running

---

## Prioritized Action Plan (Pass 2 Fix Order)

### Wave 1: Surgical, low-risk, high-value (est. 8–12 hours)

1. **Fix 12 per-test respx `return_value` violations** (test_fansly_api.py:7, test_account.py:5). Leave the 9 fixture-scaffolding `return_value` uses alone (add a short docstring note on each explaining the intentional choice).
   - Purely mechanical: `.mock(return_value=X)` → `.mock(side_effect=[X])`
   - Unblocks: retry-budget testing, prevents infinite-loop bugs
2. **Fix 7 async-fixture decorators** in `tests/fixtures/stash/stash_mixin_fixtures.py:133-191`
   - Change `@pytest.fixture` → `@pytest_asyncio.fixture`
   - Unblocks: mixin tests under `asyncio_mode="auto"`
3. **Rename `tests/crypto.py` → `tests/test_crypto.py`**
   - Unblocks: 2 currently-undiscovered test functions
4. **Fix `os.environ["TESTING"] = "1"`** in `test_logging_functional.py:47` → `monkeypatch.setenv`
5. ~~**Move `test_engine`, `test_database_sync` from session → function scope**~~ — **skipped, false positive** (both already function-scoped, see Broken-claim #3 correction above)
6. **Remove 11 shadowed fixtures** from `tests/fixtures/core/config_fixtures.py` and `tests/fixtures/download/download_fixtures.py` (root conftest already has them)

### Wave 2: High-impact MockInternal elimination (est. 30–45 hours)

1. ~~**2.1 test_fansly_downloader_ng.py full rewrite**~~ ✅ **DONE 2026-04-24** — file went from 816 → 358 lines (-56%). Deleted 7 junk fixtures (mock_config, mock_args, mock_database, mock_alembic, mock_state, mock_download_functions, mock_common_functions — ~280 LOC of false setup). Replaced 6 main-mocking tests with 9 real `_async_main` integration tests. All use `config_with_database` (real FanslyConfig + real PostgresEntityStore), verify real post-state (`_cleanup_done.is_set()`, atexit registration capture, caplog-captured log messages). `fansly_downloader_ng.main` is patched as the documented seam for testing the wrapper's own exit-code-mapping and cleanup logic. 15 tests pass, 50/50 in tests/core/. See 2.2 below for the coverage work Wave 2.1 explicitly did NOT do.
2. **2.2 fansly_downloader_ng.main() end-to-end coverage** — ✅ **DONE 2026-04-24 — `fansly_downloader_ng.py` now at 100.00% (439 stmts, 0 miss; 130 branches, 0 BrPart).** The only unreachable line (582 — defensive `break` inside the stash-polling loop; `pending_stash` can never become empty between iterations given the filter-then-break pattern at 585-596) has `# pragma: no cover` on its enclosing `if` at line 581, which is why the total stmt count reports as 439 rather than 441. Wave 2.1's rewrite correctly stopped exercising `main()` through mocks; the honest consequence is that `main()` coverage dropped from ~33% (false, via mocks) to 25.57% (real, `_async_main` only). `main()` itself (lines 276-683 of `fansly_downloader_ng.py`, 407 LOC) needed a dedicated end-to-end integration test suite.
   - **Landed 2026-04-24 — coverage lift: 25.57% → 72.00% on `fansly_downloader_ng.py` (+46.4 pts, +181% relative, threshold of 70% crossed).** 22 integration tests in `tests/functional/test_main_integration.py`:
     - Validation error: `test_main_raises_config_error_when_no_creator_names`
     - Per-mode happy path ×6 (parametrized): Timeline, Messages, Wall, Stories, Collection, Normal — via `test_main_completes_per_mode_with_empty_content`
     - Single mode (needs `post_id`): `test_main_completes_single_mode_with_post_id`
     - Stash-only: `test_main_completes_stash_only_mode`
     - Multi-creator iteration (progress-task branch): `test_main_iterates_multiple_creators`
     - Reverse-order multi-creator (line 382 log + sort reversal): `test_main_processes_creators_in_reverse_order`
     - `--use-following` empty list (returns 1): `test_main_use_following_returns_error_when_list_empty`
     - `--use-following` populated (replaces user_names): `test_main_use_following_populates_user_names_from_following`
     - `--use-following` API raises (lines 362-364, 373-375 exception handlers): `test_main_use_following_returns_error_when_api_raises`
     - Partial creator failure (ApiAccountInfoError → SOME_USERS_FAILED): `test_main_continues_when_one_creator_api_fails`
     - Missing client username (ConfigError): `test_main_returns_config_error_when_client_account_missing`
     - Background tasks wait block (lines 536-559, 627-654): `test_main_waits_for_background_tasks_to_complete` — seeds a non-Stash task via download_timeline injection seam, asserts categorization + success-log path
     - Stash background task polling loop (lines 568-617): `test_main_processes_stash_processing_background_task` — seeds a task whose coroutine qualname contains `"StashProcessing"` to trigger stash-specific routing
     - Stash task cancellation on timeout (lines 601-615): `test_main_cancels_stash_task_that_exceeds_timeout` — speeds up the 180-iteration polling loop via `asyncio.sleep` monkeypatch
     - Other-task timeout + cancellation (lines 639-647): `test_main_cancels_other_background_task_that_exceeds_timeout` — short-circuits the 30s gather via `asyncio.wait_for` monkeypatch
     - Walls processing (lines 462-475): `test_main_downloads_walls_when_state_walls_populated` — populates `walls` in the account respx response so `_update_state_from_account` sets `state.walls`
     - Daemon-mode handoff (line 676): `test_main_daemon_mode_invokes_run_daemon` — patches `run_daemon` at its module reference to verify dispatch
     - All 22 tests run in ~80s combined.
   - **`main_integration_env` fixture** — mirrors `respx_stash_processor` at `tests/fixtures/stash/stash_integration_fixtures.py:260`. The fixture owns the `with respx.mock:` and `with fake_websocket_session():` contexts, registers baseline routes (CORS preflight + device/id + account/me + account-by-username with dict-backed pivoting responder), and yields a `MainIntegrationEnv` dataclass with:
     - `config` — ready-to-run FanslyConfig with retries disabled
     - `client_id`, `creator_id`, `creator_name` — default identities
     - `fake_ws` — FakeSocket for WebSocket-send inspection
     - `accounts_by_username` — dict backing the account-lookup responder
     - `add_creator(name, id)` — register an additional creator (multi-creator / following)
     - `register_following_list(ids)` — set up the `/account/{id}/following` responder + account-details lookup
     - `register_empty_content()` — register empty responses for every download mode's bulk endpoint
   - **Test bodies dropped from ~55 LOC (inline setup) to ~8 LOC**.
   - **Supporting fixture modules** (all re-exported from `tests.fixtures` top-level):
     - `tests/fixtures/api/fake_websocket.py`: `FakeSocket`, `auth_response`, `ws_message`, `fake_websocket_session` contextmanager
     - `tests/fixtures/api/main_api_mocks.py`: `MainIntegrationEnv` dataclass, `fansly_json` envelope helper, `main_integration_env` fixture, `run_main_and_cleanup` helper
     - `tests/fixtures/core/main_test_fixtures.py`: `bypass_load_config`, `minimal_argv`, `fast_timing`
   - **Uncovered during implementation** (documented as breadcrumbs): `update_logging_config` isinstance check at logging.py:660 requires the logging module's global `_config` to be populated before `map_args_to_config` calls `set_debug_enabled` — tests call `init_logging_config` explicitly. `timing_jitter` is imported by name into ~8 modules; `fast_timing` patches every call site. Each download mode has its own zero-retry config knob (`timeline_retries=0`, `wall_retries=0`, etc.) otherwise tests take ~60s each in default retry loops. Single mode needs `config.post_id` set explicitly (download/single.py:30). Exit codes in `errors/__init__.py` are NEGATIVE (`SOME_USERS_FAILED = -7`, not `4`).
   - **Second pass, 2026-04-24** — **all residual `main()` items closed + orphaned non-`main()` lines pulled forward.** The original "Remaining" bullet split the file into "`main()` Wave 2.2 scope" vs. "outside `main()` Wave 6 scope", but Wave 6's actual line items (`metadata/story.py`, `daemon/runner.py + bootstrap.py`, `api/websocket.py`, `download/m3u8.py`) do NOT list `fansly_downloader_ng.py` — so the "Wave 6 scope" label was a hidden deferral that would have left those lines orphaned indefinitely. Per `feedback_dont_stop_early.md` ("finish each module completely"), the rest of the file was covered in the same session. Coverage lift: 72.00% → 99.65% (+27.65 pts).
     - **`main()` internal lines closed** (17 new tests):
       - Stash context integration branch (491-504): `test_main_processes_stash_context_branch_success`, `test_main_stash_background_task_failure_sets_exit_code`, `test_main_stash_branch_skips_await_when_no_background_task` — patch `stash.StashProcessing` (the _source_ namespace — the `from stash import` is a late/local import inside `main()`; patching `fansly_downloader_ng.StashProcessing` would not intercept it). Three tests cover: success, raised-task-sets-SOME_USERS_FAILED, and `_background_task` stays None.
       - Exception handlers in background-task block (557-559, 614-624, 648-652, 656-661): `test_main_other_tasks_generic_exception_cancels_pending`, `test_main_stash_cancellation_grace_wait_exception`, `test_main_stash_wait_outer_exception`, `test_main_outer_background_block_cancellederror`, `test_main_outer_background_block_generic_exception`, `test_main_task_qualname_introspection_exception_goes_to_other`, `test_main_stash_task_completes_early_in_polling_loop`. Each test discriminates on its log message ("Error during Stash task cancellation" vs. "Error waiting for Stash tasks" vs. "Error in other background tasks") to confirm the right handler fired. `test_main_outer_background_block_cancellederror` relies on `asyncio.CancelledError` being `BaseException` (not `Exception`) in Python 3.8+, so the bare `except Exception` at 557 lets CancelledError propagate to the outermost `try`.
       - Partial cancellation branches (608→607, 623→622, 645→644): `test_main_other_tasks_timeout_skips_already_done_tasks`, `test_main_stash_outer_exception_cancel_loop_skips_done_tasks`, `test_main_stash_timeout_cancel_loop_skips_done_tasks`. The last uses a `_FlippingDoneTask` shim whose `done()` call counter returns `False` for the first 181 calls (180 polling iterations + 1 filter at line 602) then flips to `True` at call 182 (the cancel-loop check at 608). Coroutine `__qualname__` is writable, so `coro.__qualname__ = "StashProcessing.run"` routes the fake through the stash categorization branch without importing real Stash code.
       - Defensive RuntimeError (301): `test_main_raises_runtime_error_when_validation_leaves_state_unset` — stubs `validate_adjust_config` to a no-op and leaves `user_names` unset; the invariant check at 300-303 fires. Invariant-regression test, not a real scenario.
     - **Non-`main()` lines pulled forward** (40 new tests in `tests/core/unit/test_fansly_downloader_ng.py`, bringing its total from 11 → 51):
       - `_check_stash_library_version` (93): `test_check_stash_library_version_raises_on_too_old`, `..._passes_on_new_enough` — monkeypatches `pkg_version` to return `"0.11.5"` and `"0.14.0"` respectively.
       - `_safe_cleanup_database` (108-139): 7 tests covering no-database, cleanup-already-done, TimeoutError fallback, TimeoutError forced-cleanup-also-fails, generic-exception fallback, generic sync-fallback-also-fails, outer exception. The outer `except Exception` at 136-139 is only reachable when `print_error` itself raises from inside the inner detail handler — patched to raise on the first "Detailed error" message.
       - `cleanup_database` no-database branch (157-158): `test_cleanup_database_no_database_branch`.
       - `cleanup_database_sync` exception (186-187) + interrupted-skip (180-182): 2 tests. The interrupted-skip test uses `monkeypatch.setattr(_handle_interrupt, "interrupted", True, raising=False)` then verifies `_cleanup_done` stays unset.
       - `_handle_interrupt` (203-213): 2 tests covering first-call (sets flag, raises `KeyboardInterrupt`) and second-call (`sys.exit(130)`). An autouse fixture `_clear_handle_interrupt_flag` cleans the module-level `interrupted` attribute before/after every test — otherwise tests that run after these on the same xdist worker see the flag stuck True and behave oddly.
       - `increase_file_descriptor_limit` (216-227): 2 tests covering success + exception paths. Skips gracefully on Windows (where `resource` module is absent).
       - `load_client_account_into_db` exception (253-256): `test_load_client_account_into_db_reraises_on_api_error`.
       - `cleanup_with_global_timeout` (686-803): 13 tests covering WS close success, WS close error (inner handler), WS outer exception (via `print_info` patched to raise on line 695 escaping the outer try), stash task cancellation, stash wait exception, stash identification outer exception (via `config.get_background_tasks` patched to raise on first call), remaining-task cancellation, background-task wait TimeoutError + generic Exception + cancel-loop exception (via task-like shim whose `done()` raises), no-database, DB cleanup exception, DB no-time-remaining, semaphore no-time-remaining (via time.time sequence `[0, 10, 50, 60]` — accounts for stash/bg calls being skipped when those collections are empty), semaphore cleanup exception.
       - `_async_main` partials + finally (812→819, 825, 844-850): 4 tests covering atexit-skip (via `monkeypatch.setattr(atexit, "_exithandlers", [...], raising=False)` — Python 3.11+ removed the public attribute), KeyboardInterrupt-with-interrupted-flag branch, cleanup-cancelled sys.exit(1), cleanup-exception sys.exit(1).
     - **Line 582 — provably unreachable defensive dead code, excluded via `# pragma: no cover`.** `if not pending_stash: break` at the top of the polling loop never fires: `pending_stash` starts non-empty (guarded by `if stash_tasks:` at line 568, initialized at 576) and the only mutation is the filter at line 585; if that filter empties it, the identical check at line 594-596 breaks in the same iteration, so the top-of-loop check at 581-582 never sees an empty list. User-approved one-line production edit on 2026-04-24: `# pragma: no cover` placed on the enclosing `if` at line 581 so coverage.py treats lines 581-582 as intentionally excluded. This is why `fansly_downloader_ng.py` reports 439 stmts / 130 branches rather than 441 / 134. The defensive check is left in place (rather than deleted) to signal intent — future code changes might make it reachable, and the guard would prevent an infinite polling loop in that case.
   - A previous draft of this report buried this item inside a Wave 6 bullet for `metadata/story.py`; that was hidden deferral and has been corrected.
3. ~~**test_validation.py full rewrite**~~ ✅ **DONE 2026-04-24 — `config/validation.py` now at 100% line + 100% branch coverage (213/213 stmts, 100/100 branches).** Full-file rewrite of `tests/config/unit/test_validation.py`:
   - **Eliminated `mock_config = MagicMock(spec=FanslyConfig)` fixture** — replaced with `validation_config` fixture that returns a real `FanslyConfig` with `config_path = tmp_path / "config.yaml"` and realistic defaults (`token = "a" * 60`, `user_agent = "Mozilla/5.0 " + "A" * 60`) so the real `token_is_valid()` / `useragent_is_valid()` methods return True naturally. Tests that want the "invalid" branch set `config.token = "short"` — that's testing the actual production invariant, not a mock's stubbed return value.
   - **Removed all internal-helper patches**: `@patch("config.validation.save_config_or_raise")`, `@patch("config.validation.textio_logger")`, `@patch("config.validation.validate_adjust_creator_name")`, `@patch("config.modes.DownloadMode")`. Tests now exercise the real char/length/space checks, real YAML save into tmp_path, real loguru output captured via caplog.
   - **Replaced `@patch("httpx.get")` with `respx.mock`** — the right tool for HTTP-boundary mocking. Also discovered `guess_user_agent` does OS-specific platform matching (Windows/Darwin/Linux) — UA tests now assert only "UA changed" rather than specific strings to avoid CI-host coupling.
   - **Mock counts**: 221 MagicMock/return_value/@patch occurrences → 15 remaining (all legitimate edge patches: `importlib.util.find_spec`, `config.browser.*` leveldb/firefox helpers, `config.validation.open_get_started_url`/`ask_correct_dir` for UI dialogs, `config.validation.sleep` for 10s wait, `helpers.checkkey.guess_check_key`).
   - **Test count**: 30 → 51 (53 collected items including 3 parametrized). Deeper per-branch tests + added coverage for previously-uncovered partial branches:
     - Line 236 (reprompt error when interactive user types non-y/n): `test_validate_adjust_token_interactive_reprompts_on_invalid_input` — feeds `["maybe", "dunno", "yes"]` sequence.
     - 196->216 (empty leveldb folder list): `test_validate_adjust_token_empty_leveldb_folders_continues`.
     - 201->196 (leveldb folder yields no token → loop continues): `test_validate_adjust_token_leveldb_folder_no_token_continues_loop`.
     - 211->216 (firefox profile yields no token): `test_validate_adjust_token_firefox_no_token_continues`.
     - Line 266 (plyvel + interactive + no account → `open_get_started_url` fires): `test_validate_adjust_token_plyvel_installed_interactive_opens_started_url`.
     - 402->391 (check_key new-key rejected then re-entered + accepted): `test_validate_adjust_check_key_user_rejects_new_key_then_accepts`.
   - **321 tests pass in `tests/config/`** — no regressions from the rewrite.
   - File went 734 → 1062 lines. Larger footprint because docstrings explicitly document why each remaining patch is legitimate-edge and what real code path each test exercises — a meaningful replacement for the old mock-heavy style that gave the appearance of coverage without exercising the real logic.
4. ~~**2.3 test_m3u8.py violations**~~ ✅ **DONE 2026-04-24 — `download/m3u8.py` now at 100.00% (294/294 stmts, 84/84 branches).** Full rewrite + significant coverage expansion; lifted from 60.88% → 100% (+39.12 pts).
   - **Eliminated all `@patch("pathlib.Path.exists/stat/unlink")`** — production code runs its real filesystem checks against `tmp_path` files created by fake objects' `.close()` / `.run()` methods (the write-real-bytes-as-side-effect pattern).
   - **Eliminated `MagicMock(spec=FanslyConfig)`** — replaced with real `FanslyConfig(program_version="0.13.0-test")` plus a small `_FakeApi` namespace object attached at `config._api` exposing only `get_with_ngsw`.
   - **Moved library patches from module-level to leaf-level** — was `@patch("download.m3u8.ffmpeg")` / `@patch("download.m3u8.av")` (stubbed entire modules); now `monkeypatch.setattr(ffmpeg, "input", ...)`, `monkeypatch.setattr(ffmpeg, "probe", ...)`, `monkeypatch.setattr(av, "open", ...)` — each test picks only the specific library entry points it cares about; production code's cookie formatting / URL building / stream mapping / packet loop logic all runs real.
   - **Stateful fakes** for `_try_direct_download_pyav` and `_mux_segments_with_pyav`: `_FakeAVStream`, `_FakeAVPacket`, `_FakeInputContainer`, `_FakeOutputContainer`, `_MuxInputContainer` classes with parameterized streams/packets/exception-behavior. This enabled deterministic per-test control of the corrupt-packet skip path, the >25% abort threshold, the per-segment exception handler, and the finally-block output.close() cleanup — all previously untested.
   - **New `_SyncExecutor`** — synchronous drop-in for `concurrent.futures.ThreadPoolExecutor`. coverage.py doesn't track threaded code by default (`concurrency=["thread"]` isn't configured project-wide), so the real `download_ts` closure inside `_try_segment_download` (lines 593-618) was invisible to coverage. Swapping in `_SyncExecutor` with a minimal `__enter__`/`__exit__`/`map` protocol lets the real code path execute synchronously; coverage instrumentation sees every line.
   - **Test count**: 30 → 57. Five new test classes including two entirely new ones: `TestMuxSegmentsWithPyAV` (8 tests, lines 418-516 — previously 0% coverage) and `TestMuxSegmentsWithFFmpeg` (4 tests, lines 532-555 — previously 0% coverage).
   - **Orchestration tests** (`TestDownloadM3U8ThreeTierStrategy`) still patch the three wrappers — that's the documented orchestration seam. An in-file comment block explicitly links each patched wrapper to its dedicated test class below, closing the audit's linkage-checkpoint requirement. Added two new orchestration tests: FFmpeg-fallback created_at (covers line 727) and M3U8Error-reraised-untouched vs generic-Exception-wrapped.
   - **230 tests pass in `tests/download/`** — integration tests at `tests/download/integration/test_m3u8_integration.py` (which patch the same wrappers at a higher level) still green. The linkage checkpoint in the original audit entry — "before finishing this cleanup, verify the migrated versions of `TestDirectDownloadFFmpeg`/`TestDirectDownloadPyAV`/`TestSegmentDownload` still exercise the wrappers' real logic end-to-end" — is satisfied: all three classes now patch only at the leaf av/ffmpeg library level while every wrapper's internal orchestration runs its real packet loop, file verification, error handlers, and finally-block cleanup.
   - **Discovered gotchas documented**: `av.error.FFmpegError.__init__` takes `(code, message, [filename, [log]])` — not a single string. Constructing with just `"..."` raises TypeError (which silently falls into the generic Exception handler, fooling coverage reports). `coroutine.__qualname__` is writable but class instances' `__qualname__` is not — only real functions/coroutines/classes have the dunder accessible via instance lookup.
   - **⚠️ Linkage checkpoint**: `tests/download/integration/test_m3u8_integration.py::test_full_m3u8_download_workflow` (and siblings in that file) justify their `@patch("download.m3u8._try_direct_download_*"/"_mux_segments_*")` decorators by pointing to dedicated coverage here: `TestDirectDownloadFFmpeg` (line 412), `TestDirectDownloadPyAV` (line 528), `TestSegmentDownload` (line 623). **Before finishing this cleanup**, verify the migrated versions of those three classes still exercise the wrappers' real logic end-to-end. If Wave 2 leaves any wrapper's behavior only weakly tested (e.g., because the replacement pattern mocks more deeply than the current `ffmpeg`/`av` leaf patches), the integration test's orchestration-seam justification stops holding and `test_full_m3u8_download_workflow` will need to be migrated off the wrapper patches too (probably by patching at the `av.open` / `subprocess.run` leaf level instead).
5. ~~**2.4 test_timeline_download.py, test_messages_download.py**~~ ✅ **DONE 2026-04-24 — combined `download/timeline.py` + `download/messages.py` at 99.55% (timeline 99.05%, messages 100.00%).** Full real-pipeline rewrite:
   - **Eliminated internal-function patches**: `@patch("download.timeline.check_page_duplicates")`, `@patch(".process_timeline_posts")`, `@patch(".process_timeline_media")`, `@patch(".fetch_and_process_media")`, `@patch(".process_download_accessible_media")`, `@patch("download.messages.process_groups_response")`, `@patch(".process_messages_metadata")` — all removed. The orchestrators now run real code against the real PostgreSQL database backing `entity_store`.
   - **Fixture pattern**: `respx_fansly_api + entity_store + mock_config + tmp_path + monkeypatch`. `mock_config` is a misnomer (it's a REAL FanslyConfig) and `entity_store` registers the store globally (`FanslyObject._store`) so production `get_store()` calls resolve to the test DB.
   - **Leaf-level patches ONLY**: `download_media` (the real CDN downloader — patched at BOTH `download.common.download_media` AND `download.media.download_media` because `common.py` imports it at module scope — patching only the source doesn't intercept the bound name); `asyncio.sleep` imported at module scope; `input_enter_continue` at three binding sites.
   - **Test counts**: timeline 17 tests (was 6), messages 11 tests (was 4) — deeper coverage including cursor-advance IndexError, cursor-advance generic-Exception→ApiError wrap, interactive vs non-interactive handler branches, daemon-path `download_messages_for_group` inference branches, empty-username Account cached-but-no-name partial, plus a real-DuplicatePageError test that pre-seeds a Post via `Post.model_validate` into the identity map and verifies the production cache-lookup triggers the raise.
   - **Three hidden production-code traps discovered and documented** in the project memory at `project_fansly_payload_shape_requirements.md` for future authors:
     1. AccountMedia payloads MUST include a nested `"media"` dict — `process_media_info` persists the nested Media FIRST to satisfy `account_media_mediaId_fkey`. Missing: FK violation caught by the orchestrator's catch-all Exception handler, silent break, empty coverage.
     2. AccountMedia MUST include `"previewId"` key (even as `None`) — `parse_media_info:92` accesses it unconditionally. Missing: KeyError caught by `fetch_and_process_media`'s silent inner try/except, `accessible_media` returns empty, all downstream asserts fail with no visible log.
     3. CDN URLs in test `media.locations[].location` MUST include `Key-Pair-Id=...` in the query string — `media/media.py:185` calls **raw `input()`** (not `input_enter_continue`) when Key-Pair-Id is absent. In test environments with no stdin: EOFError, same silent empty-list symptom.
   - **Debugging methodology** (now memorialized in `feedback_dump_fansly_calls_first.md`): `try/finally + dump_fansly_calls(respx.calls)` is the FIRST tool, not a post-mortem. When `dump_fansly_calls` showed `/account/media` never fired, a 30-second `parse_media_info` trace monkeypatch surfaced each missing key in sequence. Without `dump_fansly_calls`, the empty-accessible-media symptom is undifferentiated from "everything is broken."
   - **Both files combined**: 246 tests pass in `tests/download/`, no regressions. Final coverage: `download/timeline.py` **100.00% line + 100.00% branch**, `download/messages.py` **100.00% line + 100.00% branch**.
   - **The "unreachable" 149->127 partial branch** was actually reachable for any 2xx-non-200 response: `httpx.Response.raise_for_status()` raises on 1xx/3xx/4xx/5xx but lets 200-299 through. Practical realistic case: 204 No Content if Fansly ever changes a "no posts" response from `200 + empty arrays` to `204 + no body`. Test pattern: `respx.get(...).mock(side_effect=[Response(204), Response(200, posts=[X], accountMedia=[])])` — iter 1's 204 trips the False branch, iter 2's 200 enters the empty-media retry path which exhausts the attempts budget and exits cleanly. No production code change, no `# pragma`, just a real test case for the unlikely-but-possible edge. **This is only writable because of the project's `ALWAYS side_effect=[], NEVER return_value=` rule** — `return_value` would serve the same response on both iterations, making sequencing impossible.
6. ~~**2.5 test_stories.py gate_invocations fixture**~~ ✅ **DONE 2026-04-24 — `download/stories.py` at 100.00% line + 100.00% branch.** Full real-pipeline rewrite + collateral fixture-bug fix.
   - **Eliminated the `gate_invocations` FakeSpy fixture** that monkeypatched FIVE module-level names (`stories_module.get_store`, `api.get_media_stories`, `stories_module.process_media_stories`, `stories_module.fetch_and_process_media`, `stories_module.process_download_accessible_media`) plus a spy-list on `_mark_stories_viewed` itself.
   - **Replaced with real-HTTP boundary verification**: the gate is now asserted by counting calls to the REAL `respx.post(.../mediastory/view)` route (`mark_view_route.call_count == 1` for `mark_viewed=True`, `== 0` for `mark_viewed=False`). This is a strictly stronger guarantee than the spy-list assertion — it would also fail if `_mark_stories_viewed` itself regressed (URL change, body shape, missing call), not just if the gate logic regressed. The daemon's "don't mark viewed in background" invariant is now protected against a much wider blast radius.
   - **Test count**: 4 → 11. Added: real `_mark_stories_viewed` connection-error swallow test (covers lines 106-107 via `httpx.ConnectError` instead of just 500 response which doesn't raise from httpx); cached-MediaStoryState early-exit test (line 48, with FK-satisfying Account pre-seed); empty-mediaStories-list early return; empty-accountMedia "no downloadable media" branch (78-79); outer-Exception swallow (92-93); creator_id=None cache-skip (45->50 partial branch).
   - **Pre-seeded Account row** for tests that exercise `process_media_stories` or `MediaStoryState` cache — both have FKs to `accounts.id` (`mediaStories_accountId_fkey`, `media_story_states_accountId_fkey`). Without the seed, `download_stories`'s outer `except Exception` silently swallows the FK violation and the test passes for the wrong reason. New helper `_seed_creator_account(entity_store, creator_id, username)` reused across multiple tests.
   - **Collateral fixture fix**: `dump_fansly_calls(route.calls)` previously crashed with `ValueError` when respx's `side_effect` was an Exception (no Response produced) — `call.response` raises if `optional_response is None`. Fixed by switching to `call.has_response` + `call.optional_response` (the canonical respx API for this case). Now prints `"NO RESPONSE (exception raised)"` for those calls instead of crashing the test's `finally:` and masking the real failure. Project-wide improvement: any future respx test using exception-side-effects (like `httpx.ConnectError`, `TimeoutException`) now gets clean dump output. `tests/fixtures/api/api_fixtures.py:283-323`.
   - **Collateral feedback memory** captured at `feedback_dump_then_assert.md`: assertions go AFTER `try/finally`, never inside the `try`. Pattern: `try` contains only the function-under-test, `finally` contains only cleanup/diagnostic, `assert` lines come at the test's outermost indentation level so the dump always runs unconditionally before any assertion failure.
   - **252 tests pass in `tests/download/`** — three modules now at 100%/100%: `download/timeline.py`, `download/messages.py`, `download/stories.py`.
7. **2.6 test_runner_wiring.py FakeSpies (7) — DONE 2026-04-24** (file fully transitioned to real-pipeline pattern; three production bugs discovered + fixed in the same patch):

   ### Production bugs surfaced + fixed by mock removal (per `feedback_remove_mocks_fix_bugs.md`)
   - **Bug #1**: `daemon/runner.py:279` was `await download_wall(config, state)` — missing the required `wall_id` positional argument. Every `FullCreatorDownload` from the daemon WS bus raised `TypeError`, was caught + re-raised by `_handle_full_creator_item`'s outer except, and tripped the worker loop's error budget on the wall step. Shipped in v0.13.0 (the daemon GA release). Fix mirrors `fansly_downloader_ng.py:460-471`'s legacy convention: `if state.walls: for wall_id in sorted(state.walls): await download_wall(config, state, wall_id)`.
   - **Bug #2**: `daemon/runner.py:_refresh_following` constructed `state = DownloadState()` with no `creator_id` and called `get_following_accounts(config, state)` directly. `get_following_accounts` immediately raised `RuntimeError("client ID not set")`, the outer `try/except Exception` swallowed it, and the refresh silently no-op'd in production. Fix adds `await get_creator_account_info(config, state)` before the following-list call (mirrors `fansly_downloader_ng.py:353-361`'s legacy convention — runs the client-account variant which sets `state.creator_id` because `state.creator_name` is None).
   - **Bug #3**: `daemon/runner.py:_handle_timeline_only_item` initialized `state = DownloadState(creator_id=item.creator_id, creator_name="")` — empty string, not None. `_get_account_response` checks `if state.creator_name is None` to choose between client/creator API variants; empty string fell into the creator branch and produced `/account?usernames=` with no value. Fix mirrors `_handle_full_creator_item:260` — resolve `creator_name = await _resolve_creator_name(item.creator_id)` first; skip with warning if unknown; construct state with the resolved name.
   - All three bugs were INVISIBLE to the previous internal-mock tests — `AsyncMock(spec=download_wall)` accepted any args, mocked `_refresh_following` never ran the real code, mocked `get_creator_account_info` accepted empty creator_name silently. Mission of the branch validated in three independent instances.

   ### Test rewrites

   **First pass (intermediate state)**: replaced 7 nonlocal-flag spy functions with `patch("daemon.runner.<func>", new=AsyncMock(spec=<func>))`. Advisor flagged this as spec-narrowing, not removal — the audit's Goal #1 ("remove all internal mocks") was not advanced.

   **Second pass — pull-forward to real-pipeline (per `feedback_no_within_file_phase_splits.md`)**: rewrote ALL dispatch tests to assert via real Fansly HTTP boundaries instead of `daemon.runner.*` mocks:
   - **First pass (intermediate state)**: replaced 7 nonlocal-flag spy functions with `patch("daemon.runner.<func>", new=AsyncMock(spec=<func>))`. The advisor flagged this as spec-narrowing, not removal — `daemon.runner.download_timeline` is not an edge boundary, and the audit's Goal #1 ("remove all internal mocks") was not advanced.
   - **Second pass — pull-forward to real-pipeline pattern (per the precedent set in Wave 2.2 for `fansly_downloader_ng.py`)**: rewrote 3 of the 4 dispatch tests to assert via real Fansly HTTP boundaries instead of `daemon.runner.*` mocks:
     - `test_timeline_only_calls_download_timeline` — now drives `_handle_timeline_only_item` end-to-end through `respx.get(/api/v1/account)` + `respx.get(/api/v1/timelinenew/{id})`. Account route MUST fire (real `get_creator_account_info`); timeline route MUST fire (real `download_timeline`). Empty timeline response terminates pagination on page 1 to keep the test fast and minimize wiring. Real coverage of `daemon/runner.py:411-414` (handler happy path).
     - `test_timeline_only_does_not_call_stories_or_messages` — same pipeline as above, plus mounted-but-not-expected `mediastoriesnew` and `group` routes. Asserts `stories_route.call_count == 0` and `messages_route.call_count == 0` — fires not only if the dispatch table breaks but also if the handler ever bypasses dispatch by calling those endpoints directly.
     - `test_download_stories_only_passes_mark_viewed_false` — clone of `tests/download/unit/test_stories.py`'s mark-viewed-false pattern, routed through `_handle_stories_only_item` so `get_creator_account_info` also fires. **Critical regression guard**: `mark_view_route.call_count == 0` on the real `/api/v1/mediastory/view` endpoint. This protects the daemon's "don't mark stories viewed in the user's real Fansly account" invariant against URL changes, body-shape regressions, or anyone routing around `_mark_stories_viewed`. Real coverage of `daemon/runner.py:387-388` (handler happy path).
   - **Truly resolved (no production call site to mock)**:
     - `test_session_baseline_first_call_then_none` — the original `patch("daemon.runner.should_process_creator", side_effect=_spy_should_process)` was dead code (the test called `_spy_should_process` directly, never through the runner). Removed the dead patch; replaced the spy with `AsyncMock(spec=should_process_creator, return_value=True)` and asserted via `spy.call_args_list[i].kwargs["session_baseline"]`. This is a logic test for the consumption-pattern, not a dispatch test, so there is no "real-pipeline rewrite" pending.
   - **Third pass — pull-forward of all "Wave 6 carry-over" tests** (per user pushback: _"deferring to wave 6 is not acceptable, just like the multiple times I had to correct the agent on how it tried splitting work in to multiple phases on the same file"_):
     - `test_full_creator_download_passes_mark_viewed_false` — pulled forward; full 5-step download pipeline (account → timeline → stories(mark_viewed=False) → messages → walls) wired as real respx routes. **Critical regression guard**: `mark_view_route.call_count == 0` against `_handle_full_creator_item`. **Surfaced Bug #1 (`download_wall` signature)**.
     - `test_full_creator_download_with_uf_refreshes_following` — pulled forward; drives `_worker_loop` end-to-end → real `_handle_full_creator_item` → real `_refresh_following` → real `get_creator_account_info` (client variant) → real `get_following_accounts`. Asserts `following_route.call_count >= 1` on the real `/api/v1/account/{client_id}/following` boundary. **Surfaced Bug #2 (`_refresh_following` missing client-account-info call)**.
     - `test_full_creator_download_without_uf_skips_refresh` — pulled forward; same handler pipeline, but `use_following=False` short-circuits the refresh branch. Asserts `following_route.call_count == 0` on the mounted-but-not-expected route — stronger than the previous `nonlocal refresh_called` flag because it would also fire if any code path ever bypassed `_refresh_following` to hit `/following` directly.
     - `test_timeline_only_with_uf_refreshes_following` — pulled forward; same as the FullCreator with-uf variant but routed through `DownloadTimelineOnly` to confirm the worker-loop's `isinstance(item, (FullCreatorDownload, DownloadTimelineOnly))` post-success refresh check fires for both item types. **Surfaced Bug #3 (`_handle_timeline_only_item` empty creator_name)**.
   - **Reusable helpers added to `tests/fixtures/api/main_api_mocks.py`** (per the fixture-location policy — when in doubt, fixtures live in `tests/fixtures/`, not inlined):
     - `mount_client_account_me_route(client_id, client_username)` — registers `/api/v1/account/me` with the `{"account": {...}}` envelope shape that `_extract_account_data` expects for the client variant.
     - `mount_empty_following_route(client_id)` — registers `/api/v1/account/{client_id}/following` with an empty list response, terminating `_get_following_page` on the first page.
     - `mount_empty_creator_pipeline(creator_id, creator_name)` — registers the four per-creator endpoints (`account?usernames=`, `timelinenew/{id}`, `mediastoriesnew`, `messaging/groups`) with empty responses suitable for any daemon-dispatch test that wants to exercise the handler pipeline without downloading anything. Returns `dict[str, respx.Route]` so callers can assert per-route call_count.
   - All three helpers are exported from `tests.fixtures.api` so the import is `from tests.fixtures.api import mount_*` (per audit lines 47-48: import from package root, not submodules).
   - **Imports added**: `from download.core import download_messages, download_stories, download_timeline, download_wall, get_creator_account_info` at file top (PEP 8 compliant). All five remain in use by the test 4 rewrite which references them via `mount_empty_creator_pipeline` indirectly.
   - **Module-level helper**: `_account_response_payload(creator_id, creator_name)` at top of file, includes the mandatory `timelineStats` substructure (without it, `_update_state_from_account` raises `ApiAccountInfoError("you most likely misspelled it!")` and the entire pipeline aborts).

8. **2.7 test_runner_wiring.py \_worker_loop direct assignment (line 179, 197) — DONE 2026-04-24** (truly closed):
   - (see entry 8 below for the test_runner_wiring.py 2.7-specific notes)

9. **2.9 Category D #2 — `test_fansly_downloader_ng_extra.py` DELETED 2026-04-25** (executed AFTER 2.8 below): file audit found 8 of 9 tests were duplicates of the already-rewritten `test_fansly_downloader_ng.py` work (Cat-D item 1), zombies, or inline-mocked variants of paths covered elsewhere:
   - `test_increase_file_descriptor_limit_success/failure` — DUPLICATES of `test_fansly_downloader_ng.py:1373/1399`.
   - `test_handle_interrupt` — **ZOMBIE TEST**. Defined a fake handler local to the test (`def test_handler(signum, frame):`), then called the fake handler and asserted on its own state. Never invoked the real `_handle_interrupt`. Real coverage exists at `test_fansly_downloader_ng.py:628/663`.
   - `test_load_client_account_into_db_failure` — DUPLICATE of `test_fansly_downloader_ng.py:1429`.
   - `test_cleanup_database_sync_success/failure` — DUPLICATES of `test_fansly_downloader_ng.py:55/132`.
   - `test_cleanup_database_no_database_async` — semantically DUPLICATE of `test_fansly_downloader_ng.py:165`.
   - `test_main_invalid_config` — patched 7 internal functions (`load_config`, `set_window_title`, `update_logging_config`, `validate_adjust_config`, `validate_adjust_check_key`, `parse_args`, `setup_api`/`get_api`); covered by `test_main_use_following_returns_error_when_api_raises` in `tests/functional/test_main_integration.py` (audit line 421).
   - Commented-out `test_async_main` block (lines 168-202) — dead code.
   - **The 1 unique test** (`test_load_client_account_into_db_success`) had no positive-path equivalent in `test_fansly_downloader_ng.py`. Pulled forward as `test_load_client_account_into_db_persists_real_account`: uses the canonical Wave 2 pattern (`respx_fansly_api + config_with_database + fansly_api + entity_store + monkeypatch`), real `respx.get(/api/v1/account?usernames=...)` HTTP boundary, asserts via `store.get(Account, creator_id) is not None` instead of the original `mock_process.assert_called_once_with(...)`. The original used `MagicMock(spec=FanslyConfig)` and a fake API + fake response. (First draft of the rewrite used a hand-rolled `_FixedApi` namespace class — flagged by the advisor as a half-step / wrong-layer mock; corrected in the same commit to use the established `respx_fansly_api` pattern.)
   - **Cleanup**: removed inline `from download.downloadstate import DownloadState` from the failure test (PEP 8 — `feedback_pep8_imports.md`) and added `httpx`, `DownloadState`, `snowflake_id` to file-top imports.
   - **Verification**: `tests/core/ + tests/functional/` → `fansly_downloader_ng.py` still at 100.00% line + 100.00% branch (123 tests). Full suite: 2283 passed (was 2291; -9 deleted + 1 added = -8 net).
     **Note**: numbering above (2.6, 2.7, 2.9, 2.8) reflects a documentation-order issue, not a chronological one — 2.8 (the cluster directly below) executed BEFORE 2.9 (extra.py deletion). Reading order: 2.6 → 2.7 → 2.8 → 2.9 → 2.10. The cluster entry is preserved in its original position to keep the existing line numbers stable for reference.

10. **2.10 Category D #4 — `test_fanslyconfig.py` rewritten 2026-04-25 — `config/fanslyconfig.py` now at 100% line + 100% branch**: removed `MagicMock(spec=FanslyApi)`, `patch("config.fanslyconfig.FanslyApi")`, `patch("api.rate_limiter.RateLimiter")`, `patch("config.fanslyconfig.StashContext")`, and `patch.object(config, "get_stash_context"/"get_api"/"_save_config")` patches across the file. Replaced with real `FanslyApi`, `RateLimiter`, and `StashContext` instances — all three constructors are pure attribute initialization with no network I/O. Only edges patched: `RateLimiterDisplay.start` (would spawn a daemon thread running a Rich live display that interferes with pytest output capture), `FanslyApi.setup_session` (the actual HTTP session-setup call), `FanslyApi.login` (HTTP login call). One intentional `MagicMock(spec=asyncio.Task)` retained per the audit's infrastructure-exception rule (asyncio.Task can't be constructed without an event loop + real coroutine; spec'd-mock is the standard pattern for inspectable task-list assertions).

- **`get_stash_api` clarification (advisor-prompted finding)**: my first pass would have deleted `FanslyConfig.get_stash_api` as dead code on the basis of "zero callers." User pointed out that there are places in `stash/` that use the StashClient directly. Investigation confirmed the production pattern (`stash/processing/base.py:293→367→334,343`): `config.get_stash_context()` → `await context.get_client()` (async init) → `context.client` (sync property). `get_stash_api` is the sync convenience accessor for "give me the already-initialized client" — it requires step 2 to have run first. The original test passed only because it mocked StashContext entirely. Rewritten to inject a sentinel `_client` (testing the delegation, not the async initialization which is StashContext's responsibility), plus a second test documenting the `RuntimeError("Client not initialized")` requirement when callers skip step 2.
- **Branch coverage pull-forward** (per finish-the-file rule): added `test_login_success_persists_returned_token` (covers `get_api` lines 235-236 — the success branch of the username/password login flow that mutates `config.token` and calls `_save_config`) and `test_returns_none_path_raises_failed_to_create` (covers line 241 — the defensive RuntimeError when the outer `if user_agent and check_key and (token_is_valid or has_login_credentials):` guard at line 202 evaluates False).
- **`no_display` fixture added** (file-local, not promoted to `tests/fixtures/` because it's an implementation-detail patch specific to this file). Disables `RateLimiterDisplay.start`'s daemon-thread spawn while leaving the rest of the real `get_api` wiring intact.
- **Test counts**: 22 → 26 (4 added: 2 stash-pattern tests + 2 branch-coverage tests; some original 22 were consolidated/simplified into pure-data tests under `TestFanslyConfigBasics`). All 26 pass; full suite 2287 (was 2283, +4 net).

13. **2.11 Category D #11 — `tests/stash/processing/unit/media_mixin/test_error_handlers.py` rewritten 2026-04-25**: 4 internal-mock violations replaced; the production exception handlers at `stash/processing/mixins/media.py:197,240` are now genuinely covered for the first time.

- **Critical finding (captured as `feedback_dead_patches_pass_for_wrong_reason.md`)**: all 4 patches were targeting **dead method names**. The original test patched `client.find_image`, `client.find_scene`, `client.find_scenes_by_path_regex`, `client.find_scenes` — but production at `mixins/media.py:191,234,382,396` has been calling `self.store.get_many(Image|Scene, ...)` and `self.store.find_iter(Scene, path__regex=...)` instead. `grep -rn` across `stash/` confirmed zero production calls to the patched names. The tests "passed" coincidentally because the empty store returned `[]` without the production exception handler ever firing.
- **Two layering decisions** during the rewrite:
  - **Image/scene batch tests (197/240)**: my first pass mocked the GraphQL POST with `httpx.ConnectError`. Coverage check showed lines 197-198 + 240-241 STILL missing — the exception wasn't propagating because `stash-graphql-client`'s `StashObject.find_by_id` (`types/base.py:1847-1852`) swallows ALL exceptions internally and returns None. So transport errors never reach `store.get_many`'s caller. Switched to patching `store.get_many` directly — per CLAUDE.md "External lib leaf calls — patch the leaf call only", `store` is a `StashEntityStore` from the external `stash-graphql-client` package, so this IS the external-lib leaf boundary. Coverage confirmed 197-198 + 240-241 moved out of the missing list.
  - **Regex fallback tests (388/402)**: `find_iter` does NOT swallow exceptions, so GraphQL POST mocking with `httpx.ConnectError` propagates correctly through `_execute_find` → `find_iter` → production try/except. Tests use `side_effect=[ConnectError, valid_empty_response]` for the "fallback succeeds" variant and `side_effect=[ConnectError, ConnectError]` for the "fallback also fails" variant.
- **Test counts**: 18 tests in file (unchanged); 18/18 pass; full suite 2287 (unchanged from prior).
- **Coverage delta on `stash/processing/mixins/media.py`**: targeted exception lines 197-198 + 240-241 newly covered (line 388 already covered by the regex_fallback test that pre-existed). Module total still ~33% from this file alone — most of the file is covered by integration tests in `tests/stash/processing/integration/`.

14. **2.8 Category D #8 + #9 + #16 cluster — DONE 2026-04-25**: three small download-orchestrator tests pulled forward in one session per the no-within-file-deferral + finish-the-file rules.

- **`tests/download/unit/test_collections.py` (Cat-D #8) — `download/collections.py` 100% line + 100% branch**: removed 2 internal patches (`download.collections.fetch_and_process_media`, `download.collections.process_download_accessible_media`) across 3 of 4 tests. Real-pipeline pattern using `respx_fansly_api + entity_store + mock_config + tmp_path + monkeypatch`. New helper `_account_media_payload` reuses the shape requirements from `project_fansly_payload_shape_requirements.md`. Critical bug-finding deltas: tests now assert via the real `/api/v1/account/media` route firing AND real persistence (`store.get(Media, media_id)` returns the persisted row) AND CDN leaf invocation count, replacing the previous `mock_fetch.assert_called_once()` which would have silently passed any internal regression. Discovered + documented: `set_create_directory_for_download` requires `state.creator_name` (production callers populate it via `get_creator_account_info` first); empty `accountMedia` still triggers `download_media(config, state, [])` because the iterator is the boundary, so the semantic check is "called with empty list" not "not called".
- **`tests/download/unit/test_single_download.py` (Cat-D #9) — `download/single.py` 100% line + 100% branch (1 unreachable defensive guard pragma'd)**: removed 4 internal patches (`download.single.process_timeline_posts`, `fetch_and_process_media`, `process_download_accessible_media`, `dedupe_init`) across all 4 success-path tests. Pulled forward from 4 tests to 6 — added `test_interactive_input_loop_rejects_invalid_then_accepts` covering the `while True: input(...)` prompt loop (lines 36-48, previously fully uncovered). Discovered: real Pydantic `Post.model_validate` enforces Snowflake range (≥10^15) — original test IDs `123456789` etc. were invalid and only worked because `process_timeline_posts` was mocked; switched all to `snowflake_id()`. Discovered: real `process_timeline_posts` enforces `posts_accountId_fkey` — the no-content test response previously had empty `accounts: []` which would have FK-failed; added the post's author to the response (matches production-API behavior). Pragma'd `download/single.py:69` (the `if creator_username is None:` guard whose False branch is structurally unreachable since `creator_username` is initialized `None` at line 60 and only set inside that block).
- **`tests/fixtures/download/download_fixtures.py` (Cat-D #16)**: deleted entirely. Both fixtures (`mock_process_media_download`, `mock_process_media_bundles`) had ZERO consumers in the test suite (only re-exported from `tests/fixtures/__init__.py`). Confirmed via `grep -rn` across all of `tests/`. Per CLAUDE.md "If you are certain that something is unused, you can delete it completely." Removed the file plus the corresponding `__all__` exports from `tests/fixtures/download/__init__.py` and `tests/fixtures/__init__.py`. Encountered organically while doing Cat-D #8/#9 — surfaces the kind of finding the audit's scheduled "Follow-up: audit `tests/fixtures/`" was designed to catch.
- **Verification**: 2291/2291 pass (was 2290, +6 new tests across the two files net of any consolidation), full-suite coverage 92.80% maintained. `download/collections.py` and `download/single.py` both at 100%/100%.
- **Pivoted from the original `patch.object(runner, "_worker_loop", wraps=_instrumented_worker)` recommendation to `monkeypatch.setattr("daemon.runner._worker_loop", _instrumented_worker)`**. `wraps=` would have delegated to the real `_worker_loop`, which would consume the queue and force the test to wire up a full handler pipeline. The test only needs to verify "the worker loop received the bootstrap's queue object" — replacement (not delegation) is the right semantic.
- **`monkeypatch.setattr` over manual try/finally**: pytest's `monkeypatch` fixture restores the attribute even if the test raises BEFORE the `try:` (e.g., during fixture binding or `await asyncio.sleep(0.05)`). The original manual try/finally only restores if execution reaches the `try`.
- **Collateral cleanups**: removed the now-unused `from daemon import runner as _runner` lazy import, the `original_worker = None` placeholder, and the manual try/finally restore. Added `monkeypatch` to the test signature.
- This is NOT a "remove internal mock" item — it's a "stop bypassing pytest's fixture infrastructure" item — so it's complete on its own merits regardless of Wave 6's broader rewrite.
- **Verification**: `pytest tests/daemon/ -n8 --cov=daemon` — 171/171 pass; `daemon/runner.py` coverage 53.42% → 53.97% (small bump from Bug #3's `_resolve_creator_name` resolve+skip path now exercised by the timeline-only real-pipeline test). Remaining gap is Wave 6 scope: error-budget paths, five-loop coordination, backfill interruption — all of which require driving the daemon loops with simulated time, not the dispatch-table tests this file covers.

15. **2.12 Category D #12 — `tests/stash/processing/integration/test_base_processing.py` rewritten 2026-04-24**: 3 internal-mock violations replaced; the production exception flow at `stash/processing/base.py:343-346` (wait_for_job retry + the broadened `(RuntimeError, ValueError)` catch) and `stash/processing/base.py:197-198` (preload exception swallow) are now genuinely covered.

- **Production bug surfaced (Bug #4 in `feedback_remove_mocks_fix_bugs.md`)**: `stash/processing/base.py:346` originally caught `except RuntimeError as e` only. The underlying `stash_graphql_client.metadata_scan` actually raises `ValueError("Failed to start metadata scan: ...")` on transport failures (`stash_graphql_client/client/mixins/metadata.py:201-203`), not `RuntimeError`. The original test patched `client.metadata_scan` with `side_effect=RuntimeError(...)` — wrong exception type matching the wrong production catch — so it passed coincidentally. Fix: broadened to `except (RuntimeError, ValueError) as e` with a comment naming the lib's failure shape. Sibling pattern of `feedback_dead_patches_pass_for_wrong_reason.md`: not a dead patch (`metadata_scan` IS called by production), but a wrong-shape patch that hid the real exception type.
- **Three layering decisions** during the rewrite:
  - **Site 1 (`test_scan_creator_folder_metadata_scan_error`)**: replaced `patch.object(client, "metadata_scan", side_effect=RuntimeError(...))` with `patch.object(client, "execute", side_effect=Exception(...))`. The gql leaf is the rule-compliant external boundary; the real `metadata_scan` then runs end-to-end and raises its documented `ValueError` shape, exercising the broadened production catch.
  - **Site 2 (`test_scan_creator_folder_wait_for_job_retry`)**: replaced `patch.object(client, "wait_for_job", side_effect=[Exception, True])` with an orchestrated 4-call `client.execute` `side_effect`: (1) `CONFIG_DEFAULTS_QUERY` raises → swallowed by `metadata_scan`'s inner try/except, hardcoded defaults used; (2) `METADATA_SCAN_MUTATION` returns `{"metadataScan": "test-job-1"}`; (3) `FIND_JOB_QUERY` (call 1) raises → swallowed by `find_job`, returns `None` → `wait_for_job` raises `ValueError("Job not found")` → production loop's `except Exception` catches → retries; (4) `FIND_JOB_QUERY` (call 2) returns a `FINISHED` Job dict → `wait_for_job` returns `True` → loop exits. This exercises the full real chain rather than a single-method mock.
  - **Site 3 (`test_preload_creator_media_exception`)**: replaced `patch.object(processor, "_index_scene_files", side_effect=RuntimeError(...))` with `patch.object(store, "find_iter", side_effect=RuntimeError(...))`. `find_iter` is the external-lib leaf the production `async for` actually iterates over, and unlike `find_by_id` it does NOT swallow internally. The patch fires synchronously when `store.find_iter(Scene, ...)` is called from base.py:179, and the production `except Exception` at base.py:197-198 catches it.
- **Test counts**: 19 tests in file (unchanged); 19/19 pass.
- **Coverage delta on `stash/processing/base.py`**: production fix at line 346 newly covered (was previously a dead branch since the original test's RuntimeError side_effect matched the narrow catch by coincidence rather than realism). Lines 197-198, 343-345 also covered. Module total: 73.02% (file-only run; full integration suite covers more).
- **Knock-on test update** in `tests/stash/processing/unit/test_creator_processing.py:596` (`test_scan_creator_folder_metadata_scan_error`): a sibling unit test was asserting on the OLD buggy escape — `pytest.raises(ValueError, match="Failed to start metadata scan")` — i.e., it ratified the bug as the spec. With Bug #4's fix in place, that ValueError is now correctly caught at base.py:346 and re-raised as the documented `RuntimeError("Failed to process metadata: ...")`. Updated the assertion to `pytest.raises(RuntimeError, match="Failed to process metadata")` and added a `__cause__` check (`isinstance(excinfo.value.__cause__, ValueError)`) to pin the `raise ... from e` chain — that exception-chaining semantic is now part of the contract and should fail loudly if a future edit drops `from e` or moves the catch. Surfaced when the full suite reran clean except for this one assertion mismatch — the kind of "the bug fix broke a test that was asserting on the bug" cascade documented in `feedback_remove_mocks_fix_bugs.md`.

16. **2.13 Category D #15 — `tests/stash/processing/unit/media_mixin/test_attachment_processing.py` (3 tests) + `test_batch_processing.py` (6 tests) rewritten 2026-04-25**: 15 internal-mock instances eliminated across two files; both files now exercise the real `_process_batch_internal` end-to-end against either Docker Stash or rule-compliant external-lib leaves.

- **Two layering decisions** correspond to the two distinct concerns under test:
  - **`test_attachment_processing.py` (batch composition, 3 tests)**: switched from `respx_stash_processor + AsyncMock(_process_batch_internal)` to `real_stash_processor + entity_store + stash_cleanup_tracker` with the canonical TrueSpy pattern from `test_message_processing.py:173-238`. The spy captures the batch composition BEFORE delegating to the real `_process_batch_internal`, so the unique unit-test value (which Media objects flatten from attachment.media + bundle.accountMedia + aggregated_post recursion) is preserved even though the eventual GraphQL outcome depends on Docker state. Dropped the synthetic `mock_batch_result` returns and the `result["images"][0] == mock_image` assertions per advisor guidance — those would be brittle against real Stash. Smoke check on result shape (`"images" in result and "scenes" in result`) replaces the synthetic equality.
  - **`test_batch_processing.py` (chunking + routing, 5 of 6 tests)**: tests 1-2 (chunking math) use the same TrueSpy pattern on `_process_batch_internal` to count chunks (5 → 1 call, 45 → [20, 20, 5]); pre-set `processor._studio` to skip the studio lookup (production code path at base.py:996-998 — not a mock, just a configured short-circuit) and `monkeypatch.setattr(store, "get_many", ...)` returning `[]` so the chunked delegated calls complete silently. Tests 3-5 (routing logic for stash_id vs path-based, image vs scene) use the same `_studio` short-circuit + `monkeypatch.setattr(store, "get_many" / "find_iter", ...)` to inject `Image(organized=True)` / `Scene(organized=True)` fakes — `organized=True` triggers the production short-circuit at `_update_stash_metadata:472-486`, avoiding the heavy GraphQL update mutations while preserving the routing assertion (which collection each fake lands in). Test 6 (empty list) was already real, left alone.
- **Why not all-`real_stash_processor`**: the routing tests want to inject specific fake Image/Scene objects (different stash_ids/paths/types) to verify grouping math; against real Docker, `store.get_many(Image, [40000-40002])` returns empty and the routing assertions can't fire. `respx_stash_processor + monkeypatched store leaves` is the right shape for that — and `store.get_many` / `store.find_iter` ARE the external-lib leaves per CLAUDE.md, so the patches are rule-compliant (same boundary as Item 11 used for the find-by-id tests).
- **Helpers added (file-local, not promoted)**: `_organized_image()` / `_organized_scene()` builders for the routing tests' fake leaf returns; `_async_iter()` async-generator wrapper since `store.find_iter` is an async iterator; `fake_studio` fixture for the pre-set short-circuit. None of these belong in `tests/fixtures/` — they're routing-test scaffolding specific to this file.
- **Test counts**: 3 (attachment) + 6 (batch) = 9 total; 9/9 pass cleanly when run in isolation. The known asyncpg / Python 3.13 / macOS arm64 fragility (`asyncpg.protocol.protocol` SIGSEGV under broader test concurrency) reproduces on full-directory runs even excluding these files — confirmed by the user during this session and noted as out-of-scope; serial run within Item 15's scope is clean.
- **Follow-up noted (not in scope for Wave 2)**: both files live at `tests/stash/processing/unit/media_mixin/` but now exercise the same Docker-Stash surface as the integration tests. Relocating to `integration/` is a directory-hygiene cleanup decision; Wave 2's mission is mocks-out, so leaving them in place. Suggest reviewing in a Wave 4 fixture/structure pass.

17. **2.14 Infrastructure: PostgreSQL TEMPLATE-clone fast path for per-test DB setup, completed 2026-04-25**: replaced per-test `table_metadata.create_all()` with a session-scoped `pg_template_db` fixture that builds the schema once and is cloned by `CREATE DATABASE x TEMPLATE pg_template_db` for each subsequent test. Not a "remove internal mock" item; surfaced during Item 15 work while investigating the asyncpg `BaseProtocol` SIGSEGV that was hitting the larger test suites.

- **Why it landed in Wave 2 scope**: the recurring asyncpg crash (see referenced upstream issues `MagicStack/asyncpg#916`, `#1033`) was blocking iteration cadence on Wave 2 work. Reducing connection-creation churn was a plausible mitigation worth pursuing in-band rather than deferring.
- **Implementation pattern** (`tests/fixtures/database/database_fixtures.py`):
  - New `_pg_connection_params()` helper factors out env-var resolution that was duplicated across fixtures.
  - New session-scoped `pg_template_db` fixture: `CREATE DATABASE template_<uuid>`, runs `table_metadata.create_all()` ONCE, then `command.stamp(alembic_cfg, "head")` so callers using the production-default `Database(config)` (i.e. `skip_migrations=False`) see "already at head" and no-op instead of trying to re-run all migrations against existing tables. Engine fully disposed before yielding (PostgreSQL refuses to clone from a DB with active backends). `datistemplate = true` set as the permission marker.
  - Modified `uuid_test_db_factory`: now depends on `pg_template_db` and issues `CREATE DATABASE x TEMPLATE pg_template_db` by default. Tests that need a bare empty DB (Alembic walk tests in `tests/alembic/test_migrations.py` + `test_migration_edge.py`) opt out via `@pytest.mark.empty_db` (registered in `pyproject.toml`); the fixture honors the marker by issuing a bare `CREATE DATABASE x` instead.
  - Belt-and-suspenders `pg_terminate_backend` call before each clone — guards against any leaked connection to the template that would otherwise block the `CREATE DATABASE TEMPLATE` (which holds ACCESS EXCLUSIVE during clone).
  - Removed per-test `table_metadata.create_all()` from `test_engine`, `test_async_session`, and `entity_store` (no longer needed; tables come from the cloned template). Also removed the `except "already exists"` swallow at the old `test_engine` site — that was a workaround for parallel-test races and is dead code now that the template guarantees clean schema per test.
- **Production-caller bug surfaced during rollout (lesson learned)**: my pre-implementation grep for risky `Database(config)` callers (without `skip_migrations=True`) only searched `tests/`. I missed `fansly_downloader_ng.py:313` — the production `main()` constructs `Database(config)` against whatever DB the config points at, and `tests/functional/test_main_integration.py` exercises that code path with the test DB. Without the alembic-stamp step, all 36 tests in `test_main_integration.py` failed with `psycopg2.errors.DuplicateTable: relation "accounts" already exists` (Alembic seeing no `alembic_version` table → assuming base → trying to re-CREATE all tables that the template clone already has). Fix: add `command.stamp(alembic_cfg, "head")` to the template fixture (already in the implementation above). **Generalizable rule**: when grepping for risky callers of a refactored test fixture, search production code too — integration tests exercise production callers that share fixture state.
- **Verification & metrics** (full-suite, `pytest tests/` with default xdist + coverage):
  - Pass count: 2287 passed, 0 failed (was 2286 passed + 1 failed pre-Item-12-fix, then briefly 2253 passed + 34 failed during the missed-grep window).
  - Wall time: 224.83s (was 254.50s baseline) → **−29.67s, −11.7% reduction**. Holds across the 2200+ tests using `uuid_test_db_factory`.
  - Coverage: 92.90% (was 92.91% — within noise).
  - asyncpg SIGSEGV under broader concurrency: did NOT recur in the post-template full-suite run. This is one data point on an intermittent native crash on a fragile platform combo (asyncpg + Python 3.13 + macOS arm64); reduced connection churn is a plausible mitigation but not a confirmed fix. Templates may have reduced crash frequency, observation pending more samples; if it recurs the upstream references still apply.
- **Files touched**: `tests/fixtures/database/database_fixtures.py` (new fixture + helper, removed `create_all` from 3 fixtures); `tests/fixtures/database/__init__.py` + `tests/fixtures/__init__.py` (re-exports); `tests/alembic/test_migrations.py` + `tests/alembic/test_migration_edge.py` (`pytestmark = pytest.mark.empty_db`); `pyproject.toml` (`empty_db` marker registration). Net: ~80 LOC added.

### Wave 3: Stash GraphQL Pass 2 compliance (est. 20–30 hours)

1. **150 GraphQL assertion violations** across `tests/stash/processing/**`. Systematic fix:
   - Replace `>= N` with exact `== N` (46 instances)
   - Add request-variable verification + response-body verification for each call (104 instances)
   - Priority files (largest counts): `test_media_variants.py:38`, `test_metadata_update.py:30`, `test_background_processing.py:12`

### Wave 4: Integration test fixture cleanup + shallow-test consolidation (est. 15–25 hours)

1. **`test_metadata_update_integration.py`** (previously flagged as DbSyncAsyncMix — **withdrawn**): the sync/async mixing there is fixture setup via FactoryBoy + async code under test via asyncpg EntityStore; not a real problem post-Pydantic migration. Skip.
2. **`test_transaction_recovery.py` + `test_transaction_rollback.py`** — **VERIFIED ZOMBIE TESTS**. Both test `session_scope()` / `async_session_scope()` / `begin_nested()` / `Session.execute/rollback` error-recovery mechanics on SQLAlchemy `Session`/`AsyncSession`. These methods are defined **only** in the test-fixture file `tests/fixtures/database/database_fixtures.py:353,359` — they do NOT exist on the production `Database` class. Production `metadata/database.py:42` docstring is explicit: "asyncpg pool → PostgresEntityStore → Pydantic models (all runtime access); SA sync engine → Alembic migrations only (no ORM, no sessions)". These two files test SQLAlchemy library internals that no application code depends on. **Recommendation: delete both files** (or recast them as asyncpg pool-invalidation tests, but SQLAlchemy's savepoint mechanics are upstream's concern, not ours).
3. **Shallow-test consolidation**:
   - `tests/metadata/unit/test_hashtag.py` (15 tests → 1 parametrized)
   - `tests/daemon/unit/test_handlers.py` (45 tests → 1 parametrized matrix)
   - `tests/helpers/unit/test_common.py` (120 shallow → grouped parametrized tests)

### Wave 5: PEP 8 cleanup + legitimate-exception documentation (est. 5–8 hours)

1. **119 inline imports → file top** (alembic/test_migrations.py:24 is the biggest)
2. **117 print/logger/sleep patches → `capsys`/`caplog` fixtures + remove sleep patches** where unjustified
3. **Document legitimate `if TESTING:` usage** in `pyproject.toml:385` to verify it's not hiding production-path bugs

### Wave 6: Coverage-driven gap filling (est. 20–30 hours)

1. **metadata/story.py** (26% → 100%): Add tests exercising aggregation data extraction, missing accountId logging
2. **daemon/runner.py + bootstrap.py** (~55% → 100%): Error-budget paths, five-loop coordination, backfill interruption
   - **2.6 residual carry-over (CLEARED 2026-04-24, see Wave 2 entry 7 for full execution log)**: All 7 tests originally listed as carry-over have been rewritten in Wave 2.6 using the real-respx pattern (4 dispatch tests in `TestHandleTimelineOnlyItem` + `TestMarkViewedFalse` + 3 in `TestFollowingRefresh`; the 8th item in the original spy list — `test_session_baseline_first_call_then_none` — was a logic test with no production call site, see entry 7's "Truly resolved" sub-bullet). Two additional branch-coverage tests (`test_timeline_only_unknown_creator_skips_with_warning`, `test_full_creator_download_iterates_walls`) were added in the same patch to cover the new code paths introduced by the production-bug fixes. `test_runner_wiring.py` now has ZERO internal `daemon.runner.*` handler mocks; the only remaining `patch`/`monkeypatch` calls are (a) `monkeypatch.setattr("daemon.runner._worker_loop", _instrumented_worker)` in the bootstrap-fallback test (intentional replacement, advisor-validated semantic), (b) `patch("daemon.runner.asyncio.create_task", side_effect=_patched_create_task)` in the task-scheduling test (leaf-level instrumentation that delegates), and (c) `AsyncMock(spec=should_process_creator, return_value=True)` in the session-baseline logic test (no production call site). Wave 6's daemon/runner.py work is now strictly about coverage gap-filling for error-budget paths, five-loop coordination, and backfill interruption — NOT about removing internal mocks from this file.
3. **api/websocket.py** (67% → 100%): Reconnect backoff, malformed envelope handling
4. **download/m3u8.py** (61% → 100%): Real parser + ffmpeg subprocess edge cases (will improve as Wave 2 #9 lands)

---

## Total effort estimate

- **Wave 1 (surgical)**: 8–12 hours — do first, unblocks downstream waves
- **Wave 2 (MockInternal)**: 30–45 hours — largest impact on coverage
- **Wave 3 (GraphQL Pass 2)**: 20–30 hours — completes the originally-documented Pass 2
- **Wave 4 (integration + consolidation)**: 15–25 hours
- **Wave 5 (cleanup)**: 5–8 hours
- **Wave 6 (coverage gaps)**: 20–30 hours

**Total: 98–150 hours** (~12–19 working days) to reach the branch's stated goals of 100% compliant + 100% coverage.

Recommend executing Wave 1 today (mechanical, low-risk, enables everything else), then pausing for user review before committing to Waves 2–6.

---

## Post-Wave-1 User Review Corrections (2026-04-24)

Four items flagged by user after Wave 1 review:

1. **"Some of the return_value → side_effect changes were in fixtures, re-inspect"** — verified: all my fixture-file changes were in **docstring examples** (showing test-code usage of the fixture), not in fixture bodies. The actual fixture-level scaffolding calls (CORS preflight, GraphQL default responders) remain as `return_value=` with explanatory comments. Missed one docstring at `api_fixtures.py:169` (helper `create_mock_json_response`), now fixed. No fixture-body `return_value` was touched.

2. **"Are the tests in test_crypto.py even used?"** — verified: the tests are **complementary, not redundant**. `tests/test_crypto.py` has parametrized ground-truth hash values (e.g., `cyrb53("a", 0) == 7929297801672961`) verifying the algorithm's mathematical correctness. `test_fansly_api.py::test_cyrb53` only verifies determinism (same input → same output) — that test would pass even if cyrb53 returned `hash(input) ^ seed`. The crypto.py tests are the only ones pinning specific expected hash outputs. Keep.

3. **"I saw something try to revert @pytest_asyncio.fixture"** — verified `@pytest_asyncio.fixture` is the **established codebase convention** — `database_fixtures.py` already uses it for 12 async fixtures (lines 425, 468, 555, 614, 653, 684, 708, 732, 755, 775, 796, 816). Under `asyncio_mode = "auto"` plain `@pytest.fixture` works transparently on async functions, but `@pytest_asyncio.fixture` is more explicit and matches the sibling pattern. The Wave 1.2 change is correct; any revert attempt (e.g., from a linter) would be wrong.

4. **"tests/json vs tests/fixtures/json_data — json files should be inside the fixtures folder"** — executed. Moved all 14 JSON files from `tests/json/` to `tests/fixtures/json_data/`; `tests/json/` directory removed. Updated `database_fixtures.py:373` (`test_data_dir`) to resolve to the new location via `.parent.parent / "json_data"`. 27 metadata integration tests (which consume these JSON files) still pass.

5. **Collateral dead-fixture cleanup (follow-up)** — while investigating #4, discovered root `conftest.py` had three silent-failure fixtures (`json_timeline_data`, `json_messages_group_data`, `json_conversation_data` at lines 242-292) pointing to filenames that didn't match the real files (e.g., `timeline_response.json` vs actual `timeline-sample-account.json`). They'd been returning empty dicts forever via their `if not ...exists()` fallback. Verified via grep that no tests consume them — only re-export machinery references them. **Deleted all three fixtures** from conftest.py, removed the matching `__all__` entries, and removed the now-unused `import json` at the top. The `json_conversation_data` fixture in `database_fixtures.py:387` (which points to the correct file `conversation-sample-account.json`) is no longer shadowed and is now the real implementation. 2,151 tests still collect cleanly. (Initially scheduled this for Wave 5 — that was unjustified deferral; it's five lines of dead code in the same area just modified.)

## Wave 1 Completion Log (2026-04-24)

**Status**: Wave 1 executed. Results:

| Item                         | Outcome                                 | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ---------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1.1 per-test `return_value`  | ✅ Done (via three passes)              | **First pass** (trusted audit's sampled count): ~45 occurrences fixed in `test_fansly_api.py` + `test_account.py`. **Second pass** (completeness audit caught by user): ~41 additional occurrences across 9 more files: `test_m3u8_integration.py` (19), `test_tag_processing.py` (5), `test_error_handlers.py` (5), `test_tag_methods.py` (3), `test_stash_processing.py` (2), `test_tag_edge_cases.py` (2), `test_account_processing.py` (2), `test_studio_mixin.py` (1), `test_creator_processing.py` (1). **~86 total per-test violations fixed.** **Third pass** (anti-deferral rule established by user — "if touching a test with multiple issues, pull work forward, do not delay work"): 5 tests that got `side_effect=[response] * N` padding were fully tightened + all other known-issue cleanups applied in the same sitting: each now has (a) exact call-count assertion, (b) `try/finally` wrapping the function under test with `dump_fansly_calls`/`dump_graphql_calls` in `finally`, (c) per-call request-variable + response-data verification. Additionally: `test_update_performer_avatar_exception` had 3 MockLoggingTiming patches (`print_error`, `logger.exception`, `debug_print`) replaced with `caplog.set_level(logging.DEBUG)` + `caplog.records` filtering (converts mock-call assertions into stronger behavior assertions on actual logged messages). `test_full_m3u8_download_workflow` had 3 PathMock violations (`patch("pathlib.Path.exists")`, `patch("pathlib.Path.stat")`, `patch("builtins.open")`) replaced with real `tmp_path` file I/O, plus `url__regex` → `url__startswith` for CDN-safe matching. The 4 `@patch("download.m3u8._try_direct_download_*"/"_mux_segments_*")` decorators were kept (user-confirmed acceptable): each wrapper has dedicated unit-test coverage in `tests/download/unit/test_m3u8.py` — `TestDirectDownloadFFmpeg` (line 412), `TestDirectDownloadPyAV` (line 528), `TestSegmentDownload` (line 623) — so the integration test is free to treat them as orchestration seams. Comment in the test now cites the specific test classes to document why the patching is valid rather than a MockInternal violation. The 5 tests: `test_update_performer_avatar_exception` (2 findImages), `test_scan_creator_folder_metadata_scan_error` (ConfigurationDefaults + MetadataScan), `test_add_preview_tag_found_adds_tag` (findTags + findScenes), `test_add_preview_tag` (findTags + findImages), `test_full_m3u8_download_workflow` (master×2 + variant×1 + seg1×1 + seg2×1 + OPTIONS×5, exact per-route counts). Fixture-level legitimate `return_value` uses annotated with explanatory comments. Docstring examples in 5 fixture files updated from `return_value=` to `side_effect=[]` so they teach the correct pattern. Final state: exactly 3 `return_value=` calls remain in tests/, all in fixture files, all with "intentional — fixture-level blanket responder" comments. |
| 1.2 async fixture decorators | ✅ Done                                 | All 7 mixin fixtures in `stash_mixin_fixtures.py` moved to `@pytest_asyncio.fixture`; 80 stash mixin tests still pass.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 1.3 rename `crypto.py`       | ✅ Done                                 | `tests/crypto.py` → `tests/test_crypto.py` via `mv` (git auto-detects rename on `git add -A`). 10 parametrized test cases now discovered.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| 1.4 `os.environ["TESTING"]`  | ✅ Done                                 | `test_logging_functional.py:47` switched to `monkeypatch.setenv`. 12 tests pass.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 1.5 session-scope fixtures   | ⚠️ Skipped — **false-positive finding** | Both `test_engine` and `test_database_sync` were already function-scoped. Audit agent misread adjacent session-scoped JSON-data loaders. Master report broken-claim #3 updated with correction.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 1.6 shadowed fixtures        | ✅ Done                                 | Removed 5 duplicates from `config_fixtures.py` (kept `complete_args`), removed 5 from `download_fixtures.py` (kept `mock_process_*`). Updated `tests/fixtures/__init__.py`, `tests/fixtures/core/__init__.py`, `tests/fixtures/download/__init__.py` re-export lists. Full test collection: 2,151 tests, no errors.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |

**Smoke tests run** (post-Wave-1): tests/test_crypto.py + tests/api/unit/test_fansly_api.py + tests/download/unit/test_account.py (77 tests passed), tests/config/functional/test_logging_functional.py (12 passed), tests/stash/processing/unit/ mixin subset (80 passed).

**Git summary**: 11 modified files, 1 deleted (crypto.py), 2 new (test_crypto.py, this master report). No staged commits — user retains full control of when/how to commit.

**Next recommended step**: user review + commit Wave 1, then proceed to Wave 2 (high-impact MockInternal elimination: `test_fansly_downloader_ng.py`, `test_validation.py`, etc.).

## Wave 2 Completion Log (2026-04-25)

**Status**: Wave 2 executed end-to-end across 14 sub-entries (2.1–2.14). Captured in commit `b2df7fbf7` ("test: complete Wave 2 — eliminate internal mocks across test suite").

| Item                                                              | Outcome | Notes                                                                                                                                                                                                                                                                                                                                                         |
| ----------------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2.1 `test_fansly_downloader_ng.py` rewrite                        | ✅ Done | File rewritten; `mock_database`/`mock_alembic`/`mock_args`/etc. fixture cluster deleted (~280 LOC). Wave 2.1 LOC figure (358) is **stale** — file now 1756 LOC because Wave 2.2 added the `_async_main` end-to-end suite directly into the same module. Mock count reduced to ~4 narrow `monkeypatch.setattr("fansly_downloader_ng.main", ...)` seam patches. |
| 2.2 `main()` end-to-end coverage                                  | ✅ Done | `fansly_downloader_ng.py` reached **100.00%** statement + branch coverage (439 stmts, 0 missed; 130 branches, 0 partial). 22 integration tests in `tests/functional/test_main_integration.py` exercise per-mode happy paths, validation errors, multi-creator iteration, Stash branch, etc.                                                                   |
| 2.3 `test_m3u8.py` rewrite                                        | ✅ Done | `download/m3u8.py` reached 100% line + branch. 13 PathMocks eliminated; replaced with `tmp_path` + ffmpeg/AV leaf monkeypatches.                                                                                                                                                                                                                              |
| 2.4 timeline + messages download tests                            | ✅ Done | `download/timeline.py` 99.05%, `download/messages.py` 100%. All `process_timeline_data` / `process_timeline_media` patches removed in favor of real-pipeline tests.                                                                                                                                                                                           |
| 2.5 `test_stories.py` rewrite                                     | ✅ Done | `download/stories.py` 100% line + branch. `_mark_stories_viewed` FakeSpy replaced with respx-route call-count verification on real `/api/v1/mediastory/view` HTTP traffic.                                                                                                                                                                                    |
| 2.6 `test_runner_wiring.py` carry-over                            | ✅ Done | All 7 originally-deferred FakeSpy tests rewritten using real-respx patterns; 2 production bugs (download_wall signature, get_creator_account_info missing) surfaced + fixed in same patch (Option A discipline).                                                                                                                                              |
| 2.7 `_worker_loop` direct assignment                              | ✅ Done | Single `monkeypatch.setattr("daemon.runner._worker_loop", ...)` retained (advisor-validated test seam); zero direct `_runner._worker_loop = ...` assignments.                                                                                                                                                                                                 |
| 2.8 collections + single tests                                    | ✅ Done | `download/collections.py` + `download/single.py` both 100%. `download_fixtures.py` deleted entirely (was harboring fixture-level `mock_process_media_*` internal mocks).                                                                                                                                                                                      |
| 2.9 delete `test_fansly_downloader_ng_extra.py`                   | ✅ Done | Whole file removed (zombie + sys.exit / print mocks superseded by Wave 2.1/2.2 real-pipeline tests).                                                                                                                                                                                                                                                          |
| 2.10 `test_fanslyconfig.py` rewrite                               | ✅ Done | Real `FanslyApi` / `StashContext` / `RateLimiter` constructed throughout; `config/fanslyconfig.py` 100% line + branch.                                                                                                                                                                                                                                        |
| 2.11 `test_error_handlers.py` (media_mixin)                       | ✅ Done | "Dead patches" identified by advisor: `client.find_image` / `find_scene` patches were targeting names production didn't call. Replaced with respx + real `store.get_many(...)` paths.                                                                                                                                                                         |
| 2.12 `test_base_processing.py` integration                        | ✅ Done | Real `metadata_scan` + `wait_for_job`. Bug #4 (`except RuntimeError` too narrow — `stash_graphql_client` raises `ValueError` on transport errors) surfaced + fixed in same patch.                                                                                                                                                                             |
| 2.13 `test_attachment_processing.py` + `test_batch_processing.py` | ✅ Done | `_process_batch_internal` patches converted to TrueSpy with `await original_*()` delegation. `test_attachment_processing.py` relocated to `unit/media_mixin/` per directory consolidation.                                                                                                                                                                    |
| 2.14 PG TEMPLATE-clone fast path                                  | ✅ Done | `pg_template_db` session fixture builds schema once via `create_all()` + Alembic stamp; `uuid_test_db_factory` issues `CREATE DATABASE TEMPLATE pg_template_db` per test (file-system clone vs DDL replay). `@pytest.mark.empty_db` opt-out for Alembic walk tests. Suite wall-time -11.7% (254.5s → 224.8s baseline).                                        |

**Final state**: 2287 passed, 0 failed, 92.90% coverage at the close of Wave 2. Remaining MockInternal hot-spots (audit clusters #11–16 per-line citations no longer reflect current code; per-cluster status confirmed by 2026-04-29 cross-verification audit).

---

## Wave 3 Completion Log (2026-04-29)

**Status**: Wave 3 executed across two commits (`700c5237b` "tests/stash/processing — Wave 3 Phase 1" and `b2df7fbf7` follow-through within Wave 2 close-out).

| Item                                   | Outcome   | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| -------------------------------------- | --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3.1 GraphQLAssertWeak (104 violations) | ✅ Mostly | 100/104 fixed across `test_media_variants.py` (38), `test_metadata_update.py` (30), `test_background_processing.py` (12), and integration files. Pattern: `assert "query" in req` tautology → `assert_op(calls[i], "OperationName")` against shared helper at `tests/fixtures/stash/stash_api_fixtures.py:119`. **4 weak `"query" in req` assertions remain** in `tests/stash/processing/unit/content/test_{post_processing,message_processing,content_collection}.py` + `unit/gallery/test_process_item_gallery.py` — bounded follow-up. |
| 3.2 GraphQLCallCount (46 violations)   | ✅ Done   | All 46 `>= N` weak counts converted to exact `== N`. Pattern shifted from `Mock.call_count` to filtering captured request bodies (`assert len(filtered_calls) == N`), then ordered `assert_op(calls[i], "OperationName")` per call. 16 `len(...) >=` softness assertions remain in 3 integration files (smaller, lower-priority residue).                                                                                                                                                                                                 |
| 3.3 `assert_op_with_vars` adoption     | ✅ Done   | New helper at `tests/fixtures/stash/stash_api_fixtures.py:142` for per-call variable verification (76 uses across ~12 files). Used heavily in `test_metadata_update.py`, `test_media_variants.py`, integration `test_message_processing.py` for mutation-payload pinning.                                                                                                                                                                                                                                                                 |
| 3.4 try/finally + `dump_graphql_calls` | ✅ Mostly | 15 of 26 `tests/stash/processing/unit/*/test_*.py` files using `respx_stash_processor` now have `dump_graphql_calls` (was 0 at audit). 11 stash-unit files still missing the dump pattern (Cat B residue, separate cleanup).                                                                                                                                                                                                                                                                                                              |

**Final state**: weak GraphQL assertions reduced from 150 → ~6 active tautology lines across 4 files. `assert_op` (155 uses) + `assert_op_with_vars` (76 uses) + manual `["variables"]…` pinning (49) dominate. Cross-verified by independent audit on 2026-04-29.

---

## Wave 4 Status (2026-04-29) — Partial

Wave 4 was partially absorbed into Waves 2 and 5+6 rather than executed as a standalone wave.

| Item                                       | Outcome    | Notes                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------------------------ | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 4.1 Delete zombie `test_transaction_*.py`  | ✅ Done    | `test_transaction_recovery.py` + `test_transaction_rollback.py` deleted in commit `d36f93e28`. They tested SQLAlchemy `Session.execute`/`rollback` paths that no production code uses post-Pydantic+asyncpg migration.                                                                                                                                             |
| 4.2 Integration fixture consolidation      | ✅ Done    | All integration fixtures consolidated under `tests/fixtures/<domain>/`. JSON test data moved from `tests/json/` to `tests/fixtures/json_data/`. `respx_stash_processor` + `dump_graphql_calls` utilities live in `tests/fixtures/stash/`.                                                                                                                          |
| 4.3 Shallow-test consolidation             | ⚠️ Partial | Two flagship targets done: `test_hashtag.py` (15 → 1 parametrized + deeper EntityStore tests) and `test_handlers.py` (45 → 9 functions across 3 parametrized matrices). Suite-wide parametrize ratio remains low (~1.17%, 28 parametrize / 2395 tests). Three large directories (`api/unit`, `download/unit`, `stash/processing/unit`) have zero parametrize uses. |
| 4.4 `media_mixin/` relocate to integration | ⚠️ Partial | `test_attachment_processing.py` relocated. `test_batch_processing.py`, `test_error_handlers.py`, `test_file_handling.py`, `test_media_processing.py`, `test_metadata_update.py` still in `unit/media_mixin/`.                                                                                                                                                      |

**Open Wave 4 work**: ~30%, primarily shallow-test consolidation in `api/unit`, `download/unit`, `stash/processing/unit`, and the 5 remaining `media_mixin/` relocations. Optional polish, not blocking.

---

## Wave 5 Completion Log (2026-04-29)

**Status**: Wave 5 executed in commit `1cd1b0fca` ("test: Wave 5+6 — eliminate internal mocks, lift coverage to 96.6%").

| Item                                      | Outcome | Notes                                                                                                                                                                                                                                                                                                                                                             |
| ----------------------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 5.1 PEP 8/810 imports at top              | ✅ Done | Ruff `E402` + `I001` flag count = **0** across `tests/`. Module-level imports clean. Audit's "119 inline imports" reduced to **68** raw indented imports today, but those are predominantly intentional patterns (TYPE_CHECKING guards, post-monkeypatch re-binds, caplog logger fetches inside test functions, circular-dep avoidance in `cleanup_fixtures.py`). |
| 5.2 Legitimate-exception annotations      | ✅ Done | `# noqa: E402` markers paired with explanatory comments (e.g., `tests/conftest.py` has 10 with rationale block about `faulthandler.enable()` ordering before project imports).                                                                                                                                                                                    |
| 5.3 Drop obsolete `if TESTING:` exclusion | ✅ Done | Removed from `pyproject.toml:385` `exclude_also` list — pattern no longer present in source.                                                                                                                                                                                                                                                                      |

**Final state**: PEP 8 import compliance fully achieved by ruff's measurement; remaining 68 indented imports are legitimate function-scoped patterns.

---

## Wave 6 Status (2026-04-29) — Mostly Done

Wave 6 executed across multiple commits (`1cd1b0fca`, this session's daemon/runner.py work).

| Item                                  | Audit start | Today             | Outcome    | Notes                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------------------- | ----------- | ----------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 6.1 `metadata/story.py`               | 25.9%       | **100%**          | ✅ Done    | 5 tests in `test_story.py` cover aggregation data extraction + missing-accountId-skip logging. 0 missing lines.                                                                                                                                                                                                                                                    |
| 6.2 `daemon/runner.py + bootstrap.py` | ~55%        | **99.10% / 100%** | ✅ Mostly  | `daemon/runner.py` 99.10% line-rate (5 partial-branch arrows residual: 623→587, 767→793, 788→793, 927→935, 935→914 — bounded cleanup). `daemon/bootstrap.py` at 100% line + branch. New test modules: `test_bootstrap.py`, `test_runner_handlers.py`, `test_runner_loops.py` (1763+ new test LOC). Phase 1+2+3 work this session lifted runner.py 89.35% → 99.10%. |
| 6.3 `api/websocket.py`                | 66.7%       | **94.43%**        | ⚠️ Partial | 28 missing lines remain at: ping_worker error paths (978, 1003-1011), reconnect-on-lost-connection (1083-1085), threading boundary (1141, 1178-1179, 1205, 1258-1264), malformed envelope catch-all (394-395). 3-5 hour finish.                                                                                                                                    |
| 6.4 `download/m3u8.py`                | 60.9%       | **100%**          | ✅ Done    | Achieved via Wave 2.3 — wrappers patched as documented orchestration seams; each leaf wrapper has dedicated unit-test class (`TestDirectDownloadFFmpeg`, `TestDirectDownloadPyAV`, `TestSegmentDownload`).                                                                                                                                                         |

**Final state**: 3 of 4 items at 100%; 1 (websocket.py) at 94.43% with concrete remaining-line list. Project total coverage **88.11% → 97.25%** (+9.14 points).

---

## Reference: auditing methodology

- 46 parallel read-only agents (14 directory-scoped Explore agents, 12 concern-scoped cross-cutting scans, 20 deep-dive / spot-check agents)
- Each file audited by ≥2 independent agents (directory + concern)
- Cross-validation: finding flagged by ≥3 agents = high-confidence; flagged by 1 = human review needed
- Each agent required: file path + line number, category name, evidence
- TODO doc claims verified against live code (dated Nov 2025; today is Apr 2026 — drift expected)
- Wave 1 execution uncovered 1 false-positive finding (session-scope claim) — cross-validation pattern worked as intended
- 2026-04-29 cross-verification audit: 50 parallel agents re-audited every category and wave against current code; key finding was favorable drift (audit numbers stale, code state better than audit-snapshot reflected). Coverage table at line 344 should be read as a 2026-04-24 historical snapshot — current values are in the Wave 6 status table above.

All 46 individual agent reports are retained in the session transcript for detailed reference.
