# Changelog

<!-- markdownlint-configure-file { "MD024": { "siblings_only": true } } -->

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Prior history:** Releases before v0.13.0 are documented in
> [`ReleaseNotes.md`](https://github.com/Jakan-Kink/fansly-scraper/blob/main/ReleaseNotes.md)
> in the project's original prose-style format. The v0.13.0 entry is
> mirrored here in Keep-a-Changelog form and is the canonical structure
> for all subsequent releases.

## [Unreleased]

## [0.13.7] - 2026-05-12

WebSocket gateway refactor, livestream/daemon hardening, and production
fixes surfaced by the tests-to-100 fixture migration.

### Added

- WebSocket event classification: known no-op events (e.g. service=1,2
  post-like broadcasts) now identify concretely in trace logs instead
  of being labeled "unknown event".
- H.264 High-profile mux path for IVS livestream recordings;
  `daemon/livestream_watcher.py` split from the parent module.

### Changed

- WebSocket subsystem collapsed: subprocess `FanslyWebSocket` is the
  single production class — the threading variant and the separate
  gateway layer have been folded in.
- `cors_options_request` in `get_with_ngsw` / `get_with_ngsw_sync` is
  gated on `add_fansly_headers`. CDN downloads (which pass
  `add_fansly_headers=False`) no longer fire OPTIONS preflight — saves
  one round-trip per file and removes an anti-detection fingerprint
  real browsers don't produce on CORS-simple GETs.
- Gallery image linking switched to `Gallery.__side_mutations__["images"]`
  (SGC v0.12+) via direct `gallery.images = ...` assignment — one
  batched `addGalleryImages` per gallery save instead of N
  `imageUpdate(gallery_ids=...)` calls.
- WS ping/pong pairs removed from trace logs (2 events per 20s
  saturated slow-period logs).
- Daemon simulator no longer pins to the active state; home-feed limbo
  state fixed.
- `_resolve_variant_url` (livestream) made async; was previously blocking.

### Fixed

- Config schema validation issue.
- IVS muxing issues and logging-level mismatches in livestream
  recording.
- Stale `gallery_ids` re-submission via `imageUpdate` causing
  intermittent ~18% flake on stash gallery integration tests; the new
  gallery-side relationship path (see Changed) avoids re-submitting
  the image's full `gallery_ids` list and so the FK validation can't
  fail on cleaned-up gallery refs.

### Dependencies

- Pin `asyncpg<0.31` (downgrade 0.31.0 → 0.30.0). asyncpg 0.31.0 has a
  Cython teardown SIGSEGV regression on Python 3.13 under high-worker
  pytest configurations. Add asyncpg-stubs 0.30.2 for typing coverage.
- Poetry deps refresh.

## [0.13.6] - 2026-05-09

Livestream recorder overwrite-protection on broadcast reconnects.

### Added

- `_unique_output_path` helper in `daemon/livestream_watcher.py`: when a
  broadcast reconnect would produce the same output filename as a previously
  completed (or crash-interrupted) session, the new recording is written to
  `<stem>_part2.mp4`, `_part3.mp4`, etc. A slot is considered taken when the
  MP4 already exists with data **or** when its temp segment dir is still on
  disk from a prior crashed attempt.

### Fixed

- Livestream recorder silently overwrote the previous session's completed MP4
  whenever a creator reconnected mid-broadcast. `_record_stream` now calls
  `_unique_output_path` before creating the temp segment dir, ensuring each
  reconnect session gets a distinct output file.

## [0.13.5] - 2026-05-09

Async API migration, YAML config render-policy, interactive prompt refactor,
rate-limiter hardening, atomic downloads, URL centralization, livestream
recording, and logging sink fix.

### Added

- `daemon/livestream_watcher.py` — long-running watcher task that polls
  `GET /api/v1/streaming/followingstreams/online` and spawns a per-creator
  recording task for every followed creator who is live.
- IVS HLS recording via manual segment polling (`httpx` + `m3u8`): dedup by
  `EXT-X-MEDIA-SEQUENCE` index, `EXT-X-PREFETCH` leading-edge hint collection,
  and a 60 s `EXT-X-ENDLIST` grace period for transient creator pauses.
- PID-based PyAV mux (`_mux_ivs_segments`): probes up to 5 segments to
  identify stable audio/video PIDs, PTS-rebases packets across segment
  boundaries, and writes a single MP4.
- Orphan-segment salvage (`_salvage_orphan_segments`): on watcher startup,
  re-muxes `.<stem>_segments` temp dirs left behind by a prior crash.
- Three new `MonitoringSection` config fields: `livestream_recording_enabled`,
  `livestream_poll_interval_seconds` (default 30),
  `livestream_manifest_poll_interval_seconds` (default 3, range 1–15).
- Six new Fansly API endpoints: `get_following_streams_online`,
  `get_streaming_channel`, `get_chatroom_messages`, `get_chatrooms`,
  `get_chatrooms_settings`, `post_chatroom_subalert`.
- `StreamSession`, `StreamChannel`, `StreamingInfo` Pydantic models in
  `metadata/models.py` for ephemeral livestream API payloads.
- `textio/prompts.py` — new module centralizing all interactive user prompts;
  all call sites migrated from inline `input()`/`ainput()` to the new helpers.
- `FanslyApi.BASE_URL`, `FANSLY_HOST`, `WS_URL` class variables and 16 endpoint
  `@property` templates centralizing every Fansly URL in one place.
- `--show-config` and `--generate-config` CLI flags.
- `config_schema` render-policy markers (`always` / `conditional`) so generated
  scaffold configs include only operator-required fields.
- `respect_timeline_stats` config field; nullable `usernames` field.
- `cascade-up` semantics for section serialization in the YAML loader.
- `tests/fixtures/config/config_fixtures.py`: `unit_config`, `unit_config_path`,
  `no_display`, `validation_config` fixtures (distinct from the DB-backed `config`).
- `tests/fixtures/download/story_factories.py`: `FakeStory` factory.
- Rate limiter: asyncio.gather concurrency tests covering `floor=0` and `floor>0`
  serialization invariants.

### Changed

- All `FanslyApi` HTTP methods migrated to `async`/`httpx.AsyncClient`; the
  entire download pipeline is now fully async end-to-end.
- `RateLimiterDisplay.start()` moved from `get_api()` to `setup_api()` so the
  cached constructor remains I/O-free.
- `textio/prompts.py` consolidates all prompts; `textio/textio.py` and call
  sites simplified accordingly.
- YAML loader refactored to preserve `model_fields_set` honesty and fix the
  `_rebuild_schema_from_config` round-trip (mutates sections in place instead
  of reconstructing them).
- `config/fanslyconfig.py` section-model decomposition aligned with new
  render-policy schema.
- Regular downloads stream into a sibling `NamedTemporaryFile` and atomically
  `shutil.move` to the target path — partial files no longer reach the dedup
  hash store on interrupted downloads.
- Unbound `logger.*` calls (no `.bind(logger=...)`) now route to the textio
  console and file handlers instead of being silently dropped by all filters.
- `api/websocket.py` `http_client` typed to accept `httpx.AsyncClient` in
  production and `httpx.Client` on the legacy test path.
- `daemon/filters.py` `should_process_creator` ported to async.

### Fixed

- Rate limiter Bug A residual: `_reserve_slot` cursor stalled at
  `learned_floor=0` on cold start. Now advances by
  `max(floor, token_refill_interval)` so concurrent waiters serialise even
  before the floor warms up.
- Rate limiter: removed redundant `last_backoff_time` double-write in the
  success-reduction branch that was staling the cursor clamp.
- Config INI loader: `model_fields_set` honesty preserved during migration so
  fields touched only by defaults are not written to the output YAML.
- `_rebuild_schema_from_config` round-trip: sections are now mutated in place
  rather than reconstructed wholesale, fixing field-ordering regressions.
- Logging grapheme-cluster collapse for supplementary-plane emoji and
  VS16-modified codepoints that misaligned Rich column widths over SSH/tmux.

## [0.13.4] - 2026-05-08

Stash integration enhancements for non-aligned library layouts and
separate-host workflows, a streaming-response error-handling fix, and
test-suite housekeeping that retires unused packaging dependencies.

### Added

- `stash_context.override_dldir_w_mapped` config field. When `true`,
  `get_stash_path()` returns `mapped_path` directly and ignores the
  per-creator subfolder structure — path filters scope to the entire
  fansly-managed area in Stash. Targets users who copy scraper
  downloads to a separate Stash host and reorganise the files into a
  different folder structure (e.g. split by media type into
  `Videos/<studio>/` and `Photos/<studio>/`), so the per-creator
  subfolder name the scraper expects (`<creator>_fansly/`) never
  appears in any Stash file path. Pydantic validator rejects the flag
  at config load when `mapped_path` is unset.
- `stash_context.require_stash_only_mode` config field. When `true`,
  Stash integration only engages on `--stash-only` runs; regular
  download modes (NORMAL, TIMELINE, MESSAGES, WALL, SINGLE, STORIES,
  COLLECTION) skip every Stash code path even when `stash_context`
  is populated. Workflow: scrape locally, copy files to the Stash host
  manually, then run `--stash-only` to attribute metadata.
- `FanslyConfig.stash_active` property — single decision point for
  "should Stash run this iteration." Replaces the bare
  `stash_context_conn is not None` check at the post-download
  StashProcessing call site.
- Three-tier anchor in `_create_targeted_regex_pattern`: per-creator
  `base_path` (default), `mapped_path` (when scoped=False with mapping
  set), or code-only (no mapping). The lazy per-batch fallback now
  scopes regex queries to `mapped_path.*(codes)` instead of going
  library-wide when mapping is configured.
- `prompt-toolkit` runtime dependency. The interactive
  download-directory prompt in `pathio` now offers tab-completion,
  `~`-expansion, and inline path validation.

### Changed

- `_section_to_map` (YAML save) now uses `model_dump(by_alias=True)`
  with an alias-to-attribute lookup so aliased fields write their
  YAML key on save instead of the Python attribute name. Previously
  `LoggingSection.json_level` was dumped as `json_level: ...` which
  the loader rejected on next start (`extra_forbidden`) — config
  files written by 0.13.3 would fail to reload.
- `LoggingSection` gained a `mode="before"` validator that remaps
  legacy `json_level:` keys to the canonical `json:` alias before
  the `extra="forbid"` rejection fires, so configs broken by the
  prior save bug keep loading.
- `get_stash_path()` resolution ladder:
  override + mapped_path → `str(mapped_path)`;
  mapped_path only → prefix substitution (existing 0.13.3 behaviour);
  neither → unchanged local path.
- Stash file indexers (`_index_scene_files` / `_index_image_files`)
  now use `fileio.normalize.get_id_from_filename` for media-ID and
  preview-ID extraction instead of hand-rolled `_id_` / `_preview_id_`
  parsing. Single regex pass; `is_preview` is now available as a
  side-channel signal for downstream tagging work.

### Fixed

- Every non-200 download response was raising `httpx.ResponseNotRead`
  instead of the intended `DownloadError`. `_download_file` and
  `_download_regular_file` request `stream=True` from
  `get_with_ngsw`, then on the non-200 branch tried to format an
  error with `response.content.decode('utf-8')` — httpx forbids
  reading `.content` on a streaming response without a prior
  `.read()`. Fixed to call `response.read().decode("utf-8",
errors="replace")` first, so the actual server status and body now
  surface in error logs for failed downloads.
- `mapped_path` resolution edge case in the by-code regex fallback.

### Removed

- `pyinstaller` dev dependency. The packaging build was abandoned
  upstream and the dead-test audit flagged the harness code as
  untested-and-unreachable.
- `psutil` test dependency (typing stub `types-psutil` retained for
  type-checking).

## [0.13.3] - 2026-05-04

### Added

- `stash_context.mapped_path` config field: translates the local
  `download_directory` prefix to the path the Stash container sees,
  enabling the Stash integration for Docker / NFS setups where mount
  point prefixes differ between the scraper environment and the Stash
  server. Set to the Stash-side root (e.g. `/data/fansly`) and leave
  `download_directory` as the local path. All three Stash path
  operations (metadata scan, `path__contains` preload filter, and
  targeted regex fallback) now go through a new `get_stash_path()`
  helper in `pathio`.

### Removed

- `--stash-scheme`, `--stash-host`, `--stash-port`, `--stash-apikey`
  CLI flags. These were silently broken (the application code was
  accidentally placed inside a docstring in `check_attributes()` and
  never executed). Stash connection settings are config-file only;
  `--stash-only` (a mode flag, not a connection setting) is retained.

### Added

- WebSocket transport now runs on its own dedicated thread with a private
  asyncio loop (`FanslyWebSocket.start_in_thread()` / `stop_thread()`).
  Inbound service events are marshalled back to the main loop so handler-
  side state (EntityStore, StashClient, asyncpg pool) stays single-
  threaded.
- New `_handle_wallet_transaction` daemon handler observes Fansly
  `svc=6 type=3` wallet transaction events (subscription/PPV payments)
  and logs them at INFO so file-availability changes leave a trail.
- `_configure_warnings_capture()` wires `logging.captureWarnings(True)`
  and routes `py.warnings` records through loguru, so warnings emitted
  via `warnings.warn()` (notably `stash-graphql-client`'s
  `StashUnmappedFieldWarning`) now reach the rich console and every
  log-file sink instead of bypassing them to raw stderr.
- `InterceptHandler.emit` now routes by `record.name`: SQLAlchemy /
  asyncpg / Alembic records to the db sink, `stash_graphql_client.*` to
  the stash sink, and `py.warnings` records routed by origin file path.

### Changed

- **Eliminated WebSocket reconnect death-spiral.** The ping_worker
  previously shared the main asyncio loop with downloads, GraphQL
  processing, and polling; under load the loop drifted > 5s, the worker
  woke late and misdiagnosed its own scheduling lag as a Fansly server
  keepalive failure, tearing down a healthy connection roughly every
  30 seconds. Moving the WebSocket transport to its own thread with a
  private loop fixes the root cause (the ping timeout itself is
  spec'd from Fansly's `main.js` as `1.2 * pingInterval` and cannot
  be loosened without diverging from protocol behavior).
- **CLI flags no longer silently rewrite `config.yaml`.** Every flagged
  field (`--stash-only`, `--normal`, `--messages`, `--timeline`,
  `--collection`, `--single`, `--debug`, `-uf` / `-ufp`, `-u`, the nine
  negative-bool flags, the three positive-bool flags) is now marked as
  an _ephemeral override_ and cannot leak into the on-disk YAML. In
  particular, `--debug` no longer clobbers `debug: true` in YAML on every
  invocation that omits the flag, and `-u creator1,creator2` no longer
  overwrites a curated `user_names` list with the daemon's auto-fetched
  following list.
- **Narrowed daemon over-eager following refresh.** `_worker_loop` now
  only triggers `_refresh_following` on `FullCreatorDownload` (confirmed
  subscription WS events), not on `DownloadTimelineOnly`. The
  `/timeline/home` poll's creators are already in the following set by
  construction, so the prior per-poll-hit refresh fanned out to ~30
  account fetches per new post.
- YAML's `session_baseline` is now consume-and-reset: loaded once into
  the runtime field, then cleared and persisted as `null`. Self-heals
  YAMLs left in permanent-full-pass state by a prior schema-write bug.
- Hash-computation calls in `download/media.py` (post-download and
  mid-download checks, 4 sites) now run via `asyncio.to_thread` so they
  no longer block the main download loop.
- Bumped `stash-graphql-client` floor from `>=0.12.0` to `>=0.12.2`.
  `stash/processing/base.py` imports and catches `StashCapabilityError`,
  which only exists in SGC ≥ 0.12.2; the older floor allowed installs to
  resolve to a version missing that symbol.

### Performance

- **30-creator daemon pass: ~70 min → ~12 min (6× speedup); peak RSS:
  9 GB → ~2.4 GB.** Cumulative effect of the entries below.
- FanslyObject `_snapshot` now uses `PrivateAttr(default=None)` with
  initialization in `model_post_init`, rather than
  `default_factory=dict`. Pydantic 2.13's `init_private_attributes`
  calls `inspect.signature` on every `default_factory` per instance to
  decide whether the factory takes a validated-data argument; for
  C-implemented built-ins (`dict`) that lookup falls into
  `inspect._signature_fromstr` (~50 KB allocation per call, not cached
  upstream). At identity-map preload scale (~682K FanslyObject
  constructions across all model types) this dominated per-instance
  cost. Mirrors `stash-graphql-client@c701c4606` (the upstream sibling
  fix that ships for SGC-side entities).
- 18 mutable list field defaults converted from `= []` to
  `Field(default_factory=list)`. Pydantic 2 deep-copies mutable field
  defaults per instance to prevent shared state; `Field(default_factory=
list)` calls the factory directly without going through `copy.
deepcopy`. Eliminates ~106 MB of allocator churn (memo dicts,
  `_keep_alive` table, dispatch walks) per startup preload — confirmed
  by memray attribution dropping from 119 MB to 13 MB through the
  `copy.py` subtree.
- `PostgresEntityStore._type_index: dict[type, set[int]]` secondary
  index. Per-type iteration paths (`filter`, `find` cache-first,
  `find_one`, `count`, `find_iter`, `invalidate_type`, `cache_stats`)
  now run in O(this type) instead of O(`_cache`). Mirrors MMsD
  `postgres_entity_store.py:333` / SGC `store.py` `_type_index`.
  Maintained alongside `_cache` in `cache_instance` / `invalidate*`.
- Monotonic-clock TTL with per-type override:
  `PostgresEntityStore(default_ttl=...)` constructor argument and
  `store.set_ttl(model_type, ttl)` per-type setter. `_is_expired` uses
  `time.monotonic()` (immune to wall-clock changes). Daemon poll
  cadences span 30s (stories) to 10min (FYP), so per-type granularity
  matters; `get_from_cache` evicts and returns `None` once an entry's
  TTL has elapsed.
- `cache_stats()` rewritten to walk `_type_index` (O(types)) instead of
  scanning `_cache` (O(N)); now also exposes the existing `_stats`
  hit/miss counters in the same payload.
- Singular `belongs_to` relationships go tri-state — `UNSET` /
  `None` / object — using SGC's `UnsetType` sentinel imported from
  `stash_graphql_client.types.unset`. `__setattr__` distinguishes the
  three states; cache hits link the relationship, cache misses leave
  `UNSET` so consumers use `is_set()` to distinguish "not yet loaded"
  from "explicitly cleared". `_autolink_relationships(obj)` runs after
  `model_validate` to resolve `UNSET` → cached object after DB-load,
  preventing `to_db_dict`'s FK-derivation loop from clobbering FK
  columns to NULL.
- Stash creator-media indexes (`_scene_code_index`,
  `_image_code_index`) now store entity IDs rather than full Pydantic
  refs. Storing full objects pinned them past `invalidate_type`, so the
  per-creator heap drop was deferred to end-of-stash. `find_*_by_media_
codes` rehydrates via `store.get_many` (cache-first, batched DB-fetch
  for misses).
- Per-creator stash invalidation list extended to include `VideoFile`
  and `ImageFile` leaves. `Scene`/`Image` preload via the GraphQL
  fragment caches the file-leaf children as separate identity-map
  entries; orphaning them across creators leaked ~3.9k objects per pass
  on a typical fan with ~3k images and ~800 scenes.

### Fixed

- **`StashUnmappedFieldWarning` and other `warnings.warn()` output now
  reach loguru sinks.** Two compound failures: `logger.patch()` was
  treated as in-place but actually returns a new logger (so
  `extra["logger"]` was never bound on stdlib-routed records, and every
  sink's `record.extra.logger == "<name>"` filter rejected them), and
  `logging.captureWarnings(True)` was never called (so `warnings.warn()`
  never entered the stdlib logging path at all). Together, SGC's
  `StashUnmappedFieldWarning` and any other library's `warnings.warn()`
  output bypassed the rich console + log files entirely.
- `InterceptHandler.emit` and `SQLAlchemyInterceptHandler.emit` previously
  fell back to `level = str(record.levelno)` on unknown level names; that
  string re-raised inside loguru's `.log()` because numeric strings are
  not registered level names. Pass the int through directly — loguru
  accepts ints natively.
- `config/logging.py`'s console formatter now escapes `<` in record
  messages in addition to `{`/`}`, so traceback frame names like
  `<module>`, `<listcomp>`, and `<genexpr>` no longer crash loguru's
  colorizer with `ValueError: Tag "<module>" does not correspond to any
known color directive`. Loguru re-parses callable formatter output to
  strip tags even when `colorize=False`, so the escape is required
  regardless of sink color setting.
- `stash/processing/base.py` now catches `StashCapabilityError` distinct
  from `StashVersionError`, so per-feature appSchema gate failures
  surface as a clean error message rather than the bare exception that
  was leaking out of `get_client()`. Also broadens an over-narrow
  `except RuntimeError` (line 346): `stash_graphql_client.metadata_scan`
  raises `ValueError` on transport errors, which previously escaped
  uncaught.
- `_shutdown_js_bridge` (`helpers/checkkey.py`) previously only called
  `connection.stop()`, which terminates the Node subprocess but leaves
  several JSPyBridge Python daemon threads (`com_thread`, `stdout_thread`,
  `EventLoop.callbackExecutor`, per-task threads) blocking on
  `stream.readline()` / `queue.get()` until each individually polls and
  notices the subprocess died. Now joins all of them explicitly.
- Three daemon worker-loop bugs surfaced and fixed during test reform:
  `_handle_full_creator_download` was calling `download_wall(config,
state)` without the required `wall_id`, `_refresh_following` was
  missing `get_creator_account_info`, and `_handle_timeline_only_item`
  was passing an empty `creator_name=""` downstream.
- xdist worker shutdown no longer raises `SIGABRT` / `SIGSEGV` / `SIGBUS`.
  Root cause was daemon threads (Rich Live refresh, loguru handler
  workers, httpx connection pool teardown) racing against
  `_Py_Finalize.flush_std_files` mid-buffered-write. Fixed by setting
  `TESTING=1` before any project import (forces synchronous loguru
  sinks), enabling `faulthandler.enable(all_threads=True)` for future
  diagnostics, plus per-test cleanup discipline (autouse fixtures that
  reset Rich/loguru/httpx state after every test) and an `atexit`
  backstop in `helpers/rich_progress.py`.
- Stash GraphQL test responses aligned with SGC 0.12's numeric-string /
  UUID4 ID validator and the new `StashEntityStore.populate()` filter-
  query resolution hop that fires before mutations.

### Removed

- `FanslyWebSocket.start_background()` / `stop()` / `_background_task`
  — replaced by the thread-based lifecycle (`start_in_thread()` /
  `stop_thread()`). Migrated all in-tree callers (`api/fansly.py`,
  `daemon/runner.py`, `scripts/websocket_example.py`); no deprecation
  period.
- `--updated-to` CLI flag and the corresponding `FanslyConfig` field.
  The self-update feature was removed upstream and the field had zero
  readers.
- `tests/daemon/conftest.py` — only `tests/conftest.py` is permitted
  per the project rule. The three fixtures it housed (`fake_ws`,
  `saved_account`, `config_wired`) migrated to `tests/fixtures/`.

### Internal

- Test-suite reform: every internal mock removed in favor of real
  fixtures, factories, and `store.add()` / `store.save()` preloads.
  Edges remain mocked via `respx` (Fansly HTTP, Stash GraphQL via the
  `httpx` transport) and external-library leaf calls (`imagehash`,
  `hashlib`, ffmpeg subprocess). Total project coverage rose to ~97.5%
  with `config/logging.py` at 100%, `api/websocket.py` at 99.06%, and
  new daemon test modules covering bootstrap, runner handlers, and
  runner loops end-to-end.
- New `--dist=loadgroup` pytest configuration plus an
  `xdist_group(name)` marker. Tests pinned to the same group share an
  xdist worker so their fragile shared state (notably the
  `message_media_generator` fixture's find-or-create race against a
  shared Docker Stash) doesn't deadlock across workers.

## [0.13.1] - 2026-04-23

Patch release fixing two issues reported on Windows + Python 3.14, plus
rolling up incidental `fork-main` commits since v0.13.0.

### Added

- `docs/reference/request-signing.md` — reverse-engineered notes on Fansly's
  request-signing scheme.

### Changed

- **Dependency**: added `filelock ^3.29.0` as a direct runtime dep, replacing
  POSIX-only `fcntl` usage in the `config.ini → config.yaml` migration lock
  (see #77). filelock picks the right platform primitive automatically
  (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows).
- `poetry.lock` and pre-commit hook versions refreshed.

### Fixed

- **#77** — Unconditional `import fcntl` at `config/loader.py:28` crashed app
  startup on Windows (fcntl is POSIX-only). Swapped to
  [filelock](https://github.com/tox-dev/filelock) with identical non-blocking
  semantics. Bonus: filelock cleans up stale `config.ini.migrating.lock` files
  on release (safe — unlink happens while the lock is still held, no TOCTOU).
- **#78** — Fansly returns `locations=[{"location": null, "locationId": 1}]`
  for some media (Direct slots with no CDN path resolved yet), which the
  required-str typing on `MediaLocation.location` rejected with 8 Pydantic
  validation errors per affected `Media`. Relaxed the field to `str | None`
  and the DB column to NULL-tolerant (Alembic migration
  `bb7006ec7c0e_make_media_locations_location_nullable`). Downstream readers
  at `media/media.py:27` already handled `None` via the `loc.raw_url or
loc.location` fallback.

## [0.13.0] - 2026-04-22

First release under the Keep-a-Changelog format. Flagship feature: the
post-batch monitoring daemon. Also rolls up every notable change landed
since v0.11.0 shipped (a "v0.12" line was never cut as a distinct release).

### Added

- **Post-batch monitoring daemon** (`--daemon` / `-d` / `--monitor`) — continuous
  WebSocket-driven event dispatch for new posts, stories, PPV messages,
  message edits/deletes, subscription changes, and profile updates. Decoded
  `ServiceEvent` dicts are translated into typed `WorkItem`s
  (`DownloadTimelineOnly`, `DownloadStoriesOnly`, `DownloadMessagesForGroup`,
  `FullCreatorDownload`, `CheckCreatorAccess`, `RedownloadCreatorMedia`)
- **`ActivitySimulator`** — three-tier state machine (active → idle → hidden)
  calibrated from real browser-session profiling drives the daemon's polling
  cadence
- Timeline + story polling fallback for when the WebSocket is quiet;
  timeline-first-page probe short-circuits inactive creators
  (`lastCheckedAt` vs `post.createdAt`)
- Persistent `MonitorState` table — daemon restarts don't re-trigger every
  story or cold-scan every timeline
- Live Rich dashboard with per-creator state, WS health, queued work, and
  simulator phase
- Single shared `FanslyWebSocket` instance for both batch-sync and the
  daemon loop, with bidirectional cookie sync
- Clean SIGINT drain — `Ctrl-C` stops the queue and tears down the WS
  connection without dropping in-flight work
- **YAML configuration** (`config.yaml`) — new primary config format;
  legacy `config.ini` files auto-migrate on first run and are backed up
  as `config.ini.bak.<timestamp>`. Comments in user-edited YAML are
  preserved across load → modify → save cycles via `ruamel.yaml`
- `config.sample.yaml` template
- New `[monitoring]` config section for activity-simulator phase durations
  and per-phase polling intervals
- **New documentation**:
  - [Architecture](reference/architecture.md) — canonical public architecture reference
  - [Monitoring Daemon Cadence](reference/monitoring-cadence.md) — polling intervals + anti-detection rationale
  - [Monitoring Daemon Architecture](planning/monitoring-daemon-architecture.md) — full daemon design document (intervals, endpoints, behaviors verified against production `main.js`)
  - [Fansly WebSocket Protocol](reference/Fansly-WebSocket-Protocol.md) — WS protocol breakdown: service IDs, message types, price encoding (mills ÷ 1000), dual endpoints
  - [Manual Token Extraction](guide/manual-token-extraction.md) — fallback browser-token extraction guide (salvaged from prof79 wiki)
- `CHANGELOG.md` (this file) — new Keep-a-Changelog format going forward
- MkDocs + readthedocs-themed docs site with GHA deploy workflow to `gh-pages`
- Daemon dispatch: explicit no-op handler pattern (`_handle_noop_events` + `_NOOP_DESCRIPTIONS` in `daemon/handlers.py`) distinguishes "known event, deliberately ignored" from "unknown event, coverage gap" in logs.
- `daemon.handlers.has_handler(svc, type)` helper exposed for callers that need to differentiate handled-returned-None from no-handler-registered.
- `download/messages.py::download_messages_for_group` — daemon entry point that downloads a DM group by id, resolving creator identity from the group. Batch-path `download_messages` unchanged.
- `DownloadMessagesForGroup.sender_id` — optional field carrying the WS `senderId` so the runner can pre-populate creator identity.

### Changed

- **Dependency floor**: `stash-graphql-client` bumped from `>=0.11.0` to
  `>=0.12.0`, unlocking batched GraphQL mutations (`save_batch`,
  `execute_batch`, `save_all`), `__side_mutations__` mechanism, and
  ActiveRecord-style relationship DSL. **Transitively requires Stash
  server v0.30.0+** (appSchema 75+).
- README rewritten to cover the daemon, YAML config, and the fork's new
  canonical home at `Jakan-Kink/fansly-scraper`. Repo URLs and issue
  references throughout updated from `prof79/fansly-downloader-ng`.
- Loguru / Rich color schema split; built-in level colors updated for
  consistent palette across log streams and live display
- Emoji throughout standardized to `Emoji_Presentation=Yes` (width-2, no
  VS16 selector hacks required by any modern terminal)
- Stash ORM Migration Guide refreshed — Phase 4 (advanced features) is
  now **Ready to Start**, with the SGC v0.12 batch APIs identified as
  primary adoption targets

### Fixed

- Rich Live display corruption from stdout prints — stable frame
  rendering under concurrent logging
- Silent Rich handler errors on catch-all exceptions — now surfaced to
  the log instead of being swallowed
- Pydantic config validation errors now readable + tolerant of
  retired-field upgrades (legacy configs with dropped fields load
  without raising)
- Signal / bridge / logging race conditions on daemon teardown — errors
  no longer leak past the thread boundary
- WebSocket logger split so daemon and sync-path WS traffic don't
  interleave in the same log level
- Two WebSocket / tempfile bugs closed during retired-flag cleanup
- Daemon messages handler no longer emits `"Could not find a chat history with None"` — `_handle_messages_item` now resolves creator from `item.sender_id` and calls `download_messages_for_group`, so attachments on incoming DMs actually download.
- Daemon `_on_service_event` "not mapped" DEBUG log only fires for genuinely unknown `(svc, type)` tuples now, not handled-returned-None.

### Deprecated

- Classic prose-style `ReleaseNotes.md` is frozen at v0.13.0. New
  releases are authored in this file (`CHANGELOG.md`) going forward.

### Removed

- **Retired settings** (silently dropped from legacy configs — no
  runtime code branches on them):
  - `db_sync_commits`, `db_sync_seconds`, `db_sync_min_size` — SQLite-era
    `BackgroundSync` workaround, obsolete under PostgreSQL
  - `metadata_handling` — no-op since the Pydantic EntityStore rewrite
  - `separate_metadata` — SQLite-era flag
- `updater/` module (pycache-only remnant from the prof79-era
  self-updater, no tracked source)
- `config.sample.ini` — YAML migration makes the INI sample redundant
- Stale documentation pruned: pre-Pydantic test migration tracker,
  SA-ORM code examples from the Stash mapping reference, pre-work Stash
  integration analyses, rejected side-by-side PostgreSQL plan,
  abandoned async-conversion plan, archaic H.264/MP4 PDF + author notes
  (superseded by PyAV for mp4 hashing)

[Unreleased]: https://github.com/Jakan-Kink/fansly-scraper/compare/v0.13.7...HEAD
[0.13.7]: https://github.com/Jakan-Kink/fansly-scraper/compare/v0.13.6...v0.13.7
[0.13.6]: https://github.com/Jakan-Kink/fansly-scraper/compare/v0.13.5...v0.13.6
[0.13.5]: https://github.com/Jakan-Kink/fansly-scraper/compare/v0.13.4...v0.13.5
[0.13.4]: https://github.com/Jakan-Kink/fansly-scraper/compare/v0.13.3...v0.13.4
[0.13.3]: https://github.com/Jakan-Kink/fansly-scraper/compare/v0.13.1...v0.13.3
[0.13.1]: https://github.com/Jakan-Kink/fansly-scraper/compare/v0.13.0...v0.13.1
[0.13.0]: https://github.com/Jakan-Kink/fansly-scraper/releases/tag/v0.13.0
