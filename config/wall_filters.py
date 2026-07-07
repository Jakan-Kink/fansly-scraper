"""Wall-filter spec model and lenient YAML/JSON normalization.

WallFilterSpec captures per-creator wall include/exclude lists.
normalize_wall_filters accepts the strict mapping shape plus common YAML
list/dict confusions and returns a creator-keyed dict of WallFilterSpec.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from errors import ConfigError


WALL_FILTERS_EXAMPLE = """\
wall_filters:
  creator1: ["FULL VIDEOS"]
  creator2:
    includes: ["Promos"]
    excludes: ["previews"]"""

_INCLUDE_KEYS = frozenset({"includes", "include"})
_EXCLUDE_KEYS = frozenset({"excludes", "exclude"})
_SPEC_KEYS = _INCLUDE_KEYS | _EXCLUDE_KEYS | {"all_walls"}


class WallFilterSpec(BaseModel):
    """Per-creator wall selection: include/exclude identifier lists.

    Identifiers are wall names (case-insensitive match) or wall snowflake
    IDs. all_walls marks an operator-confirmed unfiltered creator.
    """

    model_config = ConfigDict(extra="forbid")

    includes: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)
    all_walls: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.includes and not self.excludes


def is_snowflake_token(token: str) -> bool:
    """True when token looks like a snowflake ID (all digits, >= 10 chars)."""
    return token.isascii() and token.isdigit() and len(token) >= 10


def _shape_error(context: str) -> ConfigError:
    return ConfigError(
        f"wall_filters: {context}\nExpected shape:\n{WALL_FILTERS_EXAMPLE}"
    )


def _merge_one_key_maps(value: Any) -> Any:
    """Merge [{a: x}, {b: y}] into {a: x, b: y}; pass other values through."""
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(el, dict) and len(el) == 1 for el in value)
    ):
        return value
    merged: dict[str, Any] = {}
    for el in value:
        ((key, val),) = el.items()
        if key in merged:
            raise _shape_error(f"duplicate key '{key}' after merging list entries.")
        merged[key] = val
    return merged


def _identifier_list(creator: str, value: list) -> list[str]:
    tokens: list[str] = []
    for item in value:
        if not isinstance(item, str | int) or isinstance(item, bool):
            raise _shape_error(
                f"entry for '{creator}' contains a non-identifier value: {item!r}."
            )
        tokens.append(str(item))
    return tokens


def _normalize_spec(creator: str, value: Any) -> WallFilterSpec:
    value = _merge_one_key_maps(value)
    if value is None:
        return WallFilterSpec()
    if isinstance(value, str | int) and not isinstance(value, bool):
        return WallFilterSpec(includes=[str(value)])
    if isinstance(value, list):
        return WallFilterSpec(includes=_identifier_list(creator, value))
    if isinstance(value, dict):
        keys = {str(k) for k in value}
        if keys & _SPEC_KEYS:
            unknown = keys - _SPEC_KEYS
            if unknown:
                raise _shape_error(
                    f"unknown key(s) {sorted(unknown)} for '{creator}' mixed "
                    "with includes/excludes."
                )
            includes: list[str] = []
            excludes: list[str] = []
            for key, val in value.items():
                if str(key) == "all_walls":
                    continue
                target = includes if str(key) in _INCLUDE_KEYS else excludes
                merged_val = _merge_one_key_maps(val)
                if merged_val is None:
                    continue
                if isinstance(merged_val, str | int) and not isinstance(
                    merged_val, bool
                ):
                    target.append(str(merged_val))
                elif isinstance(merged_val, list):
                    target.extend(_identifier_list(creator, merged_val))
                else:
                    raise _shape_error(
                        f"'{key}' for '{creator}' must be a list of wall names/IDs."
                    )
            return WallFilterSpec(
                includes=includes,
                excludes=excludes,
                all_walls=bool(value.get("all_walls", False)),
            )
        if all(v is None for v in value.values()):
            return WallFilterSpec(includes=[str(k) for k in value])
        raise _shape_error(f"could not interpret the entry for '{creator}'.")
    raise _shape_error(f"could not interpret the entry for '{creator}'.")


def normalize_wall_filters(raw: Any) -> dict[str, WallFilterSpec]:
    """Normalize a raw wall_filters value into {creator: WallFilterSpec}.

    Args:
        raw: Value from YAML/JSON — the strict mapping shape or any of the
            documented lenient confusions.

    Returns:
        dict keyed by sanitized creator username or account snowflake ID.

    Raises:
        ConfigError: On shapes that cannot be interpreted unambiguously.
    """
    if raw is None:
        return {}
    if isinstance(raw, WallFilterSpec):
        raise _shape_error("expected a creator mapping at the top level.")
    raw = _merge_one_key_maps(raw)
    if not isinstance(raw, dict):
        raise _shape_error("expected a mapping of creator -> walls.")
    normalized: dict[str, WallFilterSpec] = {}
    for key, value in raw.items():
        creator = str(key).strip().lstrip("@").lower()
        if not creator:
            raise _shape_error("empty creator key.")
        if creator in normalized:
            raise _shape_error(f"duplicate creator '{creator}'.")
        if isinstance(value, WallFilterSpec):
            normalized[creator] = value
        else:
            normalized[creator] = _normalize_spec(creator, value)
    return normalized
