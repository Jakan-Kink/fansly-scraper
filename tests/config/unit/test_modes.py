"""Unit tests for DownloadMode enum"""

import pytest

from config.modes import DownloadMode


class TestDownloadMode:
    """Tests for the DownloadMode enum."""

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            pytest.param(DownloadMode.NOTSET, "NOTSET", id="NOTSET"),
            pytest.param(DownloadMode.COLLECTION, "COLLECTION", id="COLLECTION"),
            pytest.param(DownloadMode.MESSAGES, "MESSAGES", id="MESSAGES"),
            pytest.param(DownloadMode.NORMAL, "NORMAL", id="NORMAL"),
            pytest.param(DownloadMode.SINGLE, "SINGLE", id="SINGLE"),
            pytest.param(DownloadMode.STORIES, "STORIES", id="STORIES"),
            pytest.param(DownloadMode.TIMELINE, "TIMELINE", id="TIMELINE"),
            pytest.param(DownloadMode.WALL, "WALL", id="WALL"),
            pytest.param(DownloadMode.STASH_ONLY, "STASH_ONLY", id="STASH_ONLY"),
        ],
    )
    def test_member_value_repr_and_instantiation(
        self, member: DownloadMode, value: str
    ) -> None:
        """Each member equals its uppercase value, str() returns it, and
        instantiation from exact / lower / capitalized case resolves to the
        member (case-insensitive lookup via the _missing_ method)."""
        assert member == value
        assert str(member) == value
        assert DownloadMode(value) == member
        assert DownloadMode(value.lower()) == member
        assert DownloadMode(value.capitalize()) == member

    def test_case_sensitive_comparison_and_invalid_value(self) -> None:
        """Direct string comparison stays case-sensitive; unknown value raises."""
        # Direct string comparison is case-sensitive
        assert DownloadMode.NORMAL != "normal"  # type: ignore[comparison-overlap]  # intentionally compares against a different-case literal to pin case-sensitivity
        assert DownloadMode.NORMAL == "NORMAL"

        # Test with invalid value
        with pytest.raises(ValueError, match="invalid_mode"):
            DownloadMode("invalid_mode")
