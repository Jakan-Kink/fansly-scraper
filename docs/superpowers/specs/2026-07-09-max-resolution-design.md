# Max-Resolution Cap (`filters.media.max_resolution`) — Design

Date: 2026-07-09
Origin: Discord follow-up to #125 ("tell it the max resolution to download cause sometimes I don't want the giant 4k versions")
Target: 0.15.1
Branch: continues on `wall-filters` (or a fresh branch off fork-main; operator's call at merge time)

## Problem

A user wants to cap download resolution — take a 1080p rendition instead of the 4K one to save space. Fansly serves most media in multiple resolutions: regular files carry a `variants` list plus a default rendition, and HLS VODs carry multiple variants in the master playlist. Today both selection points pick the single highest resolution (`_select_best_variant` in `media/media.py`, `_get_highest_quality_variant_url` in `download/m3u8.py`). There is no way to ask for a smaller rendition.

## Solution overview

A new `filters.media.max_resolution` setting caps rendition selection: pick the highest rendition whose **shorter edge** is at or below the cap; if no rendition qualifies, skip the item as a filtered download (reusing the `filters.media` skip machinery from #125). It is a rendition *cap* first (real downscaling when a smaller variant exists) and a skip only when nothing fits. It extends the existing `filters.media` section, inheriting per-creator `by_creator` overrides, the `--max-resolution` CLI override, the filtered-skip recording + `Filtered by filters.media` statistics line, and the `--single`/live-capture exemptions.

## Decisions (from brainstorming)

- **Hard ceiling with skip-on-no-fit.** Pick the highest rendition with `min(w,h) <= cap`; if renditions exist with known resolution but none qualify, skip (filtered). A downscale is a normal download (no skip record); only the no-fit case is recorded/counted.
- **Comparison on the shorter edge** — `min(width, height) <= threshold` — so portrait and landscape both work (a 1080x1920 vertical video and a 1920x1080 landscape video both qualify at `1080p`; 4K in either orientation is capped).
- **Lives in `filters.media`** — reuses by_creator, CLI, skip-recording, exemptions.
- **Accepts a named tier OR a bare pixel integer**, stored raw for config round-trip fidelity.
- **Both variant selectors get the cap** (regular files and HLS); unknown-resolution renditions never trigger a skip (silent-pass).

## Config surface (`config/media_filters.py`, `config/schema.py`)

### `Resolution` ladder

```python
from enum import StrEnum

class Resolution(StrEnum):
    P240 = "240p"
    P360 = "360p"
    P480 = "480p"
    P720 = "720p"
    P1080 = "1080p"
    P1440 = "1440p"
    UHD = "4k"

    @property
    def dimensions(self) -> tuple[int, int]:
        return {
            "240p": (426, 240),
            "360p": (640, 360),
            "480p": (854, 480),
            "720p": (1280, 720),
            "1080p": (1920, 1080),
            "1440p": (2560, 1440),
            "4k": (3840, 2160),
        }[self.value]

    @property
    def threshold(self) -> int:
        """Shorter-edge pixel count for this tier (the 'p' number)."""
        return min(self.dimensions)

    @classmethod
    def _missing_(cls, value: object) -> "Resolution | None":
        """Case-insensitive lookup plus the 2160p -> 4k alias."""
        if isinstance(value, str):
            v = value.lower()
            for member in cls:
                if member.value == v:
                    return member
            if v == "2160p":
                return cls.UHD
        return None
```

`threshold` yields the monotonic ladder `240 / 360 / 480 / 720 / 1080 / 1440 / 2160`. `dimensions` is retained for display/logging and future use; the cap comparison only needs `threshold`. `_missing_` mirrors the existing `DownloadMode` enum's case-insensitive pattern (StrEnum members are `str`, so `value.lower()` is a plain `str`).

### Stored value: raw `int | str | None`

`max_resolution` is stored in the **user's authored form** and never normalized on the way in, so a mid-run config save-back writes `4k` back as `4k` (not `2160`). This is the same round-trip-fidelity requirement that the #125 `by_creator` `field_serializer` fix established — a normalized-on-load value would clobber the user's file on the next `_save_config`.

- `MediaFiltersSection.max_resolution: int | str | None = None`
- `MediaFilterOverride.max_resolution: int | str | None = None` (by_creator, tri-state via `model_fields_set`)
- `MediaFilters.max_resolution: int | str | None = None` (runtime frozen model)

### Conversion + validation (single source of truth)

```python
def resolution_threshold(value: int | str | None) -> int | None:
    """Shorter-edge pixel cap for a max_resolution value; None = disabled.

    Accepts a Resolution tier name (case-insensitive, '2160p' alias),
    a bare pixel integer or digit-string, or 0/None (disabled).
    Raises ConfigError on anything else.
    """
```

- `None` / `0` / `""` -> `None` (disabled; `0` mirrors the other filters' 0-disables convention).
- `int` (or all-digit `str`) > 0 -> that int. Negative -> `ConfigError`.
- tier name resolvable via `Resolution(...)` -> `.threshold`.
- anything else -> `ConfigError`, message listing the named tiers **and** "or a pixel integer", carrying the shared `MEDIA_FILTERS_EXAMPLE`.

A `field_validator` on each `max_resolution` field calls `resolution_threshold(v)` to **validate** (raising on garbage) but returns `v` **unchanged** (preserving the raw form). `ConfigError` is re-raised as `ValueError` so Pydantic wraps it into a field-pathed `ValidationError`, exactly like the #125 parsers (`_parse_sizes`/`_parse_durations`), giving a clean operator-facing error through `load_yaml`.

Union note: with `int | str`, Pydantic tries `int` first, so `1080`/`"1080"` become int `1080` while `"4k"`/`"1080p"` stay strings — all fine, since `resolution_threshold` treats digit-strings and ints identically and only the `4k`/`1080p` forms (the ones worth preserving) stay as authored.

### Runtime model + resolution

- `MediaFilters` gains a `max_resolution_px` property returning `resolution_threshold(self.max_resolution)`.
- `is_active` is true when any of the four #125 limits **or** `max_resolution_px` is set.
- `for_creator(...)` merges `max_resolution` field-wise like the others (raw value carried through; tri-state absent=inherit, explicit `0`/`null`=disable).
- `ensure_valid` is unchanged — a single-ended cap has no min>max pair to cross-check.

### CLI (`config/args.py`)

`--max-resolution` ephemeral flag, same global-layer-override semantics as the #125 flags. Accepts a tier name or an int; `0` / `off` / `none` disables for the run (the enum has no zero tier, so `off`/`none` are accepted as explicit-disable sentinels alongside `0`). Stored raw as an ephemeral override; `by_creator` entries still win.

## Runtime enforcement (Approach A — cap at both selectors)

The resolved cap for the current creator is `resolve_media_filters(config, state).max_resolution_px` (an `int | None`); `resolve_media_filters` already returns `None` for SINGLE mode and inactive filters, so both selectors inherit the exemptions.

### Candidate pool and selection rule

For any media, the candidate renditions are **the default media itself plus its `variants`** (the current code already treats both as candidates: `_select_best_variant` picks the best variant, then `parse_media_info` falls back to the default when the default is higher-res). The cap-aware rule over that pool:

1. Partition renditions into *known* (both width and height present) and *unknown*.
2. `qualifying = [r for r in known if min(r.width, r.height) <= cap]`.
3. If `qualifying` -> pick `max(qualifying, key=lambda r: r.width * r.height)` (highest rendition that fits — the downscale).
4. Else if `known` is non-empty (renditions exist but all exceed the cap) -> **skip** (`MediaFilteredError("max_resolution")`).
5. Else (no known-resolution rendition at all) -> **silent pass**: fall back to today's behavior (pick highest by area / default), never skip purely for missing metadata.

### Regular files (`media/media.py`)

`_select_best_variant(media, max_px)` stays a pure `(media, cap) -> Media | None` helper that returns the highest **variant** whose `min(w,h) <= cap` (steps 1-3 over `media.variants`), or `None` when no variant qualifies. The skip-vs-downscale *decision* is centralized in `parse_media_info` (which has `state` and resolves `max_px`), because only it sees the full candidate pool — the default media plus the variants:

1. `best_variant = _select_best_variant(media, max_px)` (best fitting variant, or None).
2. `default_fits = media.width/height known and min(default) <= cap` (or cap disabled).
3. If both a fitting variant and a fitting default exist, keep the existing "default wins if higher-res" tiebreak — but only among renditions that fit the cap.
4. If neither the default nor any variant fits **and at least one candidate has known resolution** -> record the observation and raise/skip so the item is dropped from `accessible_media` with `state.filtered_count += 1`.
5. If no candidate has known resolution -> silent pass (today's behavior).

Because `parse_media_info` runs before the `download_media` loop, the regular-file skip is recorded here at parse time (via the existing `record_filter_observation`), not in the loop handler.

### HLS (`download/m3u8.py`)

`_get_highest_quality_variant_url(..., max_px)` filters master-playlist variants by `min(stream_info.resolution) <= cap` (variants with no `resolution` are the unknown bucket), picks highest-area of the qualifying set, and raises `MediaFilteredError("max_resolution")` on the step-4 no-fit case. This runs inside `download_m3u8` within the `download_media` loop, so the skip routes through the loop's existing `except MediaFilteredError` handler (record + count). The cap must be threaded to this call; the plan resolves how `state`/cap reaches `_get_highest_quality_variant_url` (currently `(config, m3u8_url, cookies)`), consistent with how `estimate_stream_size_gate` already reaches the resolved filters.

### Recording / UX

- **Skip** (no rendition fits): `lastFilteredReason="max_resolution"` merged into `meta_info`, `Filtered [max_resolution]` printed (gated by `show_downloads and show_skipped_downloads`), `filtered_count += 1`.
- **Downscale** (a smaller rendition was chosen): a normal download — no skip, no filtered record beyond the chosen variant's own `download_id`. The stats line counts skips, not downscales (accepted in brainstorming).
- Loosening or removing the cap later re-admits a previously skipped item on the next run, like the other `filters.media` limits (nothing is marked downloaded).

## Testing

Per `docs/testing/TESTING_REQUIREMENTS.md`, the `filters.media` conventions, and the rules pack. Project stays mypy-zero; the two new-code modules reach 100%.

- **Enum + conversion** (`tests/config/unit/test_media_filters.py`): `Resolution` tiers, `_missing_` (case-fold, `2160p`->UHD), `.threshold` ladder; `resolution_threshold` table — each tier -> threshold, bare int/digit-string, `0`/`None`/`""` -> None, `off`/`none` (CLI), garbage (`"huge"`, `"1080i"`, negative) -> raises; the field stores the raw form unchanged (assert a `4k`-authored value survives a `dump_yaml -> load_yaml` round-trip as `4k`, mirroring the by_creator round-trip test).
- **Schema/CLI**: `filters.media.max_resolution` parses both forms; `by_creator` override tri-state; `--max-resolution` ephemeral override (tier, int, and `off`); resolved-bounds validation unaffected (no min>max for resolution).
- **Selection** (`tests/download/unit/test_mediafilters.py` + regular/HLS): `_select_best_variant(media, cap)` picks the highest fitting variant (downscale), skips when all known renditions exceed the cap, silent-passes when width/height are absent, and handles portrait vs landscape via `min(w,h)`; HLS variant selection over a real master playlist (`build_master_playlist` with multiple `RESOLUTION=` variants) picks the fitting one and raises `MediaFilteredError` when none fit; the loop handler / parse-time path records `max_resolution` and increments `filtered_count`. Real Media/variant factories and real playlist fixtures — no mocked selectors.

## Docs / CHANGELOG

- `docs/configuration/config_options.md`: document `max_resolution` under `filters.media` — accepted forms (tier ladder + pixel int), shorter-edge semantics with a portrait example, the downscale-else-skip behavior, `by_creator` inheritance, the `--max-resolution` flag (`off` to disable per run), and the round-trip note that the authored form is preserved.
- CHANGELOG under `## [Unreleased] / Added`, one physical line, minor-version framing (additive; default absent = zero behavior change).

## Out of scope

- Minimum-resolution filtering (only a max was requested; YAGNI).
- Re-encoding/downscaling a single high-res rendition into a smaller one — this feature only *selects among renditions Fansly already provides*; it never transcodes.
- A separate downscale tally in statistics (skips are counted; downscales are silent).
- Arbitrary aspect-ratio or bitrate-based quality caps.
