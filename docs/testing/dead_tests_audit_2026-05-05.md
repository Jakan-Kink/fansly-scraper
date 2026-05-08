---
status: A+B done from sweep 1; sweep 2 (re-audit with sharper lenses) findings appended below; user triage pending
date: 2026-05-05 (sweep 1) + 2026-05-06 (sweep 2)
author: 20-agent cross-verification audits (Opus 4.7 driver, Sonnet workers); two passes
---

## Test-boundary policy (refined 2026-05-06)

The boundary you mock at depends on **what you're testing** AND **what infrastructure
you control**. Two crossed axes:

| Test target | Allowed boundaries |
|---|---|
| **Fansly API** (HTTP) — ANY test mode | **`respx` ONLY.** Never hit the real server. Risk: account suspension (cf. `feedback_api_safety.md` + `project_pyloyalfans_dead.md`). |
| **Fansly WebSocket** — ANY test mode | **`fake_ws` / `fake_websocket_session` fixture** (`tests/fixtures/api/fake_websocket.py`). Same safety reason. |
| **Stash unit / partial integration** (`respx_stash_processor` fixture) | **`respx` at HTTP boundary** is the canonical pattern. The fixture provides the respx mock context. |
| **Stash full integration** — happy path | **Real Docker Stash** (local infra you spin up + tear down via `stash_cleanup_tracker`). |
| **Stash full integration** — error path the server can't produce (e.g., asyncio cancellation, transport-level disconnect mid-flight) | **`gql.Client.execute` / `httpx`** — patch ONLY libraries external to ALL your projects. |

**The "external library" definition**: external to ALL your projects, not just this
one. SGC (`stash-graphql-client`) is one of your projects, so patching its `StashClient.execute`,
its store methods, or any SGC internal is the WRONG boundary even in integration
tests. The truly-external library underneath SGC is `gql` (and beneath that, `httpx`)
— those are valid integration-test boundaries when respx is unsuitable.

**Why respx is unsuitable in some integration tests**: the `stash_cleanup_tracker`
context manager makes real Docker Stash calls during teardown. respx, when active,
intercepts ALL httpx requests and would route those teardown calls into the mock,
breaking real cleanup. Hence integration tests must rely on real-server mode +
optional gql-library patches scoped to single failure-injection moments.

## Cleanup-rule exception: `api/` is upstream-API contract

**Rule (added 2026-05-06)**: methods in `api/fansly.py` and related upstream-API
wrappers that have zero internal callers should NOT be deleted as "dead code."
Those wrappers express the contract with the Fansly upstream API — they document
which endpoints exist, even when no current downloader code uses them.

The `zero callers = delete` pattern applies to:
- Internal helpers (`copy_old_config_values`, `verify_file_existence`, hash extractors)
- Name-shim wrappers (`get_active_session` 2-line forwarder around
  `get_active_session_async` — both async, no semantic value)
- Test infrastructure (`measure_time` decorator with no importers)

The exception applies to:
- `FanslyApi.<method>` wrappers around real upstream endpoints
- Their tests (which guard wrapper-vs-upstream-API drift even with zero
  internal callers)

When auditing tests of `api/` methods in future passes, ask: "does this method
express a contract with an external system, or is it internal logic?" If
contract → keep. If logic → dead.

## Sweep 2 — re-audit findings (2026-05-06)

Second 20-agent run with lens-based partitioning (era-fossil framing, TDD-gone-wrong
docstring smells, refined runtime-vs-Alembic SA distinction, boundary correction
to respx/gql.Client.execute). Each agent was told to skip findings already in the
sweep-1 sections below. Findings organized by era hypothesis.

### Pre-importlib alembic refactor era

- **`tests/alembic/test_env.py`** — entire file (7 tests) is dead.
  Confirmed by Lens 14 + Lens 16. All tests use `patch("alembic.context")` AND
  `patch.dict(sys.modules, {"metadata.base": MagicMock()})`. Both targets stale:
  `metadata.base` doesn't exist; fully mocked `alembic.context` means real migration
  machinery never runs. Intent already superseded by 4 standalone `test_env_*`
  functions in `test_migrations.py:747-928` that drive `command.upgrade` against a real DB.
  **Recommendation: delete the entire file.**

- **`tests/alembic/test_migrations.py:912`** — stale `patch.dict(sys.modules, {"metadata.base": None})`.
  The patch is inert; surrounding `command.upgrade`/`command.downgrade` calls are real
  and legitimate. Remove only the dead patch context.

### Removed auto-updater era

- **`tests/config/unit/test_version.py`** — entire file (3 tests) tests
  `config/version.py:get_project_version()` which has zero production callers.
  Likely auto-updater-era version helper that never got wired up.
  **Recommendation: delete file + `config/version.py` production module.**

- **`tests/core/unit/test_errors.py:39-40, 53-57`** (already in C section) —
  `EXIT_ERROR`, `UPDATE_FAILED`, `UPDATE_MANUALLY`, `UPDATE_SUCCESS` tests +
  the 4 production constants in `errors/__init__.py`'s `__all__`.

### Hash-format evolution era

- **Production extension to C5**: `extract_hash_from_filename` (the **current** `_hash2_`
  extractor in `fileio/fnmanip.py:48`) ALSO has zero production callers — production
  inlines `re.compile(r"_hash2_([a-fA-F0-9]+)")` at `fileio/dedupe.py:611`.
  All three `extract_*hash*_from_filename` functions are orphans.
  - `tests/fileio/unit/test_fnmanip.py:90-111` — extends C5 (test of orphan).
  - **Recommendation: delete all three production extractors + their tests.**

### SA → Pydantic+asyncpg migration era

- **Wrong-boundary patches replicated in `test_batch_processing.py`** (Lens 6) — same
  pattern as A4 in `test_error_handlers.py`, mass-applied to 5 more sites:
  - `tests/stash/processing/unit/media_mixin/test_batch_processing.py:95, 144, 197, 248, 312`
    — patches `respx_stash_processor.store, "get_many"` / `"find_iter"`.
  - `tests/stash/processing/integration/test_base_processing.py:591` — patches
    `real_stash_processor.store, "find_iter"` with `RuntimeError`. The file's docstring
    explicitly justifies the wrong boundary by quoting CLAUDE.md misreading
    ("rule-compliant external lib leaves") — same error A4 fixed in test_error_handlers.py.
  - **Recommendation: re-fix all 6 sites to use respx (HTTP boundary) per A4 pattern.**

- **Factory-fixture mismatch — dead-fixture-params** (Lens 10) — confirms 5 sites I
  noted as adjacent during A3:
  - `tests/stash/processing/integration/test_stash_processing.py:26, 44, 75, 105`
  - `tests/stash/processing/integration/test_metadata_update_integration.py:31`
  - **NEW**: `tests/fixtures/stash/stash_integration_fixtures.py:585` — fixture
    `message_media_generator` has dead `factory_session` param consumed by 12+ tests
    that pull in expensive sync-engine setup for no reason.

- **Structurally cannot pass — extends C2** (Lens 17): same 3 tests in
  `test_stash_processing_integration.py` (already in C2). Adds detail that line 140's
  `factory_async_session.refresh(account)` is a 3rd structural failure (FactoryHelper
  has no `.refresh()`).

### Wave-2 daemon refactor era

- **`tests/stash/processing/unit/test_background_processing.py:276, 369, 482`** —
  3 tests with 5-response respx side_effect lists where only 3 are consumed (the
  `findGalleries` responses at indices 3-4 are dead because test accounts have no
  posts/messages — `_run_worker_pool` exits with empty items). Trim 5→3 in each.

### Unfinished retry-config era

- **`tests/fixtures/api/main_api_mocks.py:595-598`** — sets `config.wall_retries = 0`,
  `config.messages_retries = 0`, `config.collection_retries = 0`, `config.single_retries = 0`.
  None are fields on `FanslyConfig` — only `timeline_retries` was actually added.
  Silent dynamic-attr writes; the fixture's "disable retry loops" intent never
  took effect.
  **Recommendation: delete the 4 writes; OR add the fields to FanslyConfig if the
  retry knobs are still wanted.**

### Pre-fixture-policy era

- **respx fixture violations** (Lens 8 + 20) — ~70+ inline `with respx.mock:` /
  `@respx.mock` violations across:
  - `tests/api/unit/test_fansly_api.py` (~19 sites)
  - `tests/api/unit/test_fansly_api_additional.py` (~14 sites)
  - `tests/api/unit/test_fansly_api_monitoring.py` (~4 sites)
  - `tests/api/unit/test_fansly_api_callback.py` (1 site)
  - `tests/download/unit/test_account.py` (~5 sites)
  - `tests/download/unit/test_account_coverage.py` (1 site)
  - `tests/metadata/integration/test_account_processing.py` (1 site)
  - `tests/daemon/unit/test_filters.py` (~14 sites)
  - `tests/daemon/unit/test_polling.py` (~17 sites)
  - `tests/daemon/unit/test_runner_wiring.py` (3 sites)

  Each registers OPTIONS with `side_effect=[httpx.Response(200)]` (single-shot)
  instead of using the fixture's blanket `return_value=`. Per project policy
  (`feedback_use_existing_respx_fixture.md`), `side_effect=[]` exhaustion is the
  intentional tripwire for over-calling — if production fires N+1 preflights,
  `StopIteration` is the test signal. The fixture's `return_value=` is the
  _intentional exception_ at fixture level for blanket coverage when call count
  isn't being asserted.

  So these inline sites are **rule-compliant** for tests that want explicit
  count assertions; the only critique is DRY (the fixture exists for tests that
  _don't_ care about preflight count and just want OPTIONS handled).

  **Daemon cluster blocker**: `respx_fansly_api` requires `mock_config`; daemon tests
  use `config_wired`. Either split the fixture into two layers or create
  `respx_daemon_api`. Daemon tests would only benefit from the fixture if they
  don't need explicit OPTIONS count assertions.

### Internal-mock policy violations (cross-era)

- **`tests/download/unit/test_account.py:650-774`** (Lens 7) — `TestGetFollowingAccounts`
  (5 tests) patches `download.account._make_rate_limited_request`. The retry/sleep/
  error logic in production `download/account.py:292` (entry delay, infinite 429-retry,
  `ApiError` on non-429) is the actual interesting code; tests bypass it.
  **Recommendation: rewrite to use respx against the real Fansly API endpoints.**

- ~~**`tests/helpers/unit/test_checkkey.py:355-403`** (Lens 7)~~
  **RECLASSIFIED 2026-05-06**: legitimate Exception 2 case (parallel to C2 Issue 3).
  The orchestrator `extract_checkkey_from_js` is a pure dispatcher over two
  extractors with deep call trees (JSPyBridge → acorn parsing → walk →
  eval_js). The extractors are independently covered by
  `TestExtractCheckkeyRegex` (real JS + `eval_js` JSPyBridge-leaf patch) and
  `TestExtractCheckkeyAstFallback` (mock acorn/walk at JSPyBridge boundary).
  Mocking the two extractor functions in the orchestrator tests is the
  pragmatic way to verify dispatch logic without re-running the extractor
  code paths already covered elsewhere. NOT a dead-test cluster.

### TDD-gone-wrong era

- **`tests/config/functional/test_logging_functional.py:241`** (Lens 1) — comment
  `# Instead of actually writing huge files, let's mock the rotation` followed by
  manually-created `fansly_downloader_ng.log` and `.log.1.gz` files via `open(..., "w")`.
  Test asserts `len(log_files) > 1` and `any(f.name.endswith(".gz"))` — both satisfied
  by the test's own writes; loguru rotation logic never runs.

### Tautological assertions (cross-era)

15 HIGH cases across 8 files (Lens 3):

- `tests/pathio/test_pathio.py:466` — literal `assert True`
- `tests/stash/processing/integration/test_base_processing.py:575` — `assert total_indexed >= 0` (`len()` always ≥ 0)
- `tests/stash/processing/unit/batch/test_worker_pool.py:41, 42, 46` — `isinstance(x, T)` / `x > 0` on freshly-constructed objects with fixed types
- `tests/download/unit/test_downloadstate.py:59, 60-61, 66-72` — isinstance/hasattr on inheritance-guaranteed members
- `tests/metadata/unit/test_entity_store_sort.py:31` — `isinstance(StrEnum_member, str)` (always True by data model)
- `tests/stash/processing/integration/test_stash_processing.py:37-39` — `hasattr` on instance methods
- `tests/stash/test_logging.py:18-19, 25-26` — `hasattr` on loguru API contract

### Cascading deletions from B1 (housekeeping)

- **`tests/conftest.py:65, 649-734, 745-747`** (Lens 19) — three orphaned performance
  fixtures (`performance_log_dir`, `performance_threshold`, `performance_tracker`),
  the `import psutil` that only `performance_tracker` uses, and 3 `__all__` entries.
  All became dead when I deleted `test_download_performance.py` in B1.

- **`pyproject.toml:312-318`** (Lens 19) — 5 marker registrations with 0 users:
  `slow`, `functional`, `performance`, `full_workflow`, `comprehensive`.

- **Empty/residue directories** (Lens 15):
  - 4 truly empty placeholders: `tests/stash/client/`, `tests/stash/types/`,
    `tests/stash/processing/unit/logs/`, `tests/stash/processing/integration/test_full_workflow/`
  - 2 deletion residue (`__pycache__` only): `tests/performance/`, `tests/metadata/helpers/`
  - 6 Nov-13-bulk scaffolded with only `__init__.py`:
    `tests/{helpers,media,textio,core,pathio,utils}/integration/` — distinct era
    "intent never materialized"

### Findings that DID NOT extend the audit

- Lens 2 (Self-ref stubs): zero new instances beyond B1
- Lens 5 (GraphQL key typos): confirmed existing 3, no new
- Lens 9 (SA Session at runtime): corroborates C2 only
- Lens 18 (Low-info empty assertions): zero findings — codebase is structurally
  clean. Tests asserting `result == []` consistently pair with `route.call_count`
  or other secondary disambiguation.

### Sweep-2 grand totals

| Category                                                                   | New HIGH sites                    |
| -------------------------------------------------------------------------- | --------------------------------- |
| `test_env.py` entirely dead                                                | 7 tests                           |
| `test_version.py` orphan version helper                                    | 3 tests + 1 module                |
| Wrong-boundary replications (test_batch_processing + test_base_processing) | 6 sites                           |
| Unconsumed respx responses (test_background_processing)                    | 3 sites                           |
| TestGetFollowingAccounts internal mocks                                    | 5 tests                           |
| TestExtractCheckkeyFromJs internal mocks                                   | 5 tests                           |
| Tautological assertions                                                    | 15 sites                          |
| Logging-rotation manually-staged-files                                     | 1 site                            |
| `extract_hash_from_filename` orphan                                        | 1 test + 1 production fn          |
| Dead-fixture-params (incl. `message_media_generator`)                      | 6 sites                           |
| Dead retry-config writes in fixture                                        | 4 writes                          |
| **Subtotal new dead-test sites**                                           | **~56**                           |
| Empty placeholder directories                                              | 12 dirs                           |
| Cascading orphans from B1                                                  | 3 fixtures + 1 import + 5 markers |

Adding sweep 2 to sweep 1: roughly **~110 HIGH-confidence dead-test sites** identified
across the test tree, distributed across ~10 distinct infrastructure-shift eras.

---

## Sweep 1 — original audit (2026-05-05)

## Work landed (A + B)

| Task                                         | Files affected                                                                                                                                                                                                     | Outcome                                                                                                                                                                                                                                          |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **B1** Self-referential stubs                | `tests/functional/test_download_workflow.py` (deleted), `tests/performance/test_download_performance.py` (deleted), `tests/download/unit/test_media_filtering.py` (partial)                                        | 14 tests removed; preserved live `TestProcessDownloadAccessibleMedia`                                                                                                                                                                            |
| **A1** `copy_old_config_values` cluster      | `tests/config/unit/test_config.py`, `config/config.py`, `config/__init__.py`                                                                                                                                       | 5 dead tests + production function deleted; orphan `ConfigParser` import cleaned up                                                                                                                                                              |
| **A2** `TestSessionBaseline` rewrite         | `tests/daemon/unit/test_runner_wiring.py`                                                                                                                                                                          | Verified no other test covers the semantic; rewrote both tests to drive `_process_timeline_candidate` end-to-end via monkeypatch at production binding sites; assertions now catch order-of-operations regressions in `daemon/runner.py:551-552` |
| **A3** Factory-fixture mismatch              | `test_tag_methods.py` (3), `test_stash_processing.py` (5), `test_metadata_update_integration.py` (6), `test_stash_processing_integration.py` (1), `tests/fixtures/database/database_fixtures.py` (docstring fixes) | 15 sites converted to `Factory.build(...)` pattern; dead `factory_session` / `factory_async_session` / `session_sync` fixture parameters removed; misleading `mock_account` and `factory_async_session` docstrings rewritten                     |
| **A4** `test_error_handlers.py` boundary fix | `tests/stash/processing/unit/media_mixin/test_error_handlers.py`                                                                                                                                                   | Two tests rewritten to patch at the documented `respx.post(...)` HTTP boundary with `httpx.ConnectError` (mirroring `test_find_stash_files_by_path_regex_fallback`'s working pattern) instead of patching SGC's internal `store.get_many`        |

## Adjacent findings deferred (not in user's A+B scope)

- **`test_stash_processing.py`** has 4 more dead `factory_session` parameters (lines 26, 44, 75, 105 — all `test_initialization`, `test_find_account_by_*`) that the audit didn't flag because there's no `.commit()` call; the fixture is purely orphaned. Same root cause as A3.
- **`test_metadata_update_integration.py:31`** has the same dead `factory_session` pattern in `test_update_image_metadata_with_studio_create`.
- **`test_stash_processing_integration.py:91-143`** still has the C2-class issues (`session=` kwarg → `TypeError`, `.refresh()` on FactoryHelper → `AttributeError`); A4 only removed the empty `.commit()` line per user directive.

## Original audit follows

# Dead-tests audit — `tests-to-100` branch

## Method

20 overlapping cross-verification agents searched `tests/**/*.py` for tests
that exercise dead/orphaned code. Each agent:

- Was given the same protocol: grep production for the patched/mocked name,
  classify HIGH (zero matches) / MEDIUM (matches in unrelated path) / skip
  LOW.
- Read-only — no edits, no `git stash`, no `pytest` runs.
- Output keyed by `(test_file:line)` for mechanical merge.

Confidence reconciliation:

- **≥2 agents agree** = real signal (confirmed cross-verification).
- **Single-agent HIGH** = personal re-verification required before action.

The 10-day-old `feedback_dead_patches_pass_for_wrong_reason.md` memory's
canonical example (`tests/stash/processing/unit/media_mixin/test_error_handlers.py:45,62,234,261`
patching `client.find_image` etc.) was confirmed FIXED by 5 independent
agents — the cleanup landed before this audit.

---

## A. ≥2-agent confirmed findings

### A1. `copy_old_config_values()` cluster — 5 dead tests

**File:** `tests/config/unit/test_config.py`
**Lines:** 254, 309/314, 458, 487, 514
**Confirmed by:** Agent 7 (HIGH) + Agent 15 (MEDIUM via `db_sync_commits` cross-ref)

`copy_old_config_values()` is defined in `config/config.py:89`, exported via
`config/__init__.py:52`, but has zero production callers. Operates on the
`old_config.ini` → `config.ini` workflow that was doubly superseded — first
by the `.ini` → `.yaml` migration, then by `old_config.ini` having no
creation path. The `db_sync_commits` field referenced in the test fixtures
is also in `_DROPPED_FIELDS` (doubly vacuous).

**User direction:** Delete the 5 tests AND delete `copy_old_config_values()`
from production.

---

### A2. `TestSessionBaseline` — 2 tests testing the test author's own helpers

**File:** `tests/daemon/unit/test_runner_wiring.py`
**Lines:** 959 (`test_session_baseline_first_call_then_none`), 992 (`test_baseline_consumed_set_is_per_creator`)
**Confirmed by:** Agent 1 (MEDIUM) + Agent 6 (HIGH)

Test 1 creates `spy = AsyncMock(spec=should_process_creator)`, manually
replays the baseline-consumption logic in the test body, then calls
`spy(...)` directly. Production code never runs. Test 2 defines a local
`_get_baseline(cid)` helper inside the test and asserts on it — a test of
Python `set` semantics on test-author-written code. Cannot detect
regressions in `daemon/runner.py:551-552`.

**User direction:** Re-write — but only after verifying no other test
already covers the production logic at `daemon/runner.py:551-552`
(`_process_timeline_candidate`'s baseline-consumption path).

---

### A3. Factory-fixture mismatch — 14 vacuous tests with empty database commits

**Files & lines:**

- `tests/stash/integration/test_stash_processing_integration.py:102`
- `tests/stash/processing/integration/test_stash_processing.py:149, 191, 248, 273, 366`
- `tests/stash/processing/integration/test_metadata_update_integration.py:135, 142, 207, 215, 223, 302`
- `tests/stash/processing/unit/gallery/test_tag_methods.py:38, 99, 145`

**Confirmed by:** Agent 3 (MEDIUM, 3 tests) + Agent 18 (HIGH, expanded to 14 tests)

Root cause: `BaseFactory` extends `factory.Factory` (NOT
`factory.alchemy.SQLAlchemyModelFactory`). The `factory_session` /
`factory_async_session` fixtures set `_meta.sqlalchemy_session = session` —
but only `SQLAlchemyOptions` reads that attribute; on `factory.Factory`
it's silently ignored. Every `factory_session.commit()` /
`session_sync.commit()` / `factory_async_session.commit()` after
`AccountFactory()` / `PostFactory()` / `HashtagFactory()` commits an
**empty transaction**.

**User clarification (2026-05-05, refined 2026-05-06):** SA ORM was used
extensively before the switch to native asyncpg via `PostgresEntityStore`.
SA is **still** used — but only for Alembic migrations (`metadata/tables.py`
schema + `alembic/versions/` revision files). At **runtime**, no SA
Session is in scope; the read/write path is entirely Pydantic + asyncpg.
So every "runtime" `session.commit()` / `session.execute(select(Foo))` /
`MagicMock(spec=Session)` reference in test code is testing a layer that
isn't there anymore. Tests in `tests/alembic/` that exercise SA patterns
remain legitimate because Alembic itself runs against SA.

**Side hazard — misleading docstrings:**

- `tests/fixtures/database/database_fixtures.py:916, 994-1000` documents
  `await session.execute(select(Account).where(...))` — would
  `ArgumentError` at runtime because `Account` is now a Pydantic model
  with no `__mapper__`.

**Recommendation:** Convert factory calls to `.build(...)`; remove the
`factory_session` / `factory_async_session` / `session_sync` fixture
parameters and the `commit()` calls; remove misleading docstrings.

---

### A4. `test_error_handlers.py` patch boundary — STILL WRONG

**File:** `tests/stash/processing/unit/media_mixin/test_error_handlers.py`
**Confirmed by:** 5 agents (1, 2, 4, 16, 17) — but agents endorsed the
WRONG fix.

The 10-day-old memory's specific dead patches at lines 45, 62, 234, 261
(patching `client.find_image`, `client.find_scene`, etc.) are gone. The
current tests patch `respx_stash_processor.context._store.get_many`.

**User correction (2026-05-05):** `store.get_many` is **not** the right
boundary. `store` is internal to the stash-graphql-client (SGC) library;
patching it bypasses SGC's real fetch behavior. The correct patch
boundary is **either**:

1. **respx HTTP mock** — intercepts the HTTP POST that `gql.Client`
   sends underneath SGC's `_session.execute()`.
2. **`gql.Client.execute()` patch** — returns a Python dict matching the
   GraphQL response shape; SGC then deserializes via real Pydantic
   `model_validate`.

Patching `store.get_many` skips both SGC's fetch logic AND the Pydantic
deserialization layer. The production code path the test claims to cover
is still not exercised end-to-end.

**Recommendation:** Re-rewrite the affected tests to use respx or
`gql.Client.execute` instead of `store.get_many`.

---

## B. Single-agent HIGH-confidence (driver-verified)

### B1. Self-referential stub pattern — 14 tests across 3 files

Verified by direct file reads (not just agent reports):

| File                                             | Lines                                                      | Test count | Verification                                                                                                                                        |
| ------------------------------------------------ | ---------------------------------------------------------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/functional/test_download_workflow.py`     | 25-50 (stubs) + 3 tests                                    | 3          | Comment at line 25 literally reads `# Mock functions for testing - replace with actual imports when modules are implemented`                        |
| `tests/performance/test_download_performance.py` | 13-36 (stubs) + 3 tests                                    | 3          | Identical pattern, identical comment                                                                                                                |
| `tests/download/unit/test_media_filtering.py`    | `_filter_media` at 38-44, `TestMediaFilteringLogic` at 47+ | 8          | `_filter_media` defined locally with docstring `"""Apply the same filtering expression used by fetch_and_process_media."""` — a copy, not an import |

All 14 tests assert on locally-defined stubs/copies, not on production
code. Cannot detect any production regression.

**User direction:** Delete all 14.

The functional/performance files contain ONLY these tests and their
stubs — entire files can be deleted. The `test_media_filtering.py` file
also contains `TestProcessDownloadAccessibleMedia` (NOT dead per Agent 14)
— delete only `_filter_media`, `_make_media`, and `TestMediaFilteringLogic`.

---

## C. Single-agent HIGH-confidence — driver did NOT re-verify; user to triage

These are real candidates per their reporting agent, but I haven't
personally read the files to confirm. User confirms before action.

### C1. API tests pass for the wrong reason — 2 tests (Agent 8)

- `tests/api/unit/test_fansly_api.py:568-585` (`test_setup_session_error`)
  patches `websockets.client.connect` but HTTP 401 short-circuits before
  websocket is reached.
- `tests/api/unit/test_fansly_api_additional.py:175-193` (renamed
  `test_get_active_session_error` after C1 rename action) — passes via
  5-second timeout in `get_active_session()` (lines 574-577 polling for
  `session_id`). Actual mechanism (traced 2026-05-06): patch IS effective
  (`from websockets import client as ws_client` makes `ws_client` a
  reference to the same module object); `await ws_client.connect(...)`
  returns the AsyncMock; `recv()` returns `{"t":0,"d":"Error message"}`
  — but type-0 is not the type-1 auth response that would populate
  `session_id`, so `session_id` is never set; the WS thread loops
  waiting for type-1 (which `return_value` keeps emitting type-0
  forever); the main thread's 5-second poll timeout fires; the
  RuntimeError's message matches the test's regex by coincidence.

  The mocked error message is **irrelevant to the test passing** —
  returning an empty string would also hit the timeout. The test
  predates the canonical `fake_ws` / `fake_websocket_session` fixture
  (`tests/fixtures/api/fake_websocket.py`) which scripts proper type-1
  auth responses. **Recommendation**: migrate to the fixture or rewrite
  to mock `recv` to raise (would exercise the actual exception-catch
  path at `api/websocket.py:583-589` and produce
  `"WebSocket session setup failed: ..."` — the OTHER half of the
  test's regex).

  (Earlier "return_value violation" framing was wrong — `return_value=`
  is correct for `unittest.mock.patch` of an external library; the
  no-`return_value` rule is specifically for respx routes where
  exhaustion is the test's count tripwire.)

**Adjacent finding (2026-05-06)**: `api/fansly.py:596-598` defines
`get_active_session()` as a 2-line async wrapper that just calls
`get_active_session_async()`. Both are async; `_async` suffix carries
no information. Only 1 production caller (`setup_session` at line 610);
only 1 dedicated test (`test_get_active_session` — already MEDIUM in C1
for testing trivial delegation via internal mock). **Recommendation**:
rename `get_active_session_async` → `get_active_session`, delete the
wrapper, delete the redundant delegation test.

### C2. Stash-processing-integration tests — 3 tests (Agents 12 + partial 18); user triage 2026-05-06

- `tests/stash/integration/test_stash_processing_integration.py:25, 91, 147`
- Issue 1 (line 91): pass `session=` kwarg the production signature
  doesn't accept (`TypeError`). **STILL DEAD** — structural failure.
- Issue 2 (line 25): call `.assert_called_once()` on a real `StashContext`
  (not a Mock) — `AttributeError`. **STILL DEAD** — structural failure.
  Also: `test_full_creator_processing_flow` directly assigns 9 internal
  AsyncMocks onto the processor instance instead of using
  `patch.object(...)` context managers, mutating the instance for the
  duration of the test (state-poisoning shape; works only because the
  instance is test-scoped). Plus mocking 9 internal methods means the
  test is mocked-out unit logic mislabeled as integration — `start_creator_processing`
  orchestration switch is the only thing actually verified.
- Issue 3 (line 147, `test_safe_background_processing_integration`):
  ~~mock `continue_stash_processing` internally — anti-pattern~~
  **RECLASSIFIED 2026-05-06**: legitimate Exception 2 case per
  `docs/testing/testing_requirements.md`. `_safe_background_processing`
  exists specifically to catch `asyncio.CancelledError` and other
  exceptions from a deep async task tree. Triggering `CancelledError`
  through real integration is impractical (would require scripting
  task-cancellation mid-flight). Mocking the input boundary
  (`continue_stash_processing`) is the most surgical way to verify each
  branch. This is NOT a dead test — it's a documented-exception unit
  test of the wrapper's error-handling logic.
- All three lack `stash_cleanup_tracker` → `xfail(strict=True)` enforced
  by `tests/conftest.py:243-256`. **User confirmed**: enforcement
  catching them is the desired behavior.

### C3. Internal mocks of forbidden surfaces — 2 tests (Agent 14)

- `tests/download/unit/test_media_download.py:104-148, 151-188`
  mock both `process_media_info` AND `parse_media_info` (internal
  functions). Branch policy: never mock internal functions.

### C4. Response-key typo dead tests — 3 tests (Agent 16); line numbers re-verified 2026-05-06

Three scene tests use `"FindScenes"` (capital F — the GraphQL **operation name**)
in the response-key slot, but SGC reads the response by the lowercase
**field name** `"findScenes"`. Mock fires; SGC sees count=0; `result == []`
passes coincidentally.

- `tests/stash/processing/unit/media_mixin/test_error_handlers.py:95`
  (`test_find_stash_files_by_path_no_images`)
- `tests/stash/processing/unit/media_mixin/test_error_handlers.py:178`
  (`test_find_stash_files_by_path_scenes_not_found`)
- `tests/stash/processing/unit/media_mixin/test_error_handlers.py:206`
  (`test_find_stash_files_by_path_scene_processing_error`)

(Earlier audit cited 110/192/218; those were pre-A4 line numbers — the A4
boundary cleanup shortened the file by ~15 lines, shifting the affected
sites. Image tests at lines 121/152 use `"findImages"` (lowercase, correct)
and are NOT dead. The regex-fallback test at line 243 uses `"findScenes"`
(lowercase, correct) and demonstrates the working pattern in the same file.)

**Recommendation**: replace `"FindScenes"` → `"findScenes"` in all three
sites. Mechanical 3-line fix. The assertions will then actually exercise
the production code path (SGC parses real findScenes data, returns count=0),
instead of the current "passes coincidentally via key-mismatch default."

### C5. Legacy hash-format extractor tests — 2+ tests (Agent 10)

- `tests/fileio/unit/test_fnmanip.py:40-88` tests
  `extract_old_hash0_from_filename` and `extract_old_hash1_from_filename`
  (zero production callers; superseded by `_hash2_` + DB column).
- `tests/fileio/integration/test_fnmanip_integration.py:100-118` —
  same pattern (MEDIUM).

### C6. Self-referential dedup test — 1 test (Agent 10)

- `tests/fileio/test_dedupe.py:95-120` imports `verify_file_existence`
  but never calls it; defines and tests inline `mock_verify_file_existence`.
  Real function tested elsewhere at line 1240, so this is dead AND
  redundant.

### C7. Tautological concurrency assertion — 1 test (Agent 13)

- `tests/stash/processing/unit/batch/test_worker_pool.py:137-138`
  `len(set(processing_started) - set(first_10_finished)) > 0` is
  mathematically always true regardless of execution order. Doesn't
  actually test concurrency.

### C8. Registered-but-unconsumed mock — 1 test (Agent 13)

- `tests/stash/processing/unit/tag_mixin/test_tag_processing.py:40-46`
  registers a respx route, but `_process_hashtags_to_tags([])`
  short-circuits on empty input before any HTTP call.

### C9. Dataclass write to non-existent field — 1 test fixture (Agent 15)

- `tests/download/unit/test_account_coverage.py:30`
  `config.separate_metadata = True` on a `FanslyConfig` dataclass that
  no longer has this field. Python silently creates a dynamic attribute
  (no `__slots__`); no production code reads it.

### C10. Stale alembic env mock — 7 patches (Agent 11)

- `tests/alembic/test_env.py:36, 71, 99, 135, 160, 182, 206` patches
  `metadata.base` which doesn't exist in production. Current
  `alembic/env.py` uses `importlib.util` from `metadata/tables.py`. The
  mock is harmless on the surface but may shadow the real
  `target_metadata` resolution path.

---

## D. Adjacent findings (not dead-test, but worth surfacing)

- **`tests/metadata/integration/common.py:8`** — `measure_time` decorator
  with zero callers; file imported by no test (Agent 11)
- **`tests/metadata/helpers/`** — empty directory, only `__pycache__`
  residue from a deleted `helpers/utils.py`
- **`tests/stash/client/`, `tests/stash/types/`** — empty placeholder
  directories
- **`tests/core/unit/test_errors.py:39-40, 53-57`** — `EXIT_ERROR`,
  `UPDATE_FAILED`, `UPDATE_MANUALLY`, `UPDATE_SUCCESS` constants tested
  but have zero production callers (likely vestigial from a removed
  auto-updater) — MEDIUM confidence (Agent 5)
- **Misleading docstrings** in `tests/fixtures/database/database_fixtures.py:916, 994-1000`
  document broken example code that would raise at runtime (covered in A3)

---

## Tally

| Category                               | Count                           |
| -------------------------------------- | ------------------------------- |
| ≥2-agent confirmed dead tests          | 22 (5 + 2 + 14 + 1 cluster)     |
| Single-agent HIGH driver-verified      | 14 (B1: self-referential stubs) |
| Single-agent HIGH unverified by driver | 17 (C1-C10)                     |
| MEDIUM-confidence                      | ~25                             |
| **Total HIGH dead-test candidates**    | **~53**                         |

## Reference: agent assignment

| Agent | Scope                                                        |
| ----- | ------------------------------------------------------------ |
| 1     | tests/daemon/unit/                                           |
| 2     | tests/stash/processing/unit/content+gallery                  |
| 3     | tests/stash/processing/unit/{batch,tag_mixin,logs}           |
| 4     | tests/stash/processing/ broad cross-check                    |
| 5     | tests/core/+functional/                                      |
| 6     | tests/daemon/ broad cross-check                              |
| 7     | tests/config/+helpers/+media/                                |
| 8     | tests/api/                                                   |
| 9     | tests/daemon/integration/                                    |
| 10    | tests/fileio/+textio/+pathio/+utils/                         |
| 11    | tests/metadata/integration/+helpers/, alembic/, performance/ |
| 12    | tests/stash/{client,types,integration}/                      |
| 13    | tests/stash/processing/unit/batch+tag (alt)                  |
| 14    | tests/download/                                              |
| 15    | Cross-cutting: removed CLI flags / config fields             |
| 16    | tests/stash/processing/unit/media_mixin/                     |
| 17    | Cross-cutting: dead client/store patches                     |
| 18    | Cross-cutting: legacy SQLAlchemy ORM patterns                |
| 19    | Cross-cutting: removed/orphan import targets                 |
| 20    | (rolled into 15)                                             |
