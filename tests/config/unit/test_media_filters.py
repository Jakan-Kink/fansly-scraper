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

    def test_username_lookup_is_sanitized(self):
        r = self._filters().for_creator("@VOD_Streamer", None)
        assert r.duration_max == 2700.0
