"""Unit tests for config/__init__.py module."""

import pytest


class TestConfigInit:
    """Tests for config/__init__.py lazy loading."""

    def test_lazy_load_browser_function(self):
        """Test lazy loading of browser functions."""
        # Import at module level to test lazy loading
        import config

        # Access a browser function through __getattr__
        # This tests lines 84-87: the lazy loading path
        close_browser_by_name = config.close_browser_by_name

        # Verify it's a callable (function)
        assert callable(close_browser_by_name)

    def test_lazy_load_multiple_browser_functions(self):
        """Test lazy loading of multiple browser functions."""
        import config

        # Test several browser functions from _BROWSER_FUNCTIONS
        find_leveldb_folders = config.find_leveldb_folders
        assert callable(find_leveldb_folders)

        get_token_from_firefox_db = config.get_token_from_firefox_db
        assert callable(get_token_from_firefox_db)

        parse_browser_from_string = config.parse_browser_from_string
        assert callable(parse_browser_from_string)

    def test_invalid_attribute_raises_error(self):
        """Test that accessing invalid attribute raises AttributeError."""
        import config

        # Try to access an attribute that doesn't exist
        # This tests line 88: the AttributeError raise
        with pytest.raises(
            AttributeError, match="has no attribute 'nonexistent_function'"
        ):
            _ = config.nonexistent_function
