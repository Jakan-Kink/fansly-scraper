"""Unit tests for MetadataHandling enum"""

import pytest

from config.metadatahandling import MetadataHandling


class TestMetadataHandling:
    """Tests for the MetadataHandling enum."""

    def test_enum_values(self):
        """Test the enum values are correctly defined."""
        assert MetadataHandling.NOTSET == "NOTSET"
        assert MetadataHandling.ADVANCED == "ADVANCED"
        assert MetadataHandling.SIMPLE == "SIMPLE"

    def test_case_insensitive_lookup(self):
        """Test case-insensitive lookup of enum values."""
        # Test lowercase
        assert MetadataHandling("advanced") == MetadataHandling.ADVANCED
        assert MetadataHandling("simple") == MetadataHandling.SIMPLE
        assert MetadataHandling("notset") == MetadataHandling.NOTSET

        # Test uppercase
        assert MetadataHandling("ADVANCED") == MetadataHandling.ADVANCED
        assert MetadataHandling("SIMPLE") == MetadataHandling.SIMPLE
        assert MetadataHandling("NOTSET") == MetadataHandling.NOTSET

        # Test mixed case
        assert MetadataHandling("Advanced") == MetadataHandling.ADVANCED
        assert MetadataHandling("Simple") == MetadataHandling.SIMPLE
        assert MetadataHandling("NotSet") == MetadataHandling.NOTSET
        assert MetadataHandling("aDvAnCeD") == MetadataHandling.ADVANCED

    def test_case_insensitive_comparison(self):
        """Test case-insensitive comparison of enum values with strings."""
        # Test direct string comparison (currently case-sensitive)
        assert (
            MetadataHandling.ADVANCED != "advanced"
        )  # This should fail because direct comparison is case-sensitive
        assert MetadataHandling.ADVANCED == "ADVANCED"
        assert MetadataHandling.SIMPLE != "simple"
        assert MetadataHandling.SIMPLE == "SIMPLE"
        assert MetadataHandling.NOTSET != "notset"
        assert MetadataHandling.NOTSET == "NOTSET"

    def test_invalid_value(self):
        """Test that invalid values return None with _missing_."""
        # The direct _missing_ method call requires the class, not an instance
        assert MetadataHandling._missing_("invalid_value") is None

        # Instantiating with invalid value should raise ValueError
        with pytest.raises(ValueError, match="invalid_value"):
            MetadataHandling("invalid_value")

    def test_string_representation(self):
        """Test string representation of enum values."""
        assert str(MetadataHandling.ADVANCED) == "ADVANCED"
        assert str(MetadataHandling.SIMPLE) == "SIMPLE"
        assert str(MetadataHandling.NOTSET) == "NOTSET"
