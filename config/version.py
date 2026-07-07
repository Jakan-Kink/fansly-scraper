"""Version utilities for the application."""

from pathlib import Path

import toml


def get_project_version(pyproject_path: Path | None = None) -> str:
    """Get the project version from pyproject.toml.

    Args:
        pyproject_path: Optional path to a pyproject.toml file. Defaults to
            the project's own pyproject.toml beside this package.

    Returns:
        str: The project version string
    """
    if pyproject_path is None:
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    with pyproject_path.open() as f:
        pyproject = toml.load(f)
    # PEP 621 `[project]` table is the source of truth; fall back to the
    # legacy `[tool.poetry]` table for older pyproject layouts.
    try:
        return pyproject["project"]["version"]
    except KeyError:
        return pyproject["tool"]["poetry"]["version"]
