"""Unit tests for version utilities"""

import pytest
from toml import TomlDecodeError

from config.version import get_project_version


@pytest.mark.parametrize(
    "content",
    [
        # PEP 621 layout (the live project's actual layout).
        pytest.param(
            '[project]\nname = "fansly-scraper"\nversion = "1.0.0"\n',
            id="pep621-project-table",
        ),
        # Legacy Poetry layout (backward-compat fallback).
        pytest.param(
            '[tool.poetry]\nname = "fansly-downloader-ng"\nversion = "1.0.0"\n',
            id="legacy-poetry-table",
        ),
    ],
)
def test_get_project_version_valid(tmp_path, content):
    """Real pyproject.toml is parsed end-to-end and the version extracted."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content, encoding="utf-8")

    assert get_project_version(pyproject) == "1.0.0"


def test_get_project_version_default_path():
    """With no path argument the project's own pyproject.toml is read.

    Regression guard: the live pyproject.toml uses the PEP 621 ``[project]``
    table, which the original ``[tool.poetry].version`` lookup could not read
    (raised ``KeyError: 'version'``). ``get_project_version`` now prefers
    ``[project].version`` and falls back to ``[tool.poetry].version``.
    """
    version = get_project_version()
    assert isinstance(version, str)
    assert version


def test_get_project_version_file_not_found(tmp_path):
    """A missing pyproject.toml surfaces FileNotFoundError from the real open()."""
    missing = tmp_path / "does_not_exist.toml"
    with pytest.raises(FileNotFoundError):
        get_project_version(missing)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(
            '[tool.poetry]\nname = "fansly-downloader-ng"\n',
            id="missing-version-key",
        ),
        pytest.param("[tool]\n", id="missing-poetry-table"),
    ],
)
def test_get_project_version_missing_version_raises_keyerror(tmp_path, content):
    """Valid TOML lacking the version key raises KeyError after a real parse."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content, encoding="utf-8")
    with pytest.raises(KeyError):
        get_project_version(pyproject)


def test_get_project_version_malformed_toml(tmp_path):
    """Genuinely malformed TOML raises a real parse error from toml.load()."""
    pyproject = tmp_path / "pyproject.toml"
    # Unterminated string / dangling key is invalid TOML and fails to parse.
    pyproject.write_text("[tool.poetry\nversion = ", encoding="utf-8")
    with pytest.raises(TomlDecodeError):
        get_project_version(pyproject)
