# Media Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Min/max file-size and duration filters (issue #125) in a new top-level `filters:` config section (with `wall_filters` relocated to `filters.wall`), enforced by metadata/wire/streaming gates that record observations without ever marking media downloaded.

**Architecture:** `config/media_filters.py` owns parsing and the frozen `MediaFilters` runtime model (global + `by_creator` tri-state overrides, `for_creator` resolution). A new `FiltersSection` in the schema hosts `wall` (relocated) and `media`. `download/mediafilters.py` owns gate logic and `meta_info` observation recording; `download_media`'s loop applies the metadata gate and centrally handles `MediaFilteredError` raised by the wire gate (`_download_regular_file` content-length), the streaming estimate gate (bitrate x duration), the running-total backstop, and the completion check. Spec: `docs/superpowers/specs/2026-07-07-media-filters-design.md`.

**Tech Stack:** Python 3.12+, Pydantic v2 (`ByteSize` for size parsing), `pytimeparse2` (duration parsing — NEW dependency, added via `poetry add pytimeparse2`), argparse, m3u8 lib, pytest + respx + factory-boy, asyncpg EntityStore.

## Global Constraints

- **Git**: The OPERATOR controls the entire commit cycle. The executor NEVER runs `git add`/`git commit`/any modifying git command — no exceptions, no "just this once". "Commit" steps mean exactly one thing: put the suggested commit message in your report file; whether/when/how anything gets committed is the operator's decision alone.
- **No plan-process references in code**: source, docstrings, comments, and test docstrings must NEVER mention the task/phase/wave/plan/review that produced them ("Task 4 adds...", "per the plan", "added in the fix pass", "Step 3"). Code describes the system as it is; provenance lives in the report file and commit message. A comment is for invariants the code can't show — nothing else.
- **Rules pack**: `.superpowers/sdd/agent-rules.md` binds every task (hooks incl. imports-at-file-top and inline-fixture bans, `tee-nocolor` pytest capture, NEVER dotted `--cov`, respx `side_effect`+`dump_fansly_calls`+`url__startswith`, real `entity_store`/factories, no `typing.cast`, no production `assert`, PEP 604, 88-col, Markdown prose unwrapped).
- **Filter semantics (verbatim from spec)**: `0`/`null`/absent = disabled (normalized `None`) at the GLOBAL layer; in `by_creator` overrides, ABSENT = inherit, explicit `0`/`null` = disabled for that creator (tri-state via `model_fields_set`). Duration limits apply to videos only; `None` duration passes. SINGLE mode and the inactive short-circuit exempt EVERY gate. Live captures (`download/livestream.py`) untouched. Filters record `observedContentLength`/`estimatedContentLength`/`lastFilteredReason` into `meta_info` JSON and NEVER touch `is_downloaded`/`content_hash`/`local_filename`.
- **Relocation**: `options.wall_filters` (unreleased) moves to `filters.wall`; `FanslyConfig.wall_filters` attr and the `--wall-filters` CLI flag are unchanged.
- Line anchors (`~line N`) are plan-time; locate by the quoted code.

---

### Task 1: `config/media_filters.py` — parsers + frozen runtime model

**Files:**
- Create: `config/media_filters.py`
- Test: `tests/config/unit/test_media_filters.py`

**Interfaces:**
- Consumes: `errors.ConfigError`; `pydantic.ByteSize` (existing dep) for size parsing; `pytimeparse2.parse` for duration strings (NEW dependency).
- Produces: `parse_size(value: Any) -> int | None`; `parse_duration(value: Any) -> float | None`; `MEDIA_FILTERS_EXAMPLE: str`; `class MediaFilterOverride(BaseModel)` (four optional fields, `extra="forbid"`, tri-state via `model_fields_set`); `class MediaFilters(BaseModel, frozen=True)` with `file_size_min: int | None`, `file_size_max: int | None`, `duration_min: float | None`, `duration_max: float | None`, `by_creator: dict[str, MediaFilterOverride]`, property `is_active -> bool`, `size_verdict(size_bytes: int | None) -> str | None`, `duration_verdict(duration: float | None) -> str | None`, `for_creator(username: str | None, creator_id: int | None) -> "MediaFilters"`, `ensure_valid(context: str) -> None` (raises `ConfigError` on `min > max`).

- [ ] **Step 0: Add the duration-parsing dependency**

Run: `poetry add pytimeparse2` (NEVER edit pyproject.toml directly). Verify: `.venv/bin/python -c "from pytimeparse2 import parse; print(parse('1:30:00'), parse('2h30m'), parse('45m'))"` → `5400 9000 2700`. If pytimeparse2 returns unexpected values for any approved form, STOP and report NEEDS_CONTEXT with the observed outputs.

- [ ] **Step 1: Write the failing tests**

Create `tests/config/unit/test_media_filters.py`:

```python
"""Unit tests for config.media_filters parsing and resolution."""

import pytest

from config.media_filters import (
    MediaFilterOverride,
    MediaFilters,
    parse_duration,
    parse_size,
)
from errors import ConfigError


class TestParseSize:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, None),
            (None, None),
            ("", None),
            ("0", None),
            (102400, 102400),
            ("102400", 102400),
            ("100KB", 100_000),
            ("100 kb", 100_000),
            ("4GB", 4_000_000_000),
            ("1GiB", 1_073_741_824),
            ("2MiB", 2_097_152),
            ("1.5MB", 1_500_000),
        ],
    )
    def test_accepted_forms(self, value, expected):
        assert parse_size(value) == expected

    @pytest.mark.parametrize("value", ["huge", "10XB", "GB4", "-5MB", [1]])
    def test_garbage_raises_with_example(self, value):
        with pytest.raises(ConfigError, match="file_size"):
            parse_size(value)


class TestParseDuration:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, None),
            (None, None),
            ("", None),
            ("0", None),
            (90, 90.0),
            (90.5, 90.5),
            ("90", 90.0),
            ("1:30", 90.0),
            ("1:30:00", 5400.0),
            ("0:03", 3.0),
            ("45s", 45.0),
            ("45m", 2700.0),
            ("2h", 7200.0),
            ("2h30m", 9000.0),
        ],
    )
    def test_accepted_forms(self, value, expected):
        assert parse_duration(value) == expected

    @pytest.mark.parametrize("value", ["soon", "1:2:3:4", "-30", "10x", [1]])
    def test_garbage_raises_with_example(self, value):
        with pytest.raises(ConfigError, match="duration"):
            parse_duration(value)


class TestMediaFilters:
    def test_inactive_by_default(self):
        assert MediaFilters().is_active is False

    def test_verdicts(self):
        f = MediaFilters(
            file_size_min=100, file_size_max=1000, duration_min=3.0, duration_max=60.0
        )
        assert f.is_active is True
        assert f.size_verdict(None) is None
        assert f.size_verdict(500) is None
        assert f.size_verdict(50) == "file_size_min"
        assert f.size_verdict(5000) == "file_size_max"
        assert f.duration_verdict(None) is None
        assert f.duration_verdict(30.0) is None
        assert f.duration_verdict(1.0) == "duration_min"
        assert f.duration_verdict(600.0) == "duration_max"

    def test_ensure_valid_raises_on_min_over_max(self):
        with pytest.raises(ConfigError, match="file_size"):
            MediaFilters(file_size_min=10, file_size_max=5).ensure_valid("global")
        with pytest.raises(ConfigError, match="duration"):
            MediaFilters(duration_min=10.0, duration_max=5.0).ensure_valid("global")
        MediaFilters(file_size_min=5, file_size_max=10).ensure_valid("global")


class TestForCreator:
    def _filters(self):
        return MediaFilters(
            file_size_max=4_000_000_000,
            duration_max=7200.0,
            by_creator={
                "vod_streamer": MediaFilterOverride(duration_max=2700.0),
                "archived": MediaFilterOverride(file_size_max=None, duration_max=None),
                "noop": MediaFilterOverride(),
                "1234567890123": MediaFilterOverride(duration_min=5.0),
            },
        )

    def test_value_override_and_inherit(self):
        r = self._filters().for_creator("vod_streamer", 111)
        assert r.duration_max == 2700.0
        assert r.file_size_max == 4_000_000_000  # inherited

    def test_explicit_none_disables(self):
        r = self._filters().for_creator("archived", 111)
        assert r.file_size_max is None
        assert r.duration_max is None
        assert r.is_active is False

    def test_empty_override_inherits_all(self):
        r = self._filters().for_creator("noop", 111)
        assert r == self._filters().for_creator("unknown", 111)

    def test_id_key_lookup(self):
        r = self._filters().for_creator("someone", 1234567890123)
        assert r.duration_min == 5.0

    def test_unknown_creator_gets_globals(self):
        base = self._filters()
        r = base.for_creator("unknown", 999)
        assert r.file_size_max == base.file_size_max
        assert r.by_creator == {}
```

(`MediaFilterOverride(file_size_max=None)` explicitly-set-to-None is the "disable" tri-state arm; `MediaFilterOverride()` leaves the field out of `model_fields_set` — the "inherit" arm.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/config/unit/test_media_filters.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -10`
Expected: `ModuleNotFoundError: No module named 'config.media_filters'`

- [ ] **Step 3: Implement `config/media_filters.py`**

```python
"""Media-filter value parsing and the frozen runtime limits model.

parse_size/parse_duration normalize human-friendly config values to bytes
and seconds, delegating to pydantic.ByteSize and pytimeparse2. MediaFilters
holds the resolved limits plus per-creator overrides and answers
pass/violate verdicts for the download gates.
"""

from typing import Any

from pydantic import (
    BaseModel,
    ByteSize,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)
from pytimeparse2 import parse as _parse_timespan

from errors import ConfigError

MEDIA_FILTERS_EXAMPLE = """\
filters:
  media:
    file_size_min: 100KB
    file_size_max: 4GB
    duration_min: "0:03"
    duration_max: 2h
    by_creator:
      vod_streamer:
        duration_max: 45m"""

_BYTE_SIZE: TypeAdapter[ByteSize] = TypeAdapter(ByteSize)


def _shape_error(kind: str, value: Any) -> ConfigError:
    return ConfigError(
        f"media_filters: could not parse {kind} value {value!r}.\n"
        f"Expected shape:\n{MEDIA_FILTERS_EXAMPLE}"
    )


def parse_size(value: Any) -> int | None:
    """Normalize a size value to bytes; 0/None/empty disables (None)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise _shape_error("file_size", value)
    if isinstance(value, int | float):
        if value < 0:
            raise _shape_error("file_size", value)
        return int(value) or None
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        try:
            size = int(_BYTE_SIZE.validate_python(text))
        except ValidationError as e:
            raise _shape_error("file_size", value) from e
        if size < 0:
            raise _shape_error("file_size", value)
        return size or None
    raise _shape_error("file_size", value)


def parse_duration(value: Any) -> float | None:
    """Normalize a duration value to seconds; 0/None/empty disables (None)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise _shape_error("duration", value)
    if isinstance(value, int | float):
        if value < 0:
            raise _shape_error("duration", value)
        return float(value) or None
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        try:
            numeric = float(text)
        except ValueError:
            parsed = _parse_timespan(text)
            if parsed is None:
                raise _shape_error("duration", value)
            seconds = float(parsed)
            if seconds < 0:
                raise _shape_error("duration", value)
            return seconds or None
        if numeric < 0:
            raise _shape_error("duration", value)
        return numeric or None
    raise _shape_error("duration", value)


class MediaFilterOverride(BaseModel):
    """Per-creator partial override; absent fields inherit the globals."""

    model_config = ConfigDict(extra="forbid")

    file_size_min: int | None = None
    file_size_max: int | None = None
    duration_min: float | None = None
    duration_max: float | None = None

    @field_validator("file_size_min", "file_size_max", mode="before")
    @classmethod
    def _parse_sizes(cls, v: Any) -> int | None:
        return parse_size(v)

    @field_validator("duration_min", "duration_max", mode="before")
    @classmethod
    def _parse_durations(cls, v: Any) -> float | None:
        return parse_duration(v)


class MediaFilters(BaseModel):
    """Resolved media-filter limits; None means that limit is disabled."""

    model_config = ConfigDict(frozen=True)

    file_size_min: int | None = None
    file_size_max: int | None = None
    duration_min: float | None = None
    duration_max: float | None = None
    by_creator: dict[str, MediaFilterOverride] = Field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return any(
            limit is not None
            for limit in (
                self.file_size_min,
                self.file_size_max,
                self.duration_min,
                self.duration_max,
            )
        )

    def size_verdict(self, size_bytes: int | None) -> str | None:
        """Violated-limit name for a byte count, or None to pass."""
        if size_bytes is None:
            return None
        if self.file_size_min is not None and size_bytes < self.file_size_min:
            return "file_size_min"
        if self.file_size_max is not None and size_bytes > self.file_size_max:
            return "file_size_max"
        return None

    def duration_verdict(self, duration: float | None) -> str | None:
        """Violated-limit name for a duration in seconds, or None to pass."""
        if duration is None:
            return None
        if self.duration_min is not None and duration < self.duration_min:
            return "duration_min"
        if self.duration_max is not None and duration > self.duration_max:
            return "duration_max"
        return None

    def for_creator(
        self, username: str | None, creator_id: int | None
    ) -> "MediaFilters":
        """Resolve the per-creator override (username key, then ID key)."""
        override = None
        if username:
            override = self.by_creator.get(username.strip().lstrip("@").lower())
        if override is None and creator_id is not None:
            override = self.by_creator.get(str(creator_id))
        if override is None:
            return self.model_copy(update={"by_creator": {}})
        update: dict[str, Any] = {"by_creator": {}}
        for name in ("file_size_min", "file_size_max", "duration_min", "duration_max"):
            if name in override.model_fields_set:
                update[name] = getattr(override, name)
        return self.model_copy(update=update)

    def ensure_valid(self, context: str) -> None:
        """Raise ConfigError when a min exceeds its max."""
        if (
            self.file_size_min is not None
            and self.file_size_max is not None
            and self.file_size_min > self.file_size_max
        ):
            raise ConfigError(
                f"media_filters ({context}): file_size_min exceeds file_size_max."
            )
        if (
            self.duration_min is not None
            and self.duration_max is not None
            and self.duration_min > self.duration_max
        ):
            raise ConfigError(
                f"media_filters ({context}): duration_min exceeds duration_max."
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/config/unit/test_media_filters.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -10`
Expected: all PASS

- [ ] **Step 5: Quality gate + hand commit to operator**

Run: `.venv/bin/ruff check config/media_filters.py tests/config/unit/test_media_filters.py && .venv/bin/ruff format --check config/media_filters.py tests/config/unit/test_media_filters.py && .venv/bin/mypy config/media_filters.py`
Expected: clean. If mypy flags the `pytimeparse2` import as untyped (no stubs published), the sanctioned escape is `# type: ignore[import-untyped]  # pytimeparse2 ships no stubs` on that import line — a genuine library-stub gap, not a silenced error. Commit message: `feat(config): media-filter parsers (ByteSize/pytimeparse2) + frozen MediaFilters model (#125)` — note the commit includes `pyproject.toml`/`poetry.lock` from Step 0.

---

### Task 2: `FiltersSection` — wall relocation + media subsection + plumbing

**Files:**
- Modify: `config/schema.py` (remove `wall_filters` from `OptionsSection` at ~line 314 and its validator at ~line 332; new `MediaFiltersSection` + `FiltersSection` near the other section classes; `filters` field on `ConfigSchema` at ~line 832)
- Modify: `config/config.py` (`_populate_config_from_schema` wall_filters copy at ~line 156; new media_filters copy)
- Modify: `config/fanslyconfig.py` (`media_filters` attr near `wall_filters`; save-back block: retarget the wall `_maybe_set` from `base.options` to `base.filters`)
- Test: `tests/config/unit/test_media_filters.py` (extend) + update relocation-affected tests in `tests/config/unit/test_wall_filters.py`

**Interfaces:**
- Consumes: Task 1's `MediaFilterOverride`, `MediaFilters`, `parse_size`, `parse_duration`; existing `WallFilterSpec`/`normalize_wall_filters`.
- Produces: `ConfigSchema.filters: FiltersSection` with `wall: dict[str, WallFilterSpec]` and `media: MediaFiltersSection`; `MediaFiltersSection.to_runtime() -> MediaFilters`; `FanslyConfig.media_filters: MediaFilters` (default inactive); `options.wall_filters` now REJECTS (`extra="forbid"`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/config/unit/test_media_filters.py` (imports merged at file top: `from config.schema import ConfigSchema, FiltersSection, MediaFiltersSection`, `from config.fanslyconfig import FanslyConfig`, `from config.config import _populate_config_from_schema`, `from config.wall_filters import WallFilterSpec`, `from pydantic import ValidationError`):

```python
class TestFiltersSection:
    def test_wall_lives_under_filters(self):
        schema = ConfigSchema.model_validate(
            {
                "options": {"download_mode": "wall"},
                "filters": {"wall": {"c1": ["FULL VIDEOS"]}},
            }
        )
        assert schema.filters.wall == {"c1": WallFilterSpec(includes=["FULL VIDEOS"])}

    def test_wall_null_normalizes_to_empty(self):
        assert FiltersSection.model_validate({"wall": None}).wall == {}

    def test_options_wall_filters_rejected(self):
        with pytest.raises(ValidationError):
            ConfigSchema.model_validate({"options": {"wall_filters": {"c1": ["A"]}}})

    def test_media_subsection_parses_and_resolves(self):
        section = MediaFiltersSection.model_validate(
            {
                "file_size_max": "4GB",
                "duration_max": "2h",
                "by_creator": {"@VOD_Streamer": {"duration_max": "45m"}},
            }
        )
        runtime = section.to_runtime()
        assert runtime.file_size_max == 4_000_000_000
        assert runtime.duration_max == 7200.0
        assert runtime.for_creator("vod_streamer", None).duration_max == 2700.0

    def test_resolved_min_over_max_raises_at_load(self):
        with pytest.raises(ValidationError, match="duration"):
            MediaFiltersSection.model_validate(
                {
                    "duration_min": "1:00",
                    "by_creator": {"c1": {"duration_max": "0:30"}},
                }
            )

    def test_defaults_are_inactive(self):
        assert MediaFiltersSection().to_runtime().is_active is False


class TestFanslyConfigMediaFilters:
    def test_populate_from_schema(self):
        schema = ConfigSchema.model_validate(
            {"filters": {"media": {"file_size_max": "1MB"}}}
        )
        config = FanslyConfig(program_version="0.14.5-test")
        _populate_config_from_schema(config, schema)
        assert config.media_filters.file_size_max == 1_000_000

    def test_wall_populates_from_new_location(self):
        schema = ConfigSchema.model_validate(
            {
                "options": {"download_mode": "wall"},
                "filters": {"wall": {"c1": ["A"]}},
            }
        )
        config = FanslyConfig(program_version="0.14.5-test")
        _populate_config_from_schema(config, schema)
        assert config.wall_filters == {"c1": WallFilterSpec(includes=["A"])}
        assert config.wall_filters["c1"] is not schema.filters.wall["c1"]  # deep copy
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/config/unit/test_media_filters.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -10`
Expected: `ImportError: cannot import name 'FiltersSection'`

- [ ] **Step 3: Implement**

In `config/schema.py`:
1. Add to imports: `from config.media_filters import MediaFilterOverride, MediaFilters, parse_duration, parse_size`.
2. DELETE from `OptionsSection`: the `wall_filters` field (~line 311-314, including its comment) and the `_normalize_wall_filters` validator (~line 332-336). Keep the `config.wall_filters` import line — it is reused below.
3. Add two section classes directly above `class ConfigSchema` (~line 805):

```python
class MediaFiltersSection(_BaseSection):
    """Min/max file-size and duration limits (global + per-creator)."""

    model_config = ConfigDict(extra="forbid")

    file_size_min: int | None = None
    file_size_max: int | None = None
    duration_min: float | None = None
    duration_max: float | None = None
    by_creator: dict[str, MediaFilterOverride] = Field(default_factory=dict)

    @field_validator("file_size_min", "file_size_max", mode="before")
    @classmethod
    def _parse_sizes(cls, v: Any) -> int | None:
        return parse_size(v)

    @field_validator("duration_min", "duration_max", mode="before")
    @classmethod
    def _parse_durations(cls, v: Any) -> float | None:
        return parse_duration(v)

    @field_validator("by_creator", mode="before")
    @classmethod
    def _sanitize_creator_keys(cls, v: Any) -> Any:
        if isinstance(v, dict):
            return {str(k).strip().lstrip("@").lower(): val for k, val in v.items()}
        return v

    @model_validator(mode="after")
    def _validate_resolved_bounds(self) -> "MediaFiltersSection":
        runtime = self.to_runtime()
        runtime.ensure_valid("global")
        for creator in runtime.by_creator:
            runtime.for_creator(creator, None).ensure_valid(f"by_creator.{creator}")
        return self

    def to_runtime(self) -> MediaFilters:
        """Build the frozen runtime model from this section."""
        return MediaFilters(
            file_size_min=self.file_size_min,
            file_size_max=self.file_size_max,
            duration_min=self.duration_min,
            duration_max=self.duration_max,
            by_creator=dict(self.by_creator),
        )


class FiltersSection(_BaseSection):
    """Content filters: per-creator wall selection and media size/duration limits."""

    model_config = ConfigDict(extra="forbid")

    wall: dict[str, WallFilterSpec] = Field(default_factory=dict)
    media: MediaFiltersSection = Field(default_factory=MediaFiltersSection)

    @field_validator("wall", mode="before")
    @classmethod
    def _normalize_wall(cls, v: Any) -> dict[str, WallFilterSpec]:
        return normalize_wall_filters(v)
```

4. On `ConfigSchema` (with the other section fields, ~line 832): `filters: FiltersSection = Field(default_factory=FiltersSection)`.

In `config/config.py` `_populate_config_from_schema`: change the wall_filters source (~line 156) from `opts.wall_filters` to `schema.filters.wall` (KEEP the `model_copy(deep=True)` comprehension exactly as-is), and add after it:

```python
    config.media_filters = schema.filters.media.to_runtime()
```

In `config/fanslyconfig.py`:
1. Import: `from config.media_filters import MediaFilters`.
2. Field near `wall_filters`: `media_filters: MediaFilters = field(default_factory=MediaFilters)`.
3. Save-back: move `_maybe_set(base.options, "wall_filters", config.wall_filters)` to `_maybe_set(base.filters, "wall", config.wall_filters)`. Do NOT add a save-back for `media` — the runtime model is frozen and CLI overrides are ephemeral, so the schema's loaded value is already correct; add this one-line comment there: `# filters.media is immutable at runtime (CLI overrides are ephemeral) — no write-back.`

- [ ] **Step 4: Run tests + relocation fallout**

Run: `.venv/bin/python -m pytest tests/config/unit/test_media_filters.py tests/config/unit/test_wall_filters.py tests/config/unit/test_schema.py tests/config/unit/test_loader.py tests/config/unit/test_fanslyconfig.py tests/config/unit/test_validation.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -15`
Expected: failures ONLY in `test_wall_filters.py` tests that construct `OptionsSection(wall_filters=...)` / `{"options": {"wall_filters": ...}}` shapes. Update those tests to the `filters.wall` path (e.g. `ConfigSchema.model_validate({"options": {"download_mode": "wall"}, "filters": {"wall": {...}}})`, `FiltersSection` instead of `OptionsSection` where relevant — including `TestMidRunSaveDoesNotMutateSchema`, which must now assert the saved YAML keeps both creators under `filters: wall:`). Re-run until all green. Every other suite in the list must pass unmodified.

- [ ] **Step 5: Quality gate + hand commit to operator**

Run: `.venv/bin/ruff check config/ tests/config/unit/test_media_filters.py tests/config/unit/test_wall_filters.py && .venv/bin/mypy config/schema.py config/config.py config/fanslyconfig.py`
Expected: clean (pre-existing mypy debt aside). Commit message: `feat(config): top-level filters section — wall relocation + media limits (#125)`

---

### Task 3: CLI flags

**Files:**
- Modify: `config/args.py` (four arguments near the other value options at ~line 333+; `_apply_media_filter_args` beside `_apply_cli_wall_filters`; call it in `map_args_to_config` right after `_apply_cli_wall_filters(args, config)` at ~line 1067)
- Modify: `tests/fixtures/config/config_fixtures.py` (`default_cli_args` gains the four new dests = `None`, mirroring `download_mode_wall_filters`)
- Test: `tests/config/unit/test_media_filters.py` (extend)

**Interfaces:**
- Consumes: `parse_size`/`parse_duration`/`MediaFilters` (Task 1); `config.media_filters` (Task 2); the `_map(argv, validation_config)` helper already defined in `tests/config/unit/test_wall_filters.py` (import it or replicate its 4 lines — check that file first and reuse its exact pattern).
- Produces: `args.file_size_min/file_size_max/duration_min/duration_max: str | None`; on any set flag, `config.media_filters` replaced via `model_copy(update=...)` + `config._ephemeral_overrides.add("media_filters")`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/config/unit/test_media_filters.py` (reuse/import the wall-filters `_map` helper pattern — file-top imports only):

```python
class TestMediaFilterCli:
    def test_flags_override_globals_ephemerally(self, validation_config):
        _map(["--file-size-max", "2GB", "--duration-min", "0:05"], validation_config)
        assert validation_config.media_filters.file_size_max == 2_000_000_000
        assert validation_config.media_filters.duration_min == 5.0
        assert "media_filters" in validation_config._ephemeral_overrides

    def test_zero_disables_for_run(self, validation_config):
        validation_config.media_filters = MediaFilters(duration_max=100.0)
        _map(["--duration-max", "0"], validation_config)
        assert validation_config.media_filters.duration_max is None

    def test_by_creator_survives_cli_global_override(self, validation_config):
        validation_config.media_filters = MediaFilters(
            duration_max=100.0,
            by_creator={"c1": MediaFilterOverride(duration_max=50.0)},
        )
        _map(["--duration-max", "200"], validation_config)
        resolved = validation_config.media_filters.for_creator("c1", None)
        assert resolved.duration_max == 50.0
        assert validation_config.media_filters.duration_max == 200.0

    def test_garbage_flag_value_raises(self, validation_config):
        with pytest.raises(ConfigError, match="file_size"):
            _map(["--file-size-max", "huge"], validation_config)

    def test_absent_flags_leave_config_untouched(self, validation_config):
        before = validation_config.media_filters
        _map(["-u", "validuser1"], validation_config)
        assert validation_config.media_filters == before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/config/unit/test_media_filters.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -10`
Expected: `unrecognized arguments: --file-size-max`

- [ ] **Step 3: Implement in `config/args.py`**

1. Imports: add `from config.media_filters import parse_duration, parse_size`.
2. Four plain arguments (NOT in the `download_modes` group), placed with the other value options:

```python
    parser.add_argument(
        "--file-size-min",
        required=False,
        default=None,
        metavar="SIZE",
        dest="file_size_min",
        help="Skip media smaller than SIZE (e.g. 100KB, 5MB, raw bytes). "
        "0 disables the limit for this run. Ephemeral override of "
        "filters.media in config.yaml.",
    )
    parser.add_argument(
        "--file-size-max",
        required=False,
        default=None,
        metavar="SIZE",
        dest="file_size_max",
        help="Skip media larger than SIZE (e.g. 4GB). 0 disables for this run.",
    )
    parser.add_argument(
        "--duration-min",
        required=False,
        default=None,
        metavar="DURATION",
        dest="duration_min",
        help="Skip videos shorter than DURATION (e.g. 3, 0:03, 45s). "
        "0 disables for this run.",
    )
    parser.add_argument(
        "--duration-max",
        required=False,
        default=None,
        metavar="DURATION",
        dest="duration_max",
        help="Skip videos longer than DURATION (e.g. 5400, 1:30:00, 2h). "
        "0 disables for this run.",
    )
```

3. Beside `_apply_cli_wall_filters`:

```python
def _apply_media_filter_args(args: argparse.Namespace, config: FanslyConfig) -> None:
    """Apply --file-size-*/--duration-* as ephemeral global-layer overrides."""
    update: dict[str, int | float | None] = {}
    if args.file_size_min is not None:
        update["file_size_min"] = parse_size(args.file_size_min)
    if args.file_size_max is not None:
        update["file_size_max"] = parse_size(args.file_size_max)
    if args.duration_min is not None:
        update["duration_min"] = parse_duration(args.duration_min)
    if args.duration_max is not None:
        update["duration_max"] = parse_duration(args.duration_max)
    if not update:
        return
    config.media_filters = config.media_filters.model_copy(update=update)
    config.media_filters.ensure_valid("CLI")
    config._ephemeral_overrides.add("media_filters")
```

4. In `map_args_to_config`, directly after `_apply_cli_wall_filters(args, config)`: `_apply_media_filter_args(args, config)`.
5. In `tests/fixtures/config/config_fixtures.py::default_cli_args`, add the four dests as `None` next to `download_mode_wall_filters=None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/config/unit/test_media_filters.py tests/config/unit/test_args.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -10`
Expected: all PASS

- [ ] **Step 5: Quality gate + hand commit to operator**

Run: `.venv/bin/ruff check config/args.py tests/ && .venv/bin/mypy config/args.py`
Expected: clean. Commit message: `feat(cli): --file-size-min/max and --duration-min/max ephemeral overrides (#125)`

---

### Task 4: `MediaFilteredError`, metadata gate, recording, loop wiring, stats

**Files:**
- Create: `download/mediafilters.py`
- Modify: `errors/__init__.py` (new exception class beside `MediaError` at ~line 108)
- Modify: `download/downloadstate.py` (`filtered_count: int = 0` beside `current_batch_duplicates`)
- Modify: `download/globalstate.py` (aggregate `filtered_count` the same way `duplicate_count` aggregates — read the file and mirror)
- Modify: `download/statistics.py` (`print_statistics_helper` gains `\n  Filtered by media_filters: {state.filtered_count}` after the Duplicates line, rendered only when `state.filtered_count` — mirror the footer-conditional style)
- Modify: `download/media.py` (`download_media` loop at ~line 490: gate after the `is_preview` skip; `except MediaFilteredError` handler in the per-item try)
- Test: `tests/download/unit/test_mediafilters.py`

**Interfaces:**
- Consumes: `MediaFilters.for_creator/verdicts` (Task 1); `config.media_filters` (Task 2); `Media.meta_info`/`duration`/`mimetype`, `get_store()`.
- Produces: `errors.MediaFilteredError(RuntimeError)` with `__init__(self, reason: str, *, observed: int | None = None, estimated: int | None = None)` and those attributes; `resolve_media_filters(config, state) -> MediaFilters | None` (None = inactive or SINGLE — the every-gate exemption helper); `check_media_filters(config, state, media) -> str | None`; `async record_filter_observation(media, *, reason: str, observed: int | None = None, estimated: int | None = None) -> None`; `DownloadState.filtered_count`; loop behavior: metadata-gate skip and `MediaFilteredError` both record + print `Filtered [{reason}]: ... -> skipped` (gated by `config.show_downloads and config.show_skipped_downloads`) + `state.filtered_count += 1` + `continue`.

- [ ] **Step 1: Write the failing tests**

Create `tests/download/unit/test_mediafilters.py`:

```python
"""Unit tests for download.mediafilters gates and recording."""

import json

import pytest

from config.media_filters import MediaFilterOverride, MediaFilters
from download.downloadstate import DownloadState
from download.mediafilters import (
    check_media_filters,
    record_filter_observation,
    resolve_media_filters,
)
from download.types import DownloadType
from errors import MediaFilteredError
from metadata.models import Media
from tests.fixtures.metadata import AccountFactory, MediaFactory
from tests.fixtures.utils.test_isolation import snowflake_id


def _state(download_type=DownloadType.TIMELINE):
    state = DownloadState(creator_name="creator1")
    state.creator_id = snowflake_id()
    state.download_type = download_type
    return state


class TestResolveMediaFilters:
    def test_inactive_returns_none(self, mock_config):
        mock_config.media_filters = MediaFilters()
        assert resolve_media_filters(mock_config, _state()) is None

    def test_single_mode_exempt(self, mock_config):
        mock_config.media_filters = MediaFilters(duration_max=10.0)
        assert resolve_media_filters(mock_config, _state(DownloadType.SINGLE)) is None

    def test_per_creator_resolution(self, mock_config):
        mock_config.media_filters = MediaFilters(
            duration_max=100.0,
            by_creator={"creator1": MediaFilterOverride(duration_max=50.0)},
        )
        resolved = resolve_media_filters(mock_config, _state())
        assert resolved is not None and resolved.duration_max == 50.0


class TestMetadataGate:
    def test_duration_bounds_videos_only(self, mock_config):
        mock_config.media_filters = MediaFilters(duration_min=5.0, duration_max=100.0)
        state = _state()
        long_video = MediaFactory.build(
            id=snowflake_id(), mimetype="video/mp4", duration=500.0
        )
        image = MediaFactory.build(
            id=snowflake_id(), mimetype="image/jpeg", duration=None
        )
        unknown = MediaFactory.build(
            id=snowflake_id(), mimetype="video/mp4", duration=None
        )
        assert check_media_filters(mock_config, state, long_video) == "duration_max"
        assert check_media_filters(mock_config, state, image) is None
        assert check_media_filters(mock_config, state, unknown) is None

    def test_recorded_size_evidence_observed_beats_estimated(self, mock_config):
        mock_config.media_filters = MediaFilters(file_size_max=1000)
        state = _state()
        media = MediaFactory.build(
            id=snowflake_id(),
            mimetype="video/mp4",
            meta_info=json.dumps(
                {"estimatedContentLength": 5000, "observedContentLength": 500}
            ),
        )
        assert check_media_filters(mock_config, state, media) is None
        media.meta_info = json.dumps({"estimatedContentLength": 5000})
        assert check_media_filters(mock_config, state, media) == "file_size_max"

    def test_no_evidence_defers(self, mock_config):
        mock_config.media_filters = MediaFilters(file_size_max=1000)
        assert (
            check_media_filters(
                mock_config,
                _state(),
                MediaFactory.build(id=snowflake_id(), mimetype="video/mp4"),
            )
            is None
        )


@pytest.mark.asyncio
class TestRecording:
    async def test_merges_without_marking(self, entity_store):
        account = AccountFactory(id=snowflake_id(), username="creator1")
        await entity_store.save(account)
        media = Media(
            id=snowflake_id(),
            accountId=account.id,
            mimetype="video/mp4",
            meta_info=json.dumps({"duration": 30}),
        )
        await entity_store.save(media)
        await record_filter_observation(
            media, reason="file_size_max", observed=6_000_000_000
        )
        payload = json.loads(media.meta_info)
        assert payload["lastFilteredReason"] == "file_size_max"
        assert payload["observedContentLength"] == 6_000_000_000
        assert payload["duration"] == 30  # existing keys preserved
        assert media.is_downloaded is False
        assert media.content_hash is None

    async def test_malformed_meta_info_skips_merge(self, entity_store):
        account = AccountFactory(id=snowflake_id(), username="creator1")
        await entity_store.save(account)
        media = Media(
            id=snowflake_id(),
            accountId=account.id,
            mimetype="video/mp4",
            meta_info="not-json{",
        )
        await entity_store.save(media)
        await record_filter_observation(media, reason="duration_max")
        assert media.meta_info == "not-json{"  # untouched, no crash


class TestMediaFilteredError:
    def test_carries_attrs(self):
        e = MediaFilteredError("file_size_max", observed=123)
        assert e.reason == "file_size_max"
        assert e.observed == 123
        assert e.estimated is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/download/unit/test_mediafilters.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -10`
Expected: `ModuleNotFoundError: No module named 'download.mediafilters'`

- [ ] **Step 3: Implement**

`errors/__init__.py`, beside `MediaError`:

```python
class MediaFilteredError(RuntimeError):
    """Control-flow signal: media skipped by filters.media limits (not a failure)."""

    def __init__(
        self,
        reason: str,
        *,
        observed: int | None = None,
        estimated: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.observed = observed
        self.estimated = estimated
```

(Export it wherever the module's `__all__`/re-export convention requires — mirror `MediaError`.)

Create `download/mediafilters.py`:

```python
"""Media-filter gates (size/duration) and meta_info observation recording."""

import json

from config import FanslyConfig
from config.media_filters import MediaFilters
from download.downloadstate import DownloadState
from download.types import DownloadType
from metadata.models import Media, get_store
from textio import print_debug


def resolve_media_filters(
    config: FanslyConfig, state: DownloadState
) -> MediaFilters | None:
    """Per-creator resolved limits, or None when filtering is exempt/inactive."""
    if state.download_type == DownloadType.SINGLE:
        return None
    if not config.media_filters.is_active and not config.media_filters.by_creator:
        return None
    resolved = config.media_filters.for_creator(state.creator_name, state.creator_id)
    return resolved if resolved.is_active else None


def _size_evidence(media: Media) -> int | None:
    """Best size evidence from meta_info: observed wins over estimated."""
    if not media.meta_info:
        return None
    try:
        payload = json.loads(media.meta_info)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("observedContentLength", "estimatedContentLength"):
        value = payload.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return int(value)
    return None


def check_media_filters(
    config: FanslyConfig, state: DownloadState, media: Media
) -> str | None:
    """Metadata gate: violated-limit name, or None to proceed."""
    filters = resolve_media_filters(config, state)
    if filters is None:
        return None
    if (media.mimetype or "").startswith("video/"):
        verdict = filters.duration_verdict(media.duration)
        if verdict:
            return verdict
    return filters.size_verdict(_size_evidence(media))


async def record_filter_observation(
    media: Media,
    *,
    reason: str,
    observed: int | None = None,
    estimated: int | None = None,
) -> None:
    """Merge filter observations into meta_info; never touches download state."""
    raw = media.meta_info
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        print_debug(
            f"media_filters: meta_info for media {media.id} is not valid JSON; "
            "skipping observation merge."
        )
        return
    if not isinstance(payload, dict):
        print_debug(
            f"media_filters: meta_info for media {media.id} is not a JSON object; "
            "skipping observation merge."
        )
        return
    payload["lastFilteredReason"] = reason
    if observed is not None:
        payload["observedContentLength"] = observed
    if estimated is not None:
        payload["estimatedContentLength"] = estimated
    media.meta_info = json.dumps(payload)
    await get_store().save(media)
```

`download/downloadstate.py`: add `filtered_count: int = 0` next to `current_batch_duplicates`. `download/globalstate.py`: aggregate it exactly like `duplicate_count` (read the aggregation function and mirror). `download/statistics.py::print_statistics_helper`: after the Duplicates line add `f"\n  Filtered by media_filters: {state.filtered_count}" if state.filtered_count else ""` (build the string before the f-string like the footer pattern, or use a conditional segment — match file style).

`download/media.py` `download_media` loop: file-top imports `from download.mediafilters import check_media_filters, record_filter_observation` and `from errors import MediaFilteredError` (merge into existing import blocks). After the `is_preview` skip (~line 490):

```python
                filter_reason = check_media_filters(config, state, media)
                if filter_reason:
                    await record_filter_observation(media, reason=filter_reason)
                    if config.show_downloads and config.show_skipped_downloads:
                        print_info(
                            f"Filtered [{filter_reason}]: "
                            f"{media.mimetype.split('/')[-2]} "
                            f"'{media.get_file_name()}' -> skipped"
                        )
                    state.filtered_count += 1
                    continue
```

And in the same per-item `try`, add an except clause BEFORE the generic handlers (alongside the existing narrow excepts — read the loop's tail to place it where `DuplicateCountError` re-raises stay intact):

```python
            except MediaFilteredError as e:
                await record_filter_observation(
                    media, reason=e.reason, observed=e.observed, estimated=e.estimated
                )
                if config.show_downloads and config.show_skipped_downloads:
                    print_info(
                        f"Filtered [{e.reason}]: '{media.get_file_name()}' -> skipped"
                    )
                state.filtered_count += 1
                continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/download/unit/test_mediafilters.py tests/download/unit/test_media_download.py tests/download/unit/test_statistics.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -10`
Expected: all PASS

- [ ] **Step 5: Quality gate + hand commit to operator**

Run: `.venv/bin/ruff check errors/ download/ tests/download/unit/test_mediafilters.py && .venv/bin/mypy download/mediafilters.py download/media.py download/statistics.py`
Expected: clean. Commit message: `feat(download): media-filter metadata gate, recording, filtered stats (#125)`

---

### Task 5: Wire gate — regular files

**Files:**
- Modify: `download/media.py` (`_download_regular_file` at ~line 319 gains `state`; content-length check after status 200; update its call site in the loop)
- Test: `tests/download/unit/test_mediafilters.py` (extend)

**Interfaces:**
- Consumes: `resolve_media_filters` (Task 4), `MediaFilteredError`.
- Produces: `_download_regular_file(config, state, media, file_save_path)`; violating `content-length` → response closed unstreamed, `MediaFilteredError(reason, observed=size)` (recording happens in the loop's handler from Task 4); missing/zero header passes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/download/unit/test_mediafilters.py` (file-top imports: `import httpx`, `import respx`, `from download.media import _download_regular_file`, `from tests.fixtures.api import dump_fansly_calls`):

```python
@pytest.mark.asyncio
class TestWireGate:
    async def _media(self, entity_store, url):
        account = AccountFactory(id=snowflake_id(), username="creator1")
        await entity_store.save(account)
        media = Media(
            id=snowflake_id(), accountId=account.id, mimetype="video/mp4"
        )
        media.download_url = url
        media.file_extension = "mp4"
        await entity_store.save(media)
        return media

    async def test_oversized_header_aborts_before_body(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        mock_config.media_filters = MediaFilters(file_size_max=1000)
        state = _state()
        url = "https://cdn.example.com/big.mp4?Key-Pair-Id=x"
        media = await self._media(entity_store, url)
        route = respx.get(url__startswith="https://cdn.example.com/big.mp4").mock(
            side_effect=[
                httpx.Response(
                    200, headers={"content-length": "5000"}, content=b"x" * 5000
                )
            ]
        )
        target = tmp_path / "big.mp4"
        try:
            with pytest.raises(MediaFilteredError) as exc_info:
                await _download_regular_file(mock_config, state, media, target)
        finally:
            dump_fansly_calls(route.calls, "test_oversized_header_aborts")
        assert exc_info.value.reason == "file_size_max"
        assert exc_info.value.observed == 5000
        assert not target.exists()
        assert not list(tmp_path.glob(".*part*"))  # no temp file created

    async def test_missing_header_passes(
        self, respx_fansly_api, mock_config, entity_store, tmp_path
    ):
        mock_config.media_filters = MediaFilters(file_size_max=1000)
        state = _state()
        url = "https://cdn.example.com/nolen.mp4?Key-Pair-Id=x"
        media = await self._media(entity_store, url)
        route = respx.get(url__startswith="https://cdn.example.com/nolen.mp4").mock(
            side_effect=[httpx.Response(200, content=b"x" * 5000)]
        )
        target = tmp_path / "nolen.mp4"
        try:
            await _download_regular_file(mock_config, state, media, target)
        finally:
            dump_fansly_calls(route.calls, "test_missing_header_passes")
        assert target.exists() and target.stat().st_size == 5000
```

(Before finalizing: check how existing `_download_regular_file`/CDN tests in `tests/download/unit/test_media_download.py` register CDN routes and construct media — mirror their conventions for the URL shape and any required media fields; keep the assertions above intact. Note whether httpx auto-sets `content-length` from `content=` in the missing-header case — if it does, use `stream=` or a transport-level response so the header is genuinely absent, mirroring how sibling tests build header-controlled responses.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/download/unit/test_mediafilters.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -10`
Expected: FAIL — `_download_regular_file` takes no `state` / no `MediaFilteredError` raised.

- [ ] **Step 3: Implement**

In `download/media.py`:
1. `_download_regular_file(config: FanslyConfig, state: DownloadState, media: Media, file_save_path: Path) -> None` — add the `state` parameter; update the single call site in `download_media`'s loop accordingly.
2. After `file_size = int(response.headers.get("content-length", 0))` (~line 335) and BEFORE the progress/tempfile block, insert:

```python
            filters = resolve_media_filters(config, state)
            if filters is not None and file_size > 0:
                reason = filters.size_verdict(file_size)
                if reason:
                    raise MediaFilteredError(reason, observed=file_size)
```

3. File-top import merge: `resolve_media_filters` (already importing from `download.mediafilters` after Task 4) and `MediaFilteredError` (already imported).
(The `finally: await response.aclose()` already guarantees the response closes without the body being read.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/download/unit/test_mediafilters.py tests/download/unit/test_media_download.py tests/download/unit/test_media_pipeline.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -10`
Expected: all PASS (the two sibling suites guard the signature change)

- [ ] **Step 5: Quality gate + hand commit to operator**

Run: `.venv/bin/ruff check download/media.py tests/download/unit/test_mediafilters.py && .venv/bin/mypy download/media.py`
Expected: clean. Commit message: `feat(download): content-length wire gate for regular files (#125)`

---

### Task 6: Streaming gates — estimate, running-total backstop, completion check

**Files:**
- Modify: `download/mediafilters.py` (add `estimate_stream_size_gate`)
- Modify: `download/m3u8.py` (`download_m3u8` at ~line 686 and `_try_segment_download` at ~line 562 gain `max_bytes: int | None = None`; `_try_direct_download_pyav` at ~line 171 same)
- Modify: `download/media.py` (`_download_m3u8_file` at ~line 385: estimate gate before the download, `max_bytes` threading, completion check after)
- Test: `tests/download/unit/test_mediafilters.py` (extend)

**Interfaces:**
- Consumes: `fetch_m3u8_segment_playlist` is NOT used by the estimate gate — only the master playlist text via `config.get_api().get_with_ngsw_sync` + the `m3u8.M3U8` parser and `get_m3u8_cookies`/`split_url` from `download/m3u8.py`; `resolve_media_filters`, `MediaFilteredError`, `record_filter_observation`.
- Produces: `async estimate_stream_size_gate(config, state, media) -> None` (raises `MediaFilteredError(reason, estimated=...)`; passes silently when duration or bandwidth unknown); `download_m3u8(config, m3u8_url, save_path, created_at=None, max_bytes=None)`; running-total abort in tier 1/tier 3 raising `MediaFilteredError("file_size_max", observed=running_total)`; completion check in `_download_m3u8_file` (min AND max, final-file truth) deleting the temp file and raising with true size.

- [ ] **Step 1: Write the failing tests**

Append to `tests/download/unit/test_mediafilters.py` (file-top imports: `from download.mediafilters import estimate_stream_size_gate`, `from tests.fixtures.api.livestream_factories import build_master_playlist`; check `build_master_playlist`'s exact signature — it takes `variant_url`, `bandwidth`, `resolution`, `codecs` kwargs):

```python
@pytest.mark.asyncio
class TestEstimateGate:
    async def _m3u8_media(self, entity_store, url, duration):
        account = AccountFactory(id=snowflake_id(), username="creator1")
        await entity_store.save(account)
        media = Media(
            id=snowflake_id(),
            accountId=account.id,
            mimetype="video/mp4",
            duration=duration,
        )
        media.download_url = url
        media.file_extension = "m3u8"
        await entity_store.save(media)
        return media

    async def test_estimate_violation_skips_before_segments(
        self, respx_fansly_api, mock_config, entity_store
    ):
        # 4,000,000 bps / 8 * 2000 s = 1_000_000_000 bytes > 500 MB max
        mock_config.media_filters = MediaFilters(file_size_max=500_000_000)
        state = _state()
        master_url = "https://cdn.example.com/vod/master.m3u8?Policy=p&Key-Pair-Id=k&Signature=s"
        media = await self._m3u8_media(entity_store, master_url, duration=2000.0)
        playlist_text = build_master_playlist(
            variant_url="https://cdn.example.com/vod/720.m3u8",
            bandwidth=4_000_000,
        )
        route = respx.get(url__startswith="https://cdn.example.com/vod/master").mock(
            side_effect=[httpx.Response(200, text=playlist_text)]
        )
        try:
            with pytest.raises(MediaFilteredError) as exc_info:
                await estimate_stream_size_gate(mock_config, state, media)
        finally:
            dump_fansly_calls(route.calls, "test_estimate_violation")
        assert exc_info.value.reason == "file_size_max"
        assert exc_info.value.estimated == 1_000_000_000

    async def test_unknown_duration_passes(
        self, respx_fansly_api, mock_config, entity_store
    ):
        mock_config.media_filters = MediaFilters(file_size_max=500)
        state = _state()
        media = await self._m3u8_media(
            entity_store,
            "https://cdn.example.com/vod2/master.m3u8?Policy=p&Key-Pair-Id=k&Signature=s",
            duration=None,
        )
        await estimate_stream_size_gate(mock_config, state, media)  # no raise

    async def test_inactive_passes_without_fetch(self, mock_config, entity_store):
        mock_config.media_filters = MediaFilters()
        state = _state()
        media = await self._m3u8_media(
            entity_store,
            "https://cdn.example.com/vod3/master.m3u8?Policy=p&Key-Pair-Id=k&Signature=s",
            duration=100.0,
        )
        await estimate_stream_size_gate(mock_config, state, media)  # no HTTP, no raise
```

Also append a running-total test driving `_try_segment_download` directly with real TS fixtures — read `tests/download/integration/test_m3u8_integration.py` and `tests/fixtures/api/livestream_factories.py` (`make_synthetic_ivs_segment`, `build_variant_playlist`) first and mirror how they wire a variant playlist + segment responses through respx/the sync client; the load-bearing assertions: with `max_bytes` below the summed segment size, `MediaFilteredError("file_size_max")` is raised, no `.mp4` output exists, and no leftover `.ts` files remain in the target dir. And a completion-check test: `_download_m3u8_file` with a `file_size_min` above the produced file's size → `MediaFilteredError("file_size_min", observed=<true size>)`, final file absent, `media.is_downloaded` False.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/download/unit/test_mediafilters.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -10`
Expected: `ImportError: cannot import name 'estimate_stream_size_gate'`

- [ ] **Step 3: Implement**

In `download/mediafilters.py` add (file-top imports: `import asyncio`, `from m3u8 import M3U8`, `from download.m3u8 import get_m3u8_cookies, split_url` — verify `split_url`'s import source in m3u8.py first):

```python
async def estimate_stream_size_gate(
    config: FanslyConfig, state: DownloadState, media: Media
) -> None:
    """Bitrate x duration estimate gate for HLS VODs; passes on unknowns.

    Raises:
        MediaFilteredError: When the estimated size violates a limit.
    """
    filters = resolve_media_filters(config, state)
    if filters is None or (filters.file_size_min is None and filters.file_size_max is None):
        return
    duration = media.duration
    if not duration or not media.download_url:
        return

    def _fetch_master_text() -> str | None:
        cookies = get_m3u8_cookies(media.download_url)
        _, file_url = split_url(media.download_url)
        response = config.get_api().get_with_ngsw_sync(
            url=file_url,
            cookies=cookies,
            add_fansly_headers=False,
            bypass_rate_limit=True,
        )
        if response.status_code != 200:
            return None
        return response.text

    master_text = await asyncio.to_thread(_fetch_master_text)
    if not master_text:
        return
    base_url, _ = split_url(media.download_url)
    playlist = M3U8(content=master_text, base_uri=base_url)
    if not playlist.playlists:
        return
    best = max(
        playlist.playlists,
        key=lambda p: (p.stream_info.resolution or (0, 0))[0]
        * (p.stream_info.resolution or (0, 0))[1],
    )
    bandwidth = best.stream_info.average_bandwidth or best.stream_info.bandwidth
    if not bandwidth:
        return
    estimated = int(bandwidth / 8 * duration)
    reason = filters.size_verdict(estimated)
    if reason:
        raise MediaFilteredError(reason, estimated=estimated)
```

In `download/m3u8.py`:
1. `download_m3u8(..., max_bytes: int | None = None)` — pass `max_bytes` to `_try_direct_download_pyav` and `_try_segment_download` (tier 2 ffmpeg cannot enforce mid-stream; the completion check is its safety net — add that one-line comment at the tier-2 call).
2. `_try_segment_download(..., max_bytes: int | None = None)`: a `threading.Event` abort flag + `itertools`-free shared counter guarded by `threading.Lock`; inside `download_ts`'s chunk loop, add each chunk's length to the counter and set the event when `max_bytes` is exceeded; check the event at the top of `download_ts` (return early) and inside the chunk loop (stop writing). After the executor block, if the event is set: delete all downloaded segment files, and `raise MediaFilteredError("file_size_max", observed=<counter value>)`.
3. `_try_direct_download_pyav(..., max_bytes: int | None = None)`: in its packet loop, every 512 packets `output_path.stat().st_size`; over `max_bytes` → close containers, delete `output_path`, `raise MediaFilteredError("file_size_max", observed=<size>)`. (Read the function body first; place the check where the loop counter already exists or add a local counter.)
4. `MediaFilteredError` must NOT be swallowed by tier fallbacks or the generic `except Exception` in `download_m3u8` — add `except MediaFilteredError: raise` above the generic handler, and make tier-1's internal try/except re-raise it rather than falling through to tier 2 (read tier 1's except structure and add the narrow re-raise first).

In `download/media.py` `_download_m3u8_file`:
1. Before the `asyncio.to_thread(download_m3u8, ...)` call: `await estimate_stream_size_gate(config, state, media)` and compute `filters = resolve_media_filters(config, state)`; pass `max_bytes=filters.file_size_max if filters else None` into the `download_m3u8` call (as a keyword through `asyncio.to_thread`).
2. After `download_m3u8` returns and BEFORE hashing: completion check —

```python
        filters = resolve_media_filters(config, state)
        if filters is not None:
            final_size = (await asyncio.to_thread(temp_path.stat)).st_size
            reason = filters.size_verdict(final_size)
            if reason:
                raise MediaFilteredError(reason, observed=final_size)
```

(The existing `finally: shutil.rmtree(temp_dir)` already deletes the violating file; `MediaFilteredError` propagates to `download_media`'s Task-4 handler which records + counts.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/download/unit/test_mediafilters.py tests/download/unit/test_m3u8.py tests/download/integration/test_m3u8_integration.py -q 2>&1 | tee-nocolor /tmp/pytest-logs/run.log | tail -10`
Expected: all PASS

- [ ] **Step 5: Quality gate + hand commit to operator**

Run: `.venv/bin/ruff check download/ tests/download/unit/test_mediafilters.py && .venv/bin/mypy download/mediafilters.py download/m3u8.py download/media.py`
Expected: clean. Commit message: `feat(download): streaming size gates — bitrate estimate, running-total backstop, completion check (#125)`

---

### Task 7: Docs, CHANGELOG, full suite

**Files:**
- Modify: `docs/configuration/config_options.md` (new top-level `filters` section docs; MOVE the existing `wall_filters` subsection under it with the path updated to `filters.wall`; add `filters.media`)
- Modify: `CHANGELOG.md` (amend the unreleased wall_filters bullet to `filters.wall`; add the media-filters Added bullet)
- Test: full suite

- [ ] **Step 1: Update `docs/configuration/config_options.md`**

Read the page's structure first. Create a `filters` section: relocate the wall_filters subsection (change every `options.wall_filters`/`options:`-example reference to `filters.wall`/`filters:`), then add a `filters.media` subsection covering, in the page's own voice: the four limits with ALL accepted value formats (`100KB`/`4GB`/`1GiB`/raw bytes; `90`/`1:30`/`1:30:00`/`45s`/`45m`/`2h`; `0`/absent = disabled), `by_creator` override semantics (absent field = inherit, explicit `0`/`null` = disable for that creator; keys are usernames or account IDs), duration = videos only / unknown passes, size checks (regular files via content-length before the body; streaming VODs via declared-bitrate x duration estimate — can over-estimate near a max — plus mid-download backstop and completion truth-check), the recording behavior (skipped items are never marked downloaded; loosening limits downloads them later), the SINGLE and live-capture exemptions, and the four CLI flags (global layer only, `by_creator` wins, `0` disables per run). Update the CLI ↔ config mapping table with the four flags.

- [ ] **Step 2: Update `CHANGELOG.md`**

Under the current Unreleased/Added section: change the existing wall_filters bullet's `options.wall_filters` reference to `filters.wall`, and add (one physical line):

```markdown
- `filters.media` — min/max file-size and duration filters (#125): global limits with per-creator `by_creator` overrides, human-friendly values (`4GB`, `1:30:00`, `45m`), enforced before download via metadata/bitrate-estimate gates and at the wire via content-length, with `--file-size-min/--file-size-max/--duration-min/--duration-max` CLI overrides; filtered media is never marked downloaded, so loosened limits pick items up later.
```

- [ ] **Step 3: Full-suite verification**

Run: `.venv/bin/python -m pytest tests/ -q --no-header -n8 -rs --timeout=60 --cov --cov-report=term-missing 2>&1 | tee-nocolor /tmp/pytest-logs/cov.log | tail -40`
Expected: zero failures/errors (known load-timing flake: `TestChatWsLoopE2E::test_records_pushed_messages_to_jsonl` — if it is the ONLY failure, re-run that file alone to confirm it passes in isolation and report it, per the ledger's open item); skips only pre-existing (`plyvel not installed`, cleanup-enforcement demo). Check `grep -E "config/media_filters|download/mediafilters" /tmp/pytest-logs/cov.log` — both new modules at 100% (add targeted tests for gaps, no pragmas; bare `--cov` only).

- [ ] **Step 4: Quality gates**

Run: `.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy config/media_filters.py download/mediafilters.py config/schema.py config/args.py download/media.py download/m3u8.py`
Expected: clean (pre-existing project-wide mypy debt aside — nothing new attributable to these files' changed lines).

- [ ] **Step 5: Hand commit to operator**

Commit message: `docs: filters section reference (wall relocation + media limits) + changelog (#125)`

---

## Plan Self-Review Notes

- Spec coverage: parsers/model (T1), FiltersSection + relocation + plumbing (T2), CLI (T3), metadata gate + recording + MediaFilteredError + stats (T4), wire gate (T5), streaming estimate/backstop/completion (T6), docs/CHANGELOG/full suite (T7). Every-gate SINGLE/inactive exemption is centralized in `resolve_media_filters` (T4) and consumed by T5/T6. Live captures: no task touches `download/livestream.py` — exemption by omission, documented in T7.
- Type consistency spot-checks: `MediaFilteredError(reason, *, observed, estimated)` used identically in T4 tests, T5 raise, T6 raises/handler; `resolve_media_filters(config, state)` signature consistent across T4-T6; `_download_regular_file` gains `state` in T5 and the T5 tests call the new signature.
- Verify-before-trusting points marked inline: httpx auto-`content-length` behavior (T5 test), `split_url` import source and tier-1 except structure (T6), sibling m3u8 integration-test conventions (T6), `build_master_playlist` signature (T6 — confirmed takes `bandwidth`).
- Known simplification vs spec wording: the estimate gate fetches the master playlist itself (one extra tiny request) rather than threading the fetch through `download_m3u8`'s tiers — accepted for module isolation.
