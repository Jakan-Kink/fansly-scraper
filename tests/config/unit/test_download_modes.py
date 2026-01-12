"""Unit tests for config/modes.py"""

from config.modes import DownloadMode


class TestDownloadMode:
    """Tests for DownloadMode enum."""

    def test_enum_values(self):
        """Test that all expected enum values exist."""
        # StrEnum with auto() creates uppercase values
        assert DownloadMode.NOTSET.value == "NOTSET"
        assert DownloadMode.COLLECTION.value == "COLLECTION"
        assert DownloadMode.MESSAGES.value == "MESSAGES"
        assert DownloadMode.NORMAL.value == "NORMAL"
        assert DownloadMode.SINGLE.value == "SINGLE"
        assert DownloadMode.TIMELINE.value == "TIMELINE"
        assert DownloadMode.WALL.value == "WALL"
        assert DownloadMode.STASH_ONLY.value == "STASH_ONLY"

    def test_case_insensitive_lookup(self):
        """Test case-insensitive lookup via _missing_."""
        assert DownloadMode("TIMELINE") == DownloadMode.TIMELINE
        assert DownloadMode("timeline") == DownloadMode.TIMELINE
        assert DownloadMode("Timeline") == DownloadMode.TIMELINE
        assert DownloadMode("MESSAGES") == DownloadMode.MESSAGES
        assert DownloadMode("messages") == DownloadMode.MESSAGES

    def test_missing_returns_none_for_invalid(self):
        """Test that invalid values return None via _missing_."""
        result = DownloadMode._missing_("invalid_mode")
        assert result is None

    def test_missing_returns_none_for_non_string(self):
        """Test that non-string values return None via _missing_."""
        result = DownloadMode._missing_(999)
        assert result is None
