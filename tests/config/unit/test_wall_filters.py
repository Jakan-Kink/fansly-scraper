"""Unit tests for config.wall_filters spec model and lenient normalization."""

import sys
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from config.args import map_args_to_config, parse_args
from config.config import _populate_config_from_schema, save_config_or_raise
from config.fanslyconfig import FanslyConfig
from config.logging import init_logging_config
from config.modes import DownloadMode
from config.schema import ConfigSchema, OptionsSection
from config.validation import validate_adjust_wall_filters
from config.wall_filters import (
    WallFilterSpec,
    is_snowflake_token,
    normalize_wall_filters,
)
from errors import ConfigError


class TestIsSnowflakeToken:
    def test_shapes(self):
        assert is_snowflake_token("1234567890")
        assert is_snowflake_token("123456789012345")
        assert not is_snowflake_token("123456789")  # too short
        assert not is_snowflake_token("abc4567890")  # non-digit
        assert not is_snowflake_token("")


class TestNormalizeWallFilters:
    def test_strict_and_lenient_value_shapes(self):
        result = normalize_wall_filters(
            {
                "Creator1": ["FULL VIDEOS", 1234567890123],
                "@creator2": {"includes": ["Promos"], "excludes": ["previews"]},
                "creator3": "solo-wall",
                "creator4": {"FULL VIDEOS": None, "Promos": None},
                "creator5": None,
                "creator6": {"include": "posts", "exclude": ["previews"]},
            }
        )
        assert result["creator1"] == WallFilterSpec(
            includes=["FULL VIDEOS", "1234567890123"]
        )
        assert result["creator2"] == WallFilterSpec(
            includes=["Promos"], excludes=["previews"]
        )
        assert result["creator3"] == WallFilterSpec(includes=["solo-wall"])
        assert result["creator4"] == WallFilterSpec(includes=["FULL VIDEOS", "Promos"])
        assert result["creator5"].is_empty
        assert result["creator6"] == WallFilterSpec(
            includes=["posts"], excludes=["previews"]
        )

    def test_keys_sanitized_and_snowflake_keys_kept(self):
        result = normalize_wall_filters({"@UserName": ["a"], "1234567890123": ["b"]})
        assert set(result) == {"username", "1234567890123"}

    def test_dash_prefixed_lists_merge_recursively(self):
        raw = [{"creator1": [{"exclude": ["bad entry"]}]}]
        assert normalize_wall_filters(raw) == {
            "creator1": WallFilterSpec(excludes=["bad entry"])
        }

    def test_none_and_empty_input(self):
        assert normalize_wall_filters(None) == {}
        assert normalize_wall_filters({}) == {}

    def test_all_walls_round_trip(self):
        result = normalize_wall_filters({"c1": {"all_walls": True}})
        assert result["c1"].all_walls is True
        assert result["c1"].is_empty

    def test_error_shapes_carry_example(self):
        with pytest.raises(ConfigError, match="unknown key"):
            normalize_wall_filters({"c": {"includes": ["a"], "typo": ["b"]}})
        with pytest.raises(ConfigError, match="duplicate"):
            normalize_wall_filters([{"c": ["a"]}, {"c": ["b"]}])
        with pytest.raises(ConfigError, match="wall_filters"):
            normalize_wall_filters("just-a-string")
        with pytest.raises(ConfigError, match="wall_filters"):
            normalize_wall_filters({"c": 3.14})
        with pytest.raises(ConfigError, match="wall_filters"):
            normalize_wall_filters({"c": [["nested-list"]]})
        # every raise carries the correct-syntax example
        with pytest.raises(ConfigError, match="creator2"):
            normalize_wall_filters("nope")

    def test_null_include_key_falls_through_to_excludes(self):
        result = normalize_wall_filters({"c": {"includes": None, "excludes": ["x"]}})
        assert result["c"] == WallFilterSpec(excludes=["x"])

    def test_include_value_wrong_type_raises(self):
        with pytest.raises(ConfigError, match="must be a list"):
            normalize_wall_filters({"c": {"includes": {"a": 1}}})

    def test_dict_without_spec_keys_and_non_none_values_raises(self):
        with pytest.raises(ConfigError, match="could not interpret"):
            normalize_wall_filters({"c": {"foo": "bar"}})

    def test_top_level_wallfilterspec_raises(self):
        with pytest.raises(ConfigError, match="creator mapping"):
            normalize_wall_filters(WallFilterSpec(includes=["a"]))

    def test_empty_creator_key_raises(self):
        with pytest.raises(ConfigError, match="empty creator"):
            normalize_wall_filters({"@": ["a"]})

    def test_duplicate_creator_after_sanitization_raises(self):
        with pytest.raises(ConfigError, match="duplicate creator"):
            normalize_wall_filters({"C1": ["a"], "c1": ["b"]})

    def test_per_creator_wallfilterspec_passthrough(self):
        spec = WallFilterSpec(includes=["x"])
        assert normalize_wall_filters({"c1": spec}) == {"c1": spec}


class TestSchemaField:
    def test_options_section_normalizes(self):
        opts = OptionsSection(wall_filters={"@C1": "FULL VIDEOS"})
        assert opts.wall_filters == {"c1": WallFilterSpec(includes=["FULL VIDEOS"])}

    def test_default_is_empty(self):
        assert OptionsSection().wall_filters == {}

    def test_schema_yaml_round_trip(self):
        schema = ConfigSchema.model_validate(
            {
                "options": {
                    "download_mode": "wall",
                    "wall_filters": {"c1": ["A"], "c2": {"excludes": ["B"]}},
                }
            }
        )
        dumped = schema.model_dump(mode="json", exclude_unset=True)
        again = ConfigSchema.model_validate(dumped)
        assert again.options.wall_filters == schema.options.wall_filters


class TestFanslyConfigPlumbing:
    def test_populate_from_schema(self):
        schema = ConfigSchema.model_validate(
            {"options": {"download_mode": "wall", "wall_filters": {"c1": ["A"]}}}
        )
        config = FanslyConfig(program_version="0.14.5-test")
        _populate_config_from_schema(config, schema)
        assert config.wall_filters == {"c1": WallFilterSpec(includes=["A"])}

    def test_default_empty_dict(self):
        config = FanslyConfig(program_version="0.14.5-test")
        assert config.wall_filters == {}


@pytest.mark.asyncio
class TestValidateAdjustWallFilters:
    async def test_noop_when_empty(self, validation_config):
        validation_config.wall_filters = {}
        await validate_adjust_wall_filters(validation_config)  # no raise

    async def test_mode_conflict_raises(self, validation_config):
        validation_config.download_mode = DownloadMode.NORMAL
        validation_config.wall_filters = {"c1": WallFilterSpec(includes=["A"])}
        with pytest.raises(ConfigError, match="download_mode: wall"):
            await validate_adjust_wall_filters(validation_config)

    async def test_use_following_conflict_raises(self, validation_config):
        validation_config.download_mode = DownloadMode.WALL
        validation_config.wall_filters = {"c1": WallFilterSpec(includes=["A"])}
        validation_config.use_following = True
        with pytest.raises(ConfigError, match="use_following"):
            await validate_adjust_wall_filters(validation_config)

    async def test_scope_derived_from_keys(self, validation_config):
        validation_config.download_mode = DownloadMode.WALL
        validation_config.wall_filters = {"c1": WallFilterSpec(includes=["A"])}
        validation_config.user_names = {"someoneelse"}
        await validate_adjust_wall_filters(validation_config)
        assert validation_config.user_names == {"c1"}
        assert "user_names" in validation_config._ephemeral_overrides

    async def test_u_subset_narrows(self, validation_config):
        validation_config.download_mode = DownloadMode.WALL
        validation_config.wall_filters = {
            "c1": WallFilterSpec(includes=["A"]),
            "c2": WallFilterSpec(includes=["B"]),
        }
        validation_config.user_names = {"c1"}
        validation_config._ephemeral_overrides.add("user_names")
        await validate_adjust_wall_filters(validation_config)
        assert set(validation_config.wall_filters) == {"c1"}
        assert "wall_filters" in validation_config._ephemeral_overrides

    async def test_u_not_in_keys_raises(self, validation_config):
        validation_config.download_mode = DownloadMode.WALL
        validation_config.wall_filters = {"c1": WallFilterSpec(includes=["A"])}
        validation_config.user_names = {"c1", "stranger"}
        validation_config._ephemeral_overrides.add("user_names")
        with pytest.raises(ConfigError, match="stranger"):
            await validate_adjust_wall_filters(validation_config)

    async def test_empty_spec_noninteractive_raises(self, validation_config):
        validation_config.download_mode = DownloadMode.WALL
        validation_config.interactive = False
        validation_config.wall_filters = {"c1": WallFilterSpec()}
        with pytest.raises(ConfigError, match="c1"):
            await validate_adjust_wall_filters(validation_config)

    async def test_empty_spec_interactive_grab_all(self, validation_config):
        validation_config.download_mode = DownloadMode.WALL
        validation_config.interactive = True
        validation_config.wall_filters = {"c1": WallFilterSpec()}
        with patch("config.validation.aconfirm", new=AsyncMock(return_value=True)):
            await validate_adjust_wall_filters(validation_config)
        assert validation_config.wall_filters["c1"].all_walls is True

    async def test_empty_spec_interactive_entry(self, validation_config):
        validation_config.download_mode = DownloadMode.WALL
        validation_config.interactive = True
        validation_config.wall_filters = {"c1": WallFilterSpec()}
        with (
            patch("config.validation.aconfirm", new=AsyncMock(return_value=False)),
            patch(
                "config.validation.aprompt_text",
                new=AsyncMock(return_value="FULL VIDEOS, 1234567890123"),
            ),
        ):
            await validate_adjust_wall_filters(validation_config)
        assert validation_config.wall_filters["c1"].includes == [
            "FULL VIDEOS",
            "1234567890123",
        ]
        assert "wall_filters" in validation_config._ephemeral_overrides


@pytest.mark.asyncio
class TestMidRunSaveDoesNotMutateSchema:
    """Regression coverage for runtime wall_filters aliasing the schema dict.

    Before the fix, ``_populate_config_from_schema`` assigned the schema's
    ``wall_filters`` dict (and its ``WallFilterSpec`` objects) directly onto
    ``config.wall_filters`` — the same dict/objects. Any runtime narrowing
    (``-u`` scoping) mutated the schema in place, and a mid-run
    ``save_config_or_raise`` (token refresh, device-id update, etc.) would
    persist the narrowed/mutated view, silently deleting the operator's
    other wall_filters entries from config.yaml.
    """

    async def test_narrowing_does_not_leak_into_saved_yaml(self, validation_config):
        schema = ConfigSchema.model_validate(
            {
                "options": {
                    "download_mode": "wall",
                    "wall_filters": {"c1": ["A"], "c2": ["B"]},
                }
            }
        )
        validation_config._schema = schema
        _populate_config_from_schema(validation_config, schema)
        assert validation_config.download_mode == DownloadMode.WALL

        # Aliasing is broken: mutating the runtime dict must not touch the
        # schema's dict/objects.
        assert validation_config.wall_filters is not schema.options.wall_filters
        assert set(schema.options.wall_filters) == {"c1", "c2"}

        # Simulate ``-u c1`` narrowing (config/args.py::_handle_user_settings).
        validation_config.user_names = {"c1"}
        validation_config._ephemeral_overrides.add("user_names")
        await validate_adjust_wall_filters(validation_config)
        assert set(validation_config.wall_filters) == {"c1"}

        # A mid-run save (token refresh, device-id update, ...) must not
        # persist the narrowed runtime view.
        save_config_or_raise(validation_config)

        written = yaml.safe_load(validation_config.config_path.read_text())
        assert set(written["options"]["wall_filters"]) == {"c1", "c2"}
        assert set(schema.options.wall_filters) == {"c1", "c2"}


def _map(argv, validation_config):
    """Parse argv and map onto the validation_config fixture.

    ``map_args_to_config`` unconditionally calls ``set_debug_enabled``,
    which reads the module-level ``config.logging._config``; priming it
    here mirrors the ``config_with_path`` fixture's convention for tests
    that exercise ``map_args_to_config`` directly.
    """
    init_logging_config(validation_config)
    with patch.object(sys, "argv", ["fansly_downloader_ng.py", *argv]):
        args = parse_args()
    return map_args_to_config(args, validation_config)


class TestWallFiltersCli:
    def test_bare_form_single_user(self, validation_config):
        _map(
            ["-u", "creator1", "--wall-filters", "FULL VIDEOS,Promos"],
            validation_config,
        )
        assert validation_config.download_mode == DownloadMode.WALL
        assert validation_config.wall_filters == {
            "creator1": WallFilterSpec(includes=["FULL VIDEOS", "Promos"])
        }
        assert "wall_filters" in validation_config._ephemeral_overrides
        assert "download_mode" in validation_config._ephemeral_overrides

    def test_bare_form_requires_exactly_one_user(self, validation_config):
        with pytest.raises(ConfigError, match="exactly one"):
            _map(
                ["-u", "creator1,creator2", "--wall-filters", "FULL VIDEOS"],
                validation_config,
            )
        with pytest.raises(ConfigError, match="exactly one"):
            _map(["--wall-filters", "FULL VIDEOS"], validation_config)

    def test_json_form_sets_scope(self, validation_config):
        _map(
            ["--wall-filters", '{"creator1": ["A"], "creator2": {"excludes": ["B"]}}'],
            validation_config,
        )
        assert validation_config.user_names == {"creator1", "creator2"}
        assert set(validation_config.wall_filters) == {"creator1", "creator2"}

    def test_json_form_with_matching_u(self, validation_config):
        _map(
            ["-u", "creator1", "--wall-filters", '{"creator1": ["A"]}'],
            validation_config,
        )
        assert validation_config.wall_filters == {
            "creator1": WallFilterSpec(includes=["A"])
        }

    def test_json_form_with_mismatched_u_raises(self, validation_config):
        with pytest.raises(ConfigError, match="match"):
            _map(
                ["-u", "other", "--wall-filters", '{"creator1": ["A"]}'],
                validation_config,
            )

    def test_invalid_json_raises(self, validation_config):
        with pytest.raises(ConfigError, match="JSON"):
            _map(["--wall-filters", '{"creator1": ['], validation_config)

    def test_empty_json_spec_raises(self, validation_config):
        with pytest.raises(ConfigError, match="empty spec"):
            _map(["--wall-filters", "{}"], validation_config)

    def test_mutually_exclusive_with_mode_flags(self, validation_config):
        with pytest.raises(SystemExit):
            _map(
                ["--timeline", "--wall-filters", "FULL VIDEOS"],
                validation_config,
            )
