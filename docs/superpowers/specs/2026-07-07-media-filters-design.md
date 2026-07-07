# Media Filters (min/max size and duration) — Design

Date: 2026-07-07
Issue: #125 ([Feature Request] Minimum and maximum filter for file size and duration)
Branch: `wall-filters` (continues after the wall_filters commit)
Status: approved (brainstorm 2026-07-07)

## Problem

A creator who streams constantly posts every full VOD to their timeline; users want to skip huge unedited VODs and sub-second clips without giving up the rest of the content. OF-Scraper offers `file_size_limit`/`file_size_min` (bytes or human-readable, enforced at download time from content-length) and `length_min`/`length_max` (seconds, videos only, pre-download list filter) — reference implementation confirmed in `~/docker/scraping-builds/OF-Scraper/ofscraper/filters/media/filters.py` and `.../download/managers/downloadmanager.py::_check_forced_skip`.

Fansly constraints that shape the design: `Media.duration` is pre-download API metadata (parsed from the raw `metadata` JSON by `_extract_video_dimensions`); no API field carries file size. Regular files reveal size via the `content-length` header when the GET starts (download/media.py `_download_regular_file`); streaming VODs (HLS/DASH) declare per-variant bitrate in the manifest (`#EXT-X-STREAM-INF:BANDWIDTH`/`AVERAGE-BANDWIDTH`; MPD `Representation@bandwidth` + `mediaPresentationDuration`), enabling a bitrate x duration size estimate before any media bytes are fetched; true live captures (IVS sliding window) have no knowable total size or duration mid-broadcast.

## Solution overview

Four limits — `file_size_min`, `file_size_max`, `duration_min`, `duration_max` — in a new top-level `filters:` config section (which also becomes the new home of the just-shipped-but-unreleased `wall_filters`, as `filters.wall`), set globally with optional per-creator overrides via `filters.media.by_creator`. Enforcement is a two-phase gate set in a new `download/mediafilters.py`: a metadata gate in the `download_media` loop (duration + any size evidence already recorded), and download-time gates (content-length header for regular files; bandwidth x duration estimate, running-total backstop, and completion check for streaming). Filtered skips record observation keys into the Media row's `meta_info` JSON and never touch download-state fields, so loosened limits later download normally.

## Config surface

### New top-level `filters` section (`config/schema.py`)

```yaml
filters:
  wall:                      # relocated wall_filters; None/absent = no wall filtering
    creator1: ["FULL VIDEOS"]
  media:
    file_size_min: 100KB
    file_size_max: 4GB
    duration_min: "0:03"
    duration_max: 2h
```

- `FiltersSection(_BaseSection)` with two fields: `wall: dict[str, WallFilterSpec]` (default `{}`; `None` normalizes to `{}`; reuses the existing lenient normalization validator from `config/wall_filters.py`) and `media: MediaFiltersSection` (default all-disabled).
- `MediaFiltersSection(_BaseSection)`: `file_size_min`/`file_size_max` normalized to `int | None` bytes; `duration_min`/`duration_max` normalized to `float | None` seconds. `0`, `null`, or absent = that filter disabled (normalized to `None`). `min > max` (either pair, both set) raises `ConfigError` at load.
- **Per-creator overrides**: `filters.media.by_creator`, a map of creator → partial override with the same four fields:

```yaml
filters:
  media:
    file_size_max: 4GB          # globals = defaults for everyone
    duration_max: 2h
    by_creator:
      vod_streamer:
        duration_max: 45m       # tighter for this creator; file_size_max inherited
      archived_creator:
        file_size_max: 0        # explicit 0 = disable THAT limit for this creator
      new_creator: {}           # no-op: inherits all globals
```

- `MediaFilterOverride` model (`extra="forbid"`, same per-field parsers): **field-level tri-state** — a field ABSENT from the YAML inherits the global; a field explicitly set to `0`/`null` disables that limit for that creator; a value overrides. Distinguished via Pydantic `model_fields_set` (absent vs explicitly-null), since both normalize the stored value to `None`. `min > max` validated on the RESOLVED per-creator result at load for each override, not just the globals.
- Override keys sanitized like `wall_filters` keys (`@`-strip, lowercase); all-digit keys >= 10 chars are account snowflake IDs. Unlike `wall_filters`, no API re-keying is needed: `by_creator` keys are lookups (not run scope), and the per-creator loop already knows both `state.creator_name` and `state.creator_id`, so resolution checks the username key first, then `str(creator_id)`.
- **`wall_filters` relocation**: `options.wall_filters` (committed post-0.14.5, never released) moves to `filters.wall`. Clean move, no `_DROPPED_FIELDS` compat: schema field relocates; `FanslyConfig.wall_filters` and all internals stay unchanged; populate/save-back mapping, wall-filters validation references, docs, and the unreleased CHANGELOG bullet update to the new path. The `--wall-filters` CLI flag is untouched.

### Value parsing (`config/media_filters.py`, import-cycle-free like `config/wall_filters.py`)

No hand-rolled unit tables — parsing delegates to existing libraries; the wrappers own only the disable semantics and error UX:

- `parse_size(value) -> int | None`: delegates to `pydantic.ByteSize` (already a dependency) — raw int/float bytes, decimal `KB`/`MB`/`GB`/`TB`, binary `KiB`/`MiB`/`GiB`/`TiB`, case-insensitive, optional space. `0`/`None`/empty → `None`.
- `parse_duration(value) -> float | None`: numeric values pass straight through as seconds; strings delegate to `pytimeparse2` (new dependency, installed via `poetry add pytimeparse2` — never editing pyproject directly) — colon forms (`"1:30"`, `"1:30:00"`), unit suffixes (`"45s"`, `"45m"`, `"2h"`), and compound forms (`"2h30m"`) for free. `0`/`None`/empty → `None`.
- Garbage input (library returns None / raises) → `ConfigError` carrying a correct-syntax example (same UX pattern as `WALL_FILTERS_EXAMPLE`); negative values raise likewise.
- `MediaFilters` runtime model (frozen) holding the four normalized values, `is_active` property, and verdict helpers: `duration_verdict(duration: float | None) -> str | None` and `size_verdict(size_bytes: int | None) -> str | None`, returning the violated-limit name (`"duration_max"`, `"file_size_min"`, ...) or `None` for pass; `None` input always passes.
- `MediaFilters.for_creator(username: str | None, creator_id: int | None) -> MediaFilters`: applies the matching `by_creator` override field-by-field (username key, then ID key) and returns a resolved frozen instance; no match returns the global instance. Called once per creator at gate entry — every gate consumes the resolved instance, so the per-creator layer adds no logic inside the gates themselves.

### FanslyConfig plumbing

- `FanslyConfig.media_filters: MediaFilters` (default all-`None` = inactive), populated in `_populate_config_from_schema` from `schema.filters.media`; save-back via `_maybe_set` on the section (immutable-frozen runtime model means no aliasing-mutation hazard, unlike wall_filters — documented in code where the wall path deep-copies).

### CLI (`config/args.py`)

- Four flags, NOT in the download-modes group (they compose with any mode): `--file-size-min`, `--file-size-max`, `--duration-min`, `--duration-max`. Values parsed with the same functions; explicit `0` disables that filter for the run; each set flag becomes an ephemeral override (never written to YAML). No mode enforcement — passive limits.
- CLI flags override the GLOBAL layer only; `by_creator` entries still win for their creators (a flag changes the default everyone else inherits).

## Runtime enforcement (`download/mediafilters.py`)

### Metadata gate — in `download_media`'s per-item loop

- `check_media_filters(config, state, media) -> str | None` (skip-reason or `None`), called directly after the existing `is_preview` skip and BEFORE `process_media_download`, so filtered items never enter download-state bookkeeping.
- Returns `None` immediately when `state.download_type == DownloadType.SINGLE` (explicitly requested post always downloads) or when `config.media_filters.is_active` is false. The SINGLE exemption and the inactive short-circuit apply to EVERY gate in this design — the wire and streaming gates receive `state` (threaded from `download_media`, which already holds it) and perform the same check, so a `--single` download is never size-gated either.
- Duration limits: checked against `media.duration`; videos only; `None` duration passes (OF-Scraper parity).
- Size limits: checked against prior evidence parsed from the row's `meta_info` JSON — `observedContentLength` (ground truth from an earlier header/completion measurement) wins over `estimatedContentLength` (bitrate math); neither present = pass, defer to download-time gates.
- On skip: merge `lastFilteredReason` into `meta_info` (telemetry; the gate recomputes from current limits each run, so nothing sticks).

### Wire gate — regular files, in `_download_regular_file`

- After status 200, read `content-length` BEFORE opening the temp file/streaming: violation → close the response unstreamed, merge `observedContentLength` + `lastFilteredReason` into `meta_info` (`store.save`, dirty-tracked), raise `MediaFilteredError` (new exception in `errors/`).
- `download_media`'s loop catches `MediaFilteredError`, prints the filtered-skip line, increments the filtered counter, continues. Missing/zero `content-length` = pass (cannot judge).

### Streaming gates — HLS/DASH VODs (`download/m3u8.py` path)

1. **Estimate gate (pre-download)**: fetch the master playlist once, pick the highest-quality variant's declared bitrate (preferring `AVERAGE-BANDWIDTH` over `BANDWIDTH` when present), estimated size = bitrate / 8 x `Media.duration`. HLS masters carry no duration, so `Media.duration` (API metadata, present on Fansly VODs) is the duration source; unknown duration or missing bandwidth → gate passes silently (backstop/completion gates still apply). Gate BOTH size bounds on the estimate; violation → record `estimatedContentLength` + `lastFilteredReason`, `MediaFilteredError`, zero media bytes fetched. (The DASH MPD example that motivated this carries `bandwidth` + `mediaPresentationDuration` the same way, but FDNG's download pipeline is HLS-only today — DASH is out of scope until the pipeline ever fetches MPDs.)
2. **Running-total backstop (`file_size_max` only)**: in the segment download loop (`_try_segment_download`) accumulate bytes across segments; crossing the max → cancel remaining segment futures, discard temp segments, record `observedContentLength` (>= running total) + reason, `MediaFilteredError`. Caps waste at `file_size_max` bytes when the declared bitrate underestimated. The PyAV direct path (`_try_direct_download_pyav`) enforces the same by statting the growing output between packet batches.
3. **Completion check (`file_size_min` only)**: after mux/download completes, stat the final file; under min → delete, record true `observedContentLength` + reason, `MediaFilteredError`. (Min violations are tiny by definition; waste is negligible.)
- **Live captures exempt**: `download/livestream.py` (IVS sliding-window recording) is untouched by all four filters — no total size or duration exists mid-broadcast.

### Recording semantics (applies to every gate)

- Recording = merging observation keys into the existing Media row's `meta_info` JSON: `observedContentLength` (header value / actual bytes), `estimatedContentLength` (bitrate math), `lastFilteredReason` (violated-limit name). Saved through the normal `store.save()` dirty-tracking path.
- Marking never happens: `is_downloaded`, `content_hash`, `local_filename` are untouched by filters. Loosening limits later downloads the item normally — every gate recomputes from current limits + stored observations.
- Known caveat (accepted): `meta_info` is an API-sourced tracked field; the next API re-serve of that media overwrites the blob and drops the injected keys, costing one extra download-time re-check on the following run. Self-healing, no stuck state.

## Statistics and output

- `DownloadState.filtered_count` (new counter) + a line in the per-creator statistics output.
- Per-item message mirrors the dedupe-skip style, gated by `show_downloads`/`show_skipped_downloads`: `Filtered [duration_max 2h]: video 'file.mp4' -> skipped`.

## Error handling

- Parser errors and `min > max` raise `ConfigError` at load/arg time with a correct-syntax example.
- `MediaFilteredError` is a control-flow signal, not a failure: caught in `download_media`'s loop; never bubbles to the per-creator error handler; never triggers `input_enter_continue`.
- Malformed/absent manifest bandwidth attributes → estimate gate passes silently (falls through to backstop/completion gates).
- Malformed `meta_info` JSON when merging observations → replace-with-fresh-object is NOT allowed (it would drop API metadata); instead skip the merge with a debug log (the filter still functions, just without persistence that run).

## Testing

Per `docs/testing/TESTING_REQUIREMENTS.md` and the fixture-location policy; respx via `respx_fansly_api`, real `entity_store` + factories, real TS/MP4 fixtures from `tests/fixtures/api/livestream_factories.py` and `tests/fixtures/fileio/`.

- **Parser tests** (`tests/config/unit/test_media_filters.py`): size/duration parsing tables (all accepted forms incl. binary suffixes and colon/unit durations; garbage raises with example; `0`/`null` disable), `min > max` raise, `FiltersSection` shape (wall relocation, `filters.wall: null` -> `{}`, media defaults), CLI flag parsing/override/ephemerality, `0`-flag disables for the run.
- **Resolution tests**: `for_creator` merge table — inherit (absent field), explicit-0 disable, value override, `{}` no-op, ID-key lookup via `creator_id`, unknown creator returns globals, tri-state via `model_fields_set`, resolved `min > max` raises at load, CLI-global override + `by_creator` precedence.
- **Gate tests** (`tests/download/unit/test_mediafilters.py`): metadata-gate verdict table (duration bounds, videos-only, `None`-duration passes, SINGLE exemption, inactive short-circuit, observed-beats-estimated evidence precedence, recorded-size reuse); wire gate with respx (violating `content-length` → body never streamed, `meta_info` merged, `is_downloaded` stays False, filtered counter incremented; missing header passes); `MediaFilteredError` loop handling.
- **Streaming tests**: estimate gate against HLS master fixtures (`tests/fixtures/api/livestream_factories.py::build_master_playlist` takes a `bandwidth` kwarg) with known bandwidth x duration products (both pass and violate, plus unknown-duration pass-through); running-total abort using real TS segment fixtures; completion-check min path (real small file, deleted); estimated-vs-observed precedence on a later metadata-gate pass.
- **Relocation tests**: existing wall-filters config tests updated to `filters.wall`; one test asserting `options.wall_filters` now rejects (`extra="forbid"`).

## Docs / packaging

- `docs/configuration/config_options.md`: new top-level `filters` section documentation — move the wall_filters subsection under it, add `filters.media` with all accepted value formats, the `by_creator` override semantics (absent = inherit, `0` = disable for that creator), the estimate-based nature of streaming size checks (declared-peak bitrate can over-estimate; `--file-size-max 0` is the per-run escape hatch), the SINGLE and live-capture exemptions, and the four CLI flags (global layer only; `by_creator` wins).
- CHANGELOG: amend the unreleased wall_filters bullet to reference `filters.wall`; add an Added bullet for media filters referencing `filters.media` + the CLI flags. Minor-version framing; existing configs unaffected (defaults disabled; `options.wall_filters` never shipped in a release).

## Out of scope

- OF-Scraper's `filter` (content-type selection) and `max_post_count` — separate concerns. Its `block_ads` needs no equivalent: FDNG's existing `download_media_previews: false` already skips promotional/preview media (the `is_preview` skip this design's metadata gate sits beside).
- Live-capture size/duration limits (no knowable totals mid-broadcast).
- Retroactive cleanup of already-downloaded files that violate current limits.
