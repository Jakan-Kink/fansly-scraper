# Max-Resolution Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `filters.media.max_resolution` — cap rendition selection to the highest rendition whose shorter edge is at or below a resolution tier, downscaling when a smaller rendition exists and skipping (filtered) only when none fits.

**Architecture:** Extends the existing `filters.media` subsystem (shipped 0.15.0). A `Resolution` StrEnum ladder + `resolution_threshold()` helper convert a raw `int | str | None` config value to a shorter-edge pixel threshold on demand (raw form stored for config round-trip fidelity). The two existing "pick highest rendition" selectors gain the cap: `_select_best_variant` (regular files) and `_get_highest_quality_variant_url` (HLS). No-fit routes through the existing `filters.media` `MediaFilteredError` skip machinery. Spec: `docs/superpowers/specs/2026-07-09-max-resolution-design.md`.

**Tech Stack:** Python 3.12+, Pydantic v2 (StrEnum + field validators), the `m3u8` lib, pytest + respx + factory-boy, asyncpg EntityStore.

## Global Constraints

- **Git**: the OPERATOR controls the entire commit cycle. Subagents NEVER run `git add`/`git commit`/any state-modifying git command — a "Commit" step means writing the suggested message into the report file only.
- **Rules pack**: `.superpowers/sdd/agent-rules.md` binds every task (imports at file top; `tee-nocolor` FOREGROUND pytest, never backgrounded; NEVER a dotted `--cov=pkg.mod`; respx via `respx_fansly_api` with `side_effect`/`try-finally dump`/`url__startswith`/`route.called`; real `entity_store`/factories/TS fixtures; no `typing.cast`; no production `assert`; Markdown prose one physical line; NO plan-process references in code — no "Task N"/"per the plan" in source, comments, or docstrings).
- **Project is mypy-zero** (prod + tests) and must stay zero: `.venv/bin/mypy . 2>&1 | tail -2` is an extra gate on every task.
- **Stored value is raw `int | str | None`** — never normalize on load; a mid-run config save-back must write `4k` back as `4k`. The `resolution_threshold()` helper is the single validate+convert point; the field validator returns the value UNCHANGED.
- **Comparison is shorter-edge**: a rendition qualifies iff `min(width, height) <= threshold`. Renditions with unknown width/height never trigger a skip (silent-pass to today's behavior).
- **Downscale is a normal download** (no skip record); only the no-fit case records `lastFilteredReason="max_resolution"`, prints `Filtered [max_resolution]`, and increments `filtered_count`.
- Line anchors below are current at plan time; locate by the quoted code, not the number.

---

### Task 1: `Resolution` ladder + `resolution_threshold` + model plumbing

**Files:**

- Modify: `config/media_filters.py` (add `Resolution`, `resolution_threshold`; extend `MediaFilterOverride`, `MediaFilters`)
- Modify: `config/schema.py` (`MediaFiltersSection`: add field + validator + `to_runtime`)
- Test: `tests/config/unit/test_media_filters.py` (extend)

**Interfaces:**

- Consumes: existing `ConfigError`, `MEDIA_FILTERS_EXAMPLE`, `parse_size`/`parse_duration`, `MediaFilters.for_creator`/`is_active`/`ensure_valid`, `MediaFiltersSection.to_runtime`.
- Produces: `class Resolution(StrEnum)` with `.dimensions`, `.threshold`, `_missing_`; `resolution_threshold(value: int | str | None) -> int | None` (raises `ConfigError` on garbage); `MediaFilterOverride.max_resolution: int | str | None`; `MediaFilters.max_resolution: int | str | None` + `max_resolution_px` property; `MediaFiltersSection.max_resolution: int | str | None`. Later tasks read `resolve_media_filters(...).max_resolution_px` (an `int | None`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/config/unit/test_media_filters.py` (imports merged at file top: `from config.media_filters import Resolution, resolution_threshold`):

```python
class TestResolution:
    def test_threshold_ladder(self):
        assert Resolution.P240.threshold == 240
        assert Resolution.P1080.threshold == 1080
        assert Resolution.UHD.threshold == 2160
        assert Resolution.P1080.dimensions == (1920, 1080)

    def test_missing_case_and_alias(self):
        assert Resolution("1080P") is Resolution.P1080
        assert Resolution("4K") is Resolution.UHD
        assert Resolution("2160p") is Resolution.UHD

    def test_members_are_str(self):
        assert Resolution.P1080.lower() == "1080p"


class TestResolutionThreshold:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            (0, None),
            ("", None),
            ("1080p", 1080),
            ("4k", 2160),
            ("1080P", 1080),
            ("2160p", 2160),
            (1080, 1080),
            ("900", 900),
            (1600, 1600),
        ],
    )
    def test_accepted(self, value, expected):
        assert resolution_threshold(value) == expected

    @pytest.mark.parametrize("value", ["huge", "1080i", "-720", -5, "720x480", [1]])
    def test_garbage_raises(self, value):
        with pytest.raises(ConfigError, match="resolution"):
            resolution_threshold(value)


class TestMaxResolutionModel:
    def test_stored_raw_and_threshold_computed(self):
        f = MediaFilters(max_resolution="4k")
        assert f.max_resolution == "4k"  # raw form preserved
        assert f.max_resolution_px == 2160
        assert f.is_active is True

    def test_disabled_when_absent(self):
        assert MediaFilters().max_resolution_px is None
        assert MediaFilters(max_resolution=0).max_resolution_px is None

    def test_for_creator_merges_max_resolution(self):
        base = MediaFilters(
            max_resolution="4k",
            by_creator={"c1": MediaFilterOverride(max_resolution="1080p")},
        )
        assert base.for_creator("c1", None).max_resolution == "1080p"
        assert base.for_creator("c1", None).max_resolution_px == 1080
        assert base.for_creator("other", None).max_resolution == "4k"
```

Also append a schema + round-trip test (mirror the existing `TestByCreatorYamlRoundTrip` dump/load helpers in this file):

```python
class TestMaxResolutionSchema:
    def test_section_parses_both_forms(self):
        section = MediaFiltersSection.model_validate(
            {"max_resolution": "1080p", "by_creator": {"c1": {"max_resolution": 720}}}
        )
        rt = section.to_runtime()
        assert rt.max_resolution == "1080p"
        assert rt.max_resolution_px == 1080
        assert rt.for_creator("c1", None).max_resolution_px == 720

    def test_garbage_raises_validationerror(self):
        with pytest.raises(ValidationError, match="resolution"):
            MediaFiltersSection.model_validate({"max_resolution": "huge"})

    def test_authored_form_survives_round_trip(self):
        schema = ConfigSchema.model_validate(
            {"filters": {"media": {"max_resolution": "4k"}}}
        )
        text = _dump_schema_to_text(schema)  # reuse the file's existing dump helper
        assert "4k" in text
        assert "2160" not in text
        again = _load_schema_from_text(text)
        assert again.filters.media.max_resolution == "4k"
```

(Before writing, read this file's existing round-trip test to reuse its exact dump/load helper names — `_dump_schema_to_text`/`_load_schema_from_text` are placeholders for whatever `TestByCreatorYamlRoundTrip` already uses; match them.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/config/unit/test_media_filters.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -12`
Expected: `ImportError: cannot import name 'Resolution'`

- [ ] **Step 3: Implement in `config/media_filters.py`**

Add imports at file top: `from enum import StrEnum`. After the `MEDIA_FILTERS_EXAMPLE` block and before `MediaFilterOverride`, add:

```python
class Resolution(StrEnum):
    """Named resolution tiers; value is the conventional label."""

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
        """Shorter-edge pixel count (the 'p' number) for this tier."""
        return min(self.dimensions)

    @classmethod
    def _missing_(cls, value: object) -> "Resolution | None":
        if isinstance(value, str):
            v = value.lower()
            for member in cls:
                if member.value == v:
                    return member
            if v == "2160p":
                return cls.UHD
        return None


def resolution_threshold(value: int | str | None) -> int | None:
    """Shorter-edge pixel cap for a max_resolution value; None disables.

    Accepts a Resolution tier name (case-insensitive, '2160p' alias), a bare
    pixel integer or digit-string, or 0/None/empty (disabled). Raises on else.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise _shape_error("resolution", value)
    if isinstance(value, int):
        if value < 0:
            raise _shape_error("resolution", value)
        return value or None
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        try:
            return Resolution(text).threshold
        except ValueError:
            pass
        if text.isdigit():
            return int(text) or None
    raise _shape_error("resolution", value)
```

Note `_shape_error(kind, value)` already exists in this module (used by `parse_size`/`parse_duration`); reuse it so the message carries `MEDIA_FILTERS_EXAMPLE`. Its `kind` string appears in the error — `"resolution"` makes the `match="resolution"` tests pass. If `_shape_error`'s signature differs, adapt the call to match the existing one.

In `MediaFilterOverride` add the field + validator:

```python
    max_resolution: int | str | None = None

    @field_validator("max_resolution", mode="before")
    @classmethod
    def _validate_max_resolution(cls, v: Any) -> int | str | None:
        resolution_threshold(v)  # validate only; raises on garbage
        return v  # preserve the authored form
```

In `MediaFilters` add the field + property, extend `is_active` and `for_creator`:

```python
    max_resolution: int | str | None = None
```

```python
    @property
    def max_resolution_px(self) -> int | None:
        return resolution_threshold(self.max_resolution)
```

`is_active` — add the resolution check:

```python
    @property
    def is_active(self) -> bool:
        return (
            any(
                limit is not None
                for limit in (
                    self.file_size_min,
                    self.file_size_max,
                    self.duration_min,
                    self.duration_max,
                )
            )
            or self.max_resolution_px is not None
        )
```

`for_creator` — add `"max_resolution"` to the merged field tuple:

```python
        for name in (
            "file_size_min",
            "file_size_max",
            "duration_min",
            "duration_max",
            "max_resolution",
        ):
            if name in override.model_fields_set:
                update[name] = getattr(override, name)
```

(`ensure_valid` is unchanged — a single-ended cap has no min>max pair.)

In `config/schema.py` `MediaFiltersSection`, add the field + validator (mirror `_parse_sizes` placement) and thread it into `to_runtime`:

```python
    max_resolution: int | str | None = None
```

```python
    @field_validator("max_resolution", mode="before")
    @classmethod
    def _validate_max_resolution(cls, v: Any) -> int | str | None:
        try:
            resolution_threshold(v)
        except ConfigError as exc:
            raise ValueError(str(exc)) from exc
        return v
```

(Import `resolution_threshold` alongside the existing `from config.media_filters import ...` line. The `ConfigError -> ValueError` re-raise matches the existing `_parse_sizes`/`_parse_durations`/`_normalize_wall` pattern so Pydantic wraps it with a field path.)

In `to_runtime`, add `max_resolution=self.max_resolution` to the `MediaFilters(...)` construction.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/config/unit/test_media_filters.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -8`
Expected: all PASS

- [ ] **Step 5: Quality gate + hand commit to operator**

Run: `.venv/bin/ruff check config/media_filters.py config/schema.py tests/config/unit/test_media_filters.py && .venv/bin/ruff format --check config/media_filters.py config/schema.py tests/config/unit/test_media_filters.py && .venv/bin/mypy . 2>&1 | tail -2`
Expected: clean; mypy zero. Commit message: `feat(config): Resolution ladder + filters.media.max_resolution model`

---

### Task 2: `--max-resolution` CLI flag

**Files:**

- Modify: `config/args.py` (new argument near the other media-filter flags; `_apply_media_filter_args`)
- Modify: `tests/fixtures/config/config_fixtures.py` (`default_cli_args` gains `max_resolution=None`)
- Test: `tests/config/unit/test_media_filters.py` (extend)

**Interfaces:**

- Consumes: `resolution_threshold` (Task 1); existing `_apply_media_filter_args`, `config.media_filters` (a `MediaFilters`), the `_map` test helper.
- Produces: `args.max_resolution: str | None`; on use, `config.media_filters` replaced via `model_copy(update={"max_resolution": ...})` + `config._ephemeral_overrides.add("media_filters")`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/config/unit/test_media_filters.py` (CLI test class, using the existing `_map` helper):

```python
class TestMaxResolutionCli:
    def test_flag_sets_tier(self, validation_config):
        _map(["--max-resolution", "1080p"], validation_config)
        assert validation_config.media_filters.max_resolution == "1080p"
        assert validation_config.media_filters.max_resolution_px == 1080
        assert "media_filters" in validation_config._ephemeral_overrides

    def test_flag_accepts_int(self, validation_config):
        _map(["--max-resolution", "900"], validation_config)
        assert validation_config.media_filters.max_resolution_px == 900

    def test_off_disables_for_run(self, validation_config):
        validation_config.media_filters = MediaFilters(max_resolution="4k")
        _map(["--max-resolution", "off"], validation_config)
        assert validation_config.media_filters.max_resolution_px is None

    def test_garbage_raises(self, validation_config):
        with pytest.raises(ConfigError, match="resolution"):
            _map(["--max-resolution", "huge"], validation_config)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/config/unit/test_media_filters.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -8`
Expected: FAIL — `unrecognized arguments: --max-resolution`

- [ ] **Step 3: Implement in `config/args.py`**

Add the argument beside `--duration-max` (a plain value option, not in the download-modes group):

```python
    parser.add_argument(
        "--max-resolution",
        required=False,
        default=None,
        metavar="RES",
        dest="max_resolution",
        help="Cap download resolution to a tier (240p, 360p, 480p, 720p, "
        "1080p, 1440p, 4k) or a shorter-edge pixel integer. Renditions above "
        "the cap are downscaled to the highest that fits, or skipped if none "
        "fits. 'off' / 'none' / 0 disables for this run. Ephemeral override "
        "of filters.media.",
    )
```

In `_apply_media_filter_args`, after the existing size/duration handling and before the `ensure_valid`/ephemeral-mark, add:

```python
    if args.max_resolution is not None:
        raw = args.max_resolution.strip()
        if raw.lower() in ("off", "none", "0"):
            update["max_resolution"] = None
        else:
            resolution_threshold(raw)  # validate; raises ConfigError on garbage
            update["max_resolution"] = raw
```

(`update` is the existing dict the function builds for the `model_copy`; `resolution_threshold` is imported alongside `parse_size`/`parse_duration`. The existing empty-`update` short-circuit and `_ephemeral_overrides.add("media_filters")` already cover this new key.)

In `tests/fixtures/config/config_fixtures.py::default_cli_args`, add `max_resolution=None` beside the other media-filter dests.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/config/unit/test_media_filters.py tests/config/unit/test_args.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -8`
Expected: all PASS

- [ ] **Step 5: Quality gate + hand commit to operator**

Run: `.venv/bin/ruff check config/args.py tests/ && .venv/bin/mypy . 2>&1 | tail -2`
Expected: clean; mypy zero. Commit message: `feat(cli): --max-resolution ephemeral override`

---

### Task 3: Regular-file cap — `_select_best_variant` + parse-time skip + shared skip helper

**Files:**

- Modify: `media/media.py` (`_select_best_variant`, `parse_media_info`)
- Modify: `download/media.py` (`fetch_and_process_media` loop; extract shared `handle_filtered_skip`; refactor `download_media`'s existing `except MediaFilteredError`)
- Test: `tests/download/unit/test_mediafilters.py` and/or `tests/media/` (extend)

**Interfaces:**

- Consumes: `resolve_media_filters` (returns `MediaFilters | None`), `.max_resolution_px`, `record_filter_observation`, `MediaFilteredError`, `_print_filtered_skip` (all existing in `download/mediafilters.py` / `download/media.py` / `errors`).
- Produces: `_select_best_variant(media, max_px: int | None)` (new second param); `parse_media_info` raises `MediaFilteredError("max_resolution")` when no known rendition fits; `async handle_filtered_skip(config, state, media, reason)` shared helper (record + print + count).

- [ ] **Step 1: Write the failing tests**

Add to `tests/download/unit/test_mediafilters.py` (real `Media`/variant factories, no mocked selector). First the pure selector:

```python
from media.media import _select_best_variant


def _variant(media_id, w, h):
    from tests.fixtures.metadata import MediaFactory
    v = MediaFactory.build(id=media_id, mimetype="video/mp4", width=w, height=h)
    # give it a location so it's a valid candidate
    v.locations = [ ... ]  # mirror how sibling tests build a MediaLocation
    return v


class TestSelectBestVariantCap:
    def test_downscales_to_fitting_variant(self):
        from tests.fixtures.metadata import MediaFactory
        media = MediaFactory.build(id=snowflake_id(), mimetype="video/mp4")
        media.variants = [
            _variant(snowflake_id(), 1280, 720),
            _variant(snowflake_id(), 1920, 1080),
            _variant(snowflake_id(), 3840, 2160),
        ]
        best = _select_best_variant(media, 1080)
        assert min(best.width, best.height) == 1080  # picked the 1080p, not 4K

    def test_none_when_no_variant_fits(self):
        from tests.fixtures.metadata import MediaFactory
        media = MediaFactory.build(id=snowflake_id(), mimetype="video/mp4")
        media.variants = [_variant(snowflake_id(), 3840, 2160)]
        assert _select_best_variant(media, 1080) is None

    def test_no_cap_picks_highest(self):
        from tests.fixtures.metadata import MediaFactory
        media = MediaFactory.build(id=snowflake_id(), mimetype="video/mp4")
        media.variants = [
            _variant(snowflake_id(), 1920, 1080),
            _variant(snowflake_id(), 3840, 2160),
        ]
        best = _select_best_variant(media, None)
        assert min(best.width, best.height) == 2160

    def test_portrait_qualifies_by_shorter_edge(self):
        from tests.fixtures.metadata import MediaFactory
        media = MediaFactory.build(id=snowflake_id(), mimetype="video/mp4")
        media.variants = [_variant(snowflake_id(), 1080, 1920)]  # portrait 1080p
        best = _select_best_variant(media, 1080)
        assert best is not None  # short edge 1080 <= 1080
```

(Imports at file top; `_variant`'s location construction must mirror how existing tests in this repo build a `MediaLocation` on a `Media` — read a sibling test first and copy the exact shape.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/download/unit/test_mediafilters.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -8`
Expected: FAIL — `_select_best_variant() takes 1 positional argument but 2 were given`

- [ ] **Step 3: Implement**

In `media/media.py`, change `_select_best_variant` to take the cap and filter to fitting variants:

```python
def _select_best_variant(media: Media, max_px: int | None = None) -> Media | None:
    """Select the highest-resolution variant matching the parent's mimetype,
    capped at max_px on the shorter edge when set."""
    if not media.variants:
        return None

    parent_mime = simplify_mimetype(media.mimetype or "")

    matching = [
        v
        for v in media.variants
        if v.locations and simplify_mimetype(v.mimetype or "") == parent_mime
    ]

    if max_px is not None:
        fitting = [
            v
            for v in matching
            if v.width and v.height and min(v.width, v.height) <= max_px
        ]
        matching = fitting

    if not matching:
        return None

    return max(matching, key=lambda v: (v.width or 0) * (v.height or 0))
```

In `parse_media_info`, resolve the cap and make the skip decision over the default+variant pool. At the top (it already imports what it needs; add `from download.mediafilters import resolve_media_filters` and `from errors import MediaFilteredError` at file top — verify no import cycle; `media/media.py` importing `download.mediafilters` may cycle, so if mypy/import fails, resolve the cap in the CALLER (`fetch_and_process_media`) and pass `max_px` into `parse_media_info` as a parameter instead). Preferred shape — pass `max_px` in as a parameter to keep `media/media.py` free of a `download.*` import:

```python
async def parse_media_info(
    state: DownloadState,
    media_info: dict,
    post_id: str | None = None,
    *,
    interactive: bool = False,
    max_px: int | None = None,
) -> Media:
```

Replace the `best = _select_best_variant(media)` block and its default-fallback with a cap-aware decision:

```python
    best = _select_best_variant(media, max_px)
    default_known = media.width is not None and media.height is not None
    default_fits = default_known and (
        max_px is None or min(media.width, media.height) <= max_px
    )

    if best or (default_fits and media.locations):
        # Prefer the default only when it fits the cap AND is higher-res.
        use_variant = best is not None
        download_url = _build_m3u8_auth_url(best) if best else None
        default_url = _get_best_location_url(media) if default_fits else None
        if default_url and (not best or (media.height or 0) > (best.height or 0)):
            download_url = default_url
            use_variant = False
        media.download_url = download_url
        if use_variant and best:
            media.download_id = best.id
    elif max_px is not None and (default_known or _any_known_variant(media)):
        # Renditions exist but none fits the cap -> filtered skip.
        raise MediaFilteredError("max_resolution")
    else:
        media.download_url = _get_best_location_url(media)
```

Add a tiny local helper `_any_known_variant(media)` returning True if any variant has both width and height (used only to distinguish "known but too big" from "unknown metadata"). If the `elif` guard reads cleaner inline, inline it. The existing preview-fallback block below stays; it should also respect `max_px` when it re-selects a preview variant (`_select_best_variant(preview_media, max_px)`).

In `download/media.py` `fetch_and_process_media`, resolve the cap once per item and pass it, catching the skip:

```python
            for info in media_infos:
                media_dict = expect_dict(info, "media info")
                filters = resolve_media_filters(config, state)
                max_px = filters.max_resolution_px if filters else None
                try:
                    all_media.append(
                        await parse_media_info(
                            state,
                            media_dict,
                            post_id,
                            interactive=config.interactive,
                            max_px=max_px,
                        )
                    )
                except MediaFilteredError as e:
                    # need the media object to record against; fetch from cache
                    await handle_filtered_skip(config, state, media_dict, e.reason)
                except Exception:
                    print_error(...)  # unchanged
                    await input_enter_continue(config.interactive)
```

The recording needs the `Media` row. `process_media_info` already persisted it to the identity map before this loop, so `handle_filtered_skip` can `get_store().get_from_cache(Media, int(media_dict["media"]["id"]))` — verify the exact id path against `parse_media_info`'s own `default_media_dict = media_info["media"]` usage and mirror it. If threading the `Media` object is cleaner, have `parse_media_info` attach it to the exception (`MediaFilteredError` already carries attributes; add the media id) — pick whichever keeps the recording correct; the report must state which.

Extract the shared skip helper in `download/media.py` and refactor `download_media`'s existing `except MediaFilteredError` to call it:

```python
async def handle_filtered_skip(
    config: FanslyConfig, state: DownloadState, media: Media, reason: str
) -> None:
    """Record a filtered-skip observation, print, and count it."""
    await record_filter_observation(media, reason=reason)
    _print_filtered_skip(config, media, reason)
    state.filtered_count += 1
```

(Match `record_filter_observation`'s actual signature. The existing `download_media` loop handler that inlines record+print+count is replaced by a call to `handle_filtered_skip`, so both the parse-time and download-time skips share one implementation.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/download/unit/test_mediafilters.py tests/download/unit/test_media_download.py tests/download/unit/test_media_pipeline.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -10`
Expected: all PASS

- [ ] **Step 5: Quality gate + hand commit to operator**

Run: `.venv/bin/ruff check media/media.py download/media.py tests/ && .venv/bin/mypy . 2>&1 | tail -2`
Expected: clean; mypy zero. Commit message: `feat(download): cap regular-file rendition selection at max_resolution`

---

### Task 4: HLS cap — `_get_highest_quality_variant_url` + thread through tiers

**Files:**

- Modify: `download/m3u8.py` (`_get_highest_quality_variant_url`, `download_m3u8`, the three tier helpers)
- Modify: `download/media.py` (`_download_m3u8_file` resolves + passes `max_px`)
- Test: `tests/download/unit/test_mediafilters.py` / `tests/download/unit/test_m3u8.py` (extend)

**Interfaces:**

- Consumes: `resolve_media_filters(...).max_resolution_px`, `MediaFilteredError`; existing `max_bytes` threading pattern from the size feature (mirror it exactly).
- Produces: `_get_highest_quality_variant_url(config, m3u8_url, cookies, max_px: int | None = None)`; `download_m3u8(..., max_resolution: int | None = None)`; the tier helpers accept and forward `max_resolution`.

- [ ] **Step 1: Write the failing test**

Add to `tests/download/unit/test_mediafilters.py` using `build_master_playlist` with multiple variants (read the fixture's real signature first — the size feature's estimate-gate tests already use it):

```python
class TestHlsResolutionCap:
    def test_picks_fitting_variant(self, respx_fansly_api, mock_config):
        # master playlist with 720p, 1080p, 4K variants; cap 1080 -> 1080 URL
        ...  # mirror the estimate-gate test's master-playlist mounting
        url = _get_highest_quality_variant_url(mock_config, master_url, {}, 1080)
        assert "1080" in url  # or assert against the known variant URL

    def test_raises_when_none_fit(self, respx_fansly_api, mock_config):
        # master playlist with only a 4K variant; cap 1080 -> MediaFilteredError
        with pytest.raises(MediaFilteredError) as exc:
            _get_highest_quality_variant_url(mock_config, master_url, {}, 1080)
        assert exc.value.reason == "max_resolution"
```

(Fill in the master-playlist mounting by copying the estimate-gate HLS test in this same file — same `build_master_playlist`/route/`dump_fansly_calls` conventions, `route.called` asserted.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/download/unit/test_mediafilters.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -8`
Expected: FAIL — `_get_highest_quality_variant_url() takes 3 positional arguments but 4 were given`

- [ ] **Step 3: Implement in `download/m3u8.py`**

`_get_highest_quality_variant_url` — add `max_px` and cap the selection:

```python
def _get_highest_quality_variant_url(
    config: FanslyConfig,
    m3u8_url: str,
    cookies: dict[str, str],
    max_px: int | None = None,
) -> str:
    ...
    if len(master_playlist.playlists) > 0:
        candidates = master_playlist.playlists
        if max_px is not None:
            fitting = [
                p
                for p in candidates
                if p.stream_info.resolution
                and min(p.stream_info.resolution) <= max_px
            ]
            if not fitting and any(p.stream_info.resolution for p in candidates):
                raise MediaFilteredError("max_resolution")
            if fitting:
                candidates = fitting
        variant_info = max(
            candidates,
            key=lambda p: (p.stream_info.resolution or (0, 0))[0]
            * (p.stream_info.resolution or (0, 0))[1],
        )
        return variant_info.absolute_uri
    ...  # unchanged 1080p-guess fallback
```

Import `MediaFilteredError` at file top. Thread `max_resolution` through `download_m3u8` and the three tier callers (lines ~163, ~203, ~319 call `_get_highest_quality_variant_url`; `fetch_m3u8_segment_playlist` at ~163 also calls it and needs the param forwarded) exactly as `max_bytes` was threaded for the size feature — add `max_resolution: int | None = None` to `download_m3u8`, `_try_direct_download_pyav`, `_try_direct_download_ffmpeg`, `_try_segment_download`, and `fetch_m3u8_segment_playlist`, forwarding to each `_get_highest_quality_variant_url(...)` call. `MediaFilteredError` must NOT be swallowed by the tier fallbacks — mirror the existing `except MediaFilteredError: raise` guards the size feature added in these same functions (verify they're present and cover the new raise).

In `download/media.py` `_download_m3u8_file`, resolve and pass the cap (it already resolves filters for the size gate — reuse that `filters` binding):

```python
        filters = resolve_media_filters(config, state)
        max_px = filters.max_resolution_px if filters else None
        # ... existing max_bytes resolution ...
        result = await asyncio.to_thread(
            download_m3u8,
            config,
            media.download_url,
            temp_path,
            media.created_at_timestamp,
            max_bytes=max_bytes,
            max_resolution=max_px,
        )
```

The HLS skip raises inside `download_m3u8` within the `download_media` loop, so the loop's existing `except MediaFilteredError` handler (now `handle_filtered_skip`) records + counts it — no new handling needed here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/download/unit/test_mediafilters.py tests/download/unit/test_m3u8.py tests/download/integration/test_m3u8_integration.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -10`
Expected: all PASS

- [ ] **Step 5: Quality gate + hand commit to operator**

Run: `.venv/bin/ruff check download/ tests/ && .venv/bin/mypy . 2>&1 | tail -2`
Expected: clean; mypy zero. Commit message: `feat(download): cap HLS variant selection at max_resolution`

---

### Task 5: Docs, CHANGELOG, full suite

**Files:**

- Modify: `docs/configuration/config_options.md` (document `max_resolution` under `filters.media`)
- Modify: `CHANGELOG.md` (Unreleased / Added)
- Test: full suite + coverage

- [ ] **Step 1: Document `max_resolution` in `docs/configuration/config_options.md`**

Under the existing `filters.media` section, add a `max_resolution` subsection in the page's voice covering: accepted forms (tier ladder `240p/360p/480p/720p/1080p/1440p/4k`, or a shorter-edge pixel integer); shorter-edge semantics with a portrait example (`a 1080x1920 vertical video qualifies at 1080p`); downscale-else-skip behavior (picks the highest rendition at/below the cap; skips as a filtered item when none fits, recorded like size/duration skips); `by_creator` inheritance; the `--max-resolution` flag with `off`/`none`/`0` to disable per run; and that the authored form is preserved on save (`4k` stays `4k`). Keep prose one physical line per paragraph/bullet.

- [ ] **Step 2: Add the CHANGELOG entry**

Under `## [Unreleased]` / `### Added` (create the heading if the section was promoted at 0.15.0 release), one physical line:

```markdown
- **Max-resolution cap (`filters.media.max_resolution`).** Cap downloads to a resolution tier (`240p`…`1440p`, `4k`) or a shorter-edge pixel integer: the highest rendition at or below the cap is downloaded (real downscaling when Fansly offers a smaller variant), and an item is skipped as a filtered download only when no rendition fits. Compares on the shorter edge so portrait and landscape both work; supports per-creator `by_creator` overrides and a `--max-resolution` CLI override (`off` disables per run); the authored form (`4k`/`1080p`) is preserved in config.yaml.
```

- [ ] **Step 3: Full-suite verification (FOREGROUND)**

Run: `.venv/bin/python -m pytest tests/ -q --no-header -n8 -rs --timeout=60 --cov --cov-report=term-missing 2>&1 | tee-nocolor /tmp/pytest-logs/cov.log | tail -40`
Expected: zero failures. Skips only pre-existing (plyvel x5, cleanup-enforcement demo). Grep `config/media_filters|download/mediafilters|media/media|download/m3u8` in the log; the two config/mediafilters modules stay at 100%, and any NEW lines in `media/media.py`/`download/m3u8.py` are covered (add targeted tests for gaps, no pragmas). If `TestChatWsLoopE2E::test_records_pushed_messages_to_jsonl` is the ONLY failure, re-run it alone to confirm the known load-timing flake and report it.

- [ ] **Step 4: Quality gates**

Run: `.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy . 2>&1 | tail -2`
Expected: clean; mypy zero.

- [ ] **Step 5: Hand commit to operator**

Commit message: `docs: filters.media.max_resolution reference + changelog`

---

## Plan Self-Review Notes

- Spec coverage: Resolution ladder + raw-storage + threshold helper + model/schema (Task 1); CLI (Task 2); regular-file cap + parse-time skip + shared skip helper (Task 3); HLS cap + tier threading (Task 4); docs/CHANGELOG/suite (Task 5). Shorter-edge comparison, downscale-else-skip, unknown-metadata silent-pass, SINGLE/live exemption (inherited via `resolve_media_filters` returning None), and round-trip fidelity (raw `int|str|None` + validator-returns-unchanged) are all covered.
- Type/name consistency: `resolution_threshold` (int|str|None -> int|None), `max_resolution_px`, `_select_best_variant(media, max_px)`, `_get_highest_quality_variant_url(..., max_px)`, `download_m3u8(..., max_resolution=)`, `handle_filtered_skip(config, state, media, reason)` used consistently across tasks.
- Two verify-before-trusting points flagged inline: the `media/media.py` → `download.mediafilters` import-cycle risk (resolved by passing `max_px` as a parameter rather than importing) and the exact `Media`-id path for recording a parse-time skip (mirror `parse_media_info`'s own `media_info["media"]["id"]` usage). Both are called out in Task 3 for the implementer to confirm against live code.
- Known simplification: `_select_best_variant`'s cap makes it a pure `(media, cap)` function; the default-rendition decision stays in `parse_media_info` (the only place that sees both), matching the spec's centralized-decision design.
