"""Media-filter value parsing and the frozen runtime limits model.

parse_size/parse_duration normalize human-friendly config values to bytes
and seconds, delegating to pydantic.ByteSize and pytimeparse2. MediaFilters
holds the resolved limits plus per-creator overrides and answers
pass/violate verdicts for the download gates.
"""

from typing import Any

import pytimeparse2  # type: ignore[import-untyped]  # pytimeparse2 ships no stubs
from pydantic import (
    BaseModel,
    ByteSize,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)

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
            parsed = pytimeparse2.parse(text)
            if parsed is None:
                raise _shape_error("duration", value) from None
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
