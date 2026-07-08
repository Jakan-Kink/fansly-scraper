"""Unit tests for config.media_filters parsing and resolution."""

import sys
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from config.args import map_args_to_config, parse_args
from config.config import _populate_config_from_schema
from config.fanslyconfig import FanslyConfig
from config.logging import init_logging_config
from config.media_filters import (
    MediaFilterOverride,
    MediaFilters,
    parse_duration,
    parse_size,
)
from config.schema import ConfigSchema, FiltersSection, MediaFiltersSection
from config.wall_filters import WallFilterSpec
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

    @pytest.mark.parametrize("value", ["huge", "10XB", "GB4", "-5MB", -5, True, [1]])
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

    @pytest.mark.parametrize(
        "value", ["soon", "1:2:3:4", "-30", "-30s", -30, True, "10x", [1]]
    )
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

    def test_no_username_falls_back_to_id_key(self):
        r = self._filters().for_creator(None, 1234567890123)
        assert r.duration_min == 5.0

    def test_unknown_creator_gets_globals(self):
        base = self._filters()
        r = base.for_creator("unknown", 999)
        assert r.file_size_max == base.file_size_max
        assert r.by_creator == {}

    def test_username_lookup_is_sanitized(self):
        r = self._filters().for_creator("@VOD_Streamer", None)
        assert r.duration_max == 2700.0


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

    def test_by_creator_duplicate_after_sanitization_raises(self):
        with pytest.raises(ValidationError, match="duplicate creator"):
            MediaFiltersSection.model_validate(
                {"by_creator": {"@Foo": {"duration_max": "1h"}, "foo": {}}}
            )


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

    def test_global_contradiction_raises(self, validation_config):
        with pytest.raises(ConfigError, match="file_size"):
            _map(
                ["--file-size-min", "10GB", "--file-size-max", "1GB"],
                validation_config,
            )

    def test_cli_global_violates_resolved_by_creator_bounds_raises(
        self, validation_config
    ):
        validation_config.media_filters = MediaFilters(
            by_creator={"c1": MediaFilterOverride(duration_max=30.0)},
        )
        with pytest.raises(ConfigError, match=r"by_creator\.c1"):
            _map(["--duration-min", "60"], validation_config)


class TestByCreatorYamlRoundTrip:
    """A ``by_creator`` override's unset fields must stay unset across a
    YAML dump/load cycle, so inheritance from the global/creator layer
    keeps working after any routine mid-run config save."""

    def test_unset_fields_stay_unset_and_explicit_nulls_survive(self, tmp_path):
        schema = ConfigSchema()
        schema.filters.media = MediaFiltersSection.model_validate(
            {
                "file_size_min": "100KB",
                "file_size_max": "4GB",
                "by_creator": {
                    "vod_streamer": {"duration_max": "45m"},
                    "archived": {"file_size_max": 0},
                },
            }
        )
        out_path = tmp_path / "media_filters.yaml"
        schema.dump_yaml(out_path)
        yaml_text = out_path.read_text(encoding="utf-8")

        reloaded = ConfigSchema.load_yaml(out_path)
        runtime = reloaded.filters.media.to_runtime()

        # (a) inheritance survives a round-trip: vod_streamer only overrides
        # duration_max, so file_size_min/file_size_max must still inherit.
        vod = runtime.for_creator("vod_streamer", None)
        assert vod.file_size_min == 100_000
        assert vod.file_size_max == 4_000_000_000
        assert vod.duration_max == 2700.0

        # (b) an authored explicit-null disable (file_size_max: 0 -> None)
        # still resolves to None, distinct from the inherited 4GB.
        archived = runtime.for_creator("archived", None)
        assert archived.file_size_max is None
        assert archived.file_size_min == 100_000
        assert (
            "file_size_max"
            in reloaded.filters.media.by_creator["archived"].model_fields_set
        )

        # (c) the written YAML must not materialize vod_streamer's unset
        # fields as explicit nulls.
        vod_idx = yaml_text.index("vod_streamer:")
        following = sorted(
            idx for idx in (yaml_text.find("archived:"),) if idx > vod_idx
        )
        end = following[0] if following else len(yaml_text)
        vod_block = yaml_text[vod_idx:end]
        for unset_field in ("file_size_min:", "file_size_max:", "duration_min:"):
            assert unset_field not in vod_block, (
                f"unset vod_streamer.{unset_field.rstrip(':')} bled into YAML:\n"
                f"{vod_block}"
            )
        assert "duration_max:" in vod_block
