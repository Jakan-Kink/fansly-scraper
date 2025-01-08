"""Version utilities for the application."""

from pathlib import Path

import toml


def get_project_version() -> str:
    """Get the project version from pyproject.toml.

    Returns:
        str: The project version string
    """
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject_path) as f:
        pyproject = toml.load(f)
    return pyproject["tool"]["poetry"]["version"]
