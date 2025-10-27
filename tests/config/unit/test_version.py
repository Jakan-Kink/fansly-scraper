"""Unit tests for version utilities"""

from unittest.mock import mock_open, patch

import pytest

from config.version import get_project_version


def test_get_project_version():
    """Test getting project version from pyproject.toml"""
    mock_toml_content = """
[tool.poetry]
version = "1.0.0"
    """

    with (
        patch("builtins.open", mock_open(read_data=mock_toml_content)),
        patch("toml.load") as mock_toml_load,
    ):
        mock_toml_load.return_value = {"tool": {"poetry": {"version": "1.0.0"}}}

        version = get_project_version()
        assert version == "1.0.0"


def test_get_project_version_file_not_found():
    """Test handling when pyproject.toml is not found"""
    with patch("builtins.open", mock_open()) as mock_file:
        mock_file.side_effect = FileNotFoundError()
        with pytest.raises(FileNotFoundError):
            get_project_version()


def test_get_project_version_invalid_toml():
    """Test handling invalid TOML content"""
    with (
        patch("builtins.open", mock_open(read_data="invalid = toml")),
        patch("toml.load") as mock_toml_load,
    ):
        mock_toml_load.return_value = {}  # Empty dict to trigger KeyError
        with pytest.raises(KeyError):
            get_project_version()
