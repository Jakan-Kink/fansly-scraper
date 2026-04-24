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

### Added

### Changed

### Fixed

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

[Unreleased]: https://github.com/Jakan-Kink/fansly-scraper/compare/v0.13.1...HEAD
[0.13.1]: https://github.com/Jakan-Kink/fansly-scraper/compare/v0.13.0...v0.13.1
[0.13.0]: https://github.com/Jakan-Kink/fansly-scraper/releases/tag/v0.13.0
