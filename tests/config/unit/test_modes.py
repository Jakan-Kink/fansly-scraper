"""Unit tests for DownloadMode enum"""

import pytest

from config.modes import DownloadMode


class TestDownloadMode:
    """Tests for the DownloadMode enum."""

    def test_enum_values(self):
        """Test the enum values are correctly defined."""
        assert DownloadMode.NOTSET == "NOTSET"
        assert DownloadMode.COLLECTION == "COLLECTION"
        assert DownloadMode.MESSAGES == "MESSAGES"
        assert DownloadMode.NORMAL == "NORMAL"
        assert DownloadMode.SINGLE == "SINGLE"
        assert DownloadMode.TIMELINE == "TIMELINE"
        assert DownloadMode.WALL == "WALL"
        assert DownloadMode.STASH_ONLY == "STASH_ONLY"

    def test_case_insensitive_comparison(self):
        """Test case-insensitive comparison of enum values."""
        # Test with case-insensitive comparisons using the _missing_ method
        assert DownloadMode("normal") == DownloadMode.NORMAL
        assert DownloadMode("NORMAL") == DownloadMode.NORMAL

        # Direct string comparison is case-sensitive
        assert DownloadMode.NORMAL != "normal"
        assert DownloadMode.NORMAL == "NORMAL"

    def test_instantiation_with_string(self):
        """Test creating enum instance from string."""
        assert DownloadMode("normal") == DownloadMode.NORMAL
        assert DownloadMode("NORMAL") == DownloadMode.NORMAL
        assert DownloadMode("Normal") == DownloadMode.NORMAL

        # Test with invalid value
        with pytest.raises(ValueError, match="invalid_mode"):
            DownloadMode("invalid_mode")

    def test_string_representation(self):
        """Test string representation of enum values."""
        assert str(DownloadMode.NORMAL) == "NORMAL"
        assert str(DownloadMode.COLLECTION) == "COLLECTION"
        assert str(DownloadMode.MESSAGES) == "MESSAGES"
        assert str(DownloadMode.SINGLE) == "SINGLE"
        assert str(DownloadMode.TIMELINE) == "TIMELINE"
        assert str(DownloadMode.WALL) == "WALL"
        assert str(DownloadMode.STASH_ONLY) == "STASH_ONLY"
        assert str(DownloadMode.NOTSET) == "NOTSET"
